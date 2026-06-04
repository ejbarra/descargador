#!/usr/bin/env python3
"""
Descargador unificado de video y audio basado en yt-dlp.

Soporta:
  - Video individual (con seleccion de calidad)
  - Audio individual (mp3/opus/m4a, con caratula y metadatos)
  - Playlists completas o seleccion de pistas
  - Informacion detallada de video/playlist

Disenado para Linux (openSUSE Tumbleweed) y Python 3.13.
Requiere FFmpeg (paquete del repositorio Packman para codecs completos).
"""

import sys
import subprocess
from pathlib import Path
from dataclasses import dataclass

try:
    import yt_dlp
except ImportError:
    print("[ERROR] yt-dlp no esta instalado.")
    print("        Instalalo con: uv pip install yt-dlp")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Configuracion y utilidades base
# ---------------------------------------------------------------------------


# Carpeta base XDG. Se respeta XDG_DOWNLOAD_DIR si esta definida.
def _carpeta_descargas_base() -> Path:
    import os

    xdg = os.environ.get("XDG_DOWNLOAD_DIR")
    if xdg:
        return Path(xdg).expanduser()
    return Path.home() / "Descargas"


BASE_DESCARGAS = _carpeta_descargas_base()

# Codecs de audio admitidos y su bitrate por defecto (kbps).
AUDIO_CODECS = {
    "mp3": "320",
    "opus": "256",
    "m4a": "256",
}


