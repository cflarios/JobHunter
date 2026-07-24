"""Funciones de CV + IA con el proveedor activo (Claude o Gemini, ver llm.py).

- analyze_cv: extrae rol, seniority, años, skills, resumen y keywords de título.
- match_jobs: puntúa una lista de empleos contra el perfil (0-100 + razón).
- analyze_fit: análisis detallado de un empleo vs el CV (coincidencias, gaps).
- improve_cv: feedback estilo Harvard/ATS + reescritura del resumen/logros.
- cover_letter: carta de presentación a medida para una oferta.

El proveedor de IA se elige desde la UI (por defecto Claude). Nunca lanza
excepción hacia el caller.
"""
import json

from jobhunter import llm


def _gemini(parts, json_out=True, max_tokens=2048):
    """Genera una respuesta con el proveedor activo. Devuelve (ok, data_or_error).
    Se conserva el nombre por compatibilidad; enruta a Claude o Gemini (llm.py)."""
    return llm.complete(parts, json_out=json_out, max_tokens=max_tokens)


def profile_blob(p):
    """Construye un resumen compacto del perfil para los prompts de match/fit."""
    if not p:
        return ""
    parts = []
    if p.get("role"):      parts.append(f"Rol: {p['role']}")
    if p.get("seniority"): parts.append(f"Seniority: {p['seniority']}")
    if p.get("years"):     parts.append(f"Años de experiencia: {p['years']}")
    if p.get("skills"):    parts.append(f"Skills: {p['skills']}")
    if p.get("summary"):   parts.append(f"Resumen: {p['summary']}")
    return "\n".join(parts)


def generated_cvs(profile):
    """profile.generated_cv → {lang: cv_dict}. Tolera el formato antiguo (CV plano
    sin claves de idioma), que se asume español. {} si no hay CV generado."""
    raw = (profile or {}).get("generated_cv")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    if any(k in ("es", "en") and isinstance(data.get(k), dict) for k in ("es", "en")):
        return {k: v for k, v in data.items() if k in ("es", "en") and isinstance(v, dict)}
    return {"es": data}


def cv_blob(cv):
    """Texto compacto de un CV estructurado (dict de build_cv) para los prompts."""
    if not isinstance(cv, dict):
        return ""
    out = []
    if cv.get("headline"): out.append(f"Titular: {cv['headline']}")
    if cv.get("summary"):  out.append(f"Resumen: {cv['summary']}")
    sk = cv.get("skills")
    if sk:
        out.append("Skills: " + (", ".join(map(str, sk)) if isinstance(sk, list) else str(sk)))
    exp = cv.get("experience")
    if isinstance(exp, list) and exp:
        out.append("Experiencia:")
        for e in exp[:6]:
            if not isinstance(e, dict):
                continue
            head = " | ".join(x for x in [e.get("title"), e.get("company"), e.get("period")] if x)
            out.append(f"- {head}")
            for b in (e.get("bullets") or [])[:5]:
                out.append(f"    · {b}")
    edu = cv.get("education")
    if isinstance(edu, list) and edu:
        out.append("Educación: " + "; ".join(
            " ".join(str(x) for x in [d.get("degree"), d.get("institution")] if x)
            for d in edu[:4] if isinstance(d, dict)))
    certs = cv.get("certifications")
    if certs:
        out.append("Certificaciones: " + (", ".join(map(str, certs))
                                          if isinstance(certs, list) else str(certs)))
    return "\n".join(out)


def reference_blob(profile):
    """Perfil de REFERENCIA para matching y análisis.

    Prefiere el **CV generado** (más rico y ya optimizado Harvard/ATS: titular,
    resumen, skills, experiencia con logros) sobre los campos sueltos del perfil.
    Cae al perfil extraído si aún no se ha generado un CV nuevo.
    """
    cvs = generated_cvs(profile)
    cv = cvs.get("es") or cvs.get("en") or (next(iter(cvs.values())) if cvs else None)
    if not cv:
        return profile_blob(profile)
    blob = cv_blob(cv)
    if not blob.strip():
        return profile_blob(profile)
    # Añadimos seniority/años del perfil: el CV estructurado no los lleva explícitos.
    extra = []
    p = profile or {}
    if p.get("seniority"): extra.append(f"Seniority: {p['seniority']}")
    if p.get("years"):     extra.append(f"Años de experiencia: {p['years']}")
    return "\n".join(extra + [blob])


