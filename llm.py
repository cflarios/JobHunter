"""Capa de proveedor de IA: enruta las llamadas a **Claude (Anthropic)** o
**Gemini (Google AI Studio)** según el ajuste `ai_provider` (por defecto claude).

Ambos módulos de IA del proyecto (cv.py y reviews.py) hablan con este módulo en
vez de con un proveedor concreto. El proveedor se elige desde la UI (página
Búsquedas) y se guarda en `settings.ai_provider`; también puede forzarse con la
variable de entorno `AI_PROVIDER` para ejecuciones manuales.

Entrada normalizada estilo Gemini `parts`: lista de dicts
  {"text": "..."}                                   → texto
  {"inline_data": {"mime_type": "...", "data": b64}} → adjunto (p. ej. PDF)

Contrato uniforme: todas las funciones devuelven `(ok, data_or_error)`.
- Con `json_out=True`, `data` es el objeto JSON ya parseado.
- Con `json_out=False`, `data` es el texto de la respuesta.
Nunca lanzan excepción hacia el caller.
"""
import os
import re
import json

import requests

# --- Modelos --------------------------------------------------------------- #
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
                   "{model}:generateContent")
# Por defecto el modelo Claude más capaz; se puede fijar otro con ANTHROPIC_MODEL.
CLAUDE_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")
# Herramienta de búsqueda web de Claude (grounding); versión para Opus 4.6+/Sonnet.
CLAUDE_WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search"}

TIMEOUT = 90
DEFAULT_PROVIDER = "claude"
PROVIDERS = ("claude", "gemini")


def resolve_provider(explicit=None):
    """Determina el proveedor a usar: argumento explícito → env → ajuste en BD."""
    if explicit in PROVIDERS:
        return explicit
    env = os.environ.get("AI_PROVIDER")
    if env in PROVIDERS:
        return env
    try:
        from db import get_db, get_setting
        con = get_db()
        p = get_setting(con, "ai_provider", DEFAULT_PROVIDER)
        con.close()
        return p if p in PROVIDERS else DEFAULT_PROVIDER
    except Exception:
        return DEFAULT_PROVIDER


def provider_label(provider=None):
    p = resolve_provider(provider)
    return "Claude (Anthropic)" if p == "claude" else "Gemini (Google)"


# --- Utilidades ------------------------------------------------------------ #
def _parse_json(txt):
    """Parseo robusto de JSON (por si llega envuelto en ```json ... ``` o con
    texto alrededor). Devuelve (ok, obj_or_error)."""
    try:
        return True, json.loads(txt)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"[\[{].*[\]}]", txt or "", re.S)
        if m:
            try:
                return True, json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return False, "El modelo no devolvió JSON válido."


# --- Gemini ---------------------------------------------------------------- #
def _gemini_key():
    import keystore
    return keystore.get_api_key("gemini")


def _gemini_complete(parts, json_out, max_tokens, web_search):
    key = _gemini_key()
    if not key:
        return False, ("Falta GEMINI_API_KEY en el servicio (Google AI Studio). "
                       "Defínela en el .env o cambia el proveedor a Claude.")
    gen = {"temperature": 0.3, "maxOutputTokens": max_tokens,
           # Gemini 2.5 gasta tokens de "thinking" que truncan la salida.
           "thinkingConfig": {"thinkingBudget": 0}}
    if json_out:
        gen["responseMimeType"] = "application/json"
    payload = {"contents": [{"parts": parts}], "generationConfig": gen}
    if web_search:
        payload["tools"] = [{"google_search": {}}]
    try:
        r = requests.post(GEMINI_ENDPOINT.format(model=GEMINI_MODEL),
                          params={"key": key}, json=payload, timeout=TIMEOUT)
    except requests.exceptions.RequestException as e:
        return False, f"Error de red al consultar Gemini: {e}"
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", r.text[:200])
        except Exception:
            msg = r.text[:200]
        return False, f"Error de la API de Gemini ({r.status_code}): {msg}"
    try:
        cands = r.json().get("candidates") or []
        if not cands:
            fb = r.json().get("promptFeedback", {})
            return False, f"Sin respuesta de Gemini ({fb.get('blockReason', 'motivo desconocido')})."
        cand = cands[0]
        txt = "".join(p.get("text", "")
                      for p in cand.get("content", {}).get("parts", []))
    except Exception:
        return False, "Respuesta inesperada de Gemini."
    txt = (txt or "").strip()
    if not txt:
        return False, "Gemini no devolvió texto."
    return (_parse_json(txt) if json_out else (True, txt))


