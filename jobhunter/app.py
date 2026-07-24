import os
import datetime as dt
from urllib.parse import quote_plus
from flask import (Flask, render_template, request, redirect, url_for,
                   jsonify, flash, Response, send_file)

from jobhunter.paths import DOCS_DIR, SEARCH_LOG

from jobhunter import applog
from jobhunter.db import get_db, init_db, get_setting, set_setting
from jobhunter import keystore
from jobhunter.fetcher import run_all
from jobhunter.reviews import generate_company_summary
from jobhunter import cv as cvai
from jobhunter import llm
from jobhunter import notifier
from jobhunter import tracker

app = Flask(__name__)
app.secret_key = "job-hunter-local-secret"

log = applog.get("web")
slog = applog.get("sched")


@app.context_processor
def inject_ai_provider():
    """Expone el proveedor de IA activo y su etiqueta a todas las plantillas."""
    con = get_db()
    prov = get_setting(con, "ai_provider", "claude")
    con.close()
    return {"ai_provider": prov, "ai_label": llm.provider_label(prov)}


@app.template_filter("md")
def md(text):
    """Markdown mínimo y seguro: escapa HTML, luego **negrita**, viñetas y saltos."""
    import re
    from markupsafe import Markup, escape
    if not text:
        return ""
    out, in_list = [], False
    for line in str(text).splitlines():
        safe = str(escape(line.strip()))
        # Encabezados Markdown (#, ##, …): se muestran en negrita, nunca literales.
        heading = bool(re.match(r"^#{1,6}\s+", safe))
        if heading:
            safe = re.sub(r"^#{1,6}\s+", "", safe)
        safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
        if not heading and (safe.startswith("- ") or safe.startswith("* ")):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{safe[2:]}</li>")
        else:
            if in_list:
                out.append("</ul>"); in_list = False
            if safe:
                out.append(f"<p><strong>{safe}</strong></p>" if heading else f"<p>{safe}</p>")
    if in_list:
        out.append("</ul>")
    return Markup("".join(out))


