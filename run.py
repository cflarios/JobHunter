#!/usr/bin/env python3
"""Punto de entrada de JobHunter: arranca el servidor web y el planificador.

    ./.venv/bin/python run.py

La app vive en el paquete `jobhunter/`; este fichero solo la lanza, de modo que la
unidad systemd apunta a una ruta estable aunque el paquete cambie por dentro.
"""
from jobhunter.app import main

if __name__ == "__main__":
    main()