# --------------------------------------------------------------------------- #
def analyze_cv(text=None, pdf_b64=None):
    """Extrae el perfil del CV. Devuelve {ok, ...campos} o {ok:False, error}."""
    instr = (
        "Eres un reclutador técnico. Analiza el CV proporcionado y extrae el "
        "perfil profesional. Responde SOLO con un objeto JSON con estas claves:\n"
        '{"role": "rol principal/título profesional", '
        '"seniority": "junior|semi-senior|senior|lead", '
        '"years": "años de experiencia (número aproximado como texto)", '
        '"skills": "10-15 habilidades/tecnologías separadas por coma, las más relevantes", '
        '"summary": "resumen del perfil en 2-3 frases", '
        '"suggested_keywords": "3-6 palabras clave de TÍTULO para buscar empleos, '
        'separadas por coma (p. ej. devops, sre, platform engineer)"}\n'
        "En español. No inventes; básate solo en el CV."
    )
    parts = [{"text": instr}]
    if pdf_b64:
        parts.append({"inline_data": {"mime_type": "application/pdf", "data": pdf_b64}})
    elif text:
        parts.append({"text": "CV:\n" + text[:20000]})
    else:
        return {"ok": False, "error": "No se proporcionó CV."}
    ok, data = _gemini(parts, json_out=True, max_tokens=1500)
    if not ok:
        return {"ok": False, "error": data}
    out = {"ok": True}
    for k in ("role", "seniority", "years", "skills", "summary", "suggested_keywords"):
        v = data.get(k)
        out[k] = ", ".join(map(str, v)) if isinstance(v, list) else (str(v) if v is not None else "")
    return out


def match_jobs(profile, jobs):
    """Puntúa empleos contra el perfil. jobs: [{id,title,company,location,salary}].
    Devuelve {ok, matches:[{id,score,reason}]}."""
    if not jobs:
        return {"ok": True, "matches": []}
    listing = "\n".join(
        f'- id={j["id"]} | {j["title"]} | {j.get("company") or "?"} | {j.get("location") or ""}'
        for j in jobs)
    instr = (
        "Eres un asesor de carrera. Dado el PERFIL del candidato y una lista de "
        "EMPLEOS, puntúa cada empleo de 0 a 100 según lo bien que encaja con el "
        "perfil (rol, seniority, skills). Responde SOLO con un array JSON: "
        '[{"id": <id>, "score": <0-100>, "reason": "<motivo breve, máx 12 palabras>"}]. '
        "Incluye TODOS los ids. En español.\n\n"
        f"PERFIL:\n{reference_blob(profile)}\n\nEMPLEOS:\n{listing}"
    )
    ok, data = _gemini([{"text": instr}], json_out=True, max_tokens=4000)
    if not ok:
        return {"ok": False, "error": data}
    if isinstance(data, dict):
        data = data.get("matches") or data.get("results") or []
    clean = []
    for m in data:
        try:
            clean.append({"id": int(m["id"]), "score": max(0, min(100, int(m["score"]))),
                          "reason": str(m.get("reason", ""))[:140]})
        except (KeyError, ValueError, TypeError):
            continue
    return {"ok": True, "matches": clean}


