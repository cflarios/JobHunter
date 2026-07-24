"""Consulta varias bolsas de empleo remoto y guarda resultados recientes.

Fuentes con API/feed público (sin API key):
  - Remotive        https://remotive.com/api/remote-jobs
  - RemoteOK        https://remoteok.com/api
  - Jobicy          https://jobicy.com/api/v2/remote-jobs
  - Himalayas       https://himalayas.app/jobs/api
  - WeWorkRemotely  RSS por categoria
"""
import os
import re
import time
import html
import calendar
import datetime as dt
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests

from jobhunter import applog
from jobhunter.db import get_db, get_setting
from jobhunter.skills import extract_skills_str

log = applog.get("search")

UA = "Mozilla/5.0 (JobHunter/1.0; +personal-job-search)"
HEADERS = {"User-Agent": UA, "Accept": "application/json"}
MAX_AGE_DAYS_DEFAULT = 3
TIMEOUT = 25


def _now_ts():
    return int(time.time())


def _to_ts(value):
    """Convierte fechas de distintos formatos a epoch (segundos). None si falla."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    # ISO 8601
    try:
        iso = s.replace("Z", "+00:00")
        d = dt.datetime.fromisoformat(iso)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return int(d.timestamp())
    except ValueError:
        pass
    # RFC 822 (RSS)
    try:
        d = parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return int(d.timestamp())
    except (TypeError, ValueError):
        return None


def _fmt_date(ts):
    if not ts:
        return ""
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


DESC_MAX = 4000        # extracto guardado por oferta (suficiente para la IA y la UI)


def _excerpt(job):
    """Extracto legible de la descripción de una oferta.

    Prefiere el campo propio de la fuente (`_desc`); si no lo hay, cae al `_text`
    (título + descripción + tags) quitándole el título del principio. Se limpia el
    HTML y se recorta a DESC_MAX. Devuelve "" si la fuente no da descripción.
    """
    raw = (job.get("_desc") or "").strip()
    if not raw:
        raw = (job.get("_text") or "").strip()
        title = (job.get("title") or "").strip()
        if title and raw.lower().startswith(title.lower()):
            raw = raw[len(title):]
    txt = _clean(raw)
    if not txt:
        return ""
    return txt[:DESC_MAX].rstrip() + ("…" if len(txt) > DESC_MAX else "")


def _clean(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(text))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# --- Filtro por titulo -------------------------------------------------- #
def title_ok(title, keywords, query):
    """El TITULO debe contener alguna palabra clave del rol.

    - keywords: cadena separada por comas (p. ej. 'devops, sre, site reliability').
      El titulo pasa si contiene AL MENOS UNA (roles puros del rol buscado).
    - Si no hay keywords, se exige que TODOS los tokens de la query esten en el titulo.
    """
    t = _clean(title).lower()
    kws = [k.strip().lower() for k in (keywords or "").split(",") if k.strip()]
    if kws:
        return any(k in t for k in kws)
    return all(tok in t for tok in query.lower().split())


# --- Filtro por ubicacion ----------------------------------------------- #
# Ubicaciones abiertas a cualquier parte del mundo.
_WORLDWIDE = [
    "anywhere", "worldwide", "world wide", "world-wide", "global", "globally",
    "everywhere", "international", "any location", "any country", "no restriction",
    "location independent", "remote, worldwide", "fully remote",
]
# Regiones que INCLUYEN a un contractor en Colombia (America Latina).
_MY_REGION = [
    "colombia", "latam", "latin america", "latinoamerica", "latinoamérica",
    "south america", "central america", "americas", "the americas",
    "north & south america", "north and south america",
]
# Ubicaciones que BLOQUEAN por region/pais/estado y excluyen a un colombiano.
_US_STATES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
]
_CA_PROVINCES = [
    "ontario", "quebec", "québec", "british columbia", "alberta", "manitoba",
    "saskatchewan", "nova scotia", "new brunswick", "newfoundland",
    "prince edward island", "yukon", "northwest territories", "nunavut",
]
# Estados Unidos (lock por autorizacion laboral).
_US_TOKENS = [
    "united states", "usa", "u.s.a", "u.s", "us only", "us-only", "us based",
    "us-based", "us remote", "remote us", "north america",
]
_CANADA = ["canada", "canadian"]
# Paises de America Latina distintos de Colombia (un lock a estos excluye a Colombia).
_LATAM_COUNTRIES = [
    "mexico", "méxico", "brazil", "brasil", "argentina", "chile", "peru", "perú",
    "ecuador", "uruguay", "bolivia", "paraguay", "venezuela", "costa rica",
    "panama", "panamá", "guatemala", "honduras", "nicaragua", "el salvador",
    "dominican republic", "puerto rico", "cuba",
]
# Regiones/paises fuera del continente americano.
_OUTSIDE_AMERICAS = [
    "europe", "european", "emea", "uk", "united kingdom", "england", "scotland",
    "wales", "ireland", "germany", "france", "spain", "netherlands", "poland",
    "portugal", "italy", "sweden", "norway", "denmark", "finland", "romania",
    "bulgaria", "ukraine", "hungary", "czech", "greece", "turkey",
    "austria", "switzerland", "belgium", "luxembourg", "netherlands", "croatia",
    "serbia", "slovakia", "slovenia", "lithuania", "latvia", "estonia", "cyprus",
    "malta", "iceland", "russia", "belarus", "moldova", "albania",
    "north macedonia", "macedonia", "bosnia", "herzegovina", "srpska", "kosovo",
    "montenegro", "armenia", "azerbaijan", "uzbekistan", "georgia (country)",
    "apac", "apj", "asia", "africa", "south africa", "australia", "new zealand",
    "oceania", "india", "middle east", "gcc", "dubai", "uae",
    "united arab emirates", "qatar", "kuwait", "bahrain", "oman", "jordan",
    "lebanon", "israel", "saudi", "morocco", "tunisia", "algeria",
    "singapore", "philippines", "indonesia", "malaysia", "vietnam", "thailand",
    "cambodia", "myanmar", "nepal", "kazakhstan", "mongolia", "taiwan",
    "japan", "china", "hong kong", "korea", "pakistan", "egypt", "nigeria",
    "ghana", "tanzania", "uganda", "ethiopia", "zimbabwe",
    "kenya", "bangladesh", "sri lanka",
]
# Ubicaciones especificas de America (permitidas solo en modo 'americas').
_AMERICAS_SPECIFIC = (_US_TOKENS + _CANADA + _LATAM_COUNTRIES
                      + _US_STATES + _CA_PROVINCES)


def _mk(tokens):
    return re.compile(r"\b(" + "|".join(re.escape(t) for t in tokens) + r")\b")


_WORLD_RE = _mk(_WORLDWIDE)
_MYREGION_RE = _mk(_MY_REGION)
_OUTSIDE_RE = _mk(_OUTSIDE_AMERICAS)
_AM_SPECIFIC_RE = _mk(_AMERICAS_SPECIFIC)
# Cualquier lock geografico concreto (para modo 'worldwide').
_SPECIFIC_RE = _mk(_OUTSIDE_AMERICAS + _AMERICAS_SPECIFIC)


def location_ok(location, mode="worldwide"):
    """Decide si la ubicacion es aceptable segun el modo elegido.

    mode='worldwide' (por defecto): solo empleos abiertos a cualquier parte del
        mundo, o que incluyan la region del usuario (LATAM/Americas/Colombia).
        Descarta lo bloqueado a un pais/estado/region concreto (US-only, estados,
        Canada, Europe, EMEA, otros paises LATAM, etc.). Vacio o generico
        ('Remote') se conserva (se asume abierto).
    mode='americas': compatible con la zona horaria de America; conserva todo el
        continente (incluye US-only, estados, Canada, LATAM) y descarta el resto.
    mode='any': sin filtro de ubicacion.
    """
    if mode == "any":
        return True
    loc = (location or "").lower()
    if not loc:
        return True

    # En cualquier modo con filtro: abierto al mundo o incluye mi region -> ok.
    if _WORLD_RE.search(loc) or _MYREGION_RE.search(loc):
        return True

    if mode == "americas":
        if _AM_SPECIFIC_RE.search(loc):   # cualquier parte del continente
            return True
        if _OUTSIDE_RE.search(loc):       # fuera de America -> fuera
            return False
        return True

    # mode == "worldwide": cualquier lock geografico concreto -> fuera.
    if _SPECIFIC_RE.search(loc):
        return False
    return True


# --------------------------------------------------------------------------- #
# Fuentes                                                                      #
# --------------------------------------------------------------------------- #
def fetch_remotive(query):
    out = []
    try:
        r = requests.get(
            "https://remotive.com/api/remote-jobs",
            params={"search": query, "limit": 100},
            headers=HEADERS, timeout=TIMEOUT,
        )
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            out.append({
                "title": j.get("title"),
                "company": j.get("company_name"),
                "url": j.get("url"),
                "source": "Remotive",
                "salary": _clean(j.get("salary")),
                "location": j.get("candidate_required_location") or "Remote",
                "posted_ts": _to_ts(j.get("publication_date")),
                "_desc": j.get("description", ""),
                "_text": j.get("title", "") + " " + j.get("description", ""),
            })
    except Exception as e:
        log.warning("[Remotive] error: %s", e)
    return out


def fetch_remoteok(query):
    out = []
    try:
        r = requests.get("https://remoteok.com/api", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        for j in data:
            if not isinstance(j, dict) or "position" not in j:
                continue
            sal = ""
            lo, hi = j.get("salary_min"), j.get("salary_max")
            if lo and hi:
                sal = f"${int(lo):,} - ${int(hi):,}"
            tags = " ".join(j.get("tags", []) or [])
            out.append({
                "title": j.get("position"),
                "company": j.get("company"),
                "url": j.get("url") or j.get("apply_url"),
                "source": "RemoteOK",
                "salary": sal,
                "location": j.get("location") or "Remote",
                "posted_ts": _to_ts(j.get("date") or j.get("epoch")),
                "_desc": j.get("description", ""),
                "_text": f"{j.get('position','')} {j.get('description','')} {tags}",
            })
    except Exception as e:
        log.warning("[RemoteOK] error: %s", e)
    return out


def fetch_jobicy(query):
    out = []
    try:
        r = requests.get(
            "https://jobicy.com/api/v2/remote-jobs",
            params={"count": 100, "tag": query}, headers=HEADERS, timeout=TIMEOUT,
        )
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            sal = ""
            lo, hi = j.get("annualSalaryMin"), j.get("annualSalaryMax")
            cur = j.get("salaryCurrency") or ""
            if lo and hi:
                sal = f"{cur} {int(lo):,} - {int(hi):,}".strip()
            out.append({
                "title": j.get("jobTitle"),
                "company": j.get("companyName"),
                "url": j.get("url"),
                "source": "Jobicy",
                "salary": sal,
                "location": j.get("jobGeo") or "Remote",
                "posted_ts": _to_ts(j.get("pubDate")),
                "_desc": j.get("jobExcerpt", ""),
                "_text": f"{j.get('jobTitle','')} {j.get('jobExcerpt','')} "
                         f"{' '.join(j.get('jobIndustry', []) or [])}",
            })
    except Exception as e:
        log.warning("[Jobicy] error: %s", e)
    return out


def fetch_himalayas(query):
    out = []
    try:
        r = requests.get(
            "https://himalayas.app/jobs/api",
            params={"limit": 100}, headers=HEADERS, timeout=TIMEOUT,
        )
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            locs = j.get("locationRestrictions") or []
            sal = ""
            lo, hi = j.get("minSalary"), j.get("maxSalary")
            if lo and hi:
                sal = f"${int(lo):,} - ${int(hi):,}"
            out.append({
                "title": j.get("title"),
                "company": j.get("companyName"),
                "url": j.get("applicationLink") or j.get("guid"),
                "source": "Himalayas",
                "salary": sal,
                "location": ", ".join(locs) if locs else "Remote",
                "posted_ts": _to_ts(j.get("pubDate")),
                "_desc": j.get("description", ""),
                "_text": f"{j.get('title','')} {j.get('description','')} "
                         f"{' '.join(j.get('categories', []) or [])}",
            })
    except Exception as e:
        log.warning("[Himalayas] error: %s", e)
    return out


_WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
]


def fetch_wwr(query):
    """WeWorkRemotely: combina los RSS de DevOps/Sysadmin y Programming."""
    out = []
    for url in _WWR_FEEDS:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                company = ""
                if ":" in title:
                    company, title = [p.strip() for p in title.split(":", 1)]
                region = item.findtext("region") or "Remote"
                out.append({
                    "title": title,
                    "company": company,
                    "url": item.findtext("link"),
                    "source": "WeWorkRemotely",
                    "salary": "",
                    "location": region,
                    "posted_ts": _to_ts(item.findtext("pubDate")),
                    "_desc": item.findtext("description", ""),
                    "_text": f"{title} {item.findtext('description','')}",
                })
        except Exception as e:
            log.warning("[WeWorkRemotely] error: %s", e)
    return out


def fetch_arbeitnow(query):
    """Arbeitnow: bolsa con API JSON pública (solo se toman los remotos)."""
    out = []
    try:
        r = requests.get("https://www.arbeitnow.com/api/job-board-api",
                         headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        for j in r.json().get("data", []):
            remote = j.get("remote")
            if not (remote is True or str(remote).lower() == "true"):
                continue
            out.append({
                "title": j.get("title"),
                "company": j.get("company_name"),
                "url": j.get("url"),
                "source": "Arbeitnow",
                "salary": "",
                "location": j.get("location") or "Remote",
                "posted_ts": _to_ts(j.get("created_at")),
                "_desc": j.get("description", ""),
                "_text": f"{j.get('title','')} {j.get('description','')}",
            })
    except Exception as e:
        log.warning("[Arbeitnow] error: %s", e)
    return out


def fetch_themuse(query):
    """The Muse: empleos remotos ('Flexible / Remote'), 2 páginas."""
    out = []
    for page in (1, 2):
        try:
            r = requests.get(
                "https://www.themuse.com/api/public/jobs",
                params={"location": "Flexible / Remote", "page": page},
                headers=HEADERS, timeout=TIMEOUT,
            )
            r.raise_for_status()
            for j in r.json().get("results", []):
                locs = ", ".join(l.get("name", "") for l in j.get("locations", []) or [])
                comp = (j.get("company") or {}).get("name")
                cats = " ".join(c.get("name", "") for c in j.get("categories", []) or [])
                out.append({
                    "title": j.get("name"),
                    "company": comp,
                    "url": (j.get("refs") or {}).get("landing_page"),
                    "source": "The Muse",
                    "salary": "",
                    "location": locs or "Remote",
                    "posted_ts": _to_ts(j.get("publication_date")),
                    "_text": f"{j.get('name','')} {cats}",
                })
        except Exception as e:
            log.warning("[The Muse] error: %s", e)
            break
    return out


def fetch_workingnomads(query):
    """Working Nomads: API JSON de empleos remotos (todas las categorías)."""
    out = []
    try:
        r = requests.get("https://www.workingnomads.com/api/exposed_jobs/",
                         headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        for j in r.json():
            out.append({
                "title": j.get("title"),
                "company": j.get("company_name"),
                "url": j.get("url"),
                "source": "Working Nomads",
                "salary": "",
                "location": j.get("location") or "Remote",
                "posted_ts": _to_ts(j.get("pub_date")),
                "_text": f"{j.get('title','')} {j.get('tags','')} {j.get('category_name','')}",
            })
    except Exception as e:
        log.warning("[Working Nomads] error: %s", e)
    return out


# Mapa de codigos ISO de pais a nombre (para que el filtro de ubicacion los entienda).
_CODE_MAP = {
    "US": "United States", "CA": "Canada", "MX": "Mexico", "BR": "Brazil",
    "CO": "Colombia", "AR": "Argentina", "CL": "Chile", "PE": "Peru",
    "GB": "United Kingdom", "UK": "United Kingdom", "IE": "Ireland",
    "DE": "Germany", "FR": "France", "ES": "Spain", "PT": "Portugal",
    "NL": "Netherlands", "PL": "Poland", "IT": "Italy", "SE": "Sweden",
    "NO": "Norway", "DK": "Denmark", "FI": "Finland", "RO": "Romania",
    "UA": "Ukraine", "HU": "Hungary", "CZ": "Czech", "GR": "Greece",
    "TR": "Turkey", "CH": "Switzerland", "AT": "Austria", "BE": "Belgium",
    "IN": "India", "SG": "Singapore", "AU": "Australia", "NZ": "New Zealand",
    "JP": "Japan", "CN": "China", "IL": "Israel", "AE": "UAE", "ZA": "South Africa",
}


def fetch_landingjobs(query):
    """Landing.jobs: API JSON con salario (solo empleos remotos)."""
    import ast
    out = []
    try:
        r = requests.get("https://landing.jobs/api/v1/jobs",
                         params={"limit": 100}, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        for j in r.json():
            remote = j.get("remote")
            if not (remote is True or str(remote).lower() == "true"):
                continue
            # Ubicacion: convertir codigos de pais a nombres.
            names = []
            locs = j.get("locations")
            if isinstance(locs, str):
                try:
                    locs = ast.literal_eval(locs)
                except (ValueError, SyntaxError):
                    locs = []
            for lc in locs or []:
                code = (lc.get("country_code") or "").upper()
                names.append(_CODE_MAP.get(code, code))
            # Salario
            lo, hi = j.get("gross_salary_low"), j.get("gross_salary_high")
            cur = j.get("currency_code") or ""
            sal = ""
            if lo and hi:
                try:
                    sal = f"{cur} {int(float(lo)):,} - {int(float(hi)):,}".strip()
                except (TypeError, ValueError):
                    sal = ""
            tags = j.get("tags")
            if isinstance(tags, str):
                tags = tags.replace("[", "").replace("]", "").replace("'", "")
            out.append({
                "title": j.get("title"),
                "company": None,
                "url": j.get("url"),
                "source": "Landing.jobs",
                "salary": sal,
                "location": ", ".join(n for n in names if n) or "Remote",
                "posted_ts": _to_ts(j.get("published_at") or j.get("created_at")),
                "_text": f"{j.get('title','')} {tags or ''}",
            })
    except Exception as e:
        log.warning("[Landing.jobs] error: %s", e)
    return out


_GOB_COMPANY_CACHE = {}


def _gob_company(cid):
    """Resuelve el nombre de empresa de Get on Board (con caché y tope de llamadas)."""
    if cid in _GOB_COMPANY_CACHE:
        return _GOB_COMPANY_CACHE[cid]
    if len(_GOB_COMPANY_CACHE) >= 15:   # acota latencia añadida
        return None
    name = None
    try:
        r = requests.get(f"https://www.getonbrd.com/api/v0/companies/{cid}",
                         headers=HEADERS, timeout=10)
        if r.ok:
            name = (r.json().get("data", {}).get("attributes", {}) or {}).get("name")
    except Exception:
        name = None
    _GOB_COMPANY_CACHE[cid] = name
    return name


def fetch_getonbrd(query):
    """Get on Board: API pública JSON (LATAM, con salario en USD, sin key)."""
    out = []
    try:
        r = requests.get(
            "https://www.getonbrd.com/api/v0/search/jobs",
            params={"query": query, "per_page": 40}, headers=HEADERS, timeout=TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json().get("data", [])
        for j in rows:
            a = j.get("attributes", {}) or {}
            comp = a.get("company")
            cid = comp.get("data", {}).get("id") if isinstance(comp, dict) else None
            # Solo resolvemos empresa si el título parece del rol (evita llamadas de más).
            title = a.get("title", "")
            relevant = any(t in title.lower() for t in query.lower().split())
            company = _gob_company(cid) if (cid and relevant) else None
            lo, hi = a.get("min_salary"), a.get("max_salary")
            sal = ""
            if lo and hi:
                try:
                    sal = f"USD {int(lo):,} - {int(hi):,}"
                except (TypeError, ValueError):
                    sal = ""
            countries = [c for c in (a.get("countries") or []) if c and c != "Remote"]
            is_remote = a.get("remote") in (True, "True", "true") or a.get("remote_modality") == "remote"
            parts = (["Remote"] if is_remote else []) + countries
            loc = " · ".join(dict.fromkeys(parts)) or "Remote"
            out.append({
                "title": title,
                "company": company,
                "url": (j.get("links") or {}).get("public_url"),
                "source": "Get on Board",
                "salary": sal,
                "location": loc,
                "posted_ts": _to_ts(a.get("published_at")),
                "_text": f"{title} {a.get('category_name','')}",
            })
    except Exception as e:
        log.warning("[Get on Board] error: %s", e)
    return out


# --------------------------------------------------------------------------- #
# RapidAPI — infraestructura reutilizable para fuentes de esta plataforma.     #
# Cada fuente RapidAPI llama a _rapidapi_get(host, path, params). La API key    #
# se lee de RAPIDAPI_KEY (override de systemd, fuera del repo); si falta, la    #
# fuente se omite en silencio (devuelve []).                                    #
# --------------------------------------------------------------------------- #
def _rapidapi_key():
    """Clave efectiva de RapidAPI: BD (cifrada, registrada en la UI) → .env.

    Se importa aquí dentro para evitar un ciclo de imports (keystore → db).
    """
    try:
        from jobhunter import keystore
        return keystore.get_api_key("rapidapi")
    except Exception:
        return os.environ.get("RAPIDAPI_KEY")


def rapidapi_enabled():
    return bool(_rapidapi_key())


def _rapidapi_get(host, path, params):
    """GET a un endpoint de RapidAPI. Devuelve JSON o None (si falta key/err)."""
    key = _rapidapi_key()
    if not key:
        return None
    try:
        r = requests.get(
            f"https://{host}/{path}", params=params,
            headers={"x-rapidapi-host": host, "x-rapidapi-key": key,
                     "Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("[RapidAPI %s] error: %s", host, e)
        return None


def fetch_linkedin(query):
    """LinkedIn vía RapidAPI (linkedin-job-search-api). Requiere RAPIDAPI_KEY."""
    out = []
    data = _rapidapi_get(
        "linkedin-job-search-api.p.rapidapi.com", "active-jb",
        {"time_frame": "7d", "title": query, "limit": 100},
    )
    if not data:
        return out
    for j in data:
        if not isinstance(j, dict):
            continue
        locs = j.get("locations_derived") or []
        wa = (j.get("ai_work_arrangement") or "").strip()
        is_remote = "remote" in wa.lower()
        loc_str = ", ".join(locs)
        if is_remote:
            location = f"Remote · {loc_str}" if loc_str else "Remote"
        else:
            location = loc_str or "—"
        # Salario (campos derivados por IA de la API).
        sal = ""
        lo, hi = j.get("ai_salary_min_value"), j.get("ai_salary_max_value")
        cur = j.get("ai_salary_currency") or ""
        unit = (j.get("ai_salary_unit_text") or "").lower()
        if lo and hi:
            try:
                sal = f"{cur} {int(lo):,} - {int(hi):,}".strip()
                if unit:
                    sal += f" / {unit}"
            except (TypeError, ValueError):
                sal = ""
        skills = j.get("ai_key_skills") or []
        out.append({
            "title": j.get("title"),
            "company": j.get("organization"),
            "url": j.get("url"),
            "source": "LinkedIn",
            "salary": sal,
            "location": location,
            "posted_ts": _to_ts(j.get("date_posted") or j.get("date_created")),
            "_text": f"{j.get('title','')} {' '.join(skills) if isinstance(skills, list) else skills}",
        })
    return out


def fetch_jsearch(query):
    """JSearch vía RapidAPI (agrega Google for Jobs: LinkedIn, Indeed, Glassdoor…).

    Requiere RAPIDAPI_KEY y que la suscripción exponga el endpoint /search.
    """
    out = []
    data = _rapidapi_get(
        "jsearch.p.rapidapi.com", "search",
        {"query": query, "page": 1, "num_pages": 1,
         "date_posted": "week", "remote_jobs_only": "true"},
    )
    if not isinstance(data, dict):
        return out
    for j in data.get("data", []) or []:
        if not isinstance(j, dict):
            continue
        parts = [p for p in (j.get("job_city"), j.get("job_state"),
                             j.get("job_country")) if p]
        loc = ", ".join(parts)
        if j.get("job_is_remote"):
            loc = "Remote" + (f" · {loc}" if loc else "")
        loc = loc or "—"
        # Salario
        lo, hi = j.get("job_min_salary"), j.get("job_max_salary")
        cur = j.get("job_salary_currency") or ""
        per = (j.get("job_salary_period") or "").lower()
        sal = ""
        if lo and hi:
            try:
                sal = f"{cur} {int(lo):,} - {int(hi):,}".strip()
                if per:
                    sal += f" / {per}"
            except (TypeError, ValueError):
                sal = ""
        desc = (j.get("job_description") or "")[:400]
        out.append({
            "title": j.get("job_title"),
            "company": j.get("employer_name"),
            "url": j.get("job_apply_link") or j.get("job_google_link"),
            "source": "JSearch",
            "salary": sal,
            "location": loc,
            "posted_ts": _to_ts(j.get("job_posted_at_timestamp")
                                or j.get("job_posted_at_datetime_utc")),
            "_desc": desc,
            "_text": f"{j.get('job_title','')} {desc}",
        })
    return out


SOURCES = [fetch_remotive, fetch_remoteok, fetch_jobicy, fetch_himalayas,
           fetch_wwr, fetch_arbeitnow, fetch_themuse, fetch_workingnomads,
           fetch_landingjobs, fetch_getonbrd, fetch_linkedin, fetch_jsearch]

# Fuentes vía RapidAPI (cuota limitada): solo se consultan si el usuario lo
# activa (setting use_rapidapi=1). Por defecto están apagadas.
RAPIDAPI_SOURCES = {fetch_linkedin, fetch_jsearch}


# --------------------------------------------------------------------------- #
# Orquestacion                                                                #
# --------------------------------------------------------------------------- #
def run_search(con, query, max_age_days=MAX_AGE_DAYS_DEFAULT,
               title_keywords=None, location_mode="worldwide", use_rapidapi=False):
    """Ejecuta todas las fuentes para una query y guarda los empleos nuevos.

    Filtros aplicados por igual a todas las fuentes:
      1. El TITULO debe coincidir con el rol (title_keywords o tokens de la query).
      2. Publicado dentro de max_age_days.
      3. La ubicacion debe pasar el filtro segun location_mode
         ('worldwide' | 'americas' | 'any').

    Las fuentes de RapidAPI (cuota limitada) solo se consultan si use_rapidapi=True.
    """
    cutoff = _now_ts() - max_age_days * 86400
    raw = []
    for src in SOURCES:
        if src in RAPIDAPI_SOURCES and not use_rapidapi:
            continue
        raw.extend(src(query))

    # Blacklist de compañías (no deben aparecer en resultados).
    try:
        blocked = {r["name"].strip().lower()
                   for r in con.execute("SELECT name FROM blocked_companies")}
    except Exception:
        blocked = set()

    seen_urls = set()
    filtered = []
    for j in raw:
        if not j.get("url") or not j.get("title"):
            continue
        if j["url"] in seen_urls:
            continue
        comp = (j.get("company") or "").strip().lower()
        if comp and comp in blocked:
            continue
        if not title_ok(j["title"], title_keywords, query):
            continue
        if not location_ok(j.get("location"), location_mode):
            continue
        ts = j.get("posted_ts")
        if ts is None or ts < cutoff:
            continue
        seen_urls.add(j["url"])
        filtered.append(j)

    # search_id
    con.execute(
        "INSERT OR IGNORE INTO searches(query) VALUES(?)", (query,)
    )
    con.commit()
    search_id = con.execute(
        "SELECT id FROM searches WHERE query=?", (query,)
    ).fetchone()["id"]

    inserted = 0
    new_jobs = []          # empleos realmente insertados (para las notificaciones)
    for j in filtered:
        # Skills desde el texto completo (título + descripción + tags de la fuente).
        skills = extract_skills_str(j.get("_text") or j.get("title", ""))
        description = _excerpt(j)
        title, company, location = (_clean(j["title"]), _clean(j.get("company")),
                                    _clean(j.get("location")))
        cur = con.execute(
            """INSERT OR IGNORE INTO jobs
               (search_id,title,company,url,source,salary,location,
                date_posted,posted_ts,skills,description,is_new)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,1)""",
            (search_id, title, company, j["url"],
             j["source"], j.get("salary", ""), location,
             _fmt_date(j.get("posted_ts")), j.get("posted_ts"), skills, description),
        )
        if cur.rowcount:
            inserted += 1
            new_jobs.append({
                "title": title, "company": company, "url": j["url"],
                "source": j["source"], "salary": j.get("salary", ""),
                "location": location, "skills": skills,
                "description": description,
            })
    con.commit()

    if inserted:
        con.execute(
            "INSERT INTO notifications(message) VALUES(?)",
            (f"{inserted} nuevo(s) empleo(s) para «{query}» "
             f"(ultimos {max_age_days} dias).",),
        )
        con.commit()
    return inserted, len(filtered), new_jobs


def run_all(query_override=None):
    """Corre todas las busquedas activas (o una sola si se pasa override)."""
    con = get_db()
    max_age = int(get_setting(con, "max_age_days", MAX_AGE_DAYS_DEFAULT) or MAX_AGE_DAYS_DEFAULT)
    location_mode = get_setting(con, "location_mode", "worldwide")
    use_rapidapi = get_setting(con, "use_rapidapi", "0") == "1"
    if query_override:
        rows = con.execute(
            "SELECT query,title_keywords,max_age_days FROM searches WHERE query=?",
            (query_override,)
        ).fetchall()
        if not rows:
            rows = [{"query": query_override, "title_keywords": None,
                     "max_age_days": None}]
    else:
        rows = con.execute(
            "SELECT query,title_keywords,max_age_days FROM searches "
            "WHERE active=1 ORDER BY id"
        ).fetchall()
        if not rows:
            rows = [{"query": "DevOps Engineer",
                     "title_keywords": "devops, sre, site reliability",
                     "max_age_days": None}]

    total_new = 0
    all_new_jobs = []       # acumulado de todas las búsquedas, para un solo email
    for r in rows:
        q, kws = r["query"], r["title_keywords"]
        age = r["max_age_days"] or max_age  # ventana propia o global por defecto
        new, seen, new_jobs = run_search(con, q, age, kws, location_mode, use_rapidapi)
        log.info("«%s» (≤%sd): %s coinciden, %s nuevos", q, age, seen, new)
        total_new += new
        all_new_jobs.extend(new_jobs)

    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con.execute(
        "INSERT INTO settings(key,value) VALUES('last_run',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (stamp,),
    )
    con.commit()
    con.close()

    # Notificación por el canal configurado (hoy: email). Nunca rompe la búsqueda.
    if all_new_jobs:
        try:
            from jobhunter import notifier
            ok, msg = notifier.send_new_jobs(all_new_jobs)
            log.info("Notificación %s — %s", "enviada" if ok else "omitida", msg)
        except Exception as e:
            log.error("Notificación: error — %s", e)

    log.info("Corrida terminada (%s): %s empleo(s) nuevo(s) en total", stamp, total_new)
    return total_new


if __name__ == "__main__":
    import sys
    from jobhunter.db import init_db
    init_db()
    run_all(sys.argv[1] if len(sys.argv) > 1 else None)
