"""
Nucleo del descargador: configuracion, logging, descargas e informacion.

Este modulo no depende de ninguna interfaz (ni Qt ni consola). Las interfaces
reciben el progreso mediante un callback y pueden cancelar una descarga en
curso con un ``threading.Event``.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    import yt_dlp
    from yt_dlp.utils import DownloadCancelled, DownloadError, format_bytes
except ImportError:  # pragma: no cover - solo informa al usuario
    print("[ERROR] yt-dlp no esta instalado.")
    print("        Instalalo con: uv pip install -r requirements.txt")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Logging
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

# Subcarpetas por defecto para cada tipo de descarga.
SUBCARPETAS = {"video": "videos", "audio": "musica"}

# Calidades de video ofrecidas (de mayor a menor).
CALIDADES_VIDEO: tuple[str, ...] = ("best", "1080p", "720p", "480p", "360p", "worst")

# Codecs de audio admitidos y su bitrate por defecto (kbps).
AUDIO_CODECS = {
    "mp3": "320",
    "opus": "256",
    "m4a": "256",
}

# Bitrates seleccionables para MP3 (el resto de codecs usa su valor fijo).
CALIDADES_MP3: tuple[str, ...] = ("320", "256", "192", "128")


@functools.lru_cache(maxsize=1)
def verificar_ffmpeg() -> bool:
    """Devuelve True si FFmpeg esta disponible en el PATH (resultado cacheado)."""
    if shutil.which("ffmpeg") is None:
        return False
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, OSError):
        return False


def version_yt_dlp() -> str:
    return yt_dlp.version.__version__


def resolver_carpeta(nombre: str | None, defecto: str) -> Path:
    """Resuelve una subcarpeta bajo BASE_DESCARGAS (o una ruta absoluta) y la crea."""
    nombre = (nombre or defecto).strip() or defecto
    ruta = Path(nombre).expanduser()
    destino = ruta if ruta.is_absolute() else BASE_DESCARGAS / ruta
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def normalizar_url(url: str) -> str:
    """Limpia la URL y antepone https:// si no trae esquema."""
    url = url.strip()
    if url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", url):
        url = "https://" + url
    return url


def fmt_duracion(segundos: float | None) -> str:
    """Formatea segundos como m:ss o h:mm:ss."""
    if not segundos:
        return "N/A"
    s = int(segundos)
    h, resto = divmod(s, 3600)
    m, s = divmod(resto, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# Comprobacion de actualizaciones (con uv)
# ---------------------------------------------------------------------------


def _nombre_normalizado(s: str) -> str:
    return s.strip().lower().replace("_", "-")


def paquetes_de_requirements(ruta: Path) -> set[str]:
    """Extrae los nombres de paquete de un requirements.txt (sin versiones)."""
    nombres: set[str] = set()
    if not ruta.exists():
        return nombres
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.split("#", 1)[0].strip()
        if not linea or linea.startswith("-"):
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

    objetivo = paquetes_de_requirements(ruta_requirements)
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
            check=False,
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
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("La actualizacion excedio el tiempo limite.")
        return

    if up.returncode == 0:
        log.info("Paquetes actualizados. Reinicia el programa para usarlos.")
    else:
        log.warning("Fallo la actualizacion: %s", up.stderr.strip())


# ---------------------------------------------------------------------------
# Opciones de descarga
# ---------------------------------------------------------------------------


@dataclass
class OpcionesVideo:
    """Parametros de descarga de video."""

    calidad: str = "best"
    metadatos: bool = True  # embeber metadatos y miniatura en el MP4

    def postprocessors(self, ffmpeg: bool) -> list[dict]:
        if not (self.metadatos and ffmpeg):
            return []
        return [
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "EmbedThumbnail", "already_have_thumbnail": False},
        ]


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
            # IMPORTANTE: los metadatos van ANTES que la caratula.
            # En Opus/Ogg la caratula la inserta mutagen como bloque de imagen;
            # si FFmpegMetadata se ejecuta despues, FFmpeg falla al re-multiplexar
            # el archivo que ya contiene ese bloque -> "Conversion failed!".
            pps.append({"key": "FFmpegMetadata", "add_metadata": True})
            pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
        return pps


