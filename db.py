import os
import sqlite3

_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_DIR, "jobs.db")


def _load_dotenv():
    """Carga el .env del proyecto (KEY=value) sin pisar variables ya definidas.
    Respaldo para ejecuciones manuales; en producción systemd usa EnvironmentFile."""
    try:
        with open(os.path.join(_DIR, ".env"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


_load_dotenv()

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches(
    id             INTEGER PRIMARY KEY,
    query          TEXT NOT NULL UNIQUE,
    title_keywords TEXT,
    max_age_days   INTEGER,
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS jobs(
    id          INTEGER PRIMARY KEY,
    search_id   INTEGER REFERENCES searches(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    company     TEXT,
    url         TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL,
    salary      TEXT,
    location    TEXT,
    date_posted TEXT,
    posted_ts   INTEGER,
    skills      TEXT,
    found_at    TEXT DEFAULT (datetime('now','localtime')),
    is_new      INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS notifications(
    id         INTEGER PRIMARY KEY,
    message    TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    read       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings(
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS blocked_companies(
    name       TEXT PRIMARY KEY COLLATE NOCASE,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS company_reviews(
    company       TEXT PRIMARY KEY,
    summary       TEXT,
    resolved_name TEXT,
    glassdoor_url TEXT,
    status        TEXT DEFAULT 'ok',
    generated_at  TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS profile(
    id                 INTEGER PRIMARY KEY CHECK (id = 1),
    cv_text            TEXT,
    role               TEXT,
    seniority          TEXT,
    years              TEXT,
    skills             TEXT,
    summary            TEXT,
    suggested_keywords TEXT,
    feedback           TEXT,
    rewrite            TEXT,
    generated_cv       TEXT,
    updated_at         TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS job_matches(
    job_id     INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    score      INTEGER,
    reason     TEXT,
    fit_detail TEXT,
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS tailored_cvs(
    job_id     INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    lang       TEXT,
    cv         TEXT,
    notes      TEXT,
    ats_score  INTEGER,
    job_desc   TEXT,
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_jobs_posted ON jobs(posted_ts DESC);
"""


def get_db():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


# Columnas añadidas después de la creación inicial de una tabla. `CREATE TABLE
# IF NOT EXISTS` no las agrega a una BD ya existente, así que las migramos aquí.
_MIGRATIONS = {
    "profile": {"generated_cv": "TEXT"},
    "company_reviews": {"glassdoor_url": "TEXT"},
    "jobs": {"skills": "TEXT"},
}


def _migrate(con):
    for table, cols in _MIGRATIONS.items():
        existing = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _backfill_skills(con):
    """Rellena jobs.skills en filas antiguas (NULL) extrayéndolas del título.
    NULL = sin procesar; '' = procesado sin skills; así solo se hace una vez.
    Las nuevas búsquedas guardan skills más ricas (del texto completo)."""
    import skills as skl
    rows = con.execute("SELECT id, title FROM jobs WHERE skills IS NULL").fetchall()
    for r in rows:
        con.execute("UPDATE jobs SET skills=? WHERE id=?",
                    (skl.extract_skills_str(r["title"]), r["id"]))


def init_db():
    con = get_db()
    con.executescript(SCHEMA)
    _migrate(con)
    con.commit()
    _backfill_skills(con)
    con.commit()
    con.close()


def get_setting(con, key, default=None):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(con, key, value):
    con.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    con.commit()


if __name__ == "__main__":
    init_db()
    print("DB inicializada en", DB_PATH)
