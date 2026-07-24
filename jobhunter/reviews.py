"""Genera un resumen de la reputación laboral de una empresa (Glassdoor y otros).

Usa el proveedor de IA activo (Claude o Gemini, ver llm.py) con búsqueda web /
grounding para resumir opiniones públicas. El proveedor se elige desde la UI
(por defecto Claude). Si falta la clave del proveedor activo, devuelve un
mensaje explicando cómo activarlo.
"""
import re

from jobhunter import llm

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
        f"- La SEGUNDA línea debe ser exactamente: GLASSDOOR: <URL directa de la "
        f"página de ESTA empresa en Glassdoor (la de Overview o Reviews, con su "
        f"employer id, p. ej. https://www.glassdoor.com/Overview/Working-at-...-EI_IE12345.11,20.htm), "
        f"tomada de los resultados de búsqueda>. Usa SOLO una URL real que hayas "
        f"visto en los resultados; NO inventes el id ni la URL. Si no la tienes con "
        f"seguridad, escribe exactamente: GLASSDOOR: none\n"
        f"- A partir de la tercera línea, el resumen en Markdown, empezando "
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


def _clean_glassdoor_url(cand):
    """Valida la URL de Glassdoor devuelta por la IA. Solo acepta una URL real de
    página de empresa (Overview/Reviews con employer id); si no, devuelve None."""
    if not cand:
        return None
    cand = cand.strip().strip("<>()[]").rstrip(".,;")
    low = cand.lower()
    if not low.startswith(("http://", "https://")) or "glassdoor.com" not in low:
        return None
    # Debe apuntar a una página de empresa, no al buscador ni a la home.
    if not re.search(r"/(overview|reviews|working-at|ei_ie|-e\d)", low):
        return None
    return cand


def _parse(text):
    """Extrae (resolved_name, glassdoor_url, summary) de la respuesta y limpia.

    Tolera artefactos de grounding de ambos proveedores: marcadores `[cite: 3]`
    de Gemini y etiquetas `<cite index="...">…</cite>` de Claude, además de la
    narración que Claude a veces intercala entre búsquedas antes del resumen."""
    # Quitar etiquetas de cita HTML de Claude, conservando su texto interior.
    text = re.sub(r"</?cite[^>]*>", "", text)
    # Quitar marcadores de cita de Gemini, p. ej. "[cite: 3, 7]".
    text = re.sub(r"\s*\[cite[^\]]*\]", "", text)

    resolved = None
    # La línea EMPRESA: puede venir precedida de narración; cortamos desde ella.
    m = re.search(r"EMPRESA:\s*(.+)", text)
    if m:
        # Nombre resuelto = resto de la línea de EMPRESA:.
        resolved = m.group(1).splitlines()[0].strip() or None
        text = text[m.end():]

    # Línea GLASSDOOR: <url> — la extraemos, validamos y la quitamos del texto.
    url = None
    mu = re.search(r"(?im)^\s*GLASSDOOR:\s*(\S+)", text)
    if mu:
        url = _clean_glassdoor_url(mu.group(1))
        text = re.sub(r"(?im)^\s*GLASSDOOR:.*$", "", text, count=1)

    lines = text.strip().splitlines()
    # Descartar líneas en blanco iniciales.
    while lines and not lines[0].strip():
        lines.pop(0)
    # Descartar una línea de preámbulo si no es viñeta/encabezado.
    if lines:
        first = lines[0].lstrip()
        if not first.startswith(("-", "*", "#", ">", "**")) and _PREAMBLE_RE.match(first):
            lines.pop(0)
    return resolved, url, "\n".join(lines).strip()


def generate_company_summary(company):
    """Devuelve dict {ok, summary, resolved, url}. Nunca lanza excepción."""
    ok, data = llm.complete([{"text": _prompt(company)}], json_out=False,
                            max_tokens=2048, web_search=True)
    if not ok:
        return {"ok": False, "resolved": None, "url": None,
                "summary": (data + "\n\nMientras tanto, usa el botón "
                            "**Ver en Glassdoor**.")}
    raw = (data or "").strip()
    if not raw:
        return {"ok": False, "resolved": None, "url": None,
                "summary": "El modelo no devolvió texto."}
    resolved, url, summary = _parse(raw)
    return {"ok": True, "resolved": resolved, "url": url, "summary": summary or raw}


if __name__ == "__main__":
    import sys
    res = generate_company_summary(sys.argv[1] if len(sys.argv) > 1 else "Truelogic")
    print("OK" if res["ok"] else "FALLO", "| resuelto:", res["resolved"])
    print("---")
    print(res["summary"])