def formato_video(calidad: str) -> str:
    """Construye el selector de formato de yt-dlp segun la calidad pedida."""
    if calidad == "best":
        return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
    if calidad == "worst":
        return "worstvideo+worstaudio/worst"
    altura = calidad.rstrip("p")
    return (
        f"bestvideo[height<={altura}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={altura}]+bestaudio/best[height<={altura}]/best"
    )


# ---------------------------------------------------------------------------
# Progreso y cancelacion
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Progreso:
    """Instantanea del avance enviada a las interfaces."""

    fase: str  # "descarga" | "postproceso" | "finalizado"
    porcentaje: float | None  # 0-100, o None si es indeterminado
    texto: str  # descripcion legible


ProgresoCallback = Callable[[Progreso], None]

# Nombres legibles de los postprocesadores de yt-dlp. Las claves son las que
# yt-dlp pone en el hook (``pp_key()``: sin el prefijo "FFmpeg" ni el sufijo "PP").
_NOMBRES_PP = {
    "ExtractAudio": "Convirtiendo audio...",
    "Metadata": "Escribiendo metadatos...",
    "EmbedThumbnail": "Embebiendo caratula...",
    "Merger": "Fusionando video y audio...",
    "VideoRemuxer": "Remultiplexando video...",
    "VideoConvertor": "Convirtiendo video...",
    "FixupM3u8": "Reparando contenedor...",
    "FixupM4a": "Reparando contenedor...",
}


class _Seguimiento:
    """Hooks de yt-dlp: reenvia el progreso y aplica la cancelacion."""

    def __init__(
        self, progreso: ProgresoCallback | None, cancelar: threading.Event | None
    ) -> None:
        self._progreso = progreso
        self._cancelar = cancelar
        self.ruta_final: Path | None = None
        self._temporales: set[str] = set()
        self._ultimo_pp: str | None = None

    # -- utilidades --------------------------------------------------------

    def _emitir(self, fase: str, pct: float | None, texto: str) -> None:
        if self._progreso is not None:
            self._progreso(Progreso(fase, pct, texto))

    def comprobar_cancelacion(self) -> None:
        if self._cancelar is not None and self._cancelar.is_set():
            raise DownloadCancelled("Cancelado por el usuario")

    def limpiar_temporales(self) -> None:
        """Borra los .part (y su .ytdl de reanudacion) que dejo una cancelacion."""
        for ruta in self._temporales:
            if not ruta.endswith(".part"):
                continue
            # yt-dlp guarda el estado de fragmentos en "<archivo final>.ytdl".
            for candidato in (ruta, ruta[: -len(".part")] + ".ytdl"):
                try:
                    os.remove(candidato)
                except OSError:
                    pass

    # -- hooks -------------------------------------------------------------

    def hook_descarga(self, d: dict) -> None:
        self.comprobar_cancelacion()
        estado = d.get("status")
        if estado == "downloading":
            tmp = d.get("tmpfilename")
            if tmp:
                self._temporales.add(tmp)
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            hechos = d.get("downloaded_bytes") or 0
            pct = hechos / total * 100 if total else None
            partes = [f"{pct:.1f}%" if pct is not None else "?%"]
            if total:
                partes.append(f"de {format_bytes(total)}")
            vel = d.get("speed")
            if vel:
                partes.append(f"a {format_bytes(vel)}/s")
            eta = d.get("eta")
            if eta:
                partes.append(f"(quedan {fmt_duracion(eta)})")
            self._emitir("descarga", pct, "Descargando " + " ".join(partes))
        elif estado == "finished":
            self._emitir("descarga", 100.0, "Descarga completada, procesando...")
        elif estado == "error":
            self._emitir("descarga", None, "Error en la descarga")

    def hook_postproceso(self, d: dict) -> None:
        # yt-dlp notifica cada postprocesador dos veces; solo emitimos la primera.
        nombre = d.get("postprocessor", "")
        if d.get("status") != "started" or nombre == self._ultimo_pp:
            return
        self._ultimo_pp = nombre
        texto = _NOMBRES_PP.get(nombre)
        if texto:
            self._emitir("postproceso", None, texto)

    def post_hook(self, ruta: str) -> None:
        self.ruta_final = Path(ruta)


