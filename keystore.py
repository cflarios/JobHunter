"""Almacén cifrado de claves de API registradas desde la UI.

Las claves que el usuario introduce en la web se guardan **cifradas** en la BD
(tabla `settings`, p. ej. `apikey_anthropic`), nunca en texto plano. La clave
maestra de cifrado (Fernet) vive en un fichero aparte `secret.key` (permisos 600,
gitignored por `*.key`), de modo que un volcado de la BD por sí solo no revela
ninguna clave de API.

El `.env` se mantiene **solo como fallback de desarrollo**: si no hay clave en la
BD, se usa la variable de entorno correspondiente.
"""
import os

from db import get_db, get_setting, set_setting

_DIR = os.path.dirname(os.path.abspath(__file__))
_KEY_FILE = os.path.join(_DIR, "secret.key")

_SETTING = {"anthropic": "apikey_anthropic", "gemini": "apikey_gemini"}
_ENV = {"anthropic": ("ANTHROPIC_API_KEY",),
        "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY")}


def _fernet():
    """Devuelve el cifrador Fernet, creando la clave maestra (600) si no existe."""
    from cryptography.fernet import Fernet
    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, "rb") as f:
            key = f.read().strip()
    else:
        key = Fernet.generate_key()
        fd = os.open(_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        os.chmod(_KEY_FILE, 0o600)
    return Fernet(key)


def set_api_key(provider, value):
    """Guarda (cifrada) la clave del proveedor en la BD. value vacío → la borra."""
    name = _SETTING.get(provider)
    if not name:
        return
    con = get_db()
    value = (value or "").strip()
    if value:
        token = _fernet().encrypt(value.encode()).decode()
        set_setting(con, name, token)
    else:
        con.execute("DELETE FROM settings WHERE key=?", (name,))
        con.commit()
    con.close()


def _db_key(provider):
    name = _SETTING.get(provider)
    if not name:
        return None
    con = get_db()
    enc = get_setting(con, name)
    con.close()
    if not enc:
        return None
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except Exception:
        return None


def get_api_key(provider):
    """Clave efectiva: primero la de la BD (registrada en la UI), luego el .env."""
    v = _db_key(provider)
    if v:
        return v
    for env in _ENV.get(provider, ()):
        v = os.environ.get(env)
        if v:
            return v
    return None


def key_source(provider):
    """'db' | 'env' | None — de dónde proviene la clave efectiva (para la UI)."""
    if _db_key(provider):
        return "db"
    for env in _ENV.get(provider, ()):
        if os.environ.get(env):
            return "env"
    return None


# --------------------------------------------------------------------------- #
# Secretos genéricos (no-IA): p. ej. la contraseña SMTP de las notificaciones. #
# Se guardan cifrados en `settings` bajo la clave `secret_<name>`, con la misma #
# clave maestra Fernet. Un volcado de la BD solo revela ciphertext.            #
# --------------------------------------------------------------------------- #
def _secret_setting(name):
    return "secret_" + name


def set_secret(name, value):
    """Guarda (cifrado) un secreto arbitrario en la BD. value vacío → lo borra."""
    con = get_db()
    key = _secret_setting(name)
    value = (value or "").strip()
    if value:
        token = _fernet().encrypt(value.encode()).decode()
        set_setting(con, key, token)
    else:
        con.execute("DELETE FROM settings WHERE key=?", (key,))
        con.commit()
    con.close()


def get_secret(name):
    """Devuelve el secreto descifrado, o None si no está guardado."""
    con = get_db()
    enc = get_setting(con, _secret_setting(name))
    con.close()
    if not enc:
        return None
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except Exception:
        return None


def has_secret(name):
    con = get_db()
    enc = get_setting(con, _secret_setting(name))
    con.close()
    return bool(enc)
