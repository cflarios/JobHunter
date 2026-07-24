#!/usr/bin/env bash
# Ejecuta la busqueda de empleos y registra la salida (corridas manuales).
# El disparo automatico lo hace el planificador in-app; ver docs/CONTEXT.md §10b.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
LOG="$ROOT/data/search.log"
mkdir -p "$ROOT/data"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') =====" >> "$LOG"
./.venv/bin/python -m jobhunter.fetcher >> "$LOG" 2>&1
