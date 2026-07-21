"""Genera un resumen de la reputación laboral de una empresa (Glassdoor y otros).

Usa la API de Gemini (Google AI Studio) con grounding de Google Search para
resumir opiniones públicas. Requiere la variable de entorno GEMINI_API_KEY
(o GOOGLE_API_KEY); si no está, devuelve un mensaje explicando cómo activarlo.
"""
import os
import re

import requests

MODEL = "gemini-2.5-flash"
ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent")
TIMEOUT = 60

# Frases de preámbulo que a veces anteceden al resumen y hay que descartar.
_PREAMBLE_RE = re.compile(
    r"^(claro|por supuesto|con gusto|aqu[ií]|a continuaci[oó]n|el siguiente|"
    r"este es|aqu[ií] tienes|aqu[ií] est[aá]|presento|te presento|"
    r"resumen de la reputaci[oó]n)", re.I)


def _prompt(company):
    return (
        f"Investiga en la web la reputación laboral de la empresa «{company}», "
        f"priorizando Glassdoor (calificación general sobre 5 y reseñas de "
        f"empleados) y, si ayuda, Indeed o Comparably.\n\n"
        f"El nombre puede estar incompleto o abreviado en mi consulta. Averigua "
        f"el nombre COMPLETO y oficial de la empresa consultando su sitio web, su "
        f"página de LinkedIn y Glassdoor, e incluye los sufijos que correspondan "
        f"(por ejemplo «Software», «Solutions», «Technologies», «Inc», «LLC»). "
        f"En Glassdoor elige la entidad que mejor corresponde y que tiene MÁS "
        f"reseñas. Ejemplo: 'Truelogic' → 'Truelogic Software Solutions'; "
        f"'Lemon.io' → 'Lemon.io'. Si de verdad no puedes ampliarlo, devuelve el "
        f"nombre tal cual.\n\n"
        f"FORMATO DE RESPUESTA (respétalo al pie de la letra):\n"
        f"- La PRIMERA línea debe ser exactamente: EMPRESA: <nombre completo canónico>\n"
        f"- A partir de la segunda línea, el resumen en Markdown, empezando "
        f"DIRECTAMENTE con la viñeta «- **Glassdoor:**». NADA antes.\n\n"
        f"Estructura del resumen:\n"
        f"- **Glassdoor:** calificación X/5 y nº aproximado de reseñas (si la encuentras).\n"
        f"- **Pros:** 3 viñetas con lo mejor valorado.\n"
        f"- **Contras:** 3 viñetas con las quejas más comunes.\n"
        f"- **Trabajo remoto / equilibrio:** 1-2 frases.\n"
        f"- **Veredicto:** 1-2 frases para alguien que evalúa un puesto remoto de "
        f"DevOps como contractor desde Colombia.\n\n"
        f"PROHIBIDO escribir cualquier frase introductoria, saludo o cierre "
        f"(nada de 'Aquí tienes', 'A continuación', 'Espero que te sirva'). "
        f"No inventes cifras; si no hay datos fiables de Glassdoor, dilo. "
        f"Sé conciso (máx. ~180 palabras)."
    )


def _parse(text):
    """Extrae el nombre resuelto (línea EMPRESA:) y limpia el preámbulo."""
    # Quitar marcadores de cita del grounding, p. ej. "[cite: 3, 7]".
    text = re.sub(r"\s*\[cite[^\]]*\]", "", text)
    resolved = None
    lines = text.strip().splitlines()
    if lines and lines[0].strip().upper().startswith("EMPRESA:"):
        resolved = lines[0].split(":", 1)[1].strip() or None
        lines = lines[1:]
    # Descartar líneas en blanco iniciales.
    while lines and not lines[0].strip():
        lines.pop(0)
    # Descartar una línea de preámbulo si no es viñeta/encabezado.
    if lines:
        first = lines[0].lstrip()
        if not first.startswith(("-", "*", "#", ">", "**")) and _PREAMBLE_RE.match(first):
            lines.pop(0)
    return resolved, "\n".join(lines).strip()


def generate_company_summary(company):
    """Devuelve dict {ok, summary, resolved}. Nunca lanza excepción."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return {
            "ok": False, "resolved": None,
            "summary": (
                "El resumen automático de opiniones requiere una **API key de "
                "Google AI Studio (Gemini)**. Define `GEMINI_API_KEY` en el "
                "servicio (ver README) y reinicia. Mientras tanto, usa el botón "
                "**Ver en Glassdoor**."
            ),
        }
    try:
        payload = {
            "contents": [{"parts": [{"text": _prompt(company)}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 2048,
                # Gemini 2.5 gasta tokens de "thinking" que cuentan contra el
                # límite y truncan el resumen; lo desactivamos.
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        r = requests.post(
            ENDPOINT.format(model=MODEL),
            params={"key": api_key}, json=payload, timeout=TIMEOUT,
        )
        if r.status_code != 200:
            msg = r.json().get("error", {}).get("message", r.text[:200]) \
                if r.headers.get("content-type", "").startswith("application/json") \
                else r.text[:200]
            return {"ok": False, "resolved": None,
                    "summary": f"Error de la API de Gemini ({r.status_code}): {msg}"}

        data = r.json()
        cands = data.get("candidates") or []
        if not cands:
            fb = data.get("promptFeedback", {})
            return {"ok": False, "resolved": None,
                    "summary": f"Sin respuesta de Gemini ({fb.get('blockReason', 'motivo desconocido')})."}
        parts = (cands[0].get("content") or {}).get("parts") or []
        raw = "".join(p.get("text", "") for p in parts).strip()
        if not raw:
            return {"ok": False, "resolved": None, "summary": "Gemini no devolvió texto."}
        resolved, summary = _parse(raw)
        return {"ok": True, "resolved": resolved, "summary": summary or raw}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "resolved": None, "summary": f"Error de red al consultar Gemini: {e}"}
    except Exception as e:
        return {"ok": False, "resolved": None, "summary": f"Error al generar el resumen: {e}"}


if __name__ == "__main__":
    import sys
    res = generate_company_summary(sys.argv[1] if len(sys.argv) > 1 else "Truelogic")
    print("OK" if res["ok"] else "FALLO", "| resuelto:", res["resolved"])
    print("---")
    print(res["summary"])