@app.template_filter("ago")
def ago(ts):
    if not ts:
        return "—"
    delta = dt.datetime.now(dt.timezone.utc).timestamp() - int(ts)
    d = int(delta // 86400)
    if d <= 0:
        h = int(delta // 3600)
        return "hoy" if h < 1 else f"hace {h}h"
    return "ayer" if d == 1 else f"hace {d} días"


@app.route("/")
def index():
    con = get_db()
    q = request.args.get("q", "").strip()
    source = request.args.get("source", "").strip()
    active_search = request.args.get("search", "").strip()
    days = request.args.get("days", "").strip()
    sort = request.args.get("sort", "").strip()

    sql = """SELECT j.*, s.query AS squery, m.score AS match_score, m.reason AS match_reason,
                    m.fit_detail AS fit_detail
             FROM jobs j
             LEFT JOIN searches s ON s.id=j.search_id
             LEFT JOIN job_matches m ON m.job_id=j.id WHERE 1=1"""
    params = []
    if q:
        sql += " AND (j.title LIKE ? OR j.company LIKE ? OR j.skills LIKE ?)"
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if source:
        sql += " AND j.source=?"
        params.append(source)
    if active_search:
        sql += " AND s.query=?"
        params.append(active_search)
    if days.isdigit():
        cutoff = int(dt.datetime.now(dt.timezone.utc).timestamp()) - int(days) * 86400
        sql += " AND j.posted_ts >= ?"
        params.append(cutoff)
    if sort == "match":
        sql += " ORDER BY (m.score IS NULL), m.score DESC, j.posted_ts DESC"
    else:
        sql += " ORDER BY j.posted_ts DESC, j.found_at DESC"
    from jobhunter import skills as skl
    # Empleos que ya tienen un CV adaptado (para mostrar la descarga al cargar).
    tailored_ids = {r["job_id"] for r in con.execute("SELECT job_id FROM tailored_cvs")}
    tracked = tracker.statuses_by_job()
    jobs = []
    for r in con.execute(sql, params).fetchall():
        j = dict(r)
        # Skills a mostrar: las guardadas o, si la fila es antigua/vacía, del título.
        stored = (j.get("skills") or "").strip()
        j["skill_list"] = ([s.strip() for s in stored.split(",") if s.strip()]
                           if stored else skl.extract_skills(j["title"]))
        j["tailored"] = j["id"] in tailored_ids
        j["track"] = tracked.get(j["id"])
        j["track_meta"] = tracker.META.get(j["track"]) if j["track"] else None
        jobs.append(j)
    prow = con.execute("SELECT generated_cv FROM profile WHERE id=1").fetchone()
    has_profile = prow is not None
    # El CV a medida se construye sobre el CV generado: sin él, no se ofrece.
    cv_langs = list(_generated_cv_langs(prow["generated_cv"]).keys()) if prow else []
    n_matches = con.execute("SELECT COUNT(*) c FROM job_matches").fetchone()["c"]

    searches = con.execute(
        "SELECT * FROM searches ORDER BY active DESC, id").fetchall()
    sources = con.execute(
        "SELECT DISTINCT source FROM jobs ORDER BY source").fetchall()
    unread = con.execute(
        "SELECT COUNT(*) c FROM notifications WHERE read=0").fetchone()["c"]
    stats = {
        "total": con.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"],
        "new": con.execute("SELECT COUNT(*) c FROM jobs WHERE is_new=1").fetchone()["c"],
        "last_run": get_setting(con, "last_run", "nunca"),
        "max_age": get_setting(con, "max_age_days", "3"),
    }
    con.close()
    return render_template("index.html", jobs=jobs, searches=searches,
                           sources=sources, unread=unread, stats=stats,
                           f_q=q, f_source=source, f_search=active_search,
                           f_days=days, f_sort=sort, has_profile=has_profile,
                           n_matches=n_matches, cv_langs=cv_langs,
                           stages=tracker.STAGES, outcomes=tracker.OUTCOMES,
                           quick_skills=skl.QUICK_DEVOPS)


@app.route("/mark-seen", methods=["POST"])
def mark_seen():
    con = get_db()
    con.execute("UPDATE jobs SET is_new=0 WHERE is_new=1")
    con.commit()
    con.close()
    return redirect(request.referrer or url_for("index"))


@app.route("/jobs/<int:job_id>/seen", methods=["POST"])
def job_toggle_seen(job_id):
    """Alterna el estado nuevo/visto de UN empleo (como un correo leído/no leído)."""
    want = request.form.get("new")               # "1" nueva, "0" vista; ausente = alternar
    con = get_db()
    row = con.execute("SELECT is_new FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        con.close()
        return jsonify(ok=False, error="Empleo no encontrado"), 404
    new_val = (1 if want == "1" else 0) if want in ("0", "1") else (0 if row["is_new"] else 1)
    con.execute("UPDATE jobs SET is_new=? WHERE id=?", (new_val, job_id))
    con.commit()
    con.close()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True, is_new=bool(new_val))
    return redirect(request.referrer or url_for("index"))


@app.route("/notifications")
def notifications():
    con = get_db()
    notes = con.execute(
        "SELECT * FROM notifications ORDER BY id DESC LIMIT 200").fetchall()
    con.execute("UPDATE notifications SET read=1 WHERE read=0")
    con.commit()
    con.close()
    return render_template("notifications.html", notes=notes)


@app.route("/notifications/clear", methods=["POST"])
def clear_notifications():
    con = get_db()
    con.execute("DELETE FROM notifications")
    con.commit()
    con.close()
    return redirect(url_for("notifications"))


@app.route("/searches", methods=["GET", "POST"])
def searches():
    con = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            q = request.form.get("query", "").strip()
            kw = request.form.get("title_keywords", "").strip()
            age = request.form.get("max_age_days", "").strip()
            if q:
                con.execute(
                    "INSERT OR IGNORE INTO searches(query,title_keywords,max_age_days) "
                    "VALUES(?,?,?)",
                    (q, kw or None, int(age) if age.isdigit() else None))
                flash(f"Búsqueda «{q}» añadida.", "ok")
        elif action == "edit_kw":
            age = request.form.get("max_age_days", "").strip()
            con.execute("UPDATE searches SET title_keywords=?, max_age_days=? WHERE id=?",
                        (request.form.get("title_keywords", "").strip() or None,
                         int(age) if age.isdigit() else None,
                         request.form.get("id")))
            flash("Búsqueda actualizada.", "ok")
        elif action == "toggle":
            con.execute("UPDATE searches SET active=1-active WHERE id=?",
                        (request.form.get("id"),))
        elif action == "delete":
            con.execute("DELETE FROM searches WHERE id=?", (request.form.get("id"),))
        elif action == "max_age":
            set_setting(con, "max_age_days", request.form.get("max_age", "3"))
            flash("Antigüedad máxima actualizada.", "ok")
        elif action == "set_location":
            mode = request.form.get("location_mode", "worldwide")
            if mode not in ("worldwide", "americas", "any"):
                mode = "worldwide"
            set_setting(con, "location_mode", mode)
            flash("Filtro de ubicación actualizado.", "ok")
        elif action == "set_rapidapi":
            on = "1" if request.form.get("rapidapi") else "0"
            set_setting(con, "use_rapidapi", on)
            flash("Fuentes RapidAPI " + ("activadas." if on == "1" else "desactivadas."), "ok")
        con.commit()
        con.close()
        return redirect(url_for("searches"))

    rows = con.execute("""
        SELECT s.*, (SELECT COUNT(*) FROM jobs j WHERE j.search_id=s.id) AS njobs
        FROM searches s ORDER BY s.active DESC, s.id""").fetchall()
    max_age = get_setting(con, "max_age_days", "3")
    location_mode = get_setting(con, "location_mode", "worldwide")
    use_rapidapi = get_setting(con, "use_rapidapi", "0") == "1"
    con.close()
    return render_template("searches.html", searches=rows, max_age=max_age,
                           location_mode=location_mode, use_rapidapi=use_rapidapi)


def _mask_key(k):
    k = (k or "").strip()
    return (k[:6] + "…" + k[-4:]) if len(k) > 12 else ("••••" if k else "")


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    con = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "set_provider":
            prov = request.form.get("ai_provider", "claude")
            if prov not in ("claude", "gemini"):
                prov = "claude"
            set_setting(con, "ai_provider", prov)
            flash("Proveedor de IA actualizado a " + llm.provider_label(prov) + ".", "ok")
        elif action in ("set_apikey", "clear_apikey"):
            provider = request.form.get("provider", "")
            label = "Claude (Anthropic)" if provider == "anthropic" else "Gemini"
            if provider not in ("anthropic", "gemini"):
                flash("Proveedor inválido.", "ok")
            elif action == "clear_apikey":
                keystore.set_api_key(provider, "")
                flash(f"Clave de {label} borrada.", "ok")
            else:
                # No registrar ni mostrar nunca el valor de la clave.
                key = request.form.get("api_key", "").strip()
                if not key or " " in key or len(key) < 10:
                    flash("Clave no guardada: parece vacía o inválida.", "ok")
                else:
                    keystore.set_api_key(provider, key)  # cifrada en la BD
                    flash(f"Clave de {label} guardada de forma segura.", "ok")
        elif action == "set_notify":
            set_setting(con, "notify_enabled", "1" if request.form.get("notify_enabled") else "0")
            # Modos de envío
            set_setting(con, "notify_immediate", "1" if request.form.get("notify_immediate") else "0")
            set_setting(con, "notify_digest", "1" if request.form.get("notify_digest") else "0")
            dtime = request.form.get("digest_time", "").strip()
            set_setting(con, "digest_time", dtime or notifier.DEFAULT_DIGEST_TIME)
            # Canales
            set_setting(con, "notify_email_on", "1" if request.form.get("notify_email_on") else "0")
            set_setting(con, "notify_telegram_on", "1" if request.form.get("notify_telegram_on") else "0")
            # Email
            set_setting(con, "notify_email", request.form.get("notify_email", "").strip())
            set_setting(con, "smtp_host", request.form.get("smtp_host", "").strip()
                        or notifier.DEFAULT_SMTP_HOST)
            port = request.form.get("smtp_port", "").strip()
            set_setting(con, "smtp_port", port if port.isdigit() else str(notifier.DEFAULT_SMTP_PORT))
            set_setting(con, "smtp_user", request.form.get("smtp_user", "").strip())
            set_setting(con, "smtp_from", request.form.get("smtp_from", "").strip())
            # Telegram
            set_setting(con, "telegram_chat_id", request.form.get("telegram_chat_id", "").strip())
            app_url = request.form.get("app_base_url", "").strip()
            if app_url:
                set_setting(con, "app_base_url", app_url)
            # Secretos: solo se tocan si escriben uno nuevo (cifrados en la BD).
            pw = request.form.get("smtp_password", "")
            if pw.strip():
                keystore.set_secret("smtp_password", pw.strip())
            tg = request.form.get("telegram_token", "")
            if tg.strip():
                keystore.set_secret("telegram_token", tg.strip())
            flash("Configuración de notificaciones guardada.", "ok")
        elif action == "clear_smtp_pass":
            keystore.set_secret("smtp_password", "")
            flash("Contraseña SMTP borrada.", "ok")
        elif action == "clear_telegram_token":
            keystore.set_secret("telegram_token", "")
            flash("Token de Telegram borrado.", "ok")
        elif action == "set_schedule":
            times = parse_times(" ".join(request.form.getlist("times")))
            set_setting(con, "search_times", ",".join(times))
            if times:
                flash("Horarios de búsqueda guardados: " + ", ".join(times)
                      + " (hora de Colombia).", "ok")
            else:
                flash("Búsqueda automática desactivada (no hay horarios). "
                      "Puedes seguir usando «Buscar ahora».", "ok")
        con.commit()
        con.close()
        if action == "test_notify":
            ok, msg = notifier.send_test()
            (log.info if ok else log.warning)("Notificación de prueba: %s", msg)
            flash(("✅ " if ok else "⚠️ ") + msg, "ok")
            return redirect(url_for("settings_page") + "#notificaciones")
        return redirect(url_for("settings_page"))

    ai_provider = get_setting(con, "ai_provider", "claude")
    search_times = parse_times(get_setting(con, "search_times", "12:00"))
    ncfg = notifier.load_config()
    con.close()

    ak = keystore.get_api_key("anthropic")
    gk = keystore.get_api_key("gemini")
    providers = [
        {"id": "anthropic", "name": "Claude (Anthropic)", "paid": True,
         "get_url": "https://console.anthropic.com/settings/keys",
         "set": bool(ak), "mask": _mask_key(ak)},
        {"id": "gemini", "name": "Gemini (Google AI Studio)", "paid": False,
         "get_url": "https://aistudio.google.com/apikey",
         "set": bool(gk), "mask": _mask_key(gk)},
    ]
    notify = {
        "enabled": ncfg["enabled"], "immediate": ncfg["immediate"],
        "digest": ncfg["digest"], "digest_time": ncfg["digest_time"],
        "email_on": ncfg["email_on"], "telegram_on": ncfg["telegram_on"],
        "to": ncfg["to"], "smtp_host": ncfg["smtp_host"], "smtp_port": ncfg["smtp_port"],
        "smtp_user": ncfg["smtp_user"], "smtp_from": ncfg["smtp_from"],
        "tg_chat_id": ncfg["tg_chat_id"], "app_url": ncfg["app_url"],
        "has_pass": bool(ncfg["smtp_pass"]), "has_tg_token": bool(ncfg["tg_token"]),
        "email_problems": notifier.email_problems(ncfg),
        "telegram_problems": notifier.telegram_problems(ncfg),
        "channels": notifier.active_channels(ncfg),
    }
    return render_template("settings.html", ai_provider=ai_provider,
                           providers=providers, notify=notify,
                           search_times=search_times)


@app.route("/run", methods=["POST"])
def run_now():
    q = request.form.get("query", "").strip() or None
    log.info("Búsqueda manual lanzada desde la UI%s", f" para «{q}»" if q else " (todas las activas)")
    total = run_all(q)
    log.info("Búsqueda manual terminada: %s empleo(s) nuevo(s)", total)
    flash(f"Búsqueda ejecutada: {total} empleo(s) nuevo(s).", "ok")
    return redirect(request.referrer or url_for("index"))


def _glassdoor_search(name):
    """URL del buscador de Glassdoor (fallback cuando no hay página directa)."""
    return ("https://www.glassdoor.com/Search/results.htm?keyword="
            + quote_plus(name or ""))


@app.route("/companies")
def companies():
    con = get_db()
    rows = con.execute("""
        SELECT j.company AS company,
               COUNT(*) AS njobs,
               MAX(j.posted_ts) AS last_ts,
               GROUP_CONCAT(DISTINCT j.source) AS sources
        FROM jobs j
        WHERE j.company IS NOT NULL AND TRIM(j.company) <> ''
        GROUP BY LOWER(j.company)
        ORDER BY njobs DESC, last_ts DESC
    """).fetchall()
    reviews = {r["company"]: r for r in
               con.execute("SELECT * FROM company_reviews").fetchall()}
    # Ofertas de cada empresa, para poder desplegarlas en la propia tarjeta sin
    # tener que ir a Empleos a buscarlas. Son pocas: se pre-renderizan.
    jobs_by_company = {}
    for r in con.execute("""
        SELECT j.id, j.company, j.title, j.url, j.salary, j.location, j.source,
               j.date_posted, j.posted_ts, j.is_new, m.score AS match_score
        FROM jobs j
        LEFT JOIN job_matches m ON m.job_id = j.id
        WHERE j.company IS NOT NULL AND TRIM(j.company) <> ''
        ORDER BY j.posted_ts DESC, j.id DESC
    """).fetchall():
        jobs_by_company.setdefault(r["company"].strip().lower(), []).append(dict(r))
    con.close()
    companies = []
    for r in rows:
        rev = reviews.get(r["company"])
        resolved = rev["resolved_name"] if rev and rev["resolved_name"] else None
        gd_name = resolved or r["company"]
        gd_url = rev["glassdoor_url"] if rev and rev["glassdoor_url"] else None
        companies.append({
            "name": r["company"],
            "gd_name": gd_name,
            "resolved": resolved if resolved and resolved.lower() != r["company"].lower() else None,
            "njobs": r["njobs"],
            "last_ts": r["last_ts"],
            "sources": (r["sources"] or "").split(","),
            # URL directa de la empresa si la IA la resolvió; si no, el buscador.
            "glassdoor": gd_url or _glassdoor_search(gd_name),
            "glassdoor_direct": bool(gd_url),
            "review": rev["summary"] if rev else None,
            "review_ok": (rev["status"] == "ok") if rev else None,
            "review_at": rev["generated_at"] if rev else None,
            "jobs": jobs_by_company.get(r["company"].strip().lower(), []),
        })
    return render_template("companies.html", companies=companies)


@app.route("/companies/summary", methods=["POST"])
def company_summary():
    company = request.form.get("company", "").strip()
    if company:
        con = get_db()
        existing = con.execute(
            "SELECT resolved_name, glassdoor_url FROM company_reviews WHERE company=?",
            (company,)).fetchone()
        # Un nombre de Glassdoor ya fijado (manual o previo) manda la búsqueda.
        pinned = existing["resolved_name"] if existing and existing["resolved_name"] else None
        old_url = existing["glassdoor_url"] if existing else None
        log.info("Resumen de reputación solicitado para «%s»", company)
        result = generate_company_summary(pinned or company)
        keep_resolved = pinned or result.get("resolved")
        # Preferimos la URL directa nueva; si esta vez no la halló, conservamos la previa.
        keep_url = result.get("url") or old_url
        con.execute(
            "INSERT INTO company_reviews(company,summary,resolved_name,glassdoor_url,status,generated_at) "
            "VALUES(?,?,?,?,?,datetime('now','localtime')) "
            "ON CONFLICT(company) DO UPDATE SET "
            "summary=excluded.summary, resolved_name=excluded.resolved_name, "
            "glassdoor_url=excluded.glassdoor_url, status=excluded.status, "
            "generated_at=excluded.generated_at",
            (company, result["summary"], keep_resolved, keep_url,
             "ok" if result["ok"] else "error"))
        con.commit()
        con.close()
        gd_name = keep_resolved or company
        gd_link = keep_url or _glassdoor_search(gd_name)
        if request.headers.get("X-Requested-With") == "fetch":
            from types import SimpleNamespace
            c = SimpleNamespace(
                review=result["summary"],
                review_ok=result["ok"],
                review_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            return jsonify(
                ok=result["ok"],
                html=render_template("_review.html", c=c, expanded=True),
                glassdoor=gd_link,
                glassdoor_direct=bool(keep_url),
                gd_name=gd_name)
        flash(f"Resumen de «{company}» {'generado' if result['ok'] else 'no disponible'}.", "ok")
    return redirect(url_for("companies") + f"#c-{quote_plus(company)}")


@app.route("/companies/glassdoor-name", methods=["POST"])
def company_glassdoor_name():
    company = request.form.get("company", "").strip()
    gd_name = request.form.get("glassdoor_name", "").strip() or None
    if company:
        con = get_db()
        # Al fijar el nombre a mano, borramos la URL directa: puede no coincidir con
        # el nuevo nombre. Volverá a resolverse al regenerar el resumen.
        con.execute(
            "INSERT INTO company_reviews(company,resolved_name,glassdoor_url) VALUES(?,?,NULL) "
            "ON CONFLICT(company) DO UPDATE SET resolved_name=excluded.resolved_name, "
            "glassdoor_url=NULL",
            (company, gd_name))
        con.commit()
        con.close()
        flash(f"Nombre de Glassdoor de «{company}» actualizado.", "ok")
    return redirect(url_for("companies") + f"#c-{quote_plus(company)}")


def _back(default_endpoint):
    """Vuelve a la página de origen (companies o blacklist) o a un fallback."""
    ref = request.referrer or ""
    if "/blacklist" in ref:
        return redirect(url_for("blacklist_page"))
    return redirect(url_for(default_endpoint))


@app.route("/companies/block", methods=["POST"])
def company_block():
    company = request.form.get("company", "").strip()
    if company:
        con = get_db()
        con.execute("INSERT OR IGNORE INTO blocked_companies(name) VALUES(?)", (company,))
        # Elimina los empleos ya guardados de esa empresa (desaparecen del listado).
        n = con.execute("DELETE FROM jobs WHERE LOWER(TRIM(company))=LOWER(TRIM(?))",
                        (company,)).rowcount
        con.commit()
        con.close()
        flash(f"«{company}» bloqueada. No volverá a aparecer en las búsquedas"
              + (f"; se quitaron {n} oferta(s) guardada(s)." if n else "."), "ok")
    return _back("companies")


@app.route("/companies/unblock", methods=["POST"])
def company_unblock():
    company = request.form.get("company", "").strip()
    if company:
        con = get_db()
        con.execute("DELETE FROM blocked_companies WHERE name=?", (company,))
        con.commit()
        con.close()
        flash(f"«{company}» desbloqueada.", "ok")
    return _back("companies")


@app.route("/blacklist")
def blacklist_page():
    con = get_db()
    blocked = con.execute(
        "SELECT name, created_at FROM blocked_companies ORDER BY created_at DESC, name COLLATE NOCASE"
    ).fetchall()
    con.close()
    return render_template("blacklist.html", blocked=blocked)


def _load_profile(con):
    row = con.execute("SELECT * FROM profile WHERE id=1").fetchone()
    return dict(row) if row else None


@app.route("/cv")
def cv_page():
    con = get_db()
    profile = _load_profile(con)
    n_jobs = con.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
    n_matches = con.execute("SELECT COUNT(*) c FROM job_matches").fetchone()["c"]
    con.close()
    cv_langs = list(_generated_cv_langs((profile or {}).get("generated_cv")).keys())
    return render_template("cv.html", profile=profile, n_jobs=n_jobs,
                           n_matches=n_matches, cv_langs=cv_langs)


def _pdf_text(data):
    """Extrae el texto de un PDF (bytes). Devuelve '' si no se puede."""
    try:
        from io import BytesIO
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(data))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages).strip()
    except Exception:
        return ""


@app.route("/cv/analyze", methods=["POST"])
def cv_analyze():
    import base64
    text = request.form.get("cv_text", "").strip()
    pdf_b64 = None
    f = request.files.get("cv_file")
    if f and f.filename:
        data = f.read(6 * 1024 * 1024)  # tope 6 MB
        if f.filename.lower().endswith(".pdf") or (f.mimetype or "").endswith("pdf"):
            pdf_b64 = base64.standard_b64encode(data).decode()
            # También extraemos el texto para poder reconstruir el CV más tarde
            # (el análisis usa el PDF inline; el guardado usa este texto).
            text = text or _pdf_text(data)
        else:
            try:
                text = text or data.decode("utf-8", "ignore")
            except Exception:
                text = text
    if not text and not pdf_b64:
        flash("Pega el texto de tu CV o sube un PDF.", "ok")
        return redirect(url_for("cv_page"))

    log.info("Analizando CV (%s)", "PDF" if pdf_b64 else "texto")
    res = cvai.analyze_cv(text=text or None, pdf_b64=pdf_b64)
    con = get_db()
    if res.get("ok"):
        con.execute(
            """INSERT INTO profile(id,cv_text,role,seniority,years,skills,summary,
                 suggested_keywords,updated_at)
               VALUES(1,?,?,?,?,?,?,?,datetime('now','localtime'))
               ON CONFLICT(id) DO UPDATE SET cv_text=excluded.cv_text,role=excluded.role,
                 seniority=excluded.seniority,years=excluded.years,skills=excluded.skills,
                 summary=excluded.summary,suggested_keywords=excluded.suggested_keywords,
                 feedback=NULL,rewrite=NULL,generated_cv=NULL,updated_at=excluded.updated_at""",
            (text[:20000] if text else None, res["role"], res["seniority"], res["years"],
             res["skills"], res["summary"], res["suggested_keywords"]))
        con.commit()
        flash("Perfil analizado. Ya puedes calcular la afinidad de tus empleos.", "ok")
    else:
        flash("No se pudo analizar el CV: " + res.get("error", ""), "ok")
    con.close()
    return redirect(url_for("cv_page"))


@app.route("/cv/apply-keywords", methods=["POST"])
def cv_apply_keywords():
    con = get_db()
    p = _load_profile(con)
    kw = (p or {}).get("suggested_keywords")
    if kw:
        row = con.execute(
            "SELECT id FROM searches WHERE active=1 ORDER BY id LIMIT 1").fetchone()
        if row:
            con.execute("UPDATE searches SET title_keywords=? WHERE id=?", (kw, row["id"]))
            con.commit()
            flash(f"Palabras clave aplicadas a tu búsqueda activa: {kw}", "ok")
        else:
            flash("No hay una búsqueda activa a la que aplicarlas.", "ok")
    con.close()
    return redirect(url_for("cv_page"))


@app.route("/cv/match", methods=["POST"])
def cv_match():
    con = get_db()
    p = _load_profile(con)
    if not p:
        con.close()
        flash("Primero analiza tu CV.", "ok")
        return redirect(url_for("cv_page"))
    jobs = [dict(r) for r in con.execute(
        "SELECT id,title,company,location,salary FROM jobs").fetchall()]
    total = 0
    for i in range(0, len(jobs), 40):        # por lotes de 40
        res = cvai.match_jobs(p, jobs[i:i + 40])
        if not res.get("ok"):
            con.close()
            flash("Error al calcular afinidad: " + res.get("error", ""), "ok")
            return redirect(url_for("cv_page"))
        for m in res["matches"]:
            con.execute(
                """INSERT INTO job_matches(job_id,score,reason,updated_at)
                   VALUES(?,?,?,datetime('now','localtime'))
                   ON CONFLICT(job_id) DO UPDATE SET score=excluded.score,
                     reason=excluded.reason,updated_at=excluded.updated_at""",
                (m["id"], m["score"], m["reason"]))
            total += 1
    con.commit()
    con.close()
    log.info("Afinidad recalculada para %s empleo(s)", total)
    flash(f"Afinidad calculada para {total} empleo(s).", "ok")
    return redirect(url_for("index", sort="match"))


@app.route("/cv/improve", methods=["POST"])
def cv_improve():
    con = get_db()
    p = _load_profile(con)
    if not p:
        con.close()
        flash("Primero analiza tu CV.", "ok")
        return redirect(url_for("cv_page"))
    res = cvai.improve_cv(p, cv_text=p.get("cv_text"))
    if res.get("ok"):
        con.execute("UPDATE profile SET feedback=?, rewrite=? WHERE id=1",
                    (res["feedback"], res["rewrite"]))
        con.commit()
    else:
        flash("No se pudo mejorar el CV: " + res.get("error", ""), "ok")
    con.close()
    return redirect(url_for("cv_page"))


def _generated_cv_langs(raw):
    """Parsea profile.generated_cv → {lang: cv_dict}. Tolera el formato antiguo
    (un único CV plano, sin claves de idioma) asumiéndolo español."""
    import json
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    if any(k in ("es", "en") and isinstance(data.get(k), dict) for k in ("es", "en")):
        return {k: v for k, v in data.items() if k in ("es", "en") and isinstance(v, dict)}
    return {"es": data}   # formato antiguo: CV plano → español


@app.route("/cv/build", methods=["POST"])
def cv_build():
    con = get_db()
    p = _load_profile(con)
    if not p:
        con.close()
        flash("Primero analiza tu CV.", "ok")
        return redirect(url_for("cv_page"))
    choice = request.form.get("cv_lang", "es")
    langs = ["es", "en"] if choice == "both" else (["en"] if choice == "en" else ["es"])
    names = {"es": "Español", "en": "English"}
    built, errors = {}, []
    for lg in langs:
        res = cvai.build_cv(p, lg)
        if res.get("ok"):
            built[lg] = res["cv"]
        else:
            errors.append(f"{names[lg]}: {res.get('error', '')}")
    if built:
        log.info("CV nuevo generado (%s)", ", ".join(built))
        import json
        con.execute("UPDATE profile SET generated_cv=? WHERE id=1",
                    (json.dumps(built, ensure_ascii=False),))
        con.commit()
        flash("CV nuevo generado (" + ", ".join(names[k] for k in built)
              + "). Descárgalo en PDF más abajo.", "ok")
    if errors:
        flash("No se pudo generar: " + " · ".join(errors), "ok")
    con.close()
    return redirect(url_for("cv_page"))


@app.route("/cv/download")
def cv_download():
    from io import BytesIO
    con = get_db()
    p = _load_profile(con)
    con.close()
    langs = _generated_cv_langs((p or {}).get("generated_cv"))
    if not langs:
        flash("Primero genera tu CV nuevo.", "ok")
        return redirect(url_for("cv_page"))
    lang = request.args.get("lang", "")
    if lang not in langs:
        lang = "es" if "es" in langs else next(iter(langs))
    data = langs[lang]
    from jobhunter import cvpdf
    pdf_bytes = cvpdf.render(data, lang=lang)
    safe = "".join(c if c.isalnum() else "_" for c in (data.get("name") or "cv")).strip("_") or "cv"
    return send_file(BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=True, download_name=f"CV_{safe}_{lang.upper()}.pdf")


@app.route("/cv/clear", methods=["POST"])
def cv_clear():
    con = get_db()
    con.execute("DELETE FROM profile")
    con.execute("DELETE FROM job_matches")
    con.execute("DELETE FROM tailored_cvs")   # dependen del CV generado
    con.commit()
    con.close()
    flash("Perfil, afinidades y CVs a medida borrados.", "ok")
    return redirect(url_for("cv_page"))


@app.route("/jobs/<int:job_id>/fit", methods=["POST"])
def job_fit(job_id):
    con = get_db()
    p = _load_profile(con)
    job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not p or not job:
        con.close()
        return jsonify(ok=False, error="Falta perfil o empleo"), 400
    res = cvai.analyze_fit(p, dict(job))
    if res.get("ok"):
        con.execute(
            """INSERT INTO job_matches(job_id,score,fit_detail,updated_at)
               VALUES(?,?,?,datetime('now','localtime'))
               ON CONFLICT(job_id) DO UPDATE SET score=COALESCE(excluded.score,job_matches.score),
                 fit_detail=excluded.fit_detail,updated_at=excluded.updated_at""",
            (job_id, res["score"], res["html"]))
        con.commit()
        con.close()
        html = render_template("_fitblock.html", detail=res["html"], expanded=True)
        return jsonify(ok=True, html=html, score=res["score"])
    con.close()
    return jsonify(ok=False, error=res.get("error", "")), 500


@app.route("/applications")
def applications_page():
    """Embudo de postulaciones (Sankey) + métricas + listado."""
    f = tracker.funnel()
    view = request.args.get("view", "activas")
    if view not in ("activas", "cerradas", "todas"):
        view = "activas"
    rows = tracker.applications(None if view == "todas" else view)
    return render_template("applications.html", funnel=f, apps=rows, view=view,
                           stages=tracker.STAGES, outcomes=tracker.OUTCOMES,
                           by_source=tracker.by_source())


@app.route("/jobs/<int:job_id>/status", methods=["POST"])
def job_status(job_id):
    """Cambia el estado de seguimiento de una oferta (AJAX o formulario)."""
    status = request.form.get("status", "").strip()
    if status == "__none__":
        tracker.remove(job_id)
        ok, msg = True, "Quitada del seguimiento."
    else:
        ok, msg = tracker.set_status(job_id, status,
                                     request.form.get("note", "").strip() or None)
    if ok and status != "__none__":
        log.info("Postulación %s → %s", job_id, status)
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=ok, message=msg, status=None if status == "__none__" else status)
    flash(msg, "ok")
    return redirect(request.referrer or url_for("applications_page"))


