# JobHunter — Contexto del proyecto (documentación interna)

> Documento de rumbo. El **README.md** es operativo (cómo se usa/despliega); este
> archivo guarda el **por qué**, las **decisiones**, los **gotchas** y el **roadmap**
> para no perder el hilo entre sesiones. Última actualización: 2026-07-23.

---

## 1. Objetivo

Buscador **personal** de empleos remotos, auto-alojado en una **Raspberry Pi**.
El usuario busca **DevOps Engineer / SRE remoto como contractor desde Colombia**.
El sistema es **multi-rol** (se pueden añadir otras búsquedas), pero el foco por
defecto es DevOps/SRE.

Requisitos originales que guían el diseño:
- Buscar empleos relacionados en internet, **publicados hace ≤ 3 días** (ajustable).
- Correr **todos los días a las 12:00** (hora Colombia).
- Página donde se **guarda** lo encontrado y que **genera notificaciones**.
- El reporte debe incluir **dónde se publicó, salario y fecha**.

Inspiración posterior: **reaver.ink** (matcheo de CV con IA). Su parte de pago la
replicamos **gratis** con Gemini (ver §7).

---

## 2. Estado actual (stack y acceso)

- **Stack:** Python 3.13 · Flask · SQLite (WAL) · systemd · Claude/Gemini API · RapidAPI.
- **Host:** Raspberry Pi (Debian 13). App en `http://192.168.1.11:8080` (LAN).
- **Repo git:** local, rama `main`, en `/home/pi/project/job-hunter/`.
- **Fuentes:** 11 bolsas de empleo.
- **IA:** proveedor **seleccionable** (Claude / Gemini) — por defecto **Claude Opus 4.8**
  (Anthropic); Gemini 2.5 Flash como alternativa gratis. Cubre resúmenes de
  empresas + CV/matching.
- **Mapas visuales:** `/architecture` y `/workflow` (dentro de la app y como
  artefactos publicados). El botón "Ver como workflow" vive dentro de Arquitectura.

---

## 3. Estructura de archivos

```
job-hunter/
├── jobhunter/          # PAQUETE de la aplicación (todo el código)
│   ├── __init__.py
│   ├── paths.py        # Rutas del proyecto en un solo sitio (ROOT, data/, docs/)
│   ├── app.py          # Servidor Flask: rutas, filtros Jinja, planificador
│   ├── db.py           # Esquema SQLite (11 tablas) + carga de .env (fallback)
│   ├── fetcher.py      # 12 fuentes, filtros (título/ubicación/fecha), orquestación
│   ├── skills.py       # Extracción de skills técnicas del texto (diccionario curado)
│   ├── llm.py          # Capa de proveedor de IA: enruta a Claude o Gemini
│   ├── applog.py       # Log central rotativo + lectura/parseo para la UI
│   ├── notifier.py     # Notificaciones multi-canal (email SMTP + Telegram)
│   ├── tracker.py      # Seguimiento de postulaciones: estados, embudo y métricas
│   ├── reviews.py      # Resumen de reputación de empresas (IA + búsqueda web)
│   ├── cv.py           # CV + IA: perfil, match, ¿encajo?, carta, CV nuevo,
│   │                   #   CV a medida por vacante (ATS) + blindaje anti-alucinación
│   ├── cvpdf.py        # Renderiza los CVs a PDF (fpdf2 + DejaVu, ≤2 págs)
│   ├── templates/      # 13 vistas Jinja2
│   └── static/
├── data/               # RUNTIME, gitignored: jobs.db, secret.key, logs/, search.log
├── docs/               # CONTEXT.md + mapas (architecture.json/html, workflow.html)
├── deploy/             # Copia de referencia de las unidades systemd (sin secretos)
├── scripts/run_search.sh   # Wrapper para corridas manuales (→ data/search.log)
├── run.py              # Punto de entrada: from jobhunter.app import main
├── requirements.txt
├── .env                # SECRETOS (gitignored, 600) — no se versiona
├── .env.example        # Plantilla versionada
└── README.md           # Operativo
```

**Estándar de layout (2026-07-23).** Antes los 12 módulos, los datos, los mapas y
los docs convivían sueltos en la raíz. Ahora: **el código va en el paquete
`jobhunter/`**, lo generado en runtime en **`data/`** y la documentación en
**`docs/`**. Reglas que lo sostienen:
- **Los imports internos son absolutos** (`from jobhunter.db import get_db`), no
  planos. Funcionan igual con `python run.py` que con `python -m jobhunter.fetcher`.
- **Ninguna ruta se recalcula con `__file__`** fuera de `jobhunter/paths.py`: ahí
  se define `ROOT`, `DATA_DIR`, `DB_PATH`, `SECRET_KEY_FILE`, `APP_LOG`, `DOCS_DIR`
  y `ENV_FILE`. Mover un módulo ya no cambia dónde vive la BD ni la clave maestra.
- **El código nunca escribe dentro del paquete**; todo lo mutable cae en `data/`,
  que está gitignored en bloque (antes `*.log` no cubría los rotados `.log.1`).
- **`run.py` es el entrypoint estable** que usa systemd, así que reorganizar el
  paquete por dentro no obliga a tocar las unidades.

También hay **memoria de Claude** en
`~/.claude/projects/-home-pi-project/memory/` (`jobhunter-project.md`,
`jobhunter-live-settings.md`) — se cargan como contexto en cada sesión.

---

## 4. Flujo de datos (resumen)