def analyze_fit(profile, job):
    """Análisis detallado de un empleo vs el CV. Devuelve {ok, score, html}."""
    instr = (
        "Eres un asesor de carrera. Compara el PERFIL con la OFERTA y responde "
        "SOLO con JSON: "
        '{"score": <0-100>, "matched": ["skills/requisitos que el candidato SÍ cumple"], '
        '"gaps": ["lo que la oferta pide y el candidato podría no tener"], '
        '"highlight": "1-2 frases: qué resaltar al postular"}. En español.\n\n'
        f"PERFIL:\n{reference_blob(profile)}\n\n"
        f"OFERTA: {job.get('title')} | {job.get('company') or '?'} | "
        f"{job.get('location') or ''} | {job.get('salary') or ''}"
        + (f"\nDESCRIPCIÓN:\n{str(job['description'])[:6000]}"
           if job.get("description") else "")
    )
    ok, data = _gemini([{"text": instr}], json_out=True, max_tokens=1500)
    if not ok:
        return {"ok": False, "error": data}
    score = 0
    try:
        score = max(0, min(100, int(data.get("score", 0))))
    except (ValueError, TypeError):
        pass
    def _md_list(items):
        return "\n".join(f"- {i}" for i in (items or []) if i)
    html = (
        f"**Afinidad: {score}%**\n\n"
        f"**✓ Coincides en:**\n{_md_list(data.get('matched'))}\n\n"
        f"**△ Podrían pedirte además:**\n{_md_list(data.get('gaps'))}\n\n"
        f"**➤ Al postular:** {data.get('highlight','')}"
    )
    return {"ok": True, "score": score, "html": html}


def improve_cv(profile, cv_text=None):
    """Feedback estilo Harvard/ATS + reescritura. Devuelve {ok, feedback, rewrite}."""
    base = f"PERFIL:\n{profile_blob(profile)}"
    if cv_text:
        base += "\n\nTEXTO DEL CV:\n" + cv_text[:16000]
    instr = (
        "Eres un experto en CVs técnicos (estándar Harvard y compatibilidad ATS). "
        "Responde SOLO con JSON: "
        '{"feedback": "markdown con 4-6 recomendaciones accionables (logros '
        'cuantificados, verbos de acción, formato ATS, qué quitar/añadir)", '
        '"rewrite": "markdown: un resumen profesional reescrito (3-4 líneas) y 3-5 '
        'viñetas de logros mejoradas con métricas de ejemplo si faltan"}. En español.\n'
        "FORMATO Markdown permitido: SOLO **negrita** y viñetas con «- ». "
        "PROHIBIDO usar encabezados (#, ##, ###) o cualquier otro símbolo de título.\n\n"
        + base
    )
    ok, data = _gemini([{"text": instr}], json_out=True, max_tokens=2600)
    if not ok:
        return {"ok": False, "error": data}
    return {"ok": True, "feedback": str(data.get("feedback", "")),
            "rewrite": str(data.get("rewrite", ""))}


def build_cv(profile, lang="es"):
    """Genera un CV nuevo, estructurado y optimizado (ATS/Harvard) a partir del CV
    original + las recomendaciones ya calculadas. `lang`: 'es' o 'en' (idioma del
    contenido). Devuelve {ok, cv} donde `cv` es un dict con las secciones, o
    {ok:False, error}. No inventa datos: usa solo lo que aparece en el CV/perfil;
    deja vacío u omite lo que no exista."""
    p = profile or {}
    lang = "en" if lang == "en" else "es"
    if lang == "en":
        lang_rule = ("- Write the ENTIRE CV in English (summary, bullets, headline, "
                     "everything). Translate content from the source if needed.\n")
        langs_example = '"languages":["Spanish (native)","English (professional)"]'
    else:
        lang_rule = "- Redacta TODO el CV en español.\n"
        langs_example = '"languages":["Español (nativo)","Inglés (profesional)"]'
    fuentes = [f"PERFIL EXTRAÍDO:\n{profile_blob(p)}"]
    if p.get("cv_text"):
        fuentes.append("CV ORIGINAL (texto):\n" + str(p["cv_text"])[:16000])
    if p.get("feedback"):
        fuentes.append("RECOMENDACIONES (Harvard/ATS):\n" + str(p["feedback"])[:4000])
    if p.get("rewrite"):
        fuentes.append("RESUMEN Y LOGROS REESCRITOS:\n" + str(p["rewrite"])[:4000])

    instr = (
        "Eres un experto en CVs técnicos (estándar Harvard, compatible con ATS). "
        "A partir de las FUENTES, redacta un CV NUEVO, aplicando las recomendaciones. "
        "REGLAS ESTRICTAS:\n"
        "- Usa SOLO información real presente en las fuentes. NO inventes empresas, "
        "fechas, títulos ni datos de contacto. Si un dato no aparece, deja el campo "
        "vacío (\"\") o la lista vacía; para contacto desconocido usa \"\".\n"
        "- Cuantifica logros cuando la fuente lo permita; verbos de acción; conciso.\n"
        "- Debe caber en 2 páginas: máx. 4 experiencias y máx. 5 viñetas por experiencia.\n"
        + lang_rule +
        "Responde SOLO con este JSON (sin texto alrededor):\n"
        '{"name":"nombre completo o \\"\\"", '
        '"headline":"titular profesional, p. ej. \'DevOps Engineer | SRE\'", '
        '"contact":{"email":"","phone":"","location":"","links":["url perfil/portafolio", "..."]}, '
        '"summary":"resumen profesional de 2-3 frases", '
        '"skills":["skill1","skill2", "..."], '
        '"experience":[{"title":"","company":"","location":"","period":"","bullets":["logro cuantificado","..."]}], '
        '"education":[{"degree":"","institution":"","period":""}], '
        '"certifications":["..."], '
        + langs_example + '}\n\n'
        + "\n\n".join(fuentes)
    )
    ok, data = _gemini([{"text": instr}], json_out=True, max_tokens=4000)
    if not ok:
        return {"ok": False, "error": data}
    if not isinstance(data, dict):
        return {"ok": False, "error": "La IA no devolvió un CV estructurado."}
    return {"ok": True, "cv": data}


