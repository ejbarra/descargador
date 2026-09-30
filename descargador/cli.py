"""Interfaz de texto (menu interactivo) del descargador."""

from __future__ import annotations

import sys
from pathlib import Path

from . import core
from .core import (
    AUDIO_CODECS,
    BASE_DESCARGAS,
    CALIDADES_MP3,
    CALIDADES_VIDEO,
    RUTA_LOG,
    SUBCARPETAS,
    OpcionesAudio,
    OpcionesVideo,
    Progreso,
    log,
)


def _elegir(prompt: str, opciones: dict[str, str], defecto: str) -> str:
    seleccion = input(prompt).strip() or defecto
    return opciones.get(seleccion, opciones[defecto])


def _pedir_url() -> str:
    return core.normalizar_url(input("URL: "))


def _progreso_consola(p: Progreso) -> None:
    """Muestra el avance en la terminal, sobrescribiendo la linea de descarga."""
    linea = "\r" + p.texto[:78].ljust(78)
    en_curso = p.fase == "descarga" and p.porcentaje is not None and p.porcentaje < 100
    sys.stdout.write(linea if en_curso else linea + "\n")
    sys.stdout.flush()


def _pedir_opciones_video() -> OpcionesVideo:
    print("\nCalidad:")
    print("  " + "   ".join(f"{i}. {c}" for i, c in enumerate(CALIDADES_VIDEO, 1)))
    calidad = _elegir(
        f"Elige calidad (1-{len(CALIDADES_VIDEO)}) [1]: ",
        {str(i): c for i, c in enumerate(CALIDADES_VIDEO, 1)},
        "1",
    )
    metadatos = input("Embeber metadatos y miniatura? (S/n): ").strip().lower() != "n"
    return OpcionesVideo(calidad=calidad, metadatos=metadatos)


def _pedir_opciones_audio() -> OpcionesAudio:
    print("\nFormato de audio:")
    print("  1. MP3   (mas compatible)")
    print("  2. Opus  (mejor relacion calidad/tamano)")
    print("  3. M4A/AAC")
    codec = _elegir(
        "Elige formato (1-3) [1]: ",
        {"1": "mp3", "2": "opus", "3": "m4a"},
        "1",
    )

    calidad = AUDIO_CODECS[codec]
    if codec == "mp3":
        print("\nCalidad MP3:")
        print(
            "  " + "   ".join(f"{i}. {c} kbps" for i, c in enumerate(CALIDADES_MP3, 1))
        )
        calidad = _elegir(
            f"Elige calidad (1-{len(CALIDADES_MP3)}) [1]: ",
            {str(i): c for i, c in enumerate(CALIDADES_MP3, 1)},
            "1",
        )

    caratula = input("Embeber caratula y metadatos? (S/n): ").strip().lower() != "n"
    if caratula and not core.verificar_ffmpeg():
        print("[AVISO] FFmpeg no disponible: se descargara sin caratula.")
        caratula = False

    return OpcionesAudio(codec=codec, calidad=calidad, caratula=caratula)


def menu() -> None:
    print("Descargador de video y audio")
    print("=" * 40)
    print(f"Carpeta base: {BASE_DESCARGAS}")
    print(f"Log:          {RUTA_LOG}")
    print(f"yt-dlp:       {core.version_yt_dlp()}")
    print(
        "FFmpeg:       "
        + (
            "detectado"
            if core.verificar_ffmpeg()
            else "NO detectado (sudo zypper install ffmpeg)"
        )
    )

    while True:
        print("\nOpciones:")
        print("  1. Descargar video")
        print("  2. Descargar audio")
        print("  3. Ver informacion (video o audio)")
        print("  4. Salir")

        opcion = input("\nElige una opcion (1-4): ").strip()

        if opcion == "1":
            url = _pedir_url()
            if not url:
                continue
            video = _pedir_opciones_video()
            defecto = SUBCARPETAS["video"]
            carpeta = core.resolver_carpeta(input(f"Subcarpeta [{defecto}]: "), defecto)
            core.descargar_video(url, carpeta, video, progreso=_progreso_consola)

        elif opcion == "2":
            url = _pedir_url()
            if not url:
                continue
            audio = _pedir_opciones_audio()
            defecto = SUBCARPETAS["audio"]
            carpeta = core.resolver_carpeta(input(f"Subcarpeta [{defecto}]: "), defecto)
            core.descargar_audio(url, carpeta, audio, progreso=_progreso_consola)

        elif opcion == "3":
            url = _pedir_url()
            if url:
                core.mostrar_info(url)

        elif opcion == "4":
            print("Hasta luego.")
            break

        else:
            print("[ERROR] Opcion no valida.")


def lanzar_cli(ruta_requirements: Path, comprobar: bool = True) -> None:
    if comprobar:
        core.comprobar_actualizaciones(ruta_requirements)
    try:
        menu()
    except (KeyboardInterrupt, EOFError):
        print()
        log.info("Interrumpido por el usuario.")
