"""Notificaciones de empleos nuevos — multi-canal y multi-modo.

Canales (el usuario activa los que quiera, se envían a todos los activos):
  · email     — SMTP con HTML estético (tablas + estilos en línea)
  · telegram  — mensaje vía Bot API (token + chat_id)

Modos de envío:
  · inmediato    — tras cada búsqueda, si aparecen empleos nuevos (notify_immediate)
  · resumen diario — a una hora fija, todo lo encontrado hoy (notify_digest + digest_time).
                     Lo dispara un hilo de fondo en la app web (ver app.py).

Config en la tabla `settings`:
  notify_enabled      '0'/'1'   — interruptor maestro
  notify_immediate    '0'/'1'   — avisar al momento (por defecto sí)
  notify_digest       '0'/'1'   — enviar resumen diario (por defecto no)
  digest_time         'HH:MM'   — hora local del resumen (por defecto 20:00)
  notify_email_on     '0'/'1'   — canal email activo
  notify_telegram_on  '0'/'1'   — canal telegram activo
  notify_email        destino   — a dónde llega el email
  smtp_host/port/user/from      — servidor de salida (por defecto Gmail)
  telegram_chat_id    id/@canal — destino de Telegram
  app_base_url                  — URL de la app para el botón "Ver todos"
Secretos cifrados vía keystore: `smtp_password`, `telegram_token`.
"""
import html as _html
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, formatdate, make_msgid

import requests

from jobhunter import applog
from jobhunter.db import get_db, get_setting, set_setting
from jobhunter import keystore

log = applog.get("notify")

DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 587
DEFAULT_APP_URL = "http://192.168.1.11:8080"
DEFAULT_DIGEST_TIME = "20:00"
TG_LIMIT = 4096          # límite de caracteres de un mensaje de Telegram
TG_MAX_JOBS = 18         # tope de ofertas por mensaje (para no pasarnos del límite)


# --------------------------------------------------------------------------- #
# Configuración                                                               #
# --------------------------------------------------------------------------- #
def load_config():
    """Lee la config de notificaciones desde `settings` (+ secretos cifrados)."""
    con = get_db()
    g = lambda k, d=None: get_setting(con, k, d)
    cfg = {
        "enabled": g("notify_enabled", "0") == "1",
        "immediate": g("notify_immediate", "1") == "1",
        "digest": g("notify_digest", "0") == "1",
        "digest_time": (g("digest_time", "") or DEFAULT_DIGEST_TIME).strip(),
        "email_on": g("notify_email_on", "1") == "1",
        "telegram_on": g("notify_telegram_on", "0") == "1",
        # Email
        "to": (g("notify_email", "") or "").strip(),
        "smtp_host": (g("smtp_host", "") or DEFAULT_SMTP_HOST).strip(),
        "smtp_port": int(g("smtp_port", str(DEFAULT_SMTP_PORT)) or DEFAULT_SMTP_PORT),
        "smtp_user": (g("smtp_user", "") or "").strip(),
        "smtp_from": (g("smtp_from", "") or "").strip(),
        # Telegram
        "tg_chat_id": (g("telegram_chat_id", "") or "").strip(),
        "app_url": (g("app_base_url", "") or DEFAULT_APP_URL).strip(),
    }
    con.close()
    cfg["smtp_from"] = cfg["smtp_from"] or cfg["smtp_user"]
    cfg["smtp_pass"] = keystore.get_secret("smtp_password") or ""
    cfg["tg_token"] = keystore.get_secret("telegram_token") or ""
    return cfg


def email_problems(cfg):
    probs = []
    if not cfg["to"]:
        probs.append("Falta el email de destino.")
    if not cfg["smtp_user"]:
        probs.append("Falta el usuario SMTP (correo remitente).")
    if not cfg["smtp_pass"]:
        probs.append("Falta la contraseña de aplicación SMTP.")
    if not cfg["smtp_host"]:
        probs.append("Falta el servidor SMTP.")
    return probs


def telegram_problems(cfg):
    probs = []
    if not cfg["tg_token"]:
        probs.append("Falta el token del bot de Telegram.")
    if not cfg["tg_chat_id"]:
        probs.append("Falta el chat ID de Telegram.")
    return probs