def _opciones_comunes(seg: _Seguimiento) -> dict:
    """Opciones base. La salida se enruta al logging del programa."""
    return {
        "quiet": True,
        "no_warnings": False,
        "ignoreerrors": False,
        "noprogress": True,
        "noplaylist": True,
        "logger": _YtdlpLogger(),
        "progress_hooks": [seg.hook_descarga],
        "postprocessor_hooks": [seg.hook_postproceso],
        "post_hooks": [seg.post_hook],
    }


# ---------------------------------------------------------------------------
# Descargas
# ---------------------------------------------------------------------------


def descargar_video(
    url: str,
    carpeta: Path,
    video: OpcionesVideo | None = None,
    progreso: ProgresoCallback | None = None,
    cancelar: threading.Event | None = None,
) -> Path | None:
    """
    Descarga un video individual fusionado a MP4.

    Devuelve la ruta del archivo final, o None si fallo o se cancelo.
    """
    video = video or OpcionesVideo()
    ffmpeg = verificar_ffmpeg()
    if not ffmpeg:
        log.warning(
            "Sin FFmpeg no se pueden fusionar video y audio; "
            "se descargara el mejor formato ya combinado."
        )
    seg = _Seguimiento(progreso, cancelar)
    opciones = _opciones_comunes(seg) | {
        "format": formato_video(video.calidad) if ffmpeg else "best",
        "outtmpl": str(carpeta / "%(title)s.%(ext)s"),
        "merge_output_format": "mp4",
        "postprocessors": video.postprocessors(ffmpeg),
        "writethumbnail": bool(video.metadatos and ffmpeg),
    }

    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            log.info("Obteniendo informacion del video...")
            info = ydl.extract_info(url, download=False)
            log.info("Titulo: %s", info.get("title", "Sin titulo"))
            log.info("Duracion: %s", fmt_duracion(info.get("duration")))
            log.info("Descargando en calidad: %s", video.calidad)
            seg.comprobar_cancelacion()
            # Reutiliza la informacion ya extraida (evita una segunda consulta).
            ydl.process_ie_result(info, download=True)
    except DownloadCancelled:
        seg.limpiar_temporales()
        log.info("Descarga cancelada por el usuario.")
        return None
    except DownloadError as e:
        if "format" not in str(e).lower():
            log.error("No se pudo descargar: %s", e)
            return None
        log.warning("Formato no disponible, reintentando con 'best'...")
        opciones["format"] = "best"
        try:
            with yt_dlp.YoutubeDL(opciones) as ydl:
                ydl.download([url])
        except DownloadCancelled:
            seg.limpiar_temporales()
            log.info("Descarga cancelada por el usuario.")
            return None
        except Exception as e2:
            log.error("No se pudo descargar: %s", e2)
            return None
    except Exception as e:
        log.error("Error inesperado: %s", e)
        return None

    _informar_resultado(seg, progreso)
    return seg.ruta_final


