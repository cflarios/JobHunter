# JobHunter

Buscador personal de empleos remotos, auto-alojado en la Raspberry Pi.
Enfocado inicialmente en **DevOps Engineer** (remoto / contractor), ampliable a cualquier rol.

## Acceso
- Desde la Pi: http://localhost:8080
- Desde la red local: http://192.168.1.11:8080

## Páginas
- **Empleos** — lista lo encontrado con fuente, salario y fecha de publicación; filtros por texto, búsqueda, fuente y ventana de días; botón "Buscar ahora".
- **Compañías** — empleadores reales detrás de las ofertas (no las bolsas); enlace a Glassdoor y resumen de opiniones generado por Claude + búsqueda web (ver abajo).
- **Búsquedas** — añadir/pausar/eliminar términos; palabras clave de título, filtro de ubicación y ventana por búsqueda.
- **Notificaciones** — historial de hallazgos; badge de no leídas se actualiza cada 30 s.

## Resumen de opiniones (Glassdoor) — usa Gemini (Google AI Studio)
El botón "Resumen de opiniones" en la página **Compañías** usa la API de **Gemini**
(`gemini-2.5-flash`) con *grounding* de Google Search para resumir la reputación de
la empresa (calificación de Glassdoor/Indeed, pros, contras, veredicto). Se cachea
en la BD. Glassdoor no tiene API pública gratuita ni permite scraping, por eso se
resume desde fuentes públicas vía Gemini.

La API key se guarda como variable de entorno del servicio (no en el código), en un
override de systemd propiedad de root:

```bash
sudo mkdir -p /etc/systemd/system/jobhunter-web.service.d
printf '[Service]\nEnvironment=GEMINI_API_KEY=TU-KEY\n' | \
  sudo tee /etc/systemd/system/jobhunter-web.service.d/apikey.conf
sudo chmod 600 /etc/systemd/system/jobhunter-web.service.d/apikey.conf
sudo systemctl daemon-reload && sudo systemctl restart jobhunter-web.service
```

Sin la key, el enlace a Glassdoor funciona igual; solo el resumen automático queda
deshabilitado (con un aviso). Obtén una key gratis en https://aistudio.google.com/apikey
Para cambiar de modelo, edita `MODEL` en `reviews.py`.

## Fuentes — 12
Sin API key (10): Remotive · RemoteOK · Jobicy · Himalayas · WeWorkRemotely
(DevOps + Programming) · Arbeitnow · The Muse · Working Nomads · Landing.jobs
(con salario) · Get on Board (LATAM, con salario en USD).

Vía RapidAPI (con `RAPIDAPI_KEY`):
- **LinkedIn** (`linkedin-job-search-api`, endpoint `active-jb`) — salario,
  modalidad y skills. Muy geo-etiquetado: en modo "mundial" rinde poco; usar modo
  "América" para US-remote. El plan BASIC tiene **cuota mensual** limitada (al
  agotarse, 429 → la fuente devuelve vacío sin romper).
- **JSearch** (`jsearch.p.rapidapi.com`, endpoint `search`) — agrega Google for
  Jobs (LinkedIn, Indeed, Glassdoor, ZipRecruiter…). Requiere que la suscripción
  exponga el endpoint `/search` (los demás endpoints pueden estar activos antes;
  si `/search` da 404, esperar la propagación o revisar el plan).

### RapidAPI (para añadir más APIs de esta plataforma)
La key se guarda como `RAPIDAPI_KEY` en el override de systemd de **ambos**
servicios (web y search), fuera del repo. En `fetcher.py` hay un helper genérico
`_rapidapi_get(host, path, params)` que pone las cabeceras `x-rapidapi-host/key`.
Añadir otra fuente de RapidAPI = escribir `fetch_x(query)` que llame a
`_rapidapi_get(...)` y agregarla a `SOURCES`. Si falta la key, esas fuentes se
omiten en silencio.

## Mi CV + IA (Gemini) — pestaña "Mi CV"
Sube tu CV (PDF o texto pegado) y Gemini:
- extrae tu **perfil** (rol, seniority, años, skills) y sugiere palabras clave de título;
- puntúa cada empleo por **afinidad 0–100** (badge y orden en la pestaña Empleos);
- **"¿Encajo aquí?"** por oferta: coincidencias, gaps y qué resaltar;
- **carta de presentación** a medida por oferta;
- **mejora tu CV** (estilo Harvard/ATS): recomendaciones + resumen y logros reescritos.

El CV se guarda **solo en la Pi** (tablas `profile` y `job_matches`); "Borrar perfil"
lo elimina. Usa la misma `GEMINI_API_KEY` que los resúmenes de empresas.

Para añadir una fuente: crear una función `fetch_x(query)` en `fetcher.py` que
devuelva dicts con las claves title/company/url/source/salary/location/posted_ts,
y agregarla a la lista `SOURCES`. Los filtros de título, ubicación y fecha se
aplican por igual a todas.

## Automatización (systemd)
- `jobhunter-web.service` — servidor web (arranca en el boot, reinicio automático).
- `jobhunter-search.timer` — corre la búsqueda **todos los días a las 12:00** (America/Bogota).
  `Persistent=true`: si la Pi estuvo apagada al mediodía, recupera la corrida al encender.

### Comandos útiles
```bash
sudo systemctl status jobhunter-web.service       # estado del sitio
systemctl list-timers jobhunter-search.timer      # próxima corrida
sudo systemctl start jobhunter-search.service     # forzar búsqueda ahora
tail -f /home/pi/project/job-hunter/search.log     # log de las búsquedas
```

## Filtros de resultados
- Sólo ofertas publicadas dentro de la ventana configurada (3 días por defecto).
- Coincidencia por término en título/descripción/etiquetas.
- Deduplicado por URL; sólo empleos nuevos generan notificación.

## Datos
SQLite en `jobs.db` (tablas: `searches`, `jobs`, `notifications`, `settings`).