# Compat: algunos sitios pueden llamar todavía a config_problems (email).
def config_problems(cfg):
    return email_problems(cfg)


def active_channels(cfg):
    """Canales activados por el usuario que además están bien configurados."""
    chans = []
    if cfg["email_on"] and not email_problems(cfg):
        chans.append("email")
    if cfg["telegram_on"] and not telegram_problems(cfg):
        chans.append("telegram")
    return chans


# --------------------------------------------------------------------------- #
# Plantilla de email (HTML embebido, seguro para clientes de correo)          #
# --------------------------------------------------------------------------- #
_BG = "#0f1420"
_CARD = "#171e2e"
_CARD2 = "#1e2739"
_LINE = "#2a3550"
_TXT = "#e6ecf5"
_MUT = "#94a3c4"
_ACCENT = "#4f8cff"
_GREEN = "#22c55e"


def _esc(s):
    return _html.escape(str(s or ""))


def _job_card(job):
    title = _esc(job.get("title"))
    company = _esc(job.get("company") or "—")
    url = _esc(job.get("url") or "#")
    source = _esc(job.get("source") or "")
    salary = (job.get("salary") or "").strip()
    location = (job.get("location") or "").strip()

    meta = []
    if location:
        meta.append(f"📍 {_esc(location)}")
    if salary:
        meta.append(f"💰 {_esc(salary)}")
    if source:
        meta.append(f"🌐 {source}")
    meta_html = (
        f'<div style="color:{_MUT};font-size:13px;margin-top:6px">'
        + " &nbsp;·&nbsp; ".join(meta) + "</div>"
    ) if meta else ""

    skills = [s.strip() for s in (job.get("skills") or "").split(",") if s.strip()][:6]
    skills_html = ""
    if skills:
        pills = "".join(
            f'<span style="display:inline-block;background:rgba(79,140,255,.14);'
            f'color:{_ACCENT};border-radius:20px;padding:2px 10px;font-size:12px;'
            f'margin:4px 4px 0 0">{_esc(s)}</span>'
            for s in skills
        )
        skills_html = f'<div style="margin-top:8px">{pills}</div>'

    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 12px">
      <tr><td style="background:{_CARD2};border:1px solid {_LINE};border-radius:12px;padding:16px 18px">
        <a href="{url}" style="color:{_TXT};font-size:17px;font-weight:700;text-decoration:none;line-height:1.35">{title}</a>
        <div style="color:{_ACCENT};font-size:14px;font-weight:600;margin-top:3px">{company}</div>
        {meta_html}
        {skills_html}
        <div style="margin-top:14px">
          <a href="{url}" style="display:inline-block;background:{_ACCENT};color:#ffffff;
             text-decoration:none;font-weight:600;font-size:13px;padding:9px 16px;border-radius:8px">
            Ver oferta →</a>
        </div>
      </td></tr>
    </table>"""


def render_email(jobs, app_url=DEFAULT_APP_URL, digest=False):
    """HTML del correo. digest=True cambia el copy del hero a 'resumen del día'."""
    n = len(jobs)
    plural = "s" if n != 1 else ""
    cards = "".join(_job_card(j) for j in jobs)
    if digest:
        emoji, headline = "🗓️", "Tu resumen del día"
        sub = (f"Hoy encontré <b style=\"color:{_GREEN}\">{n} oferta{plural}</b> "
               f"que coincide{'n' if n != 1 else ''} con tu búsqueda.")
    else:
        emoji, headline = "🎯", "¡Encontré nuevos empleos!"
        sub = (f"Hay <b style=\"color:{_GREEN}\">{n} oferta{plural}</b> nueva{plural} "
               f"que coincide{'n' if n != 1 else ''} con tu búsqueda.")
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{_BG};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};padding:28px 12px">
<tr><td align="center">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%">

    <tr><td style="padding:0 6px 22px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="vertical-align:middle">
            <span style="font-size:22px;font-weight:800;color:{_TXT};letter-spacing:-.01em">
              Job<span style="color:{_ACCENT}">Hunter</span></span>
          </td>
          <td align="right" style="vertical-align:middle;color:{_MUT};font-size:12px">
            {formatdate(localtime=True)[:16]}
          </td>
        </tr>
      </table>
    </td></tr>

    <tr><td style="background:{_CARD};border:1px solid {_LINE};border-radius:16px;padding:26px 24px;text-align:center">
      <div style="font-size:40px;line-height:1">{emoji}</div>
      <div style="font-size:23px;font-weight:800;color:{_TXT};margin-top:10px">{headline}</div>
      <div style="color:{_MUT};font-size:15px;margin-top:6px">{sub}</div>
    </td></tr>

    <tr><td style="height:20px"></td></tr>

    <tr><td style="padding:0 2px">
      {cards}
    </td></tr>

    <tr><td style="padding:10px 2px 0;text-align:center">
      <a href="{_esc(app_url)}" style="display:inline-block;background:{_GREEN};color:#04210f;
         text-decoration:none;font-weight:700;font-size:14px;padding:12px 22px;border-radius:10px">
        Ver todos en JobHunter</a>
    </td></tr>

    <tr><td style="padding:24px 6px 0;text-align:center;color:{_MUT};font-size:12px;line-height:1.6">
      Recibes este correo porque activaste las notificaciones de JobHunter.<br>
      Puedes desactivarlas en <b>Configuración → Notificaciones</b>.
    </td></tr>

  </table>
</td></tr>
</table>
</body></html>"""


