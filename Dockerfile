# JobHunter — imagen autocontenida.
#
# Un solo contenedor sirve la web, el planificador (hilo de fondo) y las
# notificaciones — igual que `run.py` en la Pi. Los datos (BD, clave maestra de
# cifrado y logs) viven en /app/data, pensado para ir en un volumen.
FROM python:3.13-slim

# fonts-dejavu-core: la fuente Unicode que usa el PDF del CV (jobhunter/cvpdf.py).
# tzdata: para que el planificador respete la zona horaria (TZ).
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Bogota

WORKDIR /app

# Dependencias primero, para aprovechar la caché de capas de Docker.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Solo lo que necesita la app en runtime. docs/ va incluido: lo sirven las
# rutas /architecture y /workflow.
COPY jobhunter/ ./jobhunter/
COPY docs/ ./docs/
COPY run.py ./

# Usuario sin privilegios y directorio de datos (montado como volumen). Con un
# volumen con nombre, Docker hereda este propietario, así que las escrituras
# (BD, secret.key, logs) funcionan sin ajustes de permisos.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data \
    && chown -R app:app /app
USER app

EXPOSE 8080
VOLUME ["/app/data"]

# El endpoint /api/unread es ligero y siempre responde JSON si la app está viva.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/unread', timeout=5)" || exit 1

CMD ["python", "run.py"]
