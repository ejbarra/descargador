#!/usr/bin/env python3
"""
Descargador de video y audio basado en yt-dlp.

Soporta:
  - Video individual (con seleccion de calidad, metadatos y miniatura).
  - Audio individual (mp3/opus/m4a, con caratula y metadatos).
  - Informacion detallada de un video o audio.

Incluye:
  - Interfaz grafica (PySide6 / Qt 6, nativa en Wayland) y, opcionalmente,
    interfaz de texto (--cli).
  - Cancelacion de la descarga en curso y limpieza de archivos parciales.
  - Registro de actividad y errores en un archivo de log.
  - Comprobacion y actualizacion automatica de los paquetes de requirements
    al iniciar (usando uv).

Disenado para Linux (openSUSE Tumbleweed) y Python 3.13.
Requiere FFmpeg (paquete del repositorio Packman para codecs completos).
La GUI requiere PySide6:  uv pip install -r requirements.txt

El codigo esta organizado en el paquete ``descargador``:
  core.py  nucleo (descargas, informacion, logging, actualizaciones)
  gui.py   interfaz grafica (PySide6)
  cli.py   interfaz de texto
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from descargador import __version__
from descargador.core import log


def _hay_display() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def main() -> int:
    p = argparse.ArgumentParser(description="Descargador de video y audio (yt-dlp).")
    p.add_argument("url", nargs="?", help="URL con la que abrir la GUI (opcional).")
    p.add_argument(
        "--cli", action="store_true", help="Usar interfaz de texto en vez de la GUI."
    )
    p.add_argument(
        "--no-update",
        action="store_true",
        help="Omitir la comprobacion de actualizaciones.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args()

    ruta_req = Path(__file__).resolve().parent / "requirements.txt"
    comprobar = not args.no_update

    if not args.cli:
        if not _hay_display():
            log.warning("No se detecto sesion grafica (WAYLAND_DISPLAY/DISPLAY).")
        else:
            try:
                from descargador.gui import lanzar_gui
            except ImportError as e:
                log.error("No se pudo cargar la GUI (%s).", e)
                log.error("Instala PySide6 con: uv pip install -r requirements.txt")
            else:
                return lanzar_gui(ruta_req, comprobar=comprobar, url_inicial=args.url)
        log.info("Usando interfaz de texto como alternativa.")

    from descargador.cli import lanzar_cli

    lanzar_cli(ruta_req, comprobar=comprobar)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.info("Interrumpido por el usuario.")
        sys.exit(0)