@app.route("/jobs/<int:job_id>/note", methods=["POST"])
def job_note(job_id):
    tracker.set_note(job_id, request.form.get("note", "").strip() or None)
    flash("Nota guardada.", "ok")
    return redirect(request.referrer or url_for("applications_page"))


@app.route("/jobs/<int:job_id>/tailor", methods=["POST"])
def job_tailor(job_id):
    """Adapta el CV generado a esta vacante (optimización ATS) y lo cachea."""
    import json
    con = get_db()
    p = _load_profile(con)
    job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not p or not job:
        con.close()
        return jsonify(ok=False, error="Falta perfil o empleo"), 400
    langs = _generated_cv_langs(p.get("generated_cv"))
    if not langs:
        con.close()
        return jsonify(ok=False,
                       error="Primero genera tu CV nuevo en «Mi CV» — es la base que se adapta."), 400
    lang = request.form.get("lang", "")
    if lang not in langs:
        lang = "es" if "es" in langs else next(iter(langs))
    # Si el usuario no pega nada, usamos el extracto que guardamos al ingerir.
    jd = request.form.get("jd", "").strip()[:12000] or (job["description"] or "").strip()
    log.info("Generando CV a medida para «%s» (%s, %s, descripción: %s)",
             job["title"], job["company"] or "?", lang, "sí" if jd else "no")
    res = cvai.tailor_cv(langs[lang], dict(job), lang=lang, job_desc=jd or None, profile=p)
    if not res.get("ok"):
        con.close()
        log.error("CV a medida falló para «%s»: %s", job["title"], res.get("error", ""))
        return jsonify(ok=False, error=res.get("error", "")), 500
    con.execute(
        """INSERT INTO tailored_cvs(job_id,lang,cv,notes,ats_score,job_desc,updated_at)
           VALUES(?,?,?,?,?,?,datetime('now','localtime'))
           ON CONFLICT(job_id) DO UPDATE SET lang=excluded.lang, cv=excluded.cv,
             notes=excluded.notes, ats_score=excluded.ats_score,
             job_desc=excluded.job_desc, updated_at=excluded.updated_at""",
        (job_id, lang, json.dumps(res["cv"], ensure_ascii=False), res["notes"],
         res["ats_score"], jd or None))
    con.commit()
    con.close()
    log.info("CV a medida listo para «%s»: encaje ATS %s%%", job["title"], res["ats_score"])
    html = render_template("_tailorblock.html", notes=res["notes"], job_id=job_id,
                           lang=lang, expanded=True)
    return jsonify(ok=True, html=html)


