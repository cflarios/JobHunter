import os
import datetime as dt
from urllib.parse import quote_plus
from flask import (Flask, render_template, request, redirect, url_for,
                   jsonify, flash, Response, send_file)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from db import get_db, init_db, get_setting, set_setting
from fetcher import run_all
from reviews import generate_company_summary
import cv as cvai

app = Flask(__name__)
app.secret_key = "job-hunter-local-secret"


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
        safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
        if safe.startswith("- ") or safe.startswith("* "):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{safe[2:]}</li>")
        else:
            if in_list:
                out.append("</ul>"); in_list = False
            if safe:
                out.append(f"<p>{safe}</p>")
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
        sql += " AND (j.title LIKE ? OR j.company LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
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
    jobs = con.execute(sql, params).fetchall()
    has_profile = con.execute("SELECT 1 FROM profile WHERE id=1").fetchone() is not None
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
                           n_matches=n_matches)


@app.route("/mark-seen", methods=["POST"])
def mark_seen():
    con = get_db()
    con.execute("UPDATE jobs SET is_new=0 WHERE is_new=1")
    con.commit()
    con.close()
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
        con.commit()
        con.close()
        return redirect(url_for("searches"))

    rows = con.execute("""
        SELECT s.*, (SELECT COUNT(*) FROM jobs j WHERE j.search_id=s.id) AS njobs
        FROM searches s ORDER BY s.active DESC, s.id""").fetchall()
    max_age = get_setting(con, "max_age_days", "3")
    location_mode = get_setting(con, "location_mode", "worldwide")
    con.close()
    return render_template("searches.html", searches=rows, max_age=max_age,
                           location_mode=location_mode)


@app.route("/run", methods=["POST"])
def run_now():
    q = request.form.get("query", "").strip() or None
    total = run_all(q)
    flash(f"Búsqueda ejecutada: {total} empleo(s) nuevo(s).", "ok")
    return redirect(request.referrer or url_for("index"))


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
    con.close()
    companies = []
    for r in rows:
        rev = reviews.get(r["company"])
        resolved = rev["resolved_name"] if rev and rev["resolved_name"] else None
        gd_name = resolved or r["company"]
        companies.append({
            "name": r["company"],
            "gd_name": gd_name,
            "resolved": resolved if resolved and resolved.lower() != r["company"].lower() else None,
            "njobs": r["njobs"],
            "last_ts": r["last_ts"],
            "sources": (r["sources"] or "").split(","),
            "glassdoor": "https://www.glassdoor.com/Search/results.htm?keyword="
                         + quote_plus(gd_name),
            "review": rev["summary"] if rev else None,
            "review_ok": (rev["status"] == "ok") if rev else None,
            "review_at": rev["generated_at"] if rev else None,
        })
    return render_template("companies.html", companies=companies)


@app.route("/companies/summary", methods=["POST"])
def company_summary():
    company = request.form.get("company", "").strip()
    if company:
        con = get_db()
        existing = con.execute(
            "SELECT resolved_name FROM company_reviews WHERE company=?", (company,)
        ).fetchone()
        # Un nombre de Glassdoor ya fijado (manual o previo) manda la búsqueda.
        pinned = existing["resolved_name"] if existing and existing["resolved_name"] else None
        result = generate_company_summary(pinned or company)
        keep_resolved = pinned or result.get("resolved")
        con.execute(
            "INSERT INTO company_reviews(company,summary,resolved_name,status,generated_at) "
            "VALUES(?,?,?,?,datetime('now','localtime')) "
            "ON CONFLICT(company) DO UPDATE SET "
            "summary=excluded.summary, resolved_name=excluded.resolved_name, "
            "status=excluded.status, generated_at=excluded.generated_at",
            (company, result["summary"], keep_resolved,
             "ok" if result["ok"] else "error"))
        con.commit()
        con.close()
        if request.headers.get("X-Requested-With") == "fetch":
            from types import SimpleNamespace
            c = SimpleNamespace(
                review=result["summary"],
                review_ok=result["ok"],
                review_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            gd_name = keep_resolved or company
            return jsonify(
                ok=result["ok"],
                html=render_template("_review.html", c=c),
                glassdoor="https://www.glassdoor.com/Search/results.htm?keyword="
                          + quote_plus(gd_name),
                gd_name=gd_name)
        flash(f"Resumen de «{company}» {'generado' if result['ok'] else 'no disponible'}.", "ok")
    return redirect(url_for("companies") + f"#c-{quote_plus(company)}")


@app.route("/companies/glassdoor-name", methods=["POST"])
def company_glassdoor_name():
    company = request.form.get("company", "").strip()
    gd_name = request.form.get("glassdoor_name", "").strip() or None
    if company:
        con = get_db()
        con.execute(
            "INSERT INTO company_reviews(company,resolved_name) VALUES(?,?) "
            "ON CONFLICT(company) DO UPDATE SET resolved_name=excluded.resolved_name",
            (company, gd_name))
        con.commit()
        con.close()
        flash(f"Nombre de Glassdoor de «{company}» actualizado.", "ok")
    return redirect(url_for("companies") + f"#c-{quote_plus(company)}")


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
    return render_template("cv.html", profile=profile, n_jobs=n_jobs, n_matches=n_matches)


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
        else:
            try:
                text = text or data.decode("utf-8", "ignore")
            except Exception:
                text = text
    if not text and not pdf_b64:
        flash("Pega el texto de tu CV o sube un PDF.", "ok")
        return redirect(url_for("cv_page"))

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
                 feedback=NULL,rewrite=NULL,updated_at=excluded.updated_at""",
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


@app.route("/cv/clear", methods=["POST"])
def cv_clear():
    con = get_db()
    con.execute("DELETE FROM profile")
    con.execute("DELETE FROM job_matches")
    con.commit()
    con.close()
    flash("Perfil y afinidades borrados.", "ok")
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
        html = render_template("_fitblock.html", detail=res["html"])
        return jsonify(ok=True, html=html, score=res["score"])
    con.close()
    return jsonify(ok=False, error=res.get("error", "")), 500


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


@app.route("/architecture")
def architecture_page():
    path = os.path.join(BASE_DIR, "architecture.html")
    if not os.path.exists(path):
        return "architecture.html no encontrado", 404
    return send_file(path)


@app.route("/architecture.json")
def architecture_json_file():
    path = os.path.join(BASE_DIR, "architecture.json")
    if not os.path.exists(path):
        return jsonify(error="not found"), 404
    return send_file(path, mimetype="application/json")


@app.route("/workflow")
def workflow_page():
    path = os.path.join(BASE_DIR, "workflow.html")
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


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8080, debug=False)