```
Planificador in-app a las horas configuradas (o Usuario "Buscar ahora")
   → fetcher.run_all()   (run_search.sh sigue disponible para corridas manuales)
      → run_search(): consulta las 11 fuentes (HTTP)
         → filtros uniformes: title_ok → location_ok → ventana de días → dedup(URL)
            → INSERT OR IGNORE en `jobs` + notificación si hay nuevos
   → Flask lee jobs.db → Empleos / Compañías / Mi CV / Notificaciones
   → El navegador sondea /api/jobs-status (45s) y /api/unread (30s) para avisar
     sin recargar.

Ramas bajo demanda (Gemini):
   Compañías → /companies/summary → reviews.py → Gemini(grounding) → company_reviews
   Mi CV     → /cv/analyze → cv.py → Gemini → profile
             → /cv/match → job_matches → afinidad en Empleos
             → /jobs/{id}/fit y /cover → análisis y carta por oferta
```

Ver el detalle visual en `/architecture` y `/workflow`.

---

## 5. Fuentes de empleo (11)

| Fuente | Tipo | Notas |
|---|---|---|
| Remotive | API JSON | `search=query` |
| RemoteOK | API JSON | feed completo, filtro local |
| Jobicy | API JSON | `tag=query` — **devuelve todo su feed**, el filtro de título es imprescindible |
| Himalayas | API JSON | feed de remotos |
| WeWorkRemotely | RSS ×2 | feeds DevOps/Sysadmin + Programming |
| Arbeitnow | API JSON | solo empleos remotos |
| The Muse | API JSON | `location=Flexible/Remote`, 2 páginas |
| Working Nomads | API JSON | todas las categorías |
| Landing.jobs | API JSON | incluye salario; usa códigos ISO de país (mapeados) |
| Get on Board | API JSON v0 | LATAM, salario USD; empresa vía `/companies/{id}` (caché acotada) |
| **LinkedIn** | **RapidAPI** | `linkedin-job-search-api` endpoint `active-jb`; salario/modalidad/skills. Plan BASIC = cuota mensual limitada (429 al agotarse) |
| **JSearch** | **RapidAPI** | `jsearch.p.rapidapi.com` endpoint `search` (agrega Google for Jobs). Requiere que la suscripción exponga `/search` |

**Añadir una fuente pública:** escribir `fetch_x(query)` en `fetcher.py` que
devuelva dicts con `title/company/url/source/salary/location/posted_ts` (+ `_text`
para el match de título) y agregarla a la lista `SOURCES`.

**Añadir una fuente de RapidAPI:** usar el helper genérico
`_rapidapi_get(host, path, params)` (pone cabeceras `x-rapidapi-host/key`),
escribir `fetch_x`, sumarla a `SOURCES` **y** a `RAPIDAPI_SOURCES` (para que el
interruptor la cubra). Si falta `RAPIDAPI_KEY`, esas fuentes se **omiten en
silencio**.

**Interruptor RapidAPI (cuota limitada):** las fuentes de `RAPIDAPI_SOURCES`
(LinkedIn, JSearch) están **apagadas por defecto** y solo se consultan si el
setting `use_rapidapi=1` (casilla "Fuentes RapidAPI" en la página Búsquedas).
`run_search(..., use_rapidapi)` las salta cuando está apagado, así el cron no
gasta cuota. Recordar apagarlas cuando no se necesiten.

---

## 6. Filtros (aplicados por igual a todas las fuentes)

En `fetcher.run_search()`, en este orden:

1. **`title_ok(title, keywords, query)`** — el **TÍTULO** debe contener alguna
   palabra clave del rol. Cada búsqueda tiene `title_keywords` (p. ej.
   `devops, sre, site reliability`). Evita roles tangenciales ("Data Engineer que
   menciona DevOps"). Si no hay keywords, exige que el título contenga todos los
   tokens de la query.
2. **`location_ok(location, mode)`** — modos:
   - `worldwide` (por defecto): solo abiertos a cualquier parte o que incluyan
     LATAM/Americas/Colombia; descarta locks a país/estado.
   - `americas`: todo el continente (incluye US-only).
   - `any`: sin filtro.
3. **Ventana de días** — `posted_ts` dentro de `max_age_days` (por búsqueda o global).
4. **Dedup** — `INSERT OR IGNORE` por URL única.

También, antes de los filtros anteriores: **blacklist de compañías**
(`blocked_companies`) — los empleos cuya empresa esté bloqueada se descartan
(comparación case-insensitive por `name.strip().lower()`). Se gestiona en la
**página Bloqueos** (`/blacklist`): **añadir a mano** el nombre (bloqueo
preventivo, sin gastar cómputo) + lista con "Desbloquear". El botón "🚫 Bloquear"
por tarjeta en Compañías sigue existiendo. Al bloquear, `/companies/block` también
**borra** los empleos ya guardados de esa empresa (desaparecen al instante).
`/companies/block` y `/unblock` vuelven al **origen** (referrer: Compañías o
Bloqueos) vía `app._back()`.

Configurable desde la página **Búsquedas** (por búsqueda) y con ajustes globales.

**Skills (`skills.py`, columna `jobs.skills`):** al ingerir, `extract_skills_str()`
saca las skills técnicas del **`_text`** de cada fuente (título + descripción +
tags/categorías) con un **diccionario curado** (matching por palabra completa,
alias k8s→Kubernetes, etc.; evita falsos positivos como "go"/"rest"). Se guardan
separadas por coma. En **Empleos** se muestran como **pills clicables** (llevan a
`?q=<skill>`) y el buscador `q` también matchea la columna `skills` (título/empresa/
skill). `db._backfill_skills()` rellena una vez las filas antiguas desde el título
(NULL=sin procesar, ''=procesado sin skills); las skills ricas llegan con la próxima
búsqueda. Fallback en la vista: si un empleo no tiene skills guardadas, se extraen
del título al vuelo.

---

## 7. IA con proveedor seleccionable (Claude / Gemini)

**Selector en la UI** (página **Configuración** → "Proveedor de IA"), guardado en
`settings.ai_provider` (por defecto `claude`). Toda la IA pasa por **`llm.py`**,
que expone `complete(parts, json_out=, max_tokens=, web_search=)` y enruta a:
- **Claude** (`ANTHROPIC_API_KEY`, modelo `claude-opus-4-8`, override con
  `ANTHROPIC_MODEL`). Búsqueda web = herramienta de servidor `web_search_20260209`;
  se maneja `pause_turn` reenviando el turno. Sin `thinking` (más barato/rápido).
- **Gemini** (`GEMINI_API_KEY`, `gemini-2.5-flash`). **Gotcha crítico:** Gemini 2.5
  gasta tokens de "thinking" que truncan la salida (`finishReason: MAX_TOKENS`);
  solución `thinkingConfig.thinkingBudget = 0` + `maxOutputTokens` holgado.
  Grounding = `tools:[{google_search:{}}]`.

`llm.py` acepta `parts` estilo Gemini (`{"text"}` / `{"inline_data"}`) y los
traduce a bloques de Claude (`document`/`image`). Devuelve `(ok, data_or_error)`;
con `json_out` parsea el JSON de forma robusta (tolera ```json y texto alrededor).
Puede forzarse el proveedor con la env `AI_PROVIDER` en runs manuales.

- **`reviews.py`** — resumen de reputación de empresas (Glassdoor) con **búsqueda
  web / grounding**. Glassdoor no tiene API pública gratis ni permite scraping,
  por eso se resume desde la web. El prompt pide 3 líneas de cabecera:
  `EMPRESA: <nombre canónico>`, `GLASSDOOR: <URL directa de la empresa>` y luego el
  resumen. `_parse()` extrae ambas y limpia artefactos de cita de ambos proveedores
  (`[cite:...]` de Gemini y `<cite index=...>` de Claude) + la narración que Claude
  intercala (corta desde `EMPRESA:`). La **URL directa** (página Overview/Reviews de
  la empresa) se **valida** con `_clean_glassdoor_url()` (debe ser glassdoor.com y
  apuntar a página de empresa, no al buscador) y se cachea en
  `company_reviews.glassdoor_url`. El botón **"Ver en Glassdoor"** usa esa URL
  directa; si no hay, cae al **buscador** (`_glassdoor_search()`) y el botón dice
  "Buscar en Glassdoor". Fijar el nombre a mano (`/companies/glassdoor-name`) **borra**
  la URL (puede no coincidir); se re-resuelve al regenerar. Un regenerado que no
  halle URL **conserva** la previa.
- **`cv.py`** — funciones inspiradas en reaver.ink:
  - **`reference_blob(profile)` — el CV GENERADO es la referencia.** `match_jobs`,
    `analyze_fit` y `cover_letter` ya no puntúan contra los campos sueltos del perfil,
    sino contra el **CV generado** (`build_cv`), mucho más rico: titular, resumen,
    skills, experiencia con logros (`generated_cvs()` + `cv_blob()`). Si aún no hay CV
    generado, cae al `profile_blob` de siempre. `improve_cv` **sigue** usando
    `profile_blob` a propósito: mejora el CV original, no debe realimentarse de su
    propia salida.
  - `analyze_cv` (PDF inline o texto) → perfil (rol, seniority, años, skills, keywords).
  - `match_jobs` → afinidad 0–100 por empleo (badge y orden en Empleos).
  - `analyze_fit` → "¿Encajo aquí?" por oferta (coincidencias, gaps, qué resaltar).
  - `cover_letter` → carta de presentación a medida.
  - `improve_cv` → feedback Harvard/ATS + reescritura.
  - **`tailor_cv(base_cv, job, lang, job_desc, profile)`** → **CV a medida de una
    vacante** (optimización ATS). Parte del **CV generado** y lo adapta al puesto:
    reordena skills, reformula viñetas con la terminología de la oferta, reescribe
    titular y resumen. Devuelve `{cv, notes, ats_score}`; `notes` lista keywords
    incorporadas, cambios y **gaps** (lo que la oferta pide y el CV no respalda —
    se reporta, **nunca** se inventa). El usuario puede **pegar la descripción de la
    oferta** (`job_desc`): es de donde salen las keywords reales del ATS, ya que no
    guardamos la descripción de los empleos.
  - **`_enforce_facts(base_cv, cv)` — red de seguridad anti-alucinación.** El prompt
    prohíbe falsear datos, pero la IA **no siempre obedece** (en pruebas convirtió
    "DevOps Engineer" en "Site Reliability Engineer" y reordenó experiencias). Por eso
    **en código** se restauran desde el CV base: nombre y contacto, y por cada
    experiencia **cargo, empresa, período y ubicación** (casadas por empresa), además
    del **orden cronológico**; educación e idiomas se copian tal cual y las
    certificaciones se filtran a las ya existentes. Una experiencia cuya empresa no
    esté en el CV base se **descarta**. Lo legítimo (viñetas reformuladas, titular,
    resumen, orden de skills) **se conserva**. Las correcciones aplicadas se muestran
    al usuario en «🛡 Correcciones automáticas de integridad».
  - `build_cv(profile, lang)` → **CV NUEVO** en JSON estructurado (name/headline/
    contact/summary/skills/experience/education/certifications/languages) aplicando
    las recomendaciones. **No inventa** datos: usa solo lo real del CV/perfil, deja
    vacío lo que no exista. `lang` = `es`|`en`. **Idioma elegible en la UI**
    (Español / English / Ambos): `/cv/build` lee `cv_lang`; para "ambos" genera dos
    CVs. Se cachea en `profile.generated_cv` como `{lang: cv}` (formato antiguo plano
    = un solo CV → se trata como `es`; `app._generated_cv_langs()` tolera ambos).
- **Gotcha de formato:** los prompts que devuelven Markdown (`improve_cv`) piden
  **no usar encabezados** (`#`, `##`) — solo **negrita** y viñetas. Como red de
  seguridad, el filtro `md` convierte cualquier línea `#…` en negrita (nunca sale
  el `##` literal).
- En subidas PDF, `app._pdf_text()` (pypdf) extrae el texto y lo guarda en
  `profile.cv_text` (el análisis usa el PDF inline; el texto sirve para reconstruir).
- **PDF del CV nuevo** (`cvpdf.py`, fpdf2 + DejaVu Sans para acentos): `render(cv)`
  dibuja un layout compacto de una columna. **Garantiza ≤2 páginas** con doble pase
  (si a escala 1.0 excede, reintenta a 0.86). `render(cv, lang)` localiza los
  títulos de sección (es/en). `/cv/build` genera y cachea el JSON; `/cv/download?lang=`
  lo renderiza y sirve como adjunto `CV_<nombre>_<LANG>.pdf`.
- **Etiquetas de proveedor en la UI:** un `@app.context_processor` inyecta `ai_label`
  a todas las plantillas, así los pies "Generado por…", "Análisis por…",
  "Resumen por…" reflejan el proveedor **activo** (antes decían "Gemini" fijo).

**Privacidad:** el CV se guarda **solo en la Pi** (`profile`/`job_matches`);
"Borrar perfil" lo elimina.

---

## 7b. Notificaciones (`notifier.py`) y página Configuración

**Sistema multi-canal y multi-modo.** El aviso **in-app** (tabla `notifications` +
badge) sigue igual e independiente; email/Telegram son canales **adicionales** que el
usuario activa. `notifier` despacha a **todos los canales activos y bien configurados**
(`active_channels(cfg)`).

- **Canales** (el usuario marca los que quiera):
  - **email (SMTP)** — HTML estético (ver abajo).
  - **telegram** — mensaje vía **Bot API** (`sendMessage`, `parse_mode=HTML`); usa el
    subconjunto HTML de Telegram (`<b>`, `<a>`… sin CSS), lista las ofertas con enlace
    (tope `TG_MAX_JOBS=18`, corta a 4096 chars). Añadir otro canal = una función
    `_send_x` + rama en `_dispatch`.
- **Modos de envío** (independientes, se pueden combinar):
  - **inmediato** (`notify_immediate`, por defecto **on**): al final de
    `fetcher.run_all()`, si hubo empleos nuevos → `send_new_jobs(all_new_jobs)`. Un
    **solo** aviso con **todos** los nuevos (acumulados de todas las búsquedas).
    `run_search()` devuelve ahora `(inserted, seen, new_jobs)` y `run_all()` acumula.
    En `try/except`: **nunca** rompe la búsqueda; loguea enviada/omitida/error.
  - **resumen diario** (`notify_digest`, por defecto off; hora `digest_time`, def.
    `20:00` hora Colombia): `send_daily_digest()` junta lo de hoy
    (`collect_todays_jobs()`, filtra por `found_at >= date('now','localtime')`).
    **Lo dispara un hilo demonio en la app web** (`app._digest_scheduler`, cada 60 s →
    `notifier.maybe_send_digest()`), **idempotente** vía `settings.last_digest_date`
    (no duplica aunque el proceso reinicie). **No hace falta un timer systemd nuevo**:
    la app web ya corre siempre.
- **Email HTML estético** (`notifier.render_email(jobs, url, digest=)`): **tablas +
  estilos en línea** (Gmail ignora `<style>`/CSS externo), paleta de la app, cabecera
  JobHunter, hero (🎯 "¡Encontré nuevos empleos!" o 🗓️ "Tu resumen del día"), una
  tarjeta por oferta (título enlazado, empresa, 📍/💰/🌐, pills de skills, "Ver
  oferta") y CTA "Ver todos". Alternativa `text/plain` incluida.
- **Config en `/settings`** (Configuración): interruptor maestro + modos (inmediato,
  resumen+hora) + por canal su toggle y sus campos. Email: destino + SMTP (host,
  puerto, usuario, "De", contraseña de app). Telegram: token + chat ID. **Secretos**
  (`smtp_password`, `telegram_token`) **cifrados** vía `keystore.set_secret(...)`
  (misma clave maestra Fernet); nunca en texto plano ni visibles. Botón **"Enviar
  prueba"** (`send_test`, va a los canales listos, ignora el maestro), y borrado de
  cada secreto. Avisa de canales activados pero incompletos
  (`email_problems`/`telegram_problems`).
- **Gmail:** **contraseña de aplicación** (no la normal; requiere verificación en 2
  pasos), 16 chars (con o sin espacios). Puerto 587 = STARTTLS; 465 = SSL (ambos, el
  helper detecta por puerto). **Telegram:** token de @BotFather + chat ID de
  @userinfobot (o ID de grupo/canal con el bot como admin).
- **La página Configuración** también aloja el **Proveedor de IA** + **Claves de API**
  (movidos desde Búsquedas). Búsquedas quedó solo con lo suyo (ubicación, RapidAPI,
  ventana global, alta/edición). Entrada ⚙️ en la nav.

---

## 8. Base de datos (SQLite `jobs.db`, WAL) — 11 tablas

| Tabla | Para qué |
|---|---|
| `searches` | Búsquedas: query, title_keywords, max_age_days, active |
| `blocked_companies` | Blacklist: empresas que no deben aparecer (name PRIMARY KEY COLLATE NOCASE) |
| `jobs` | Empleos: title, company, url (unique), source, salary, location, posted_ts, skills, **description** (extracto), is_new |
| `notifications` | Avisos de hallazgos (read) |
| `settings` | Config global (location_mode, max_age_days, last_run, ai_provider, use_rapidapi, **search_times** [horas del planificador], last_scheduled_run, notify_* [enabled/immediate/digest/digest_time/email_on/telegram_on/email], smtp_*, telegram_chat_id, last_digest_date, app_base_url; `apikey_*`/`secret_smtp_password`/`secret_telegram_token` cifrados) |
| `company_reviews` | Caché de resúmenes de empresa + resolved_name + glassdoor_url (página directa) |
| `profile` | Perfil del CV (1 fila): cv_text, role, skills, summary, suggested_keywords, feedback, rewrite, generated_cv (JSON del CV nuevo) |
| `job_matches` | Afinidad por empleo: score, reason, fit_detail |
| `applications` | Estado ACTUAL de cada postulación: job_id (PK→jobs), status, applied_at, closed_at, notes |
| `application_events` | HISTORIAL de transiciones (from_status→to_status): es lo que permite dibujar el embudo |
| `tailored_cvs` | CV a medida por vacante: job_id (PK→jobs), lang, cv (JSON), notes, ats_score, job_desc, updated_at |

---

## 9. Rutas HTTP principales

Empleos `/` · Buscar ahora `/run` · Búsquedas `/searches` · **Configuración**
`/settings` (proveedor de IA + claves + notificaciones/SMTP; POST: `set_provider`,
`set_apikey`/`clear_apikey`, `set_notify`, `clear_smtp_pass`, `clear_telegram_token`,
`test_notify`, `set_schedule` [horarios del planificador]) ·
**Postulaciones** `/applications` (embudo Sankey + métricas; POST
`/jobs/<id>/status` y `/jobs/<id>/note`) ·
**Logs** `/logs` (consola en vivo; + `/api/logs`, `/logs/clear`, `/logs/download`) ·
Notificaciones `/notifications` · Compañías `/companies` (+ `/companies/summary`,
`/companies/glassdoor-name`, `/companies/block`, `/companies/unblock`) ·
**Bloqueos** `/blacklist` (blacklist de compañías, alta manual) ·
En **Compañías**, la píldora «📋 N oferta(s)» es un **botón** que despliega, en la
propia tarjeta, las vacantes de esa empresa (título enlazado a la **postulación
real**, salario, ubicación, fecha, fuente, badge NUEVO y % de afinidad) más un
enlace «Ver estas ofertas en Empleos» (`/?q=<empresa>`). Las ofertas se
**pre-renderizan** en `companies()` (`jobs_by_company`, agrupadas por
`company.strip().lower()`): son pocas decenas, así que abrir es instantáneo y sin
AJAX. Evita tener que ir a Empleos a buscarlas a mano.
**Mi CV** `/cv` (+ `/cv/analyze`, `/cv/match`, `/cv/improve`, `/cv/apply-keywords`,
`/cv/build`, `/cv/download?lang=`) · por oferta `/jobs/<id>/fit`, `/cover`,
**`/tailor`** (CV a medida ATS, AJAX) y **`/cv.pdf`** (descarga ese CV) ·
mapas `/architecture` `/architecture.json` `/workflow` · polling `/api/unread`
`/api/jobs-status`.

---

## 9b. Logging y página de Logs (`applog.py`)

**Un único log central** en `logs/jobhunter.log` (rotativo: 512 KB × 3 backups) al que
escriben **los dos procesos** (web y las corridas de búsqueda), más un `StreamHandler`
para que la salida siga llegando al journal y a `search.log` como antes.

Formato pensado para parsearse en la UI:
`2026-07-23 18:04:11 | INFO | search | «DevOps Engineer» (≤3d): 20 vistos, 1 nuevos`

- `applog.get(name)` → logger hijo de `jh` (`web`, `search`, `sched`, `notify`).
  Los `print()` dispersos de `fetcher.py` y del planificador se migraron a este log.
- **Página `/logs`** — **vista de consola** (terminal oscura, monoespaciada, coloreada
  por nivel) con **4 fuentes** en pestañas: **App** (`jobhunter.log`, incluye rotados),
  **Búsquedas** (`search.log`), **Sistema · web** y **Sistema · búsqueda** (journald).
  Filtros de **nivel** y **texto**, selector de líneas (200/500/1000), **modo en vivo**
  (sondeo 5 s) con auto-scroll que **respeta el scroll manual**, descargar y vaciar.
- `/api/logs?source=&n=&level=&q=` devuelve las líneas ya parseadas
  (`ts/level/src/msg`). Las del journal se parsean aparte (`_parse_journal`).
- **Seguridad:** `source` es una **lista blanca cerrada** (`LOG_SOURCES`); nunca se
  interpola en el comando de `journalctl`, solo indexa el diccionario. `pi` puede leer
  el journal por pertenecer al grupo `adm`.
- **Gotcha:** los ficheros rotados se llaman `jobhunter.log.1`, `.2`… y **no** casan
  con el patrón `*.log` del `.gitignore`; por eso se ignora el directorio `logs/`.

---

## 9c. Seguimiento de postulaciones (`tracker.py`) y descripción de la oferta

**Por qué hay dos tablas y no una.** `applications` guarda el estado **actual**;
`application_events` guarda el **historial** de transiciones. Si una postulación
acaba en `rechazado`, el estado actual no cuenta que pasó por entrevista técnica —
y sin ese recorrido el embudo sería falso. El Sankey se construye contando
transiciones reales (`_reached()` calcula la etapa **máxima alcanzada** por cada
oferta a partir de los eventos).

- **Etapas** (`STAGES`): interesado → postulado → screening → técnica → final →
  oferta → aceptada. **Salidas** (`OUTCOMES`, desde cualquier etapa): rechazado,
  sin respuesta (ghosteado), me retiré.
- **Nodo «En curso».** Sin él el Sankey *pierde* flujo (llegaron 8, salen 7) y
  parece un error de cuadre; en realidad son postulaciones esperando en esa etapa.
  Se calcula con el estado actual y hace que **cada etapa cuadre**:
  `llegaron = avanzan + caen + esperan`. Verificado con un escenario de 9 ofertas.
- **`IMPLIED_MIN` — etapa mínima que implica un desenlace.** Bug real detectado en
  uso: si marcas una oferta directamente como `rechazado` o `ghosteado` **sin pasar
  antes por `postulado`**, `_reached()` no la registraba (el desenlace no es una
  etapa) y la oferta **desaparecía por completo del embudo**. Ahora se infiere la
  etapa mínima al leer: rechazado/ghosteado ⇒ `postulado` (no te pueden rechazar ni
  ignorar una candidatura que nunca enviaste), retirada ⇒ `interesado`. Se hace en
  **lectura**, no escribiendo eventos falsos en el historial, y con `max()` para no
  pisar un recorrido más avanzado ya registrado.
- **`_reached()` REPRODUCE el historial, no hace un máximo ciego.** Segundo bug de
  uso: si marcabas «aceptada» por error y lo devolvías a «entrevista técnica», el
  embudo seguía mostrando una oferta aceptada, porque se tomaba el máximo de todo
  el historial. Ahora se recorren los eventos **en orden**: un evento de *etapa*
  fija la posición (**el último manda**, así que retroceder corrige de verdad — en
  un proceso real no se retrocede, si retrocedes es que te equivocaste) y un
  *desenlace* solo puede **subir** hasta su `IMPLIED_MIN`, nunca bajar lo recorrido.
  Cubierto por 9 casos de regresión (avance con rechazo tardío, corrección hacia
  atrás, desenlaces en seco, reapertura tras rechazo…).
- **Los desenlaces van todos en la ÚLTIMA columna** del Sankey. Colocarlos justo
  detrás de la etapa de la que caen los dejaba mezclados con una etapa activa en la
  misma columna (p. ej. «Rechazado» junto a «Contacto RR. HH.») y no se distinguía
  el embudo de sus salidas. Con la columna final se lee: izquierda = recorrido,
  derecha = dónde acabó cada una, y la **primera columna cuadra con la última**.
- **Sankey dibujado a mano en SVG** (`applications.html`), sin librerías externas:
  la Pi trabaja offline y el CSP de los artefactos bloquea CDNs. Nodos como barras
  por columna, cintas de Bézier con grosor proporcional. Las salidas se colocan una
  columna después de la última etapa de la que caen, para que ninguna cinta vaya
  hacia atrás.
- **Métricas**: tasa de respuesta/entrevista/oferta y `by_source()`, que responde a
  «¿qué bolsa me responde de verdad?» — es lo que permite decidir dónde invertir.
- **UI**: selector de estado en cada tarjeta de Empleos (AJAX, sin recargar) y
  página **Postulaciones** (`/applications`) con embudo, KPIs, tabla por fuente y
  listado filtrable (en curso / cerradas / todas).

**Extracto de la descripción (`jobs.description`).** Se guarda al ingerir con
`fetcher._excerpt()`: prefiere el campo `_desc` de la fuente (añadido a las 7 que
lo traen) y si no, usa `_text` quitándole el título; limpia HTML y recorta a
`DESC_MAX = 4000`. Resuelve la limitación de que el match usara **solo el título**:
ahora `analyze_fit` recibe la descripción y el **CV a medida la usa como respaldo**
si el usuario no pega nada. Las filas antiguas quedan vacías y se rellenan solas en
la siguiente búsqueda (no se hace scraping retroactivo de las URLs).

---

## 10. Despliegue (systemd) y planificador in-app

- **`jobhunter-web.service`** — servidor Flask, puerto 8080, arranca en boot,
  `Restart=on-failure`. **Aloja también el planificador** (hilo demonio
  `app._scheduler`, ver §10b).
- **`jobhunter-search.service`** — `oneshot`, lanza `run_search.sh` (queda para
  **corridas manuales**: `sudo systemctl start jobhunter-search.service`).
- **`jobhunter-search.timer`** — **DESACTIVADO** (`systemctl disable --now`). Antes
  disparaba la búsqueda a las 12:00 fijo; ahora la **hora (o varias) la define el
  usuario** desde Configuración y las ejecuta el planificador in-app. Las unidades
  siguen en `deploy/` como referencia; para volver al modelo systemd bastaría
  `systemctl enable --now jobhunter-search.timer` (pero entonces habría que vaciar
  `search_times` para no duplicar).

### 10b. Planificador in-app (§ nuevo)

El **web service corre 24/7**, así que aloja un **hilo demonio** (`app._scheduler`)
que cada ~20 s, una vez por minuto (hora local = America/Bogota):
- **Búsquedas:** si `HH:MM` está en `settings.search_times` (lista "HH:MM" separada por
  comas, por defecto `12:00`) y no se corrió ya ese minuto (`last_scheduled_run`),
  lanza `run_all()` en otro hilo (con `_search_lock` para no solaparse).
- **Resumen diario:** llama `notifier.maybe_send_digest()` (idempotente).

Configurable en **Configuración → ⏰ Programación de búsquedas** (`set_schedule`): UI
para **añadir/quitar varias horas**. Sin horas = búsqueda automática **off** (queda
"Buscar ahora"). Ventaja vs. systemd: **multi-hora y editable desde la web sin sudo**;
contra: no hay catch-up tipo `Persistent=true` (si la Pi está apagada a esa hora, ese
disparo se pierde — pero el web service está siempre arriba).

**Gotcha:** los **dos** servicios necesitan las API keys (el `search` usa
`RAPIDAPI_KEY` para LinkedIn; el `web` usa ambas para "Buscar ahora", la IA, **el
planificador y las notificaciones**). Ambos cargan las variables con
`EnvironmentFile=…/.env`.

Comandos útiles:
```bash
sudo systemctl status jobhunter-web.service        # estado del sitio + planificador
journalctl -u jobhunter-web.service -f | grep -E "scheduler|digest"  # ver disparos
sudo systemctl start jobhunter-search.service      # forzar búsqueda ahora (manual)
tail -f /home/pi/project/job-hunter/search.log     # log de búsquedas
sudo systemctl restart jobhunter-web.service       # tras cambios en app/templates
```

---

## 10c. Docker (para replicar en otros equipos)

El despliegue de referencia es **nativo con systemd** (la Pi). Además, en la raíz
hay un `Dockerfile` + `docker-compose.yml` **listos pero no usados aquí**, para
replicar el proyecto en cualquier equipo. Un **único contenedor** corre `run.py`:
web + planificador (hilo) + notificaciones. Decisiones y gotchas:

- **Base `python:3.13-slim`.** Se instala **`fonts-dejavu-core`** porque
  `cvpdf.py` referencia `/usr/share/fonts/truetype/dejavu/DejaVuSans*.ttf` (sin la
  fuente, el PDF del CV falla). `tzdata` para que el planificador respete `TZ`.
- **Solo se copia lo de runtime**: `jobhunter/`, `docs/` (lo sirven `/architecture`
  y `/workflow`) y `run.py`. `deploy/`, `scripts/`, `.venv/` y **los secretos** se
  excluyen vía `.dockerignore` (crítico: nunca hornear `.env`, `*.key` ni la BD).
- **Datos en el volumen `jobhunter-data` → `/app/data`.** Usuario no-root (`app`,
  uid 1000); con volumen con nombre Docker hereda el propietario, así que las
  escrituras (BD, `secret.key`, logs) funcionan sin ajustes de permisos.
- **Claves de IA opcionales**: por entorno (`${VAR:-}` en compose, sustituido
  desde un `.env` junto al compose) o cifradas desde la UI. `db._load_dotenv` usa
  `setdefault`, así que las variables de entorno del contenedor mandan.
- **Logs de systemd (journald) no existen en el contenedor**: `app._journal_lines`
  degrada con un mensaje controlado; las fuentes de fichero (app/búsquedas) sí van.
- **Healthcheck** contra `/api/unread` (ligero). `restart: unless-stopped`.
- **Verificado en la Pi** (build + smoke test): 9 rutas 200, PDF con acentos,
  esquema (11 tablas), TZ America/Bogota y persistencia tras reiniciar el contenedor.
- **Gotcha:** no migrar a gunicorn multi-worker — el planificador es un hilo
  demonio **in-process**; con varios workers habría varios planificadores. `run.py`
  (un proceso) es lo correcto, igual que en la Pi.

---

## 11. Secretos y `.env`

- Las claves (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `RAPIDAPI_KEY`) viven **solo**
  en `.env` (permisos 600, **gitignored**). `.env.example` es la plantilla
  versionada. Cada proveedor de IA usa su propia clave; si falta la del proveedor
  activo, la IA degrada con un mensaje que sugiere cambiar de proveedor.
- Carga: systemd con `EnvironmentFile` **y** `db.py` como fallback (`setdefault`,
  no pisa variables ya definidas) para ejecuciones manuales.
- **Claves de IA configurables desde la UI** (página **Configuración** → Proveedor
  de IA → sección **colapsable** "Claves de API"). El `.env` es **solo para desarrollo**
  (fallback); las claves que el usuario registra desde la web se guardan **cifradas
  en la BD** vía **`keystore.py`**:
  - `keystore.set_api_key(provider, value)` cifra con **Fernet** y guarda en
    `settings` (`apikey_anthropic`/`apikey_gemini`); value vacío → borra.
  - `keystore.get_api_key(provider)` = **BD (descifrada) → fallback `.env`**.
    `llm.py` (`_anthropic_key`/`_gemini_key`) lo usa; efecto inmediato sin reiniciar.
  - **Clave maestra Fernet en `secret.key`** (fichero aparte, permisos 600,
    gitignored por `*.key`), no en el `.env` ni en la BD → un volcado de la BD por
    sí solo **no revela** ninguna clave (solo ciphertext `gAAAA…`).
  - Seguridad UI: input `type=password` + `autocomplete=new-password`; la clave
    **nunca** se muestra completa (solo máscara `prefijo…4últimos`), no se registra
    ni se "flashea". Botones **con iconos** (💾 guardar / 🗑 borrar, borrar usa el
    modal in-page). Enlaces para obtener la clave (Anthropic Console / Google AI
    Studio). Dep nueva: `cryptography` (requirements.txt).
- **Si se mueve el proyecto de carpeta**, actualizar la ruta absoluta del
  `EnvironmentFile` en los dos drop-ins de systemd
  (`/etc/systemd/system/jobhunter-{web,search}.service.d/apikey.conf`).
- Las keys de Gemini/RapidAPI actuales son de **prueba**; rotar cuando toque.

---

## 12. Decisiones clave (y por qué)

- **Modo de ubicación `worldwide` por defecto** — como contractor desde Colombia
  no sirven empleos geo-locked a un estado/país. Se conservan solo los realmente
  abiertos o que incluyan LATAM/Americas/Colombia. Para ver US-remote, cambiar a
  modo `americas`.
- **Filtro por título, no por descripción** — evita falsos positivos y la basura
  del feed completo de Jobicy.
- **Ventana ≤ 3 días** es la del usuario; si un día no hay nada nuevo, es correcto
  (los feeds a veces publican con 4–6 días de retraso).
- **IA con proveedor seleccionable, Claude por defecto** — el usuario ahora tiene
  API key de Anthropic y prefiere Claude (Opus 4.8) para producción; Gemini queda
  como alternativa gratis, elegible desde la UI sin tocar código.
- **Paginación y ciertos spinners en cliente** — se pidió "frontend puro".
- **Compañías y "¿Encajo?" por AJAX** — spinner garantizado y sin recargar; los
  botones de búsqueda usan spinner síncrono (`class="busy"`).

---

## 13. Limitaciones y gotchas conocidos

- **Panel lateral (base.html).** Los iconos son **SVG de línea monocromos** (macro
  `ico()` en `base.html`), no emojis: los emojis traían color y pesos visuales
  dispares y hacían que el panel se sintiera cargado pese a tener pocos ítems. El
  activo se marca con **fondo tintado + icono en acento** (antes una barra dura).
  **Bloqueos es una subsección de Compañías** (`.side-sub`, indentada con guía
  vertical). La **documentación** (Arquitectura, Workflow, Repositorio) es una
  **fila compacta de iconos** con tooltip, sin etiqueta ni filas completas. Al
  contraer el panel, la subsección pierde la guía y la fila de docs se apila.
- **Filtro flotante de skills (Empleos).** Panel translúcido (`#skfx`, blur) que se
  abre con un botón fijo «⚗ Skills» y filtra **en cliente** las tarjetas ya
  cargadas por sus skills (`data-skills` en cada `.job-card`, canónicas en
  minúsculas). Chips de un clic con una lista **DevOps curada**
  (`skills.QUICK_DEVOPS`, 24) que muestran cuántos empleos tienen cada una, más un
  campo para **escribir cualquier skill** a mano. Modo **Cualquiera/Todas** (OR/AND).
  El match es por **substring** (así «kube» encuentra Kubernetes; a cambio, términos
  muy cortos pueden colar algún falso positivo). Estado (skills + modo) **persistido
  en localStorage**. El **paginador de Empleos se reescribió** para combinar filtro y
  paginación: pagina solo sobre las coincidencias y actualiza el contador; ya **no
  usa `jhPaginate`** (las demás páginas lo siguen usando).
- **Gotcha Jinja:** en el CSS del panel, `@media(...){#skfx-launch{…}}` disparaba
  «Missing end of comment tag» porque Jinja lee `{#` como inicio de comentario. Se
  evita con un espacio: `{ #skfx-launch`.
- **Zona de acciones de cada empleo (index.html).** Todos los controles (Ver
  oferta, seguimiento, ¿encajo?, carta, CV a medida) comparten **ancho (172px) y
  alto (34px)** vía `.job-actions`: antes convivían un `<a>`, un `<select>` y tres
  `<button>` con paddings propios y el borde izquierdo quedaba dentado. Jerarquía:
  **verde** = acción principal (Ver oferta), selector = seguimiento, y las tres
  herramientas de IA agrupadas bajo una etiqueta «Asistente IA» con separador. En
  móvil la columna se vuelve una fila que envuelve.
- **Indicador nuevo/visto clicable.** El badge NUEVO pasó a ser un botón
  (`.new-toggle`, un punto + etiqueta) que **alterna `jobs.is_new` por empleo** vía
  `POST /jobs/<id>/seen` (AJAX, como marcar un correo leído/no leído). El borde
  verde de la tarjeta (`.job-card.is-new`) se actualiza al vuelo. Sigue existiendo
  «Marcar todo como visto» (`/mark-seen`) para el lote.
- **El usuario edita la config en vivo desde la web** (ventana, keywords, modo).
  **No pisar sus ajustes** sin preguntar (ver memoria `jobhunter-live-settings`).
- **RapidAPI: cuotas y propagación.** El plan gratuito/BASIC tiene cuota mensual
  (LinkedIn 429 al agotarse). Al suscribir una API nueva, algunos endpoints
  responden antes que otros (JSearch: `/job-details` y `/estimated-salary`
  funcionaron mientras `/search` daba 404 "endpoint does not exist"). Las fuentes
  RapidAPI degradan a `[]` sin romper; se activan solas cuando la cuota/endpoint
  resuelven. Verificar en la página de la API en RapidAPI que el endpoint usado
  esté incluido en el plan suscrito.
- **Filtro de ubicación = blocklist de países/regiones**: es "whack-a-mole".
  LinkedIn devuelve nombres completos de ciudades/países del mundo; se ampliaron
  mucho las listas, pero puede colarse algún remoto de un país no listado. En
  modo `worldwide` esto rinde **pocos** resultados de LinkedIn (muy geo-etiquetado).
- **Del lado del EMPLEO seguimos usando solo el TÍTULO** (no guardamos la
  descripción). Del lado del CV la referencia ya es el **CV generado** completo
  (`reference_blob`). Para el **CV a medida** se sortea la limitación pidiendo al
  usuario que **pegue la descripción** de la oferta en el propio panel.
- **La lista de empleos no se auto-refresca**; hay una barra "empleos nuevos"
  (sondeo 45s) que avisa y recarga a un clic. El badge de notificaciones sí se
  actualiza solo (30s).
- **Los mapas (architecture/workflow) hay que regenerarlos a mano** cuando cambia
  el sistema (contando fuentes, tablas, etc.).

---

## 14. Roadmap / próximos pasos

- **Más fuentes de RapidAPI** (el camino ya está preparado con `_rapidapi_get`).
- ✅ **Notificaciones** por **email (SMTP, HTML estético)** y **Telegram**, en modo
  **inmediato** y/o **resumen diario** — **hecho** (`notifier.py`, página
  Configuración; ver §7b). Próximo posible: webhook genérico, adjuntar el CV a medida
  al email, o filtro por afinidad mínima para notificar solo los mejores matches.
- **Acceso desde fuera de la LAN** (Tailscale / Cloudflare Tunnel) si se quiere
  consultar desde el móvil con datos.
- **Guardar un extracto de la descripción** del empleo para mejorar el match del CV.
- **Auto-refresh opcional** de la lista de Empleos.
- Publicar el repo en GitHub (hoy es local).

---

## 15. Cómo desarrollar / probar

```bash
cd /home/pi/project/job-hunter
# venv
source .venv/bin/activate   # o usar ./.venv/bin/python directamente

# probar el fetcher (carga .env por db.py)
./.venv/bin/python fetcher.py                 # corre todas las búsquedas activas
./.venv/bin/python fetcher.py "Cloud Engineer" # una query puntual

# probar módulos de IA (necesitan GEMINI_API_KEY del .env)
./.venv/bin/python cv.py "texto del CV..."
./.venv/bin/python reviews.py "GitLab"

# tras editar app.py/templates:
sudo systemctl restart jobhunter-web.service
```

**Convención:** al terminar un cambio no trivial, verificar en la app real
(no solo con curl), y actualizar los mapas + este documento si el sistema cambió.
