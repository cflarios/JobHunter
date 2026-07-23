# JobHunter — Contexto del proyecto (documentación interna)

> Documento de rumbo. El **README.md** es operativo (cómo se usa/despliega); este
> archivo guarda el **por qué**, las **decisiones**, los **gotchas** y el **roadmap**
> para no perder el hilo entre sesiones. Última actualización: 2026-07-22.

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
├── app.py              # Servidor Flask: rutas, filtro Markdown, favicon, mapas
├── db.py               # Esquema SQLite (8 tablas) + carga de .env (fallback)
├── fetcher.py          # 11 fuentes, filtros (título/ubicación/fecha), orquestación
├── llm.py              # Capa de proveedor de IA: enruta a Claude o Gemini (ai_provider)
├── reviews.py          # Resumen de reputación de empresas (IA + búsqueda web/grounding)
├── cv.py               # CV + IA: analizar, match, ¿encajo?, carta, mejorar, generar CV
├── cvpdf.py            # Renderiza el CV nuevo a PDF (fpdf2 + DejaVu, ≤2 págs)
├── run_search.sh       # Wrapper del cron (→ fetcher.py, log en search.log)
├── templates/          # 8 vistas Jinja2 (base, index, searches, notifications,
│                       #   companies, cv, _review, _fitblock)
├── deploy/             # Copia de referencia de las unidades systemd (sin secretos)
├── architecture.json   # Modelo estructurado del sistema (fuente de verdad)
├── architecture.html   # Mapa visual autocontenido (embebe el JSON)
├── workflow.html       # Workflow interactivo tipo n8n (nodos + aristas)
├── .env                # SECRETOS (gitignored, 600) — no se versiona
├── .env.example        # Plantilla versionada
├── README.md           # Operativo
└── CONTEXT.md          # Este documento
```

También hay **memoria de Claude** en
`~/.claude/projects/-home-pi-project/memory/` (`jobhunter-project.md`,
`jobhunter-live-settings.md`) — se cargan como contexto en cada sesión.

---

## 4. Flujo de datos (resumen)

```
Timer 12:00 (o Usuario "Buscar ahora")
   → run_search.sh → fetcher.run_all()
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
(comparación case-insensitive por `name.strip().lower()`). Se gestiona desde la
página Compañías (botón "🚫 Bloquear" por tarjeta; sección de bloqueadas para
desbloquear). Al bloquear, `/companies/block` también **borra** los empleos ya
guardados de esa empresa para que desaparezcan del listado al instante.

Configurable desde la página **Búsquedas** (por búsqueda) y con ajustes globales.

---

## 7. IA con proveedor seleccionable (Claude / Gemini)

**Selector en la UI** (página Búsquedas → "Proveedor de IA"), guardado en
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
  por eso se resume desde la web. Resuelve el nombre canónico para el enlace de
  Glassdoor y cachea en `company_reviews`. `_parse()` limpia artefactos de cita de
  ambos proveedores (`[cite:...]` de Gemini y `<cite index=...>` de Claude) y la
  narración que Claude intercala antes del resumen (corta desde la línea `EMPRESA:`).
- **`cv.py`** — funciones inspiradas en reaver.ink:
  - `analyze_cv` (PDF inline o texto) → perfil (rol, seniority, años, skills, keywords).
  - `match_jobs` → afinidad 0–100 por empleo (badge y orden en Empleos).
  - `analyze_fit` → "¿Encajo aquí?" por oferta (coincidencias, gaps, qué resaltar).
  - `cover_letter` → carta de presentación a medida.
  - `improve_cv` → feedback Harvard/ATS + reescritura.
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

## 8. Base de datos (SQLite `jobs.db`, WAL) — 8 tablas