# --- Claude (Anthropic) ---------------------------------------------------- #
def _anthropic_key():
    import keystore
    return keystore.get_api_key("anthropic")


def _to_claude_content(parts):
    """Traduce los `parts` estilo Gemini a bloques de contenido de Claude."""
    blocks = []
    for p in parts:
        if "text" in p:
            blocks.append({"type": "text", "text": p["text"]})
        elif "inline_data" in p:
            d = p["inline_data"]
            mime = d.get("mime_type", "")
            if mime == "application/pdf":
                blocks.append({"type": "document", "source": {
                    "type": "base64", "media_type": mime, "data": d["data"]}})
            elif mime.startswith("image/"):
                blocks.append({"type": "image", "source": {
                    "type": "base64", "media_type": mime, "data": d["data"]}})
            # otros tipos se ignoran silenciosamente
    return blocks


def _claude_complete(parts, json_out, max_tokens, web_search):
    key = _anthropic_key()
    if not key:
        return False, ("Falta ANTHROPIC_API_KEY en el servicio. Defínela en el "
                       ".env o cambia el proveedor a Gemini.")
    try:
        import anthropic
    except ImportError:
        return False, "El SDK de Anthropic no está instalado (pip install anthropic)."

    client = anthropic.Anthropic(api_key=key)
    system = ("Eres un asistente preciso. Responde solo con lo que se te pide, "
              "sin preámbulos ni comentarios adicionales.")
    if json_out:
        system += " Cuando se pida JSON, devuelve únicamente JSON válido, sin ``` ni texto alrededor."

    kwargs = {"model": CLAUDE_MODEL, "max_tokens": max_tokens, "system": system}
    if web_search:
        kwargs["tools"] = [CLAUDE_WEB_SEARCH]

    messages = [{"role": "user", "content": _to_claude_content(parts)}]
    try:
        # Las herramientas de servidor (búsqueda web) pueden pausar el turno; se
        # reanuda reenviando la conversación hasta un número acotado de veces.
        for _ in range(4):
            resp = client.messages.create(messages=messages, **kwargs)
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            break
    except anthropic.APIStatusError as e:
        return False, f"Error de la API de Claude ({e.status_code}): {getattr(e, 'message', str(e))}"
    except anthropic.APIConnectionError as e:
        return False, f"Error de red al consultar Claude: {e}"
    except Exception as e:
        return False, f"Error al consultar Claude: {e}"

    if resp.stop_reason == "refusal":
        return False, "Claude rechazó la solicitud por motivos de seguridad."
    txt = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    if not txt:
        return False, "Claude no devolvió texto."
    return (_parse_json(txt) if json_out else (True, txt))


# --- API pública ----------------------------------------------------------- #
def complete(parts, *, json_out=False, max_tokens=2048, web_search=False, provider=None):
    """Genera una respuesta con el proveedor activo. Devuelve (ok, data_or_error)."""
    prov = resolve_provider(provider)
    if prov == "gemini":
        return _gemini_complete(parts, json_out, max_tokens, web_search)
    return _claude_complete(parts, json_out, max_tokens, web_search)


if __name__ == "__main__":
    import sys
    prov = sys.argv[1] if len(sys.argv) > 1 else None
    ok, data = complete([{"text": "Di 'hola' en una palabra."}],
                        json_out=False, max_tokens=50, provider=prov)
    print(resolve_provider(prov), "→", "OK" if ok else "FALLO")
    print(data)