def render_test_email(app_url=DEFAULT_APP_URL):
    sample = [{
        "title": "Senior DevOps Engineer (Remote)",
        "company": "Ejemplo Cloud Inc.",
        "url": app_url,
        "source": "Prueba",
        "salary": "USD 90k–120k",
        "location": "Remote · Worldwide",
        "skills": "Kubernetes, Terraform, AWS, CI/CD",
    }]
    html = render_email(sample, app_url)
    banner = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{_BG};padding:14px 12px 0"><tr><td align="center">'
        f'<div style="max-width:600px;background:rgba(34,197,94,.12);border:1px solid {_GREEN};'
        f'color:{_GREEN};border-radius:10px;padding:10px 14px;font-family:system-ui,sans-serif;'
        f'font-size:13px;text-align:center">✅ Este es un correo de <b>prueba</b>. '
        f'Tus notificaciones de JobHunter están configuradas correctamente.</div>'
        f'</td></tr></table>'
    )
    return html.replace("<body style=\"margin:0;padding:0;background:%s;\">" % _BG,
                        "<body style=\"margin:0;padding:0;background:%s;\">%s" % (_BG, banner))


# --------------------------------------------------------------------------- #
# Plantilla de Telegram (subconjunto HTML de la Bot API)                      #
# --------------------------------------------------------------------------- #
def render_telegram(jobs, app_url=DEFAULT_APP_URL, digest=False):
    """Mensaje HTML para Telegram. Telegram solo admite <b>,<i>,<a>,<code>… (sin CSS)."""
    n = len(jobs)
    plural = "s" if n != 1 else ""
    if digest:
        head = f"🗓️ <b>Tu resumen del día — JobHunter</b>\nHoy encontré <b>{n}</b> oferta{plural}."
    else:
        head = f"🎯 <b>¡Encontré nuevos empleos!</b>\n<b>{n}</b> oferta{plural} nueva{plural} en JobHunter."
    lines = [head, ""]
    for j in jobs[:TG_MAX_JOBS]:
        title = _esc(j.get("title"))
        url = _esc(j.get("url") or app_url)
        company = _esc(j.get("company") or "—")
        bits = [f'💼 <a href="{url}"><b>{title}</b></a>', f"🏢 {company}"]
        extra = []
        if (j.get("location") or "").strip():
            extra.append("📍 " + _esc(j["location"].strip()))
        if (j.get("salary") or "").strip():
            extra.append("💰 " + _esc(j["salary"].strip()))
        if (j.get("source") or "").strip():
            extra.append("🌐 " + _esc(j["source"].strip()))
        line = "\n".join(bits)
        if extra:
            line += "\n" + " · ".join(extra)
        lines.append(line)
        lines.append("")
    if n > TG_MAX_JOBS:
        lines.append(f"…y <b>{n - TG_MAX_JOBS}</b> más.")
    lines.append(f'👉 <a href="{_esc(app_url)}">Ver todos en JobHunter</a>')
    text = "\n".join(lines)
    return text[:TG_LIMIT]