@app.route("/jobs/<int:job_id>/cv.pdf")
def job_cv_download(job_id):
    """Descarga el CV adaptado a esta vacante en PDF."""
    import json
    from io import BytesIO
    con = get_db()
    row = con.execute(
        "SELECT t.*, j.company, j.title FROM tailored_cvs t "
        "JOIN jobs j ON j.id=t.job_id WHERE t.job_id=?", (job_id,)).fetchone()
    con.close()
    if not row:
        flash("Aún no has generado el CV a medida de esa vacante.", "ok")
        return redirect(url_for("index"))
    try:
        data = json.loads(row["cv"])
    except (ValueError, TypeError):
        flash("El CV a medida guardado no es válido; vuelve a generarlo.", "ok")
        return redirect(url_for("index"))
    from jobhunter import cvpdf
    pdf_bytes = cvpdf.render(data, lang=row["lang"] or "es")
    def _safe(s):
        return "".join(c if c.isalnum() else "_" for c in (s or "")).strip("_")
    name = _safe(data.get("name")) or "CV"
    comp = _safe(row["company"]) or "vacante"
    return send_file(BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=True,
                     download_name=f"CV_{name}_{comp}_{(row['lang'] or 'es').upper()}.pdf")


@app.route("/jobs/<int:job_id>/cover", methods=["POST"])
def job_cover(job_id):
    con = get_db()
    p = _load_profile(con)
    job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    con.close()
    if not p or not job:
        return jsonify(ok=False, error="Falta perfil o empleo"), 400
    res = cvai.cover_letter(p, dict(job))
    if res.get("ok"):
        return jsonify(ok=True, text=res["text"])
    return jsonify(ok=False, error=res.get("error", "")), 500


FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="14" fill="#0f1420"/>'
    '<circle cx="27" cy="27" r="13" fill="none" stroke="#4f8cff" stroke-width="6"/>'
    '<line x1="37" y1="37" x2="52" y2="52" stroke="#4f8cff" stroke-width="7" stroke-linecap="round"/>'
    '<circle cx="50" cy="15" r="7" fill="#22c55e"/></svg>'
)


@app.route("/favicon.svg")
@app.route("/favicon.ico")
def favicon():
    return Response(FAVICON_SVG, mimetype="image/svg+xml")


@app.route("/api/unread")
def api_unread():
    con = get_db()
    c = con.execute("SELECT COUNT(*) c FROM notifications WHERE read=0").fetchone()["c"]
    con.close()
    return jsonify(unread=c)


# Fuentes de log que la UI puede leer. Lista blanca cerrada: el parámetro `source`
# nunca se interpola en un comando; solo indexa este diccionario.
LOG_SOURCES = {
    "app":    {"label": "App", "kind": "file"},
    "search": {"label": "Búsquedas", "kind": "file",
               "path": SEARCH_LOG},
    "web":    {"label": "Sistema · web", "kind": "unit",
               "unit": "jobhunter-web.service"},
    "svc":    {"label": "Sistema · búsqueda", "kind": "unit",
               "unit": "jobhunter-search.service"},
}


