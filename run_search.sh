#!/usr/bin/env bash
# Ejecuta la busqueda diaria de empleos y registra la salida.
cd "$(dirname "$0")" || exit 1
LOG="$(dirname "$0")/search.log"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') =====" >> "$LOG"
./.venv/bin/python fetcher.py >> "$LOG" 2>&1
