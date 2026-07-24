"""Rutas del proyecto, resueltas en un solo sitio.

Antes cada módulo calculaba sus rutas con `os.path.dirname(__file__)`, lo que ataba
los datos al directorio del código. Con el layout estándar el paquete vive en
`jobhunter/` y todo lo demás cuelga de la **raíz del proyecto** (su padre):

    job-hunter/            ← ROOT
    ├── jobhunter/         ← el paquete (este fichero)
    ├── data/              ← DATA_DIR: BD, clave maestra y logs (no versionado)
    │   └── logs/          ← LOG_DIR
    ├── docs/              ← DOCS_DIR: CONTEXT.md y los mapas
    ├── deploy/            ← unidades systemd (referencia)
    ├── scripts/           ← run_search.sh
    └── .env               ← ENV_FILE (secretos de desarrollo)

Importar de aquí en vez de recalcular rutas evita que mover un módulo rompa dónde
se guardan la base de datos o la clave de cifrado.
"""
import os

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PKG_DIR)

DATA_DIR = os.path.join(ROOT, "data")
LOG_DIR = os.path.join(DATA_DIR, "logs")
DOCS_DIR = os.path.join(ROOT, "docs")

DB_PATH = os.path.join(DATA_DIR, "jobs.db")
SECRET_KEY_FILE = os.path.join(DATA_DIR, "secret.key")
APP_LOG = os.path.join(LOG_DIR, "jobhunter.log")
SEARCH_LOG = os.path.join(DATA_DIR, "search.log")
ENV_FILE = os.path.join(ROOT, ".env")


def ensure_dirs():
    """Crea los directorios de datos si no existen (idempotente)."""
    os.makedirs(LOG_DIR, exist_ok=True)