def _norm(s):
    return " ".join(str(s or "").lower().split())


def _enforce_facts(base_cv, cv):
    """Blinda los datos verificables del CV adaptado contra alucinaciones.

    La optimización ATS puede reordenar skills, reformular viñetas, el titular y el
    resumen — pero **nunca** los hechos comprobables. Aquí se restauran desde el CV
    base: identidad y contacto; y por cada experiencia el **cargo**, la empresa, el
    período y la ubicación (se casan por empresa). Se conserva además el **orden
    cronológico** del CV base. Educación e idiomas se copian tal cual y las
    certificaciones se filtran a las que ya existían. Una experiencia cuya empresa
    no esté en el CV base se descarta (sería inventada).

    Devuelve (cv_corregido, [correcciones_aplicadas]).
    """
    if not isinstance(base_cv, dict) or not isinstance(cv, dict):
        return cv, []
    fixed = []
    touched_id = False
    for k in ("name", "contact"):          # identidad y contacto: intocables
        if base_cv.get(k) is not None and cv.get(k) != base_cv.get(k):
            cv[k] = base_cv.get(k)
            touched_id = True
    if touched_id:
        fixed.append("Se restauraron tu nombre y datos de contacto originales.")
    base_exp = [e for e in (base_cv.get("experience") or []) if isinstance(e, dict)]
    base_keys = {_norm(e.get("company")) for e in base_exp}
    chosen = {}
    for e in (cv.get("experience") or []):
        if not isinstance(e, dict):
            continue
        key = _norm(e.get("company"))
        if key not in base_keys:
            fixed.append(f"Experiencia descartada (empresa que no está en tu CV): "
                         f"{e.get('company') or '—'}")
        elif key not in chosen:
            chosen[key] = e
    out_exp = []
    for b in base_exp:                     # emitimos en el ORDEN del CV base
        e = chosen.get(_norm(b.get("company")))
        if e is None:
            continue                       # la IA la omitió por relevancia: se respeta
        for k in ("title", "company", "period", "location"):
            if b.get(k) is not None and e.get(k) != b.get(k):
                if k == "title":
                    fixed.append(f"Cargo restaurado en {b.get('company')}: "
                                 f"«{e.get(k)}» → «{b.get(k)}»")
                e[k] = b.get(k)
        out_exp.append(e)
    if [_norm(x.get("company")) for x in (cv.get("experience") or []) if isinstance(x, dict)] \
            != [_norm(x.get("company")) for x in out_exp]:
        fixed.append("Se restauró el orden cronológico de tus experiencias.")
    cv["experience"] = out_exp
    for k in ("education", "languages"):   # no hay razón para tocarlos
        if base_cv.get(k) is not None:
            cv[k] = base_cv.get(k)
    base_certs = base_cv.get("certifications")
    if isinstance(base_certs, list):
        allowed = {_norm(c) for c in base_certs}
        got = [c for c in (cv.get("certifications") or [])]
        for c in got:
            if _norm(c) not in allowed:
                fixed.append(f"Certificación descartada (no está en tu CV): {c}")
        cv["certifications"] = [c for c in got if _norm(c) in allowed] or base_certs
    return cv, fixed


