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

import llm


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
        f"PERFIL:\n{profile_blob(profile)}\n\nEMPLEOS:\n{listing}"
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
        f"PERFIL:\n{profile_blob(profile)}\n\n"
        f"OFERTA: {job.get('title')} | {job.get('company') or '?'} | "
        f"{job.get('location') or ''} | {job.get('salary') or ''}"
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


def cover_letter(profile, job):
    """Carta de presentación a medida (texto plano). Devuelve {ok, text}."""
    instr = (
        "Escribe una carta de presentación breve (máx 160 palabras), en español, "
        "profesional y concreta, para que este candidato postule a la oferta. "
        "Sin encabezados de dirección ni fecha; empieza con 'Estimado equipo de...'. "
        "Conecta 2-3 fortalezas del perfil con lo que sugiere el puesto. "
        "Responde solo con la carta.\n\n"
        f"PERFIL:\n{profile_blob(profile)}\n\n"
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