# --------------------------------------------------------------------------- #
# Envío por canal                                                             #
# --------------------------------------------------------------------------- #
def _send_email(cfg, subject, html_body):
    problems = email_problems(cfg)
    if problems:
        return False, "email: " + " ".join(problems)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr(("JobHunter", cfg["smtp_from"]))
    msg["To"] = cfg["to"]
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="jobhunter.local")
    msg.attach(MIMEText("Tienes nuevos empleos en JobHunter. Abre la app para verlos.",
                        "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    host, port = cfg["smtp_host"], cfg["smtp_port"]
    try:
        if port == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=30, context=ctx) as s:
                s.login(cfg["smtp_user"], cfg["smtp_pass"])
                s.sendmail(cfg["smtp_from"], [cfg["to"]], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.ehlo()
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
                s.login(cfg["smtp_user"], cfg["smtp_pass"])
                s.sendmail(cfg["smtp_from"], [cfg["to"]], msg.as_string())
        return True, f"email → {cfg['to']}"
    except smtplib.SMTPAuthenticationError:
        return False, ("email: autenticación SMTP rechazada. Para Gmail usa una "
                       "«contraseña de aplicación» (no tu contraseña normal).")
    except Exception as e:
        return False, f"email: error al enviar ({e})"


def _send_telegram(cfg, text):
    problems = telegram_problems(cfg)
    if problems:
        return False, "telegram: " + " ".join(problems)
    url = f"https://api.telegram.org/bot{cfg['tg_token']}/sendMessage"
    try:
        r = requests.post(url, timeout=20, data={
            "chat_id": cfg["tg_chat_id"],
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        })
        data = r.json() if r.content else {}
        if data.get("ok"):
            return True, f"telegram → {cfg['tg_chat_id']}"
        desc = data.get("description", f"HTTP {r.status_code}")
        return False, f"telegram: {desc}"
    except Exception as e:
        return False, f"telegram: error al enviar ({e})"


def _dispatch(cfg, jobs, subject, digest=False):
    """Envía a todos los canales activos. Devuelve (algún_ok, mensaje_combinado)."""
    chans = active_channels(cfg)
    if not chans:
        # Nada listo: recopila por qué (para el mensaje de la UI/log).
        why = []
        if cfg["email_on"]:
            why += email_problems(cfg)
        if cfg["telegram_on"]:
            why += telegram_problems(cfg)
        if not cfg["email_on"] and not cfg["telegram_on"]:
            why.append("Ningún canal activado.")
        return False, " ".join(why) or "Sin canales configurados."
    results, any_ok = [], False
    if "email" in chans:
        ok, m = _send_email(cfg, subject, render_email(jobs, cfg["app_url"], digest))
        any_ok = any_ok or ok
        (log.info if ok else log.error)("%s (%s empleos)", m, len(jobs))
        results.append(("✓ " if ok else "✗ ") + m)
    if "telegram" in chans:
        ok, m = _send_telegram(cfg, render_telegram(jobs, cfg["app_url"], digest))
        any_ok = any_ok or ok
        (log.info if ok else log.error)("%s (%s empleos)", m, len(jobs))
        results.append(("✓ " if ok else "✗ ") + m)
    return any_ok, " | ".join(results)


# --------------------------------------------------------------------------- #
# Recolección de empleos de hoy (para el resumen diario)                      #
# --------------------------------------------------------------------------- #
def collect_todays_jobs():
    """Empleos guardados hoy (found_at del día en curso), más recientes primero."""
    con = get_db()
    rows = con.execute(
        """SELECT title, company, url, source, salary, location, skills
           FROM jobs WHERE found_at >= date('now','localtime')
           ORDER BY posted_ts DESC, found_at DESC"""
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# API pública                                                                 #
# --------------------------------------------------------------------------- #
def send_new_jobs(jobs, force=False):
    """Aviso INMEDIATO tras una búsqueda. Nunca lanza excepción."""
    if not jobs:
        return False, "Sin empleos que notificar."
    cfg = load_config()
    if not force:
        if not cfg["enabled"]:
            return False, "Notificaciones desactivadas."
        if not cfg["immediate"]:
            return False, "Aviso inmediato desactivado (solo resumen diario)."
    n = len(jobs)
    subject = f"🎯 {n} nuevo{'s' if n != 1 else ''} empleo{'s' if n != 1 else ''} — JobHunter"
    return _dispatch(cfg, jobs, subject, digest=False)


def send_daily_digest(force=False):
    """RESUMEN diario con todo lo encontrado hoy. Nunca lanza excepción."""
    cfg = load_config()
    if not force:
        if not cfg["enabled"] or not cfg["digest"]:
            return False, "Resumen diario desactivado."
    jobs = collect_todays_jobs()
    if not jobs:
        return False, "Hoy no hubo empleos nuevos."
    n = len(jobs)
    subject = f"🗓️ Resumen del día: {n} empleo{'s' if n != 1 else ''} — JobHunter"
    return _dispatch(cfg, jobs, subject, digest=True)


def send_test():
    """Envía una prueba a los canales activos (ignora el interruptor maestro)."""
    cfg = load_config()
    chans = active_channels(cfg)
    if not chans:
        probs = []
        if cfg["email_on"]:
            probs += ["Email: " + p for p in email_problems(cfg)]
        if cfg["telegram_on"]:
            probs += ["Telegram: " + p for p in telegram_problems(cfg)]
        return False, (" ".join(probs)
                       or "Activa y configura al menos un canal (email o Telegram).")
    results, any_ok = [], False
    if "email" in chans:
        ok, m = _send_email(cfg, "🔔 Prueba de notificación — JobHunter",
                            render_test_email(cfg["app_url"]))
        any_ok = any_ok or ok
        results.append(("✓ " if ok else "✗ ") + m)
    if "telegram" in chans:
        text = ("🔔 <b>Prueba de notificación — JobHunter</b>\n"
                "✅ Tu canal de Telegram está configurado correctamente.")
        ok, m = _send_telegram(cfg, text)
        any_ok = any_ok or ok
        results.append(("✓ " if ok else "✗ ") + m)
    return any_ok, " | ".join(results)


# --------------------------------------------------------------------------- #
# Planificador del resumen diario (lo llama el hilo de fondo de la app web)   #
# --------------------------------------------------------------------------- #
def maybe_send_digest(now=None):
    """Envía el resumen si toca (hora alcanzada y no enviado hoy). Idempotente.

    Devuelve (enviado_bool, mensaje) o (False, motivo). Pensado para llamarse cada
    minuto desde un hilo; usa `last_digest_date` para no repetir en el mismo día.
    """
    import datetime as _dt
    cfg = load_config()
    if not cfg["enabled"] or not cfg["digest"]:
        return False, "off"
    now = now or _dt.datetime.now()
    today = now.strftime("%Y-%m-%d")
    con = get_db()
    last = get_setting(con, "last_digest_date", "")
    con.close()
    if last == today:
        return False, "ya enviado hoy"
    # ¿Alcanzada la hora configurada?
    try:
        hh, mm = [int(x) for x in cfg["digest_time"].split(":", 1)]
    except Exception:
        hh, mm = 20, 0
    if (now.hour, now.minute) < (hh, mm):
        return False, "aún no es la hora"
    ok, msg = send_daily_digest()
    # Marcamos el día como hecho aunque no hubiera empleos, para no reintentar en bucle.
    con = get_db()
    set_setting(con, "last_digest_date", today)
    con.close()
    return ok, msg


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "digest":
        print("Resumen diario:", *send_daily_digest(force=True))
    elif len(sys.argv) > 1 and sys.argv[1] == "preview":
        with open("/tmp/jobhunter_email_preview.html", "w", encoding="utf-8") as f:
            f.write(render_test_email())
        print("Vista previa en /tmp/jobhunter_email_preview.html")
    else:
        print("Prueba:", *send_test())
