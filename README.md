# JobHunter

Buscador personal de empleos remotos, auto-alojado en una Raspberry Pi, con
emparejamiento de CV por IA. Enfocado en **DevOps / SRE** remoto (contractor desde
Colombia), ampliable a cualquier rol.

> Este README es **operativo** (cómo se usa y se despliega). El **porqué**, las
> decisiones y los gotchas están en [`docs/CONTEXT.md`](docs/CONTEXT.md).

## Acceso
- Desde la Pi: http://localhost:8080
- Desde la LAN: http://192.168.1.11:8080

## Estructura

```
job-hunter/
├── jobhunter/          # el paquete de la aplicación
│   ├── app.py          # servidor Flask: rutas, filtros Jinja, planificador
│   ├── paths.py        # rutas del proyecto resueltas en un solo sitio
│   ├── db.py           # esquema SQLite (11 tablas) + carga del .env
│   ├── fetcher.py      # 12 fuentes de empleo, filtros y orquestación
│   ├── skills.py       # extracción de skills técnicas del texto
│   ├── llm.py          # router de IA: Claude o Gemini
│   ├── cv.py           # CV + IA: perfil, afinidad, ¿encajo?, carta, CV a medida
│   ├── cvpdf.py        # renderiza los CVs a PDF (fpdf2)
│   ├── reviews.py      # resumen de reputación de empresas
│   ├── notifier.py     # notificaciones (email SMTP + Telegram)
│   ├── tracker.py      # seguimiento de postulaciones: estados, embudo y métricas
│   ├── keystore.py     # secretos cifrados (Fernet)
│   ├── applog.py       # log central rotativo
│   ├── templates/      # 13 vistas Jinja2
│   └── static/
├── data/               # runtime, NO versionado: jobs.db, secret.key, logs/
├── docs/               # CONTEXT.md + mapas (architecture.*, workflow.html)
├── deploy/             # unidades systemd (referencia, sin secretos)
├── scripts/run_search.sh
├── run.py              # punto de entrada
├── Dockerfile          # imagen autocontenida (opcional, para replicar)
├── docker-compose.yml  # un contenedor: web + planificador + notificaciones
├── requirements.txt
└── .env                # secretos de desarrollo (NO versionado)
```

Todo lo que se genera en runtime vive en `data/`; el código nunca escribe dentro
del paquete. Las rutas se resuelven en `jobhunter/paths.py`.

