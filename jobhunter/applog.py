"""Log central de JobHunter, visible desde la UI (página Logs).

Un único fichero rotativo `logs/jobhunter.log` al que escriben **los dos procesos**
(el web service y las corridas de búsqueda), más un `StreamHandler` para que la
salida siga apareciendo en el journal / `search.log` como hasta ahora.

Formato pensado para parsearse fácil en la consola de la UI:

    2026-07-23 18:04:11 | INFO    | search    | «DevOps Engineer» (≤3d): 20 vistos, 1 nuevos

Uso:
    from jobhunter import applog
    log = applog.get("search")      # web | search | sched | notify | ai | cv
    log.info("…")
"""
import logging
import os
from logging.handlers import RotatingFileHandler

from jobhunter.paths import APP_LOG as LOG_FILE, LOG_DIR, ensure_dirs

SEP = " | "
FMT = "%(asctime)s" + SEP + "%(levelname)-7s" + SEP + "%(name)-9s" + SEP + "%(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = 512 * 1024      # 512 KB por fichero
BACKUPS = 3                 # jobhunter.log.1 … .3

_ready = False


def setup(stream=True, level=logging.INFO):
    """Configura el logging una sola vez por proceso. Idempotente."""
    global _ready
    if _ready:
        return
    ensure_dirs()
    root = logging.getLogger("jh")
    root.setLevel(level)
    root.propagate = False
    fmt = logging.Formatter(FMT, datefmt=DATEFMT)
    try:
        fh = RotatingFileHandler(LOG_FILE, maxBytes=MAX_BYTES,
                                 backupCount=BACKUPS, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass                 # sin disco/permisos: seguimos solo con la consola
    if stream:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    _ready = True


def get(name="app"):
    """Logger hijo de 'jh' (el nombre se ve en la columna «origen» de la UI)."""
    setup()
    return logging.getLogger("jh." + name)


# --------------------------------------------------------------------------- #
# Lectura para la UI                                                          #
# --------------------------------------------------------------------------- #
def _tail_file(path, n):
    """Últimas n líneas de un fichero, sin cargarlo entero en memoria."""
    if not os.path.exists(path):
        return []
    chunk, data, size = 64 * 1024, b"", os.path.getsize(path)
    with open(path, "rb") as f:
        pos = size
        while pos > 0 and data.count(b"\n") <= n:
            step = min(chunk, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[-n:]


def read_app_log(n=300):
    """Lee el log de la app incluyendo los ficheros rotados si hace falta."""
    lines = []
    # De más antiguo a más nuevo: .3, .2, .1, actual
    for i in range(BACKUPS, 0, -1):
        p = f"{LOG_FILE}.{i}"
        if os.path.exists(p) and len(lines) < n:
            lines += _tail_file(p, n)
    lines += _tail_file(LOG_FILE, n)
    return lines[-n:]


def parse(line):
    """'ts | LEVEL | origen | mensaje' → dict. Tolera líneas que no encajen."""
    parts = line.split(SEP, 3)
    if len(parts) == 4:
        ts, level, src, msg = (p.strip() for p in parts)
        if src.startswith("jh."):
            src = src[3:]
        return {"ts": ts, "level": level, "src": src, "msg": msg, "raw": line}
    # Línea suelta (traceback, salida de otro proceso…)
    low = line.lower()
    level = ("ERROR" if ("error" in low or "traceback" in low or "exception" in low)
             else ("WARNING" if "warn" in low else "INFO"))
    return {"ts": "", "level": level, "src": "", "msg": line, "raw": line}
