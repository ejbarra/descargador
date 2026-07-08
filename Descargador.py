#!/usr/bin/env python3
"""
Descargador de video y audio basado en yt-dlp.

Soporta:
  - Video individual (con seleccion de calidad).
  - Audio individual (mp3/opus/m4a, con caratula y metadatos).
  - Informacion detallada de un video o audio.

Incluye:
  - Registro de actividad y errores en un archivo de log.
  - Comprobacion y actualizacion automatica de los paquetes de requirements
    al iniciar (usando uv).
  - Interfaz grafica (tkinter) y, opcionalmente, interfaz de texto (--cli).

Disenado para Linux (openSUSE Tumbleweed) y Python 3.13.
Requiere FFmpeg (paquete del repositorio Packman para codecs completos).
La GUI requiere tkinter:  sudo zypper install python313-tk
"""

from __future__ import annotations

import os
import re
import sys
import json
import queue
import shutil
import logging
import threading
import subprocess
from pathlib import Path
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler

try:
    import yt_dlp
except ImportError:
    print("[ERROR] yt-dlp no esta instalado.")
    print("        Instalalo con: uv pip install yt-dlp")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Logging (punto 1: registro de errores)
# ---------------------------------------------------------------------------


def _ruta_estado() -> Path:
    """Carpeta de estado segun XDG (~/.local/state/descargador por defecto)."""
    base = os.environ.get("XDG_STATE_HOME")
    raiz = Path(base) if base else Path.home() / ".local" / "state"
    destino = raiz / "descargador"
    destino.mkdir(parents=True, exist_ok=True)
    return destino


RUTA_LOG = _ruta_estado() / "descargador.log"