| Tabla | Para qué |
|---|---|
| `searches` | Búsquedas: query, title_keywords, max_age_days, active |
| `blocked_companies` | Blacklist: empresas que no deben aparecer (name PRIMARY KEY COLLATE NOCASE) |
| `jobs` | Empleos: title, company, url (unique), source, salary, location, posted_ts, is_new |
| `notifications` | Avisos de hallazgos (read) |
| `settings` | Config global (location_mode, max_age_days, last_run…) |
| `company_reviews` | Caché de resúmenes de empresa + resolved_name |
| `profile` | Perfil del CV (1 fila): cv_text, role, skills, summary, suggested_keywords, feedback, rewrite, generated_cv (JSON del CV nuevo) |
| `job_matches` | Afinidad por empleo: score, reason, fit_detail |

---

## 9. Rutas HTTP principales

Empleos `/` · Buscar ahora `/run` · Búsquedas `/searches` · Notificaciones
`/notifications` · Compañías `/companies` (+ `/companies/summary`,
`/companies/glassdoor-name`, `/companies/block`, `/companies/unblock`) ·
**Mi CV** `/cv` (+ `/cv/analyze`, `/cv/match`, `/cv/improve`, `/cv/apply-keywords`,
`/cv/build`, `/cv/download?lang=`) · por oferta `/jobs/<id>/fit` y `/cover` ·
mapas `/architecture` `/architecture.json` `/workflow` · polling `/api/unread`
`/api/jobs-status`.

---

## 10. Despliegue (systemd)

- **`jobhunter-web.service`** — servidor Flask, puerto 8080, arranca en boot,
  `Restart=on-failure`.
- **`jobhunter-search.service`** — `oneshot`, lanza `run_search.sh`.
- **`jobhunter-search.timer`** — `OnCalendar=*-*-* 12:00:00` (America/Bogota),
  `Persistent=true` (recupera la corrida si la Pi estuvo apagada al mediodía).

**Gotcha:** los **dos** servicios necesitan las API keys (el `search` usa
`RAPIDAPI_KEY` para LinkedIn; el `web` usa ambas para "Buscar ahora" y la IA).
Ambos cargan las variables con `EnvironmentFile=…/.env`.

Comandos útiles:
```bash
sudo systemctl status jobhunter-web.service        # estado del sitio
systemctl list-timers jobhunter-search.timer       # próxima corrida
sudo systemctl start jobhunter-search.service      # forzar búsqueda ahora
tail -f /home/pi/project/job-hunter/search.log     # log de búsquedas
sudo systemctl restart jobhunter-web.service       # tras cambios en app/templates
```

---

## 11. Secretos y `.env`

- Las claves (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `RAPIDAPI_KEY`) viven **solo**
  en `.env` (permisos 600, **gitignored**). `.env.example` es la plantilla
  versionada. Cada proveedor de IA usa su propia clave; si falta la del proveedor
  activo, la IA degrada con un mensaje que sugiere cambiar de proveedor.
- Carga: systemd con `EnvironmentFile` **y** `db.py` como fallback (`setdefault`,
  no pisa variables ya definidas) para ejecuciones manuales.
- **Claves de IA configurables desde la UI** (página Búsquedas → Proveedor de IA →
  sección **colapsable** "Claves de API"). El `.env` es **solo para desarrollo**
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
- **El match del CV usa solo el TÍTULO del empleo** (no guardamos la descripción).
  Es suficiente para ranking, pero mejorable si algún día guardamos un extracto.
- **La lista de empleos no se auto-refresca**; hay una barra "empleos nuevos"
  (sondeo 45s) que avisa y recarga a un clic. El badge de notificaciones sí se
  actualiza solo (30s).
- **Los mapas (architecture/workflow) hay que regenerarlos a mano** cuando cambia
  el sistema (contando fuentes, tablas, etc.).

---

## 14. Roadmap / próximos pasos

- **Más fuentes de RapidAPI** (el camino ya está preparado con `_rapidapi_get`).
- **Notificaciones por email** (SMTP con `cristianferlariosm@gmail.com`) cuando
  aparezcan empleos nuevos — hoy son in-app.
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