def _journal_lines(unit, n):
    """Últimas n líneas del journal de una unidad (pi está en el grupo `adm`)."""
    import subprocess
    try:
        out = subprocess.run(
            ["journalctl", "-u", unit, "-n", str(n), "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=12)
        if out.returncode != 0:
            return [f"(no se pudo leer el journal de {unit}: {out.stderr.strip()})"]
        return [l for l in out.stdout.splitlines() if l.strip()]
    except Exception as e:
        return [f"(error al leer el journal: {e})"]


_JOURNAL_RE = None


def _parse_journal(line):
    """'2026-07-23T18:03:22-05:00 host proceso[pid]: mensaje' → columnas limpias."""
    import re
    global _JOURNAL_RE
    if _JOURNAL_RE is None:
        _JOURNAL_RE = re.compile(
            r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})[^ ]*\s+\S+\s+([^\[:]+)(?:\[\d+\])?:\s?(.*)$")
    m = _JOURNAL_RE.match(line)
    if not m:
        from jobhunter import applog as al
        return al.parse(line)
    date, hhmmss, proc, msg = m.groups()
    low = msg.lower()
    level = ("ERROR" if ("error" in low or "traceback" in low or "failed" in low
                         or "exception" in low)
             else ("WARNING" if ("warn" in low or "deprecat" in low) else "INFO"))
    return {"ts": f"{date} {hhmmss}", "level": level, "src": proc.strip()[:9],
            "msg": msg, "raw": line}


@app.route("/logs")
def logs_page():
    return render_template("logs.html", sources=LOG_SOURCES)


@app.route("/api/logs")
def api_logs():
    """Líneas de log ya parseadas para la consola de la UI."""
    from jobhunter import applog as al
    src = request.args.get("source", "app")
    if src not in LOG_SOURCES:
        src = "app"
    try:
        n = max(20, min(2000, int(request.args.get("n", 300))))
    except (TypeError, ValueError):
        n = 300
    spec = LOG_SOURCES[src]
    if spec["kind"] == "unit":
        raw = _journal_lines(spec["unit"], n)
        lines = [_parse_journal(l) for l in raw]
    elif src == "app":
        lines = [al.parse(l) for l in al.read_app_log(n)]
    else:
        lines = [al.parse(l) for l in al._tail_file(spec["path"], n)]
    level = request.args.get("level", "").upper()
    if level in ("INFO", "WARNING", "ERROR"):
        order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
        floor = order[level]
        lines = [l for l in lines if order.get(l["level"], 1) >= floor]
    q = request.args.get("q", "").strip().lower()
    if q:
        lines = [l for l in lines if q in l["raw"].lower()]
    return jsonify(source=src, label=spec["label"], count=len(lines), lines=lines)


@app.route("/logs/clear", methods=["POST"])
def logs_clear():
    """Vacía el log de la app (los del sistema los gestiona journald)."""
    from jobhunter import applog as al
    try:
        open(al.LOG_FILE, "w").close()
        log.info("Log de la app vaciado desde la UI")
        flash("Log de la app vaciado.", "ok")
    except OSError as e:
        flash(f"No se pudo vaciar el log: {e}", "ok")
    return redirect(url_for("logs_page"))


@app.route("/logs/download")
def logs_download():
    from jobhunter import applog as al
    if not os.path.exists(al.LOG_FILE):
        flash("Todavía no hay log de la app.", "ok")
        return redirect(url_for("logs_page"))
    return send_file(al.LOG_FILE, mimetype="text/plain", as_attachment=True,
                     download_name="jobhunter.log")


@app.route("/architecture")
def architecture_page():
    path = os.path.join(DOCS_DIR, "architecture.html")
    if not os.path.exists(path):
        return "architecture.html no encontrado", 404
    return send_file(path)


@app.route("/architecture.json")
def architecture_json_file():
    path = os.path.join(DOCS_DIR, "architecture.json")
    if not os.path.exists(path):
        return jsonify(error="not found"), 404
    return send_file(path, mimetype="application/json")


@app.route("/workflow")
def workflow_page():
    path = os.path.join(DOCS_DIR, "workflow.html")
    if not os.path.exists(path):
        return "workflow.html no encontrado", 404
    return send_file(path)


@app.route("/api/jobs-status")
def api_jobs_status():
    con = get_db()
    row = con.execute(
        "SELECT COUNT(*) c, COALESCE(MAX(id),0) m FROM jobs").fetchone()
    con.close()
    return jsonify(total=row["c"], latest=row["m"])


import threading
_search_lock = threading.Lock()


def parse_times(raw):
    """'12:00, 18:30' → ['12:00','18:30'] (válidas, normalizadas, sin duplicados)."""
    out, seen = [], set()
    for tok in (raw or "").replace(",", " ").split():
        try:
            hh, mm = tok.split(":")
            hh, mm = int(hh), int(mm)
        except (ValueError, TypeError):
            continue
        if 0 <= hh < 24 and 0 <= mm < 60:
            t = f"{hh:02d}:{mm:02d}"
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def _run_search_scheduled(cur):
    """Ejecuta run_all() en segundo plano, sin solaparse con otra corrida."""
    if not _search_lock.acquire(blocking=False):
        slog.warning("Búsqueda ya en curso a las %s; se omite este disparo", cur)
        return
    try:
        total = run_all()
        slog.info("Búsqueda programada (%s) completada: %s empleo(s) nuevo(s)", cur, total)
    except Exception as e:
        slog.error("Error en la búsqueda programada: %s", e)
    finally:
        _search_lock.release()


def _scheduler():
    """Hilo de fondo (hora local = America/Bogota). Cada minuto:
      · dispara run_all() a las horas configuradas en `search_times` (una o varias);
      · comprueba el resumen diario (notifier.maybe_send_digest, idempotente).
    Idempotencia entre reinicios vía `last_scheduled_run`/`last_digest_date`."""
    import time
    import datetime as dt
    last_minute = None
    while True:
        try:
            now = dt.datetime.now()
            cur = now.strftime("%H:%M")
            if cur != last_minute:           # actuar una vez por minuto
                last_minute = cur
                stamp = now.strftime("%Y-%m-%d ") + cur
                con = get_db()
                times = parse_times(get_setting(con, "search_times", "12:00"))
                already = get_setting(con, "last_scheduled_run", "") == stamp
                if cur in times and not already:
                    set_setting(con, "last_scheduled_run", stamp)
                    con.close()
                    slog.info("Disparando búsqueda programada de las %s", cur)
                    threading.Thread(target=_run_search_scheduled, args=(cur,),
                                     daemon=True).start()
                else:
                    con.close()
                try:
                    sent, msg = notifier.maybe_send_digest(now)
                    if sent:
                        slog.info("Resumen diario enviado — %s", msg)
                except Exception as e:
                    slog.error("Error al enviar el resumen diario: %s", e)
        except Exception as e:
            slog.error("Error en el planificador: %s", e)
        time.sleep(20)


def main(host="0.0.0.0", port=8080):
    """Arranca la app: esquema al día, planificador en segundo plano y servidor."""
    init_db()
    log.info("JobHunter arrancado — servidor en :%s y planificador activo", port)
    threading.Thread(target=_scheduler, daemon=True).start()
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