log = logging.getLogger("descargador")
log.setLevel(logging.INFO)
if not log.handlers:
    _fh = RotatingFileHandler(
        RUTA_LOG, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    _fh.setFormatter(
        logging.Formatter(
            "%(asctime)s  %(levelname)-7s  %(message)s", "%Y-%m-%d %H:%M:%S"
        )
    )
    log.addHandler(_fh)

    _ch = logging.StreamHandler(sys.stdout)
    _ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    log.addHandler(_ch)


class _YtdlpLogger:
    """Adaptador que reenvia los mensajes de yt-dlp al logging del programa."""

    def debug(self, msg: str) -> None:
        # yt-dlp envia tanto debug como info por aqui; filtramos el ruido.
        if not msg.startswith("[debug] "):
            log.info(msg)

    def info(self, msg: str) -> None:
        log.info(msg)

    def warning(self, msg: str) -> None:
        log.warning(msg)

    def error(self, msg: str) -> None:
        log.error(msg)


# ---------------------------------------------------------------------------
# Configuracion base
# ---------------------------------------------------------------------------


def _carpeta_descargas_base() -> Path:
    """Carpeta base XDG. Respeta XDG_DOWNLOAD_DIR si esta definida."""
    xdg = os.environ.get("XDG_DOWNLOAD_DIR")
    return Path(xdg).expanduser() if xdg else Path.home() / "Descargas"


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
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _resolver_carpeta(nombre: str | None, defecto: str) -> Path:
    """Resuelve una subcarpeta bajo BASE_DESCARGAS y la crea."""
    nombre = (nombre or defecto).strip() or defecto
    destino = (BASE_DESCARGAS / nombre).expanduser()
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def _fmt_duracion(segundos: int | None) -> str:
    if not segundos:
        return "N/A"
    return f"{segundos // 60}:{segundos % 60:02d}"


# ---------------------------------------------------------------------------
# Comprobacion de actualizaciones (punto 3, con uv)
# ---------------------------------------------------------------------------


def _nombre_normalizado(s: str) -> str:
    return s.strip().lower().replace("_", "-")


def _paquetes_de_requirements(ruta: Path) -> set[str]:
    """Extrae los nombres de paquete de un requirements.txt (sin versiones)."""
    nombres: set[str] = set()
    if not ruta.exists():
        return nombres
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.split("#", 1)[0].strip()
        if not linea:
            continue
        m = re.match(r"^[A-Za-z0-9_.\-]+", linea)
        if m:
            nombres.add(_nombre_normalizado(m.group(0)))
    return nombres


def comprobar_actualizaciones(
    ruta_requirements: Path, auto: bool = True, timeout: int = 60
) -> None:
    """
    Lista los paquetes del requirements que esten desactualizados (uv) y,
    si auto=True, los actualiza con 'uv pip install --upgrade'.

    Apunta explicitamente al interprete actual (--python sys.executable) para
    operar sobre el venv en uso, sin depender de VIRTUAL_ENV.
    """
    if shutil.which("uv") is None:
        log.info("uv no esta en el PATH; se omite la comprobacion de actualizaciones.")
        return

    objetivo = _paquetes_de_requirements(ruta_requirements)
    if not objetivo:
        log.info("No se encontro requirements.txt; se omite la comprobacion.")
        return

    log.info("Comprobando actualizaciones de paquetes...")
    try:
        res = subprocess.run(
            [
                "uv",
                "pip",
                "list",
                "--outdated",
                "--format",
                "json",
                "--python",
                sys.executable,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log.warning("La comprobacion de actualizaciones excedio el tiempo limite.")
        return

    if res.returncode != 0:
        log.warning(
            "No se pudo consultar paquetes desactualizados: %s", res.stderr.strip()
        )
        return

    try:
        datos = json.loads(res.stdout or "[]")
    except json.JSONDecodeError:
        log.warning("Respuesta no valida de uv al listar paquetes.")
        return

    pendientes = [
        d for d in datos if _nombre_normalizado(d.get("name", "")) in objetivo
    ]
    if not pendientes:
        log.info("Todos los paquetes del requirements estan al dia.")
        return

    for d in pendientes:
        log.info(
            "Actualizacion disponible: %s %s -> %s",
            d.get("name"),
            d.get("version", "?"),
            d.get("latest_version", "?"),
        )

    if not auto:
        return

    nombres = [d["name"] for d in pendientes]
    log.info("Actualizando: %s", ", ".join(nombres))
    try:
        up = subprocess.run(
            ["uv", "pip", "install", "--upgrade", "--python", sys.executable, *nombres],
            capture_output=True,
            text=True,
            timeout=timeout * 5,
        )
    except subprocess.TimeoutExpired:
        log.warning("La actualizacion excedio el tiempo limite.")
        return

    if up.returncode == 0:
        log.info("Paquetes actualizados correctamente.")
    else:
        log.warning("Fallo la actualizacion: %s", up.stderr.strip())


# ---------------------------------------------------------------------------
# Opciones de descarga
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
            # IMPORTANTE (punto 2): los metadatos van ANTES que la caratula.
            # En Opus/Ogg la caratula la inserta mutagen como bloque de imagen;
            # si FFmpegMetadata se ejecuta despues, FFmpeg falla al re-multiplexar
            # el archivo que ya contiene ese bloque -> "Conversion failed!".
            pps.append({"key": "FFmpegMetadata", "add_metadata": True})
            pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
        return pps


def _opciones_comunes() -> dict:
    """Opciones base. La salida se enruta al logging del programa."""
    return {
        "quiet": True,
        "no_warnings": False,
        "ignoreerrors": False,
        "noprogress": True,
        "logger": _YtdlpLogger(),
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


def descargar_video(
    url: str, carpeta: Path, calidad: str = "best", progreso=None
) -> bool:
    """Descarga un video individual fusionado a MP4. Devuelve True si tuvo exito."""
    opciones = _opciones_comunes() | {
        "format": _formato_video(calidad),
        "outtmpl": str(carpeta / "%(title)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
    }
    if progreso:
        opciones["progress_hooks"] = [progreso]

    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            log.info("Obteniendo informacion del video...")
            info = ydl.extract_info(url, download=False)
            log.info("Titulo: %s", info.get("title", "Sin titulo"))
            log.info("Duracion: %s", _fmt_duracion(info.get("duration")))
            log.info("Descargando en calidad: %s", calidad)
            ydl.download([url])
            log.info("Descarga completada.")
            return True
    except yt_dlp.utils.DownloadError as e:
        if "format" in str(e).lower():
            log.warning("Formato no disponible, reintentando con 'best'...")
            opciones["format"] = "best"
            try:
                with yt_dlp.YoutubeDL(opciones) as ydl:
                    ydl.download([url])
                    log.info("Descarga completada con formato alternativo.")
                    return True
            except Exception as e2:
                log.error("No se pudo descargar: %s", e2)
        else:
            log.error("No se pudo descargar: %s", e)
    except Exception as e:
        log.error("Error inesperado: %s", e)
    return False


def descargar_audio(
    url: str, carpeta: Path, audio: OpcionesAudio, progreso=None
) -> bool:
    """Descarga solo el audio de un video individual. Devuelve True si tuvo exito."""
    ffmpeg = verificar_ffmpeg()
    if not ffmpeg:
        log.warning(
            "Sin FFmpeg no se puede convertir ni embeber caratula; "
            "se descargara el audio original sin procesar."
        )

    opciones = _opciones_comunes() | {
        "format": "bestaudio/best",
        "outtmpl": str(carpeta / "%(title)s.%(ext)s"),
        "noplaylist": True,
    }
    if ffmpeg:
        opciones["postprocessors"] = audio.postprocessors(ffmpeg)
        opciones["writethumbnail"] = audio.caratula
    if progreso:
        opciones["progress_hooks"] = [progreso]

    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            destino = audio.codec.upper() if ffmpeg else "original"
            log.info("Descargando audio (%s)...", destino)
            ydl.download([url])
            log.info("Audio descargado.")
            return True
    except Exception as e:
        log.error("No se pudo descargar el audio: %s", e)
        return False


# ---------------------------------------------------------------------------
# Informacion (punto 1: distinguir video vs audio; sin playlists)
# ---------------------------------------------------------------------------


def _es_solo_audio(info: dict) -> bool:
    """True si ninguno de los formatos disponibles contiene pista de video."""
    formatos = info.get("formats") or []
    for f in formatos:
        if f.get("vcodec") not in (None, "none"):
            return False
    return bool(formatos)  # si no hay formatos, no afirmamos nada


def obtener_info(url: str) -> dict | None:
    """Extrae la informacion de un unico video/audio (nunca una playlist)."""
    opciones = {"quiet": True, "noplaylist": True, "logger": _YtdlpLogger()}
    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            log.info("Obteniendo informacion...")
            return ydl.extract_info(url, download=False)
    except Exception as e:
        log.error("No se pudo obtener informacion: %s", e)
        return None


def formatear_info(info: dict) -> str:
    """Devuelve un bloque de texto legible con los datos del elemento."""
    tipo = "Audio (sin video)" if _es_solo_audio(info) else "Video"
    vistas = info.get("view_count")
    vistas_txt = f"{vistas:,}" if isinstance(vistas, int) else "N/A"

    lineas = [
        f"Tipo:     {tipo}",
        f"Titulo:   {info.get('title', 'N/A')}",
        f"Duracion: {_fmt_duracion(info.get('duration'))}",
        f"Vistas:   {vistas_txt}",
        f"Canal:    {info.get('uploader', 'N/A')}",
        f"Sitio:    {info.get('extractor', 'N/A')}",
    ]
    if tipo == "Video":
        alturas = [
            f.get("height") for f in (info.get("formats") or []) if f.get("height")
        ]
        if alturas:
            lineas.append(f"Maxima:   {max(alturas)}p")
    return "\n".join(lineas)


def mostrar_info(url: str) -> dict | None:
    info = obtener_info(url)
    if info is None:
        return None
    for linea in formatear_info(info).splitlines():
        log.info(linea)
    return info


# ---------------------------------------------------------------------------
# Interfaz de texto (--cli)
# ---------------------------------------------------------------------------


def _elegir(prompt: str, opciones: dict[str, str], defecto: str) -> str:
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


def menu() -> None:
    print("Descargador de video y audio")
    print("=" * 40)
    print(f"Carpeta base: {BASE_DESCARGAS}")
    print(f"Log:          {RUTA_LOG}")
    print(f"yt-dlp:       {yt_dlp.version.__version__}")
    print(
        "FFmpeg:       "
        + (
            "detectado"
            if verificar_ffmpeg()
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
            url = input("URL: ").strip()
            if url:
                mostrar_info(url)

        elif opcion == "4":
            print("Hasta luego.")
            break

        else:
            print("[ERROR] Opcion no valida.")


# ---------------------------------------------------------------------------
# Interfaz grafica (punto 4, tkinter)
# ---------------------------------------------------------------------------


def lanzar_gui(ruta_requirements: Path, comprobar: bool = True) -> None:
    import tkinter as tk
    from tkinter import ttk, filedialog

    cola: queue.Queue = queue.Queue()

    # Handler que vuelca el logging a la cola para mostrarlo en la GUI.
    class _Sink(logging.Handler):
        def emit(self, record):
            try:
                cola.put(("log", self.format(record)))
            except Exception:
                pass

    sink = _Sink()
    sink.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sink)

    root = tk.Tk()
    root.title("Descargador de video y audio")
    root.geometry("780x600")
    root.minsize(640, 480)

    # Menu contextual (clic derecho) con Cortar/Copiar/Pegar/Seleccionar todo,
    # ya que tkinter no lo ofrece por defecto en Linux.
    def _agregar_menu_contextual(entry: tk.Entry) -> None:
        menu_ctx = tk.Menu(entry, tearoff=0)
        menu_ctx.add_command(
            label="Cortar", command=lambda: entry.event_generate("<<Cut>>")
        )
        menu_ctx.add_command(
            label="Copiar", command=lambda: entry.event_generate("<<Copy>>")
        )
        menu_ctx.add_command(
            label="Pegar", command=lambda: entry.event_generate("<<Paste>>")
        )
        menu_ctx.add_separator()
        menu_ctx.add_command(
            label="Seleccionar todo",
            command=lambda: entry.select_range(0, "end"),
        )

        def mostrar(evt):
            entry.focus_set()
            menu_ctx.tk_popup(evt.x_root, evt.y_root)

        entry.bind("<Button-3>", mostrar)

    cont = ttk.Frame(root, padding=12)
    cont.pack(fill="both", expand=True)
    cont.columnconfigure(1, weight=1)

    # --- URL ---
    ttk.Label(cont, text="URL:").grid(row=0, column=0, sticky="w", pady=4)
    var_url = tk.StringVar()
    entry_url = ttk.Entry(cont, textvariable=var_url)
    entry_url.grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)
    _agregar_menu_contextual(entry_url)

    # --- Carpeta destino ---
    ttk.Label(cont, text="Destino:").grid(row=1, column=0, sticky="w", pady=4)
    var_carpeta = tk.StringVar(value=str(BASE_DESCARGAS / "musica"))
    entry_carpeta = ttk.Entry(cont, textvariable=var_carpeta)
    entry_carpeta.grid(row=1, column=1, sticky="ew", pady=4)
    _agregar_menu_contextual(entry_carpeta)

    def examinar():
        inicio = var_carpeta.get() or str(BASE_DESCARGAS)
        elegido = filedialog.askdirectory(
            initialdir=inicio, title="Carpeta de descarga"
        )
        if elegido:
            var_carpeta.set(elegido)

    ttk.Button(cont, text="Examinar...", command=examinar).grid(
        row=1, column=2, sticky="e", padx=(8, 0)
    )

    # --- Modo ---
    var_modo = tk.StringVar(value="audio")
    marco_modo = ttk.LabelFrame(cont, text="Tipo de descarga", padding=8)
    marco_modo.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 4))

    _defaults = {
        "video": str(BASE_DESCARGAS / "videos"),
        "audio": str(BASE_DESCARGAS / "musica"),
        "info": str(BASE_DESCARGAS),
    }

    def cambiar_modo():
        modo = var_modo.get()
        # Mostrar/ocultar paneles de subopciones.
        if modo == "video":
            marco_video.grid()
            marco_audio.grid_remove()
        elif modo == "audio":
            marco_video.grid_remove()
            marco_audio.grid()
        else:
            marco_video.grid_remove()
            marco_audio.grid_remove()
        # Sugerir carpeta solo si el campo sigue en un valor por defecto conocido.
        if var_carpeta.get() in _defaults.values():
            var_carpeta.set(_defaults[modo])
        boton.config(text="Ver informacion" if modo == "info" else "Descargar")

    for txt, val in (("Video", "video"), ("Audio", "audio"), ("Informacion", "info")):
        ttk.Radiobutton(
            marco_modo, text=txt, value=val, variable=var_modo, command=cambiar_modo
        ).pack(side="left", padx=10)

    # --- Subopciones video ---
    marco_video = ttk.LabelFrame(cont, text="Opciones de video", padding=8)
    marco_video.grid(row=3, column=0, columnspan=3, sticky="ew", pady=4)
    ttk.Label(marco_video, text="Calidad:").pack(side="left", padx=(0, 8))
    var_calidad = tk.StringVar(value="best")
    ttk.Combobox(
        marco_video,
        textvariable=var_calidad,
        state="readonly",
        width=10,
        values=["best", "720p", "480p", "360p", "worst"],
    ).pack(side="left")

    # --- Subopciones audio ---
    marco_audio = ttk.LabelFrame(cont, text="Opciones de audio", padding=8)
    marco_audio.grid(row=3, column=0, columnspan=3, sticky="ew", pady=4)

    ttk.Label(marco_audio, text="Formato:").grid(
        row=0, column=0, sticky="w", padx=(0, 8)
    )
    var_codec = tk.StringVar(value="mp3")
    combo_codec = ttk.Combobox(
        marco_audio,
        textvariable=var_codec,
        state="readonly",
        width=8,
        values=list(AUDIO_CODECS.keys()),
    )
    combo_codec.grid(row=0, column=1, sticky="w")

    ttk.Label(marco_audio, text="Calidad:").grid(
        row=0, column=2, sticky="w", padx=(16, 8)
    )
    var_aq = tk.StringVar(value="320")
    combo_aq = ttk.Combobox(
        marco_audio,
        textvariable=var_aq,
        state="readonly",
        width=8,
        values=["320", "256", "192", "128"],
    )
    combo_aq.grid(row=0, column=3, sticky="w")

    var_caratula = tk.BooleanVar(value=True)
    ttk.Checkbutton(
        marco_audio, text="Embeber caratula y metadatos", variable=var_caratula
    ).grid(row=0, column=4, sticky="w", padx=(16, 0))

    def cambiar_codec(_evt=None):
        # La eleccion de calidad solo aplica a MP3; el resto usa su valor fijo.
        if var_codec.get() == "mp3":
            combo_aq.config(state="readonly")
            if var_aq.get() not in ("320", "256", "192", "128"):
                var_aq.set("320")
        else:
            var_aq.set(AUDIO_CODECS[var_codec.get()])
            combo_aq.config(state="disabled")

    combo_codec.bind("<<ComboboxSelected>>", cambiar_codec)

    # --- Progreso y estado ---
    var_progreso = tk.DoubleVar(value=0.0)
    ttk.Progressbar(cont, variable=var_progreso, maximum=100).grid(
        row=4, column=0, columnspan=3, sticky="ew", pady=(10, 2)
    )
    var_estado = tk.StringVar(value="Listo.")
    ttk.Label(cont, textvariable=var_estado).grid(
        row=5, column=0, columnspan=3, sticky="w"
    )

    # --- Log ---
    marco_log = ttk.LabelFrame(cont, text="Registro", padding=4)
    marco_log.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
    cont.rowconfigure(6, weight=1)
    txt_log = tk.Text(marco_log, height=12, wrap="word", state="disabled")
    scroll = ttk.Scrollbar(marco_log, command=txt_log.yview)
    txt_log.configure(yscrollcommand=scroll.set)
    txt_log.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    # --- Boton de accion ---
    boton = ttk.Button(cont, text="Descargar")
    boton.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(10, 0))

    # --- Hook de progreso (corre en el hilo de trabajo) ---
    def hook(d):
        estado = d.get("status")
        if estado == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            hechos = d.get("downloaded_bytes") or 0
            if total:
                cola.put(("progress", hechos / total * 100))
            pct = d.get("_percent_str", "").strip()
            spd = d.get("_speed_str", "").strip()
            cola.put(("status", f"Descargando... {pct} {spd}"))
        elif estado == "finished":
            cola.put(("progress", 100.0))
            cola.put(("status", "Procesando (postproceso)..."))

    # --- Trabajo en hilo aparte ---
    def trabajo():
        url = var_url.get().strip()
        modo = var_modo.get()
        try:
            if modo == "info":
                mostrar_info(url)
            else:
                carpeta = Path(var_carpeta.get()).expanduser()
                carpeta.mkdir(parents=True, exist_ok=True)
                if modo == "video":
                    descargar_video(url, carpeta, var_calidad.get(), progreso=hook)
                else:
                    audio = OpcionesAudio(
                        codec=var_codec.get(),
                        calidad=var_aq.get(),
                        caratula=var_caratula.get(),
                    )
                    descargar_audio(url, carpeta, audio, progreso=hook)
        finally:
            cola.put(("done", True))

    def iniciar():
        if not var_url.get().strip():
            cola.put(("log", "[ERROR] Ingresa una URL."))
            return
        boton.config(state="disabled")
        var_progreso.set(0)
        var_estado.set("Trabajando...")
        threading.Thread(target=trabajo, daemon=True).start()

    boton.config(command=iniciar)

    # --- Drenado de la cola hacia los widgets (hilo principal de Tk) ---
    def drenar():
        try:
            while True:
                tipo, val = cola.get_nowait()
                if tipo == "log":
                    txt_log.config(state="normal")
                    txt_log.insert("end", val + "\n")
                    txt_log.see("end")
                    txt_log.config(state="disabled")
                elif tipo == "progress":
                    var_progreso.set(val)
                elif tipo == "status":
                    var_estado.set(val)
                elif tipo == "done":
                    boton.config(state="normal")
                    var_estado.set("Listo.")
        except queue.Empty:
            pass
        root.after(120, drenar)

    cambiar_modo()
    cambiar_codec()
    root.after(120, drenar)

    log.info("Carpeta base: %s", BASE_DESCARGAS)
    log.info("Log: %s", RUTA_LOG)
    log.info("yt-dlp: %s", yt_dlp.version.__version__)
    log.info("FFmpeg: %s", "detectado" if verificar_ffmpeg() else "NO detectado")

    if comprobar:
        threading.Thread(
            target=lambda: comprobar_actualizaciones(ruta_requirements), daemon=True
        ).start()

    try:
        root.mainloop()
    finally:
        log.removeHandler(sink)


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Descargador de video y audio (yt-dlp).")
    p.add_argument(
        "--cli", action="store_true", help="Usar interfaz de texto en vez de la GUI."
    )
    p.add_argument(
        "--no-update",
        action="store_true",
        help="Omitir la comprobacion de actualizaciones.",
    )
    args = p.parse_args()

    ruta_req = Path(__file__).resolve().parent / "requirements.txt"

    if args.cli:
        if not args.no_update:
            comprobar_actualizaciones(ruta_req)
        menu()
        return

    try:
        lanzar_gui(ruta_req, comprobar=not args.no_update)
    except Exception as e:
        log.error("No se pudo iniciar la GUI (%s).", e)
        log.error("Instala tkinter con: sudo zypper install python313-tk")
        log.info("Usando interfaz de texto como alternativa.")
        if not args.no_update:
            comprobar_actualizaciones(ruta_req)
        menu()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Interrumpido por el usuario.")
        sys.exit(0)
