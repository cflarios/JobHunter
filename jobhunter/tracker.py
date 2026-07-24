"""Seguimiento de postulaciones: estados, embudo y métricas.

La app encontraba y preparaba ofertas, pero no recordaba qué habías hecho con
ellas. Aquí vive esa memoria: en qué punto está cada postulación, cómo llegó ahí
y qué dice el conjunto (tasa de respuesta, de entrevista, de oferta).

**Por qué hay historial y no solo un estado.** Si una postulación acaba en
`rechazado`, el estado actual no cuenta que pasó por entrevista técnica. Sin ese
recorrido no se puede dibujar el embudo. Por eso cada cambio escribe una fila en
`application_events` y el Sankey se construye contando transiciones reales.
"""
from jobhunter.db import get_db

# Etapas del embudo, en orden. `key` es lo que se guarda en la BD.
STAGES = [
    {"key": "interesado", "label": "Interesado", "icon": "☆", "color": "#8595bd"},
    {"key": "postulado",  "label": "Postulado",  "icon": "➤", "color": "#4f8cff"},
    {"key": "screening",  "label": "Contacto RR. HH.", "icon": "☏", "color": "#7c8bff"},
    {"key": "tecnica",    "label": "Entrevista técnica", "icon": "⌨", "color": "#a78bfa"},
    {"key": "final",      "label": "Entrevista final", "icon": "★", "color": "#c084fc"},
    {"key": "oferta",     "label": "Oferta",     "icon": "✦", "color": "#22c55e"},
    {"key": "aceptada",   "label": "Aceptada",   "icon": "✓", "color": "#16a34a"},
]
# Salidas: pueden ocurrir desde cualquier etapa activa.
OUTCOMES = [
    {"key": "rechazado", "label": "Rechazado", "icon": "✕", "color": "#ef4444"},
    {"key": "ghosteado", "label": "Sin respuesta", "icon": "…", "color": "#f59e0b"},
    {"key": "retirada",  "label": "Me retiré",  "icon": "↩", "color": "#8595bd"},
]

STAGE_KEYS = [s["key"] for s in STAGES]
OUTCOME_KEYS = [o["key"] for o in OUTCOMES]
ALL_STATUSES = STAGE_KEYS + OUTCOME_KEYS
META = {s["key"]: s for s in STAGES + OUTCOMES}

# Desde `postulado` en adelante cuenta como postulación real (para las métricas).
APPLIED_FROM = STAGE_KEYS.index("postulado")


def is_valid(status):
    return status in META


def stage_index(status):
    """Posición en el embudo, o None si es una salida."""
    return STAGE_KEYS.index(status) if status in STAGE_KEYS else None


# --------------------------------------------------------------------------- #
# Escritura                                                                   #
# --------------------------------------------------------------------------- #
def set_status(job_id, status, note=None):
    """Fija el estado de una postulación y registra la transición.

    Devuelve (ok, mensaje). No hace nada si el estado no cambia, para no llenar
    el historial de eventos repetidos.
    """
    if not is_valid(status):
        return False, f"Estado desconocido: {status}"
    con = get_db()
    row = con.execute("SELECT status FROM applications WHERE job_id=?", (job_id,)).fetchone()
    prev = row["status"] if row else None
    if prev == status:
        con.close()
        return True, "Sin cambios."
    # `applied_at` se sella la primera vez que entra en el embudo real.
    applied = "datetime('now','localtime')" if stage_index(status) is not None and \
        stage_index(status) >= APPLIED_FROM else "NULL"
    closed = "datetime('now','localtime')" if status in OUTCOME_KEYS or \
        status == "aceptada" else "NULL"
    con.execute(
        f"""INSERT INTO applications(job_id,status,applied_at,closed_at,notes,updated_at)
            VALUES(?,?,{applied},{closed},?,datetime('now','localtime'))
            ON CONFLICT(job_id) DO UPDATE SET
              status=excluded.status,
              applied_at=COALESCE(applications.applied_at,excluded.applied_at),
              closed_at=excluded.closed_at,
              notes=COALESCE(excluded.notes,applications.notes),
              updated_at=excluded.updated_at""",
        (job_id, status, note))
    con.execute(
        "INSERT INTO application_events(job_id,from_status,to_status,note) VALUES(?,?,?,?)",
        (job_id, prev, status, note))
    con.commit()
    con.close()
    return True, f"Estado: {META[status]['label']}."


def set_note(job_id, note):
    con = get_db()
    con.execute(
        """INSERT INTO applications(job_id,status,notes,updated_at)
           VALUES(?,'interesado',?,datetime('now','localtime'))
           ON CONFLICT(job_id) DO UPDATE SET notes=excluded.notes,
             updated_at=excluded.updated_at""", (job_id, note))
    con.commit()
    con.close()


