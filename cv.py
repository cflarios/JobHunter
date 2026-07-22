"""Funciones de CV + IA con Gemini (Google AI Studio).

- analyze_cv: extrae rol, seniority, años, skills, resumen y keywords de título.
- match_jobs: puntúa una lista de empleos contra el perfil (0-100 + razón).
- analyze_fit: análisis detallado de un empleo vs el CV (coincidencias, gaps).
- improve_cv: feedback estilo Harvard/ATS + reescritura del resumen/logros.
- cover_letter: carta de presentación a medida para una oferta.

Requiere GEMINI_API_KEY (o GOOGLE_API_KEY). Nunca lanza excepción hacia el caller.
"""
import os
import re
import json

import requests

MODEL = "gemini-2.5-flash"
ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent")
TIMEOUT = 90


def _key():
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def _gemini(parts, json_out=True, max_tokens=2048):
    """Llama a Gemini. Devuelve (ok, data_or_error). parts: lista de 'parts'."""
    key = _key()
    if not key:
        return False, "Falta GEMINI_API_KEY en el servicio (ver README)."
    gen = {"temperature": 0.3, "maxOutputTokens": max_tokens,
           "thinkingConfig": {"thinkingBudget": 0}}
    if json_out:
        gen["responseMimeType"] = "application/json"
    payload = {"contents": [{"parts": parts}], "generationConfig": gen}
    try:
        r = requests.post(ENDPOINT.format(model=MODEL), params={"key": key},
                          json=payload, timeout=TIMEOUT)
    except requests.exceptions.RequestException as e:
        return False, f"Error de red al consultar Gemini: {e}"
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", r.text[:200])
        except Exception:
            msg = r.text[:200]
        return False, f"Error de la API de Gemini ({r.status_code}): {msg}"
    try:
        cand = (r.json().get("candidates") or [])[0]
        txt = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    except Exception:
        return False, "Respuesta inesperada de Gemini."
    if not json_out:
        return True, txt.strip()
    # Parseo robusto de JSON (por si llega envuelto en ```json ... ```).
    try:
        return True, json.loads(txt)
    except json.JSONDecodeError:
        m = re.search(r"[\[{].*[\]}]", txt, re.S)
        if m:
            try:
                return True, json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return False, "Gemini no devolvió JSON válido."


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
        'viñetas de logros mejoradas con métricas de ejemplo si faltan"}. En español.\n\n'
        + base
    )
    ok, data = _gemini([{"text": instr}], json_out=True, max_tokens=2600)
    if not ok:
        return {"ok": False, "error": data}
    return {"ok": True, "feedback": str(data.get("feedback", "")),
            "rewrite": str(data.get("rewrite", ""))}


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