def descargar_audio(
    url: str,
    carpeta: Path,
    audio: OpcionesAudio | None = None,
    progreso: ProgresoCallback | None = None,
    cancelar: threading.Event | None = None,
) -> Path | None:
    """
    Descarga solo el audio de un video individual.

    Devuelve la ruta del archivo final, o None si fallo o se cancelo.
    """
    audio = audio or OpcionesAudio()
    ffmpeg = verificar_ffmpeg()
    if not ffmpeg:
        log.warning(
            "Sin FFmpeg no se puede convertir ni embeber caratula; "
            "se descargara el audio original sin procesar."
        )

    seg = _Seguimiento(progreso, cancelar)
    opciones = _opciones_comunes(seg) | {
        "format": "bestaudio/best",
        "outtmpl": str(carpeta / "%(title)s.%(ext)s"),
    }
    if ffmpeg:
        opciones["postprocessors"] = audio.postprocessors(ffmpeg)
        opciones["writethumbnail"] = audio.caratula

    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            destino = audio.codec.upper() if ffmpeg else "original"
            log.info("Descargando audio (%s)...", destino)
            ydl.download([url])
    except DownloadCancelled:
        seg.limpiar_temporales()
        log.info("Descarga cancelada por el usuario.")
        return None
    except Exception as e:
        log.error("No se pudo descargar el audio: %s", e)
        return None

    _informar_resultado(seg, progreso)
    return seg.ruta_final


def _informar_resultado(seg: _Seguimiento, progreso: ProgresoCallback | None) -> None:
    if seg.ruta_final is not None:
        log.info("Guardado en: %s", seg.ruta_final)
    else:
        log.info("Descarga completada.")
    if progreso is not None:
        progreso(Progreso("finalizado", 100.0, "Completado"))


# ---------------------------------------------------------------------------
# Informacion (distingue video vs audio; sin playlists)
# ---------------------------------------------------------------------------


def es_solo_audio(info: dict) -> bool:
    """
    True si ningun formato contiene pista de video.

    Solo se afirma cuando al menos un formato declara explicitamente
    vcodec="none"; un vcodec desconocido (None, tipico del extractor generico)
    no permite concluir nada.
    """
    formatos = info.get("formats") or []
    if not formatos:
        return False
    for f in formatos:
        if f.get("vcodec") not in (None, "none") or f.get("height"):
            return False
    return any(f.get("vcodec") == "none" for f in formatos)


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


def _fmt_fecha(yyyymmdd: str | None) -> str:
    if not yyyymmdd or len(yyyymmdd) != 8:
        return "N/A"
    return f"{yyyymmdd[6:8]}/{yyyymmdd[4:6]}/{yyyymmdd[0:4]}"


def _fmt_entero(valor) -> str:
    return f"{valor:,}".replace(",", ".") if isinstance(valor, int) else "N/A"


def formatear_info(info: dict) -> str:
    """Devuelve un bloque de texto legible con los datos del elemento."""
    tipo = "Audio (sin video)" if es_solo_audio(info) else "Video"

    lineas = [
        f"Tipo:      {tipo}",
        f"Titulo:    {info.get('title', 'N/A')}",
        f"Duracion:  {fmt_duracion(info.get('duration'))}",
        f"Canal:     {info.get('uploader') or info.get('channel') or 'N/A'}",
        f"Subido:    {_fmt_fecha(info.get('upload_date'))}",
        f"Vistas:    {_fmt_entero(info.get('view_count'))}",
        f"Me gusta:  {_fmt_entero(info.get('like_count'))}",
        f"Sitio:     {info.get('extractor', 'N/A')}",
    ]
    if tipo == "Video":
        alturas = sorted(
            {f.get("height") for f in (info.get("formats") or []) if f.get("height")}
        )
        if alturas:
            lineas.append(f"Maxima:    {alturas[-1]}p")
            lineas.append("Calidades: " + ", ".join(f"{h}p" for h in reversed(alturas)))
    tam = info.get("filesize") or info.get("filesize_approx")
    if tam:
        lineas.append(f"Tamano:    ~{format_bytes(tam)} (mejor formato)")
    return "\n".join(lineas)


def mostrar_info(url: str) -> dict | None:
    info = obtener_info(url)
    if info is None:
        return None
    for linea in formatear_info(info).splitlines():
        log.info(linea)
    return info