def remove(job_id):
    """Saca la oferta del seguimiento (borra estado e historial)."""
    con = get_db()
    con.execute("DELETE FROM application_events WHERE job_id=?", (job_id,))
    con.execute("DELETE FROM applications WHERE job_id=?", (job_id,))
    con.commit()
    con.close()


# --------------------------------------------------------------------------- #
# Lectura                                                                     #
# --------------------------------------------------------------------------- #
def statuses_by_job():
    """{job_id: status} para pintar el control en la lista de empleos."""
    con = get_db()
    out = {r["job_id"]: r["status"] for r in
           con.execute("SELECT job_id, status FROM applications")}
    con.close()
    return out


def applications(status=None):
    """Postulaciones con los datos del empleo, más recientes primero."""
    con = get_db()
    sql = """SELECT a.*, j.title, j.company, j.url, j.source, j.salary, j.location,
                    j.posted_ts, m.score AS match_score
             FROM applications a
             JOIN jobs j ON j.id = a.job_id
             LEFT JOIN job_matches m ON m.job_id = a.job_id"""
    params = []
    if status in ("activas", "cerradas"):
        op = "NOT IN" if status == "activas" else "IN"
        sql += f" WHERE a.status {op} ({','.join('?' * len(OUTCOME_KEYS))})"
        params = OUTCOME_KEYS
    elif status and is_valid(status):
        sql += " WHERE a.status = ?"
        params = [status]
    sql += " ORDER BY a.updated_at DESC, a.job_id DESC"
    rows = [dict(r) for r in con.execute(sql, params).fetchall()]
    con.close()
    for r in rows:
        r["meta"] = META.get(r["status"], META["interesado"])
    return rows


# Etapa MÍNIMA que un desenlace da por supuesta. Si marcas «rechazado» o «sin
# respuesta», es porque llegaste a postular: no te pueden rechazar ni ignorar una
# candidatura que nunca enviaste. «Me retiré», en cambio, puede pasar estando solo
# interesado. Sin esto, marcar el desenlace directamente (sin pasar antes por
# «postulado») hacía que la oferta desapareciera del embudo.
IMPLIED_MIN = {
    "rechazado": "postulado",
    "ghosteado": "postulado",
    "retirada": "interesado",
}


def _reached(con):
    """Etapa alcanzada por cada postulación, **reproduciendo** su historial.

    No vale con quedarse con el estado actual: una postulación rechazada tras la
    entrevista técnica sí «alcanzó» la técnica y debe contar como tal en el embudo.
    Pero tampoco vale un máximo ciego sobre todo el historial, porque entonces
    **corregir un error no tendría efecto**: si marcas «aceptada» por equivocación y
    lo devuelves a «entrevista técnica», el embudo seguiría enseñando una oferta
    aceptada para siempre.

    Por eso se recorren los eventos **en orden** con dos reglas:
      · un evento de ETAPA fija la posición — el último manda, así que un cambio
        hacia atrás corrige de verdad (en un proceso real no se retrocede: si
        retrocedes es que te equivocaste);
      · un DESENLACE solo puede *subir* hasta el mínimo que implica (IMPLIED_MIN),
        nunca bajar lo ya recorrido.
    """
    hist = {}
    for r in con.execute("SELECT job_id, to_status FROM application_events ORDER BY id"):
        hist.setdefault(r["job_id"], []).append(r["to_status"])
    # Filas sin historial (datos anteriores a los eventos): sirve el estado actual.
    for r in con.execute("SELECT job_id, status FROM applications"):
        hist.setdefault(r["job_id"], [r["status"]])

    reached = {}
    for job_id, steps in hist.items():
        top = -1
        for st in steps:
            i = stage_index(st)
            if i is not None:
                top = i                      # la última etapa marcada manda
            else:
                j = stage_index(IMPLIED_MIN.get(st, ""))
                if j is not None:
                    top = max(top, j)        # un desenlace solo sube el mínimo
        if top >= 0:
            reached[job_id] = top
    return reached