def tailor_cv(base_cv, job, lang="es", job_desc=None, profile=None):
    """Adapta el CV YA GENERADO a una vacante concreta para maximizar el paso por ATS.

    `base_cv`: dict del CV generado (build_cv). `job`: fila del empleo.
    `job_desc`: texto de la oferta pegado por el usuario (opcional pero muy
    recomendable: es de donde salen las keywords reales del ATS).

    **No inventa nada**: solo reordena, reformula con la terminología de la vacante
    y hace aflorar lo que ya está en el CV. Lo que la oferta pide y el CV no
    respalda se reporta en `gaps`, nunca se falsea.

    Devuelve {ok, cv, notes, ats_score} o {ok:False, error}.
    """
    lang = "en" if lang == "en" else "es"
    lang_rule = ("- Write the ENTIRE tailored CV in English.\n" if lang == "en"
                 else "- Redacta TODO el CV adaptado en español.\n")
    oferta = (f"Puesto: {job.get('title')}\n"
              f"Empresa: {job.get('company') or '?'}\n"
              f"Ubicación: {job.get('location') or ''}\n"
              f"Salario: {job.get('salary') or 'no indicado'}\n"
              f"Skills detectadas: {job.get('skills') or '—'}")
    fuentes = [f"CV BASE (JSON del CV ya generado):\n{json.dumps(base_cv, ensure_ascii=False)}",
               f"VACANTE:\n{oferta}"]
    if job_desc:
        fuentes.append("DESCRIPCIÓN DE LA VACANTE (fuente principal de keywords ATS):\n"
                       + str(job_desc)[:12000])
    if (profile or {}).get("cv_text"):
        fuentes.append("CV ORIGINAL (por si hay detalles reales no incluidos en el CV base):\n"
                       + str(profile["cv_text"])[:8000])

    instr = (
        "Eres un experto en optimización de CVs para sistemas ATS (Applicant "
        "Tracking Systems). Adapta el CV BASE a la VACANTE para maximizar la "
        "probabilidad de superar el filtro automático y la criba del reclutador.\n"
        "REGLAS ESTRICTAS (inviolables):\n"
        "- **NO inventes NADA**: ni empresas, ni fechas, ni títulos, ni "
        "certificaciones, ni tecnologías que el candidato no tenga. Es fraude y "
        "se detecta en la entrevista.\n"
        "- **NUNCA cambies el cargo (`title`), la empresa (`company`), el período "
        "(`period`) ni la ubicación de una experiencia**: cópialos LITERALMENTE del "
        "CV BASE. Si el candidato fue 'DevOps Engineer', NO lo conviertas en 'Site "
        "Reliability Engineer' aunque la vacante lo pida — eso es falsear su "
        "historial. La afinidad con el puesto se transmite en el titular, el "
        "resumen y las viñetas, no falseando cargos.\n"
        "- **NO reordenes las experiencias**: mantén el mismo orden cronológico "
        "inverso del CV BASE. Sí puedes reordenar las viñetas dentro de cada "
        "experiencia y la lista de skills.\n"
        "- **No infles la seniority** en el titular ni en el resumen (si el perfil "
        "es semi-senior, no escribas 'Senior').\n"
        "- No toques educación, idiomas ni datos de contacto.\n"
        "- SÍ puedes: reordenar skills y viñetas por relevancia; reformular usando "
        "la TERMINOLOGÍA EXACTA de la vacante cuando describa de verdad lo que el "
        "candidato hizo (p. ej. si el CV dice 'contenedores' y la oferta dice "
        "'Docker/Kubernetes' y el CV los menciona, usa los términos de la oferta); "
        "reescribir titular y resumen para espejar el puesto; destacar las "
        "experiencias y logros más relevantes y restar espacio a los que no lo son.\n"
        "- Incorpora las keywords de la vacante SOLO si el CV las respalda.\n"
        "- Lo que la vacante pide y el CV NO respalda va en \"gaps\" (para que el "
        "candidato lo sepa), NUNCA dentro del CV.\n"
        "- Mantén máx. 4 experiencias y máx. 5 viñetas por experiencia (≤2 páginas).\n"
        "- Conserva los datos de contacto y el nombre tal cual vienen en el CV BASE.\n"
        + lang_rule +
        "Responde SOLO con este JSON (sin texto alrededor):\n"
        '{"cv":{"name":"","headline":"","contact":{"email":"","phone":"","location":"","links":[]},'
        '"summary":"","skills":[],"experience":[{"title":"","company":"","location":"",'
        '"period":"","bullets":[]}],"education":[{"degree":"","institution":"","period":""}],'
        '"certifications":[],"languages":[]},'
        '"ats_score":<0-100: encaje estimado del CV adaptado con la vacante>,'
        '"keywords_added":["keywords de la vacante que SÍ se han podido incorporar"],'
        '"changes":["cambios concretos hechos, máx 6"],'
        '"gaps":["requisitos de la vacante que el CV no respalda, máx 6"]}\n\n'
        + "\n\n".join(fuentes)
    )
    ok, data = _gemini([{"text": instr}], json_out=True, max_tokens=4500)
    if not ok:
        return {"ok": False, "error": data}
    if not isinstance(data, dict) or not isinstance(data.get("cv"), dict):
        return {"ok": False, "error": "La IA no devolvió un CV adaptado válido."}
    try:
        score = max(0, min(100, int(data.get("ats_score", 0))))
    except (ValueError, TypeError):
        score = 0

    def _lst(key):
        v = data.get(key) or []
        return [str(x) for x in v if x] if isinstance(v, list) else ([str(v)] if v else [])

    # Red de seguridad: la IA no siempre respeta las reglas de integridad, así que
    # los hechos comprobables se restauran desde el CV base antes de guardar nada.
    cv_out, fixed = _enforce_facts(base_cv, data["cv"])

    def _md(title, items):
        return f"**{title}**\n" + ("\n".join(f"- {i}" for i in items) if items
                                   else "- —") + "\n\n"
    notes = (f"**Encaje ATS estimado: {score}%**\n\n"
             + _md("🔑 Keywords incorporadas", _lst("keywords_added"))
             + _md("✎ Cambios aplicados", _lst("changes"))
             + _md("△ No respaldado por tu CV (revísalo tú)", _lst("gaps")))
    if fixed:
        notes += _md("🛡 Correcciones automáticas de integridad", fixed)
    return {"ok": True, "cv": cv_out, "notes": notes, "ats_score": score}


def cover_letter(profile, job):
    """Carta de presentación a medida (texto plano). Devuelve {ok, text}."""
    instr = (
        "Escribe una carta de presentación breve (máx 160 palabras), en español, "
        "profesional y concreta, para que este candidato postule a la oferta. "
        "Sin encabezados de dirección ni fecha; empieza con 'Estimado equipo de...'. "
        "Conecta 2-3 fortalezas del perfil con lo que sugiere el puesto. "
        "Responde solo con la carta.\n\n"
        f"PERFIL:\n{reference_blob(profile)}\n\n"
        f"OFERTA: {job.get('title')} | {job.get('company') or ''} | {job.get('location') or ''}"
    )
    ok, data = _gemini([{"text": instr}], json_out=False, max_tokens=1200)
    if not ok:
        return {"ok": False, "error": data}
    return {"ok": True, "text": data}


if __name__ == "__main__":
    import sys
    r = analyze_cv(text=sys.argv[1] if len(sys.argv) > 1 else
                   "Ingeniero DevOps con 5 años. Kubernetes, Terraform, AWS, CI/CD, Docker, Python.")
    print(json.dumps(r, ensure_ascii=False, indent=2))