def verificar_ffmpeg() -> bool:
    """Devuelve True si FFmpeg esta disponible en el PATH."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _resolver_carpeta(nombre: str | None, defecto: str) -> Path:
    """Resuelve una carpeta destino bajo BASE_DESCARGAS y la crea."""
    nombre = (nombre or defecto).strip() or defecto
    destino = (BASE_DESCARGAS / nombre).expanduser()
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def _fmt_duracion(segundos: int | None) -> str:
    if not segundos:
        return "N/A"
    return f"{segundos // 60}:{segundos % 60:02d}"


# ---------------------------------------------------------------------------
# Configuracion de descarga
# ---------------------------------------------------------------------------


@dataclass
class OpcionesAudio:
    """Parametros de extraccion de audio."""

    codec: str = "mp3"
    calidad: str = "320"
    caratula: bool = True

    def postprocessors(self, ffmpeg: bool) -> list[dict]:
        pps: list[dict] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": self.codec,
                "preferredquality": self.calidad,
            }
        ]
        if self.caratula and ffmpeg:
            pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
            pps.append({"key": "FFmpegMetadata", "add_metadata": True})
        return pps


def _opciones_comunes() -> dict:
    """
    Opciones base seguras y modernas.

    Notas frente a la version antigua:
      - No se desactiva la verificacion SSL (certifi en openSUSE esta al dia).
      - No se fija un User-Agent: yt-dlp gestiona el suyo, mas actualizado.
      - El re-empaquetado MP4 (+faststart) lo aplica yt-dlp automaticamente
        al fusionar con FFmpeg, por lo que se elimina el "reparador" manual.
    """
    return {
        "quiet": False,
        "no_warnings": False,
        "ignoreerrors": False,
        "noprogress": False,
    }


def _formato_video(calidad: str) -> str:
    """Construye el selector de formato segun la calidad pedida."""
    if calidad == "best":
        return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
    if calidad == "worst":
        return "worstvideo+worstaudio/worst"
    altura = calidad.replace("p", "")
    return (
        f"bestvideo[height<={altura}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={altura}]+bestaudio/best[height<={altura}]/best"
    )


# ---------------------------------------------------------------------------
# Descargas
# ---------------------------------------------------------------------------


def descargar_video(url: str, carpeta: Path, calidad: str = "best") -> None:
    """Descarga un video individual fusionado a MP4."""
    opciones = _opciones_comunes() | {
        "format": _formato_video(calidad),
        "outtmpl": str(carpeta / "%(title)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            print("[INFO] Obteniendo informacion del video...")
            info = ydl.extract_info(url, download=False)
            print(f"[INFO] Titulo: {info.get('title', 'Sin titulo')}")
            print(f"[INFO] Duracion: {_fmt_duracion(info.get('duration'))}")
            print(f"[INFO] Descargando en calidad: {calidad}")
            ydl.download([url])
            print("[OK] Descarga completada.")
    except yt_dlp.utils.DownloadError as e:
        if "format" in str(e).lower():
            print("[AVISO] Formato no disponible, reintentando con 'best'...")
            opciones["format"] = "best"
            try:
                with yt_dlp.YoutubeDL(opciones) as ydl:
                    ydl.download([url])
                    print("[OK] Descarga completada con formato alternativo.")
            except Exception as e2:
                print(f"[ERROR] No se pudo descargar: {e2}")
        else:
            print(f"[ERROR] No se pudo descargar: {e}")
    except Exception as e:
        print(f"[ERROR] Error inesperado: {e}")


def descargar_audio(url: str, carpeta: Path, audio: OpcionesAudio) -> None:
    """Descarga solo el audio de un video individual."""
    ffmpeg = verificar_ffmpeg()
    if not ffmpeg:
        print("[AVISO] Sin FFmpeg no se puede convertir ni embeber caratula.")
        print("        Se descargara el audio original sin procesar.")

    opciones = _opciones_comunes() | {
        "format": "bestaudio/best",
        "outtmpl": str(carpeta / "%(title)s.%(ext)s"),
        "noplaylist": True,
    }
    if ffmpeg:
        opciones["postprocessors"] = audio.postprocessors(ffmpeg)
        opciones["writethumbnail"] = audio.caratula

    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            destino = audio.codec.upper() if ffmpeg else "original"
            print(f"[INFO] Descargando audio ({destino})...")
            ydl.download([url])
            print("[OK] Audio descargado.")
    except Exception as e:
        print(f"[ERROR] No se pudo descargar el audio: {e}")


def descargar_playlist(
    url: str,
    carpeta: Path,
    audio: OpcionesAudio,
    indices: list[int] | None = None,
) -> None:
    """
    Descarga una playlist completa o solo ciertos indices como audio.

    El filtrado por indice usa 'playlist_items' de yt-dlp, que es mas
    eficiente que extraer toda la info y luego descargar pista por pista.
    """
    ffmpeg = verificar_ffmpeg()
    opciones = _opciones_comunes() | {
        "format": "bestaudio/best",
        "outtmpl": str(carpeta / "%(playlist_index)02d - %(title)s.%(ext)s"),
        "ignoreerrors": True,
        "noplaylist": False,
    }
    if ffmpeg:
        opciones["postprocessors"] = audio.postprocessors(ffmpeg)
        opciones["writethumbnail"] = audio.caratula
    if indices:
        opciones["playlist_items"] = ",".join(str(i) for i in indices)

    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            n = f"{len(indices)} pista(s)" if indices else "playlist completa"
            print(f"[INFO] Descargando {n} en {audio.codec.upper()}...")
            print(f"[INFO] Destino: {carpeta}")
            ydl.download([url])
            print("[OK] Descarga finalizada.")
    except Exception as e:
        print(f"[ERROR] No se pudo descargar la playlist: {e}")


# ---------------------------------------------------------------------------
# Informacion
# ---------------------------------------------------------------------------


def _convertir_url_music(url: str) -> str:
    """Convierte una URL de YouTube Music a YouTube normal."""
    if "music.youtube.com" not in url:
        return url
    if "list=" in url:
        list_id = url.split("list=")[1].split("&")[0]
        nueva = f"https://www.youtube.com/playlist?list={list_id}"
        print(f"[INFO] URL convertida a YouTube: {nueva}")
        return nueva
    return url


def es_playlist(url: str) -> bool:
    indicadores = ("playlist?list=", "&list=", "music.youtube.com/playlist")
    return any(i in url for i in indicadores)


def mostrar_info(url: str) -> dict | None:
    """Muestra informacion de un video o playlist. Devuelve la info cruda."""
    url = _convertir_url_music(url)
    es_lista = es_playlist(url)

    opciones = {
        "quiet": True,
        "extract_flat": es_lista,
        "playlistend": 200 if es_lista else None,
    }

    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            print("[INFO] Obteniendo informacion...")
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        print(f"[ERROR] No se pudo obtener informacion: {e}")
        return None

    if "entries" in info:
        entries = [e for e in info["entries"] if e]
        print(f"\n[PLAYLIST] {info.get('title', 'Sin titulo')}")
        print(f"[PLAYLIST] Total de pistas: {len(entries)}")
        print("-" * 60)
        for i, entry in enumerate(entries, 1):
            titulo = entry.get("title", "Sin titulo")
            dur = _fmt_duracion(entry.get("duration"))
            print(f"{i:3d}. {titulo}  ({dur})")
        return info

    print(f"\n[VIDEO] Titulo:   {info.get('title', 'N/A')}")
    print(f"[VIDEO] Duracion: {_fmt_duracion(info.get('duration'))}")
    vistas = info.get("view_count")
    print(f"[VIDEO] Vistas:   {vistas:,}" if vistas else "[VIDEO] Vistas:   N/A")
    print(f"[VIDEO] Canal:    {info.get('uploader', 'N/A')}")
    print(f"[VIDEO] Sitio:    {info.get('extractor', 'N/A')}")
    return info


# ---------------------------------------------------------------------------
# Parseo de seleccion (ej: "1,3,5-10,15")
# ---------------------------------------------------------------------------


def parsear_seleccion(texto: str, maximo: int) -> list[int]:
    indices: set[int] = set()
    try:
        for parte in texto.split(","):
            parte = parte.strip()
            if "-" in parte:
                inicio, fin = map(int, parte.split("-"))
                indices.update(range(inicio, fin + 1))
            elif parte:
                indices.add(int(parte))
    except ValueError:
        print("[ERROR] Formato invalido. Usa por ejemplo: 1,3,5-10,15")
        return []
    return [i for i in sorted(indices) if 1 <= i <= maximo]


# ---------------------------------------------------------------------------
# Entrada interactiva auxiliar
# ---------------------------------------------------------------------------


def _elegir(prompt: str, opciones: dict[str, str], defecto: str) -> str:
    """Muestra un menu corto y devuelve el valor elegido."""
    seleccion = input(prompt).strip() or defecto
    return opciones.get(seleccion, opciones[defecto])


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
        print("  1. 320 kbps   2. 256 kbps   3. 192 kbps   4. 128 kbps")
        calidad = _elegir(
            "Elige calidad (1-4) [1]: ",
            {"1": "320", "2": "256", "3": "192", "4": "128"},
            "1",
        )

    caratula = input("Embeber caratula y metadatos? (S/n): ").strip().lower() != "n"
    if caratula and not verificar_ffmpeg():
        print("[AVISO] FFmpeg no disponible: se descargara sin caratula.")
        caratula = False

    return OpcionesAudio(codec=codec, calidad=calidad, caratula=caratula)


# ---------------------------------------------------------------------------
# Menu principal
# ---------------------------------------------------------------------------


def menu() -> None:
    print("Descargador unificado de video y audio")
    print("=" * 40)
    print(f"Carpeta base: {BASE_DESCARGAS}")
    print(f"yt-dlp: {yt_dlp.version.__version__}")
    if verificar_ffmpeg():
        print("FFmpeg: detectado")
    else:
        print("FFmpeg: NO detectado (instala con: sudo zypper install ffmpeg)")

    while True:
        print("\nOpciones:")
        print("  1. Descargar video")
        print("  2. Descargar audio (individual)")
        print("  3. Descargar playlist (audio)")
        print("  4. Ver informacion de video/playlist")
        print("  5. Salir")

        opcion = input("\nElige una opcion (1-5): ").strip()

        if opcion == "1":
            url = input("URL del video: ").strip()
            if not url:
                continue
            print("\nCalidad:")
            print("  1. Mejor   2. 720p   3. 480p   4. 360p   5. Menor")
            calidad = _elegir(
                "Elige calidad (1-5) [1]: ",
                {"1": "best", "2": "720p", "3": "480p", "4": "360p", "5": "worst"},
                "1",
            )
            carpeta = _resolver_carpeta(input("Subcarpeta [videos]: "), "videos")
            descargar_video(url, carpeta, calidad)

        elif opcion == "2":
            url = input("URL del video: ").strip()
            if not url:
                continue
            audio = _pedir_opciones_audio()
            carpeta = _resolver_carpeta(input("Subcarpeta [musica]: "), "musica")
            descargar_audio(url, carpeta, audio)

        elif opcion == "3":
            url = input("URL de la playlist: ").strip()
            if not url:
                continue
            url = _convertir_url_music(url)
            info = mostrar_info(url)
            if not info or "entries" not in info:
                print("[AVISO] No se detecto una playlist valida.")
                continue
            total = len([e for e in info["entries"] if e])

            print("\n  1. Descargar todo")
            print("  2. Seleccionar pistas (ej: 1,3,5-10)")
            sub = input("Elige (1-2) [1]: ").strip() or "1"

            indices = None
            if sub == "2":
                seleccion = input("Numeros: ").strip()
                indices = parsear_seleccion(seleccion, total)
                if not indices:
                    print("[AVISO] Seleccion vacia o invalida.")
                    continue

            audio = _pedir_opciones_audio()
            carpeta = _resolver_carpeta(input("Subcarpeta [playlists]: "), "playlists")
            descargar_playlist(url, carpeta, audio, indices)

        elif opcion == "4":
            url = input("URL: ").strip()
            if url:
                mostrar_info(url)

        elif opcion == "5":
            print("Hasta luego.")
            break

        else:
            print("[ERROR] Opcion no valida.")


if __name__ == "__main__":
    try:
        menu()
    except KeyboardInterrupt:
        print("\n[INFO] Interrumpido por el usuario.")
        sys.exit(0)