def funnel():
    """Datos del embudo para el Sankey.

    Devuelve {nodes, links, totals}. Cada etapa i enlaza con la i+1 (los que
    avanzaron) y con las salidas de los que se quedaron ahí. El ancho de cada
    flujo es el número de postulaciones.
    """
    con = get_db()
    reached = _reached(con)
    final_status = {r["job_id"]: r["status"] for r in
                    con.execute("SELECT job_id, status FROM applications")}
    con.close()

    # Cuántas alcanzaron cada etapa (acumulado: si llegó a 'final', pasó por todas).
    stage_count = [0] * len(STAGES)
    for jid, top in reached.items():
        for i in range(top + 1):
            stage_count[i] += 1

    # Salidas: a qué etapa llegó cada postulación que acabó en un estado terminal.
    drops = {}       # (etapa_index, outcome) -> n
    for jid, st in final_status.items():
        if st in OUTCOME_KEYS:
            top = reached.get(jid)
            if top is None:
                continue
            drops[(top, st)] = drops.get((top, st), 0) + 1

    # Esperando: postulaciones cuyo estado ACTUAL es esa etapa y aún no han
    # avanzado ni cerrado. Sin este nodo el Sankey «perdería» flujo en cada etapa
    # (llegaron 8, salen 7) y parecería un error de cuadre.
    waiting = {}
    last = len(STAGES) - 1
    for jid, st in final_status.items():
        i = stage_index(st)
        if i is not None and i != last:      # 'aceptada' es final feliz, no espera
            waiting[i] = waiting.get(i, 0) + 1

    nodes, index = [], {}
    for i, s in enumerate(STAGES):
        if stage_count[i]:
            index[("stage", i)] = len(nodes)
            nodes.append({"id": f"stage-{s['key']}", "label": s["label"],
                          "value": stage_count[i], "color": s["color"], "col": i})
    for o in OUTCOMES:
        tot = sum(v for (st, k), v in drops.items() if k == o["key"])
        if tot:
            index[("out", o["key"])] = len(nodes)
            nodes.append({"id": f"out-{o['key']}", "label": o["label"],
                          "value": tot, "color": o["color"], "col": None})
    if waiting:
        index[("out", "_waiting")] = len(nodes)
        nodes.append({"id": "out-waiting", "label": "En curso",
                      "value": sum(waiting.values()), "color": "#38bdf8", "col": None})

    links = []
    for i in range(len(STAGES) - 1):
        adv = stage_count[i + 1]
        if adv and ("stage", i) in index and ("stage", i + 1) in index:
            links.append({"source": index[("stage", i)], "target": index[("stage", i + 1)],
                          "value": adv, "color": STAGES[i]["color"], "kind": "advance"})
    for (i, okey), n in sorted(drops.items()):
        if ("stage", i) in index and ("out", okey) in index:
            links.append({"source": index[("stage", i)], "target": index[("out", okey)],
                          "value": n, "color": META[okey]["color"], "kind": "drop"})
    for i, n in sorted(waiting.items()):
        if ("stage", i) in index and ("out", "_waiting") in index:
            links.append({"source": index[("stage", i)], "target": index[("out", "_waiting")],
                          "value": n, "color": "#38bdf8", "kind": "waiting"})

    applied = stage_count[APPLIED_FROM] if len(stage_count) > APPLIED_FROM else 0
    def pct(n):
        return round(100 * n / applied) if applied else 0
    si = {s["key"]: stage_count[i] for i, s in enumerate(STAGES)}
    totals = {
        "seguidas": len(reached),
        "postuladas": applied,
        "con_respuesta": si.get("screening", 0),
        "entrevistas": si.get("tecnica", 0),
        "ofertas": si.get("oferta", 0),
        "aceptadas": si.get("aceptada", 0),
        "ghosteadas": sum(v for (st, k), v in drops.items() if k == "ghosteado"),
        "rechazadas": sum(v for (st, k), v in drops.items() if k == "rechazado"),
        "tasa_respuesta": pct(si.get("screening", 0)),
        "tasa_entrevista": pct(si.get("tecnica", 0)),
        "tasa_oferta": pct(si.get("oferta", 0)),
    }
    return {"nodes": nodes, "links": links, "totals": totals}


def by_source():
    """Rendimiento por bolsa de empleo: ¿cuál te consigue respuestas de verdad?"""
    con = get_db()
    reached = _reached(con)
    rows = {r["job_id"]: r["source"] for r in con.execute("SELECT id AS job_id, source FROM jobs")}
    con.close()
    agg = {}
    for jid, top in reached.items():
        src = rows.get(jid, "—")
        d = agg.setdefault(src, {"source": src, "postuladas": 0, "respuestas": 0, "entrevistas": 0})
        if top >= APPLIED_FROM:
            d["postuladas"] += 1
        if top >= STAGE_KEYS.index("screening"):
            d["respuestas"] += 1
        if top >= STAGE_KEYS.index("tecnica"):
            d["entrevistas"] += 1
    out = [d for d in agg.values() if d["postuladas"]]
    for d in out:
        d["tasa"] = round(100 * d["respuestas"] / d["postuladas"]) if d["postuladas"] else 0
    return sorted(out, key=lambda d: (-d["postuladas"], d["source"]))