## Puesta en marcha

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.example .env        # rellena las claves que uses
./.venv/bin/python run.py   # http://localhost:8080
```

### Con Docker (replicar en cualquier equipo)

La instalación de referencia corre nativa en la Pi (systemd, arriba). Para
**replicar el proyecto en otro dispositivo** hay un `Dockerfile` y un
`docker-compose.yml` listos: un solo contenedor sirve la web, el planificador y
las notificaciones.

```bash
docker compose up -d          # construye y arranca en http://localhost:8080
docker compose logs -f        # salida en vivo
docker compose down           # detener (los datos persisten en el volumen)
```

- Los datos (BD, clave maestra de cifrado y logs) viven en el volumen
  `jobhunter-data`, así que sobreviven a recrear el contenedor.
- Las **claves de IA** son opcionales al arrancar: se pueden registrar cifradas
  desde la UI (Configuración) o pasarlas por entorno creando un `.env` junto al
  compose (`ANTHROPIC_API_KEY=…`, `GEMINI_API_KEY=…`, `RAPIDAPI_KEY=…`).
- Cambia el puerto con `PORT=9000 docker compose up -d` y la zona horaria del
  planificador con `TZ=Europe/Madrid` (por defecto `America/Bogota`).
- La página **Logs** muestra las fuentes de la app; las de systemd (journald) no
  existen dentro del contenedor y aparecen como no disponibles (sin romper).

## Páginas
- **Empleos** — lo encontrado con fuente, salario, fecha, skills y un extracto de
  la **descripción**; filtros por texto/búsqueda/fuente/ventana; % de afinidad con
  tu CV; por oferta: *¿encajo aquí?*, *carta*, **CV a medida** (ATS) y el selector
  de **estado de tu postulación**.
- **Compañías** — empleadores reales (no las bolsas). La píldora de ofertas
  despliega sus vacantes con enlace directo a la postulación. Glassdoor y resumen
  de reputación por IA. Se pueden **bloquear** (subsección *Bloqueos*).
- **Postulaciones** — tu embudo real en un **diagrama Sankey**: cuántas enviaste,
  cuántas llegaron a entrevista técnica, cuáles acabaron en oferta, en rechazo o
  sin respuesta. Con tasas de respuesta y **qué bolsa te contesta de verdad**.
- **Mi CV** — sube el CV y la IA extrae tu perfil, puntúa empleos, mejora el
  currículum y **genera uno nuevo en PDF** (es la referencia del sistema).
- **Búsquedas** — términos, palabras clave de título, ubicación, ventana, RapidAPI.
- **Notificaciones** — historial in-app (badge cada 30 s).
- **Logs** — consola en vivo del comportamiento de la app.
- **Configuración** — proveedor de IA + claves, notificaciones y horarios.

## Configuración (página ⚙️)

**Claves de API** — las de IA (Claude/Gemini) y la de **RapidAPI** se registran
desde la web y se guardan **cifradas** (Fernet) en la BD; el `.env` queda como
fallback. La clave maestra vive en `data/secret.key` (600, fuera del repo).

**Proveedor de IA** — por defecto **Claude** (`claude-opus-4-8`); **Gemini**
(`gemini-2.5-flash`) como alternativa gratis.

**Notificaciones** — avisos de empleos nuevos por **email (SMTP)** y/o
**Telegram**, en modo **inmediato** y/o **resumen diario** a una hora fija. Para
Gmail hace falta una *contraseña de aplicación*. Botón de envío de prueba.

**Programación de búsquedas** — una o varias horas al día (hora de Colombia). Lo
ejecuta un planificador dentro de la app web, no un timer de systemd.

## Fuentes — 12

Sin API key (10): Remotive · RemoteOK · Jobicy · Himalayas · WeWorkRemotely
(DevOps + Programming) · Arbeitnow · The Muse · Working Nomads · Landing.jobs ·
Get on Board (LATAM, salario en USD).

Vía RapidAPI, **apagadas por defecto** por su cuota mensual; se activan con la
casilla *Fuentes RapidAPI* en Búsquedas: **LinkedIn** y **JSearch** (Google for
Jobs). La clave se registra desde **Configuración → 🔑 Clave de RapidAPI** (se
guarda cifrada, efecto inmediato) o, como alternativa, en `RAPIDAPI_KEY` del
`.env`. Sin clave, esas dos fuentes se omiten en silencio.

**Añadir una fuente:** escribe `fetch_x(query)` en `jobhunter/fetcher.py` que
devuelva dicts con `title/company/url/source/salary/location/posted_ts` y súmala a
`SOURCES` (para RapidAPI, usa el helper `_rapidapi_get()` y añádela también a
`RAPIDAPI_SOURCES`). Los filtros se aplican por igual a todas.

## Seguimiento de postulaciones

Marca el estado de cada oferta desde su tarjeta en **Empleos** (se guarda sin
recargar). Las etapas son **interesado → postulado → contacto RR. HH. → entrevista
técnica → entrevista final → oferta → aceptada**, y desde cualquiera de ellas
puedes cerrar con **rechazado**, **sin respuesta** o **me retiré**.

En **Postulaciones** verás el embudo en un Sankey (dibujado sin librerías externas),
los KPIs y una tabla de rendimiento por bolsa. Se guarda el **historial completo**
de transiciones, no solo el estado actual: así el embudo sabe que una candidatura
rechazada tras la entrevista técnica sí llegó a esa etapa. Si te equivocas al
marcar, basta con volver atrás: la última etapa marcada es la que manda.

## Filtros
- El **título** debe contener alguna palabra clave del rol.
- **Ubicación** según el modo (mundial / América / sin filtro).
- Publicado dentro de la **ventana** configurada (3 días por defecto).
- **Dedup** por URL; solo los empleos nuevos generan notificación.
- Empresas en la **blacklist** se descartan antes que nada.

## Automatización (systemd)
- `jobhunter-web.service` — servidor web + planificador; arranca en el boot,
  `Restart=on-failure`.
- `jobhunter-search.service` — `oneshot` para **corridas manuales**.
- `jobhunter-search.timer` — **desactivado**: lo sustituye el planificador in-app,
  cuyo horario se configura desde la web.

```bash
sudo systemctl status jobhunter-web.service          # estado del sitio
sudo systemctl restart jobhunter-web.service         # tras cambiar código
sudo systemctl start jobhunter-search.service        # forzar búsqueda ahora
journalctl -u jobhunter-web.service -f               # salida en vivo
tail -f data/search.log                              # log de búsquedas
```

## Desarrollo

```bash
./.venv/bin/python run.py                     # app completa
./.venv/bin/python -m jobhunter.fetcher       # una corrida de búsqueda
./.venv/bin/python -m jobhunter.fetcher "Cloud Engineer"
./.venv/bin/python -m jobhunter.notifier      # envía una notificación de prueba
./.venv/bin/python -m jobhunter.db            # crea/actualiza el esquema
```

## Datos

SQLite en `data/jobs.db` (WAL), 11 tablas: `searches`, `jobs`, `notifications`,
`settings`, `blocked_companies`, `company_reviews`, `profile`, `job_matches`,
`tailored_cvs`, `applications` (estado actual de cada postulación) y
`application_events` (historial de transiciones, del que sale el embudo).
El CV se guarda **solo en la Pi**; "Borrar perfil" lo elimina.
