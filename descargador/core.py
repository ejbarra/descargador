"""
Nucleo del descargador: configuracion, logging, descargas e informacion.

Este modulo no depende de ninguna interfaz (ni Qt ni consola). Las interfaces
reciben el progreso mediante un callback y pueden cancelar una descarga en
curso con un ``threading.Event``.

Las funciones de descarga trabajan sobre un video individual salvo que se pida
``lista=True``: en ese caso, si la URL corresponde a una lista de reproduccion,
se descarga la lista completa en una subcarpeta propia (ver la seccion
"Listas de reproduccion").
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
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    import yt_dlp
    from yt_dlp.utils import (
        DownloadCancelled,
        DownloadError,
        format_bytes,
        sanitize_filename,
    )
except ImportError:  # pragma: no cover - solo informa al usuario
    print("[ERROR] yt-dlp no esta instalado.")
    print("        Instalelo con: uv pip install -r requirements.txt")
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
        log.info("Paquetes actualizados. Reinicie el programa para usarlos.")
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
    # Solo en modo lista: posicion del elemento en curso y total de la lista.
    # El porcentaje sigue siendo el del archivo en curso.
    elemento: int | None = None
    total: int | None = None


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
    """
    Hooks de yt-dlp: reenvia el progreso y aplica la cancelacion.

    Con ``en_lista=True`` el texto de progreso se antepone con la posicion del
    elemento en curso ("Video 3 de 25 - ...") y se acumulan en ``rutas`` los
    archivos terminados de la lista.
    """

    def __init__(
        self,
        progreso: ProgresoCallback | None,
        cancelar: threading.Event | None,
        en_lista: bool = False,
    ) -> None:
        self._progreso = progreso
        self._cancelar = cancelar
        self._en_lista = en_lista
        self.ruta_final: Path | None = None
        self.rutas: list[Path] = []
        self._temporales: set[str] = set()
        self._ultimo_pp: str | None = None

    # -- utilidades --------------------------------------------------------

    def _posicion(self, d: dict) -> tuple[int | None, int | None]:
        """Posicion del elemento y total de la lista segun el info_dict del hook."""
        if not self._en_lista:
            return None, None
        info = d.get("info_dict") or {}
        indice = info.get("playlist_index")
        total = info.get("n_entries") or info.get("playlist_count")
        return (
            indice if isinstance(indice, int) else None,
            total if isinstance(total, int) else None,
        )

    def _emitir(
        self, fase: str, pct: float | None, texto: str, d: dict | None = None
    ) -> None:
        if self._progreso is None:
            return
        elemento, total = self._posicion(d or {})
        if elemento is not None:
            prefijo = f"Video {elemento} de {total}" if total else f"Video {elemento}"
            texto = f"{prefijo} - {texto}"
        self._progreso(Progreso(fase, pct, texto, elemento, total))

    def comprobar_cancelacion(self) -> None:
        if self._cancelar is not None and self._cancelar.is_set():
            raise DownloadCancelled("Cancelado por el usuario")

    def filtro_cancelacion(self, _info: dict, *, incomplete: bool = False) -> None:
        """
        ``match_filter`` de yt-dlp: se evalua antes de cada elemento de la lista.

        Permite cancelar entre un video y el siguiente (durante la extraccion no
        hay hooks de progreso). Devolver None significa "descargar".
        """
        self.comprobar_cancelacion()

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
            # Un archivo nuevo reinicia la deduplicacion de postprocesadores.
            self._ultimo_pp = None
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
            self._emitir("descarga", pct, "Descargando " + " ".join(partes), d)
        elif estado == "finished":
            self._emitir("descarga", 100.0, "Descarga completada, procesando...", d)
        elif estado == "error":
            self._emitir("descarga", None, "Error en la descarga", d)

    def hook_postproceso(self, d: dict) -> None:
        # yt-dlp notifica cada postprocesador dos veces; solo emitimos la primera.
        nombre = d.get("postprocessor", "")
        if d.get("status") != "started" or nombre == self._ultimo_pp:
            return
        self._ultimo_pp = nombre
        texto = _NOMBRES_PP.get(nombre)
        if texto:
            self._emitir("postproceso", None, texto, d)

    def post_hook(self, ruta: str) -> None:
        # yt-dlp lo llama una vez por video, tras terminar todo el postproceso.
        self.ruta_final = Path(ruta)
        self.rutas.append(self.ruta_final)


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
# Listas de reproduccion
# ---------------------------------------------------------------------------

# Archivo oculto que identifica la subcarpeta de una lista (id, titulo, URL).
ARCHIVO_LISTA = ".descargador_lista.json"

# Registro de descargas (``download_archive`` de yt-dlp) por modo. Son archivos
# separados para que bajar la lista en audio no omita los videos, ni al reves.
ARCHIVOS_REGISTRO = {
    "video": ".descargas_video.txt",
    "audio": ".descargas_audio.txt",
}

# Nombre de cada archivo de la lista: la posicion conserva el orden original.
PLANTILLA_LISTA = "%(playlist_index)03d - %(title)s.%(ext)s"

# Pausa aleatoria (segundos) antes de cada descarga de la lista, para reducir
# los errores 429 (demasiadas solicitudes) de YouTube.
PAUSA_LISTA = (1, 3)

MENSAJE_VIDEO_INDIVIDUAL = (
    "La URL corresponde a un solo video, no a una lista; "
    "se descargara de forma individual."
)


@dataclass(frozen=True)
class EntradaLista:
    """Un elemento de la lista tal como lo devuelve la extraccion ligera."""

    posicion: int  # 1-based, coincide con %(playlist_index)s
    id: str | None
    titulo: str | None

    def descripcion(self) -> str:
        return f"{self.posicion} - {self.titulo or self.id or 'Sin titulo'}"


@dataclass
class InfoLista:
    """Datos de una lista obtenidos con extract_flat (sin descargar)."""

    id: str
    titulo: str
    url: str
    autor: str | None = None
    entradas: list[EntradaLista] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.entradas)


@dataclass
class ResultadoLista:
    """Resultado de descargar una lista completa."""

    carpeta: Path  # subcarpeta de la lista
    titulo: str
    total: int  # elementos de la lista
    rutas: list[Path] = field(default_factory=list)  # archivos terminados ahora
    descargados: int = 0  # descargados en esta ejecucion
    omitidos: int = 0  # ya presentes en el registro antes de empezar
    fallidos: list[EntradaLista] = field(default_factory=list)
    cancelado: bool = False

    @property
    def disponibles(self) -> int:
        """Elementos que quedan en la carpeta: los nuevos mas los ya descargados."""
        return self.descargados + self.omitidos


def extraer_ligero(url: str) -> dict | None:
    """
    Extraccion rapida que no resuelve los elementos de una lista.

    Con ``extract_flat="in_playlist"`` una lista devuelve ``_type="playlist"``
    y sus entradas sin procesar (id, titulo, URL); un video individual se
    extrae de forma normal.
    """
    opciones = {
        "quiet": True,
        "no_warnings": False,
        "noplaylist": False,
        "extract_flat": "in_playlist",
        "logger": _YtdlpLogger(),
    }
    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            log.info("Comprobando si la URL corresponde a una lista...")
            return ydl.extract_info(url, download=False)
    except Exception as e:
        log.error("No se pudo obtener informacion: %s", e)
        return None


def es_lista(info: dict | None) -> bool:
    """True si el resultado de la extraccion ligera es una lista."""
    return bool(info) and info.get("_type") in ("playlist", "multi_video")


def leer_info_lista(info: dict, url: str = "") -> InfoLista:
    """Convierte el resultado de la extraccion ligera en un InfoLista."""
    entradas = [
        EntradaLista(
            posicion=posicion,
            id=e.get("id"),
            titulo=e.get("title"),
        )
        # La posicion cuenta tambien las entradas vacias, igual que yt-dlp.
        for posicion, e in enumerate(info.get("entries") or [], 1)
        if e
    ]
    id_lista = str(info.get("id") or info.get("playlist_id") or url)
    return InfoLista(
        id=id_lista,
        titulo=str(info.get("title") or info.get("playlist_title") or id_lista),
        url=str(info.get("webpage_url") or info.get("original_url") or url),
        autor=info.get("uploader") or info.get("channel") or None,
        entradas=entradas,
    )


def nombre_carpeta_lista(titulo: str, id_lista: str = "") -> str:
    """
    Nombre de carpeta seguro para la lista.

    Usa ``sanitize_filename`` de yt-dlp y ademas quita espacios y puntos
    iniciales, para que la carpeta no quede oculta ni con nombre vacio.
    """
    for candidato in (titulo, id_lista):
        nombre = sanitize_filename(candidato or "").strip().lstrip(".").strip()
        if nombre:
            return nombre
    return "lista"


def leer_json_lista(carpeta: Path) -> dict | None:
    """Lee el .descargador_lista.json de una carpeta; None si falta o no es valido."""
    try:
        datos = json.loads((carpeta / ARCHIVO_LISTA).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return datos if isinstance(datos, dict) else None


def resolver_carpeta_lista(destino: Path, lista: InfoLista) -> Path:
    """
    Carpeta donde se guarda la lista. No crea nada; solo decide la ruta.

    Orden de busqueda:
      1. El propio destino, si su .descargador_lista.json tiene el mismo id
         (el usuario eligio la subcarpeta de la lista, quiza ya movida).
      2. Una subcarpeta directa del destino con ese mismo id (sirve aunque se
         haya renombrado o el autor haya cambiado el titulo de la lista).
      3. Una subcarpeta nueva con el titulo saneado de la lista. Si ya existe
         una carpeta con ese nombre que pertenece a otra lista, se agrega el id
         al nombre para no mezclarlas.
    """

    def es_de_la_lista(carpeta: Path) -> bool:
        datos = leer_json_lista(carpeta)
        return datos is not None and datos.get("id") == lista.id

    if es_de_la_lista(destino):
        return destino
    try:
        subcarpetas = sorted(p for p in destino.iterdir() if p.is_dir())
    except OSError:
        subcarpetas = []
    for sub in subcarpetas:
        if es_de_la_lista(sub):
            return sub

    nombre = nombre_carpeta_lista(lista.titulo, lista.id)
    candidata = destino / nombre
    if leer_json_lista(candidata) is not None:  # existe y es de otra lista
        candidata = destino / f"{nombre} [{nombre_carpeta_lista(lista.id)}]"
    return candidata


def preparar_carpeta_lista(carpeta: Path, lista: InfoLista) -> None:
    """Crea la carpeta de la lista y escribe (o actualiza) su .json."""
    carpeta.mkdir(parents=True, exist_ok=True)
    datos = {"id": lista.id, "titulo": lista.titulo, "url": lista.url}
    (carpeta / ARCHIVO_LISTA).write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def archivo_registro(carpeta: Path, modo: str) -> Path:
    """Registro de descargas de la lista para el modo dado ("video" o "audio")."""
    try:
        return carpeta / ARCHIVOS_REGISTRO[modo]
    except KeyError:
        raise ValueError(f"Modo no valido para el registro: {modo!r}") from None


def leer_registro(ruta: Path) -> set[str] | None:
    """
    Ids de video presentes en un registro de yt-dlp ("<extractor> <id>").

    Devuelve un conjunto vacio si el archivo aun no existe y None si no se
    pudo leer.
    """
    if not ruta.exists():
        return set()
    try:
        texto = ruta.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    ids: set[str] = set()
    for linea in texto.splitlines():
        partes = linea.strip().split(None, 1)
        if len(partes) == 2:
            ids.add(partes[1].strip())
    return ids


def resumir_lista(
    entradas: list[EntradaLista],
    antes: set[str] | None,
    despues: set[str] | None,
) -> tuple[list[EntradaLista], list[EntradaLista], list[EntradaLista]]:
    """
    Clasifica las entradas comparando el registro antes y despues de descargar.

    Devuelve (descargadas, omitidas, fallidas). yt-dlp solo anota en el
    registro los videos terminados con exito, de modo que lo que no estaba
    antes ni esta despues no se pudo descargar (borrado, privado, bloqueado o
    con error). Si el registro posterior no se pudo leer, no se puede saber
    que fallo y ``descargadas`` y ``fallidas`` quedan vacias.
    """
    previas = antes or set()
    omitidas = [e for e in entradas if e.id in previas]
    if despues is None:
        return [], omitidas, []
    pendientes = [e for e in entradas if e.id not in previas]
    descargadas = [e for e in pendientes if e.id in despues]
    fallidas = [e for e in pendientes if e.id not in despues]
    return descargadas, omitidas, fallidas


def _escapar_plantilla(texto: str) -> str:
    """Escapa '%' para que una ruta literal no se interprete como plantilla."""
    return texto.replace("%", "%%")


def con_alternativa(formato: str) -> str:
    """
    Agrega "/best" al final del selector si no lo tiene.

    En modo lista la alternativa va dentro de la propia cadena de formato para
    que yt-dlp la resuelva video por video (un reintento externo volveria a
    descargar la lista completa).
    """
    return formato if formato.split("/")[-1] == "best" else f"{formato}/best"


def _opciones_lista(seg: _Seguimiento, carpeta: Path, registro: Path) -> dict:
    """Opciones que se agregan a las comunes al descargar una lista."""
    ruta = _escapar_plantilla(str(carpeta.resolve()))
    return {
        "noplaylist": False,
        # Un video borrado, privado o bloqueado no detiene la lista.
        "ignoreerrors": "only_download",
        "outtmpl": os.path.join(ruta, PLANTILLA_LISTA),
        "download_archive": str(registro.resolve()),
        "sleep_interval": PAUSA_LISTA[0],
        "max_sleep_interval": PAUSA_LISTA[1],
        # Evita la miniatura/descripcion de la propia lista (writethumbnail
        # tambien se aplicaria a la lista y dejaria una imagen suelta).
        "allow_playlist_files": False,
        "match_filter": seg.filtro_cancelacion,
    }


def opciones_video(
    seg: _Seguimiento,
    carpeta: Path,
    video: OpcionesVideo,
    ffmpeg: bool,
    registro: Path | None = None,
) -> dict:
    """
    Opciones de yt-dlp para descargar video.

    Sin ``registro`` se arma la descarga individual (comportamiento de
    siempre); con ``registro`` se arma la descarga de una lista en ``carpeta``.
    """
    opciones = _opciones_comunes(seg) | {
        "format": formato_video(video.calidad) if ffmpeg else "best",
        "outtmpl": str(carpeta / "%(title)s.%(ext)s"),
        "merge_output_format": "mp4",
        "postprocessors": video.postprocessors(ffmpeg),
        "writethumbnail": bool(video.metadatos and ffmpeg),
    }
    if registro is not None:
        opciones |= _opciones_lista(seg, carpeta, registro)
        opciones["format"] = con_alternativa(opciones["format"])
    return opciones


def opciones_audio(
    seg: _Seguimiento,
    carpeta: Path,
    audio: OpcionesAudio,
    ffmpeg: bool,
    registro: Path | None = None,
) -> dict:
    """Opciones de yt-dlp para extraer audio (individual o, con ``registro``, lista)."""
    opciones = _opciones_comunes(seg) | {
        "format": "bestaudio/best",
        "outtmpl": str(carpeta / "%(title)s.%(ext)s"),
    }
    if ffmpeg:
        opciones["postprocessors"] = audio.postprocessors(ffmpeg)
        opciones["writethumbnail"] = audio.caratula
    if registro is not None:
        opciones |= _opciones_lista(seg, carpeta, registro)
        opciones["format"] = con_alternativa(opciones["format"])
    return opciones


ConstructorOpciones = Callable[[_Seguimiento, Path, Path], dict]


def _detectar_lista(url: str) -> dict | None:
    """
    Extraccion ligera para decidir entre lista y video individual.

    Devuelve el resultado si es una lista; None si es un video individual o si
    no se pudo determinar (en ambos casos se sigue con la descarga individual).
    """
    info = extraer_ligero(url)
    if info is None:
        log.warning(
            "No se pudo determinar si la URL es una lista; "
            "se intentara la descarga individual."
        )
        return None
    if es_lista(info):
        return info
    log.info(MENSAJE_VIDEO_INDIVIDUAL)
    return None


def _descargar_lista(
    url: str,
    info: dict,
    destino: Path,
    modo: str,
    construir: ConstructorOpciones,
    progreso: ProgresoCallback | None,
    cancelar: threading.Event | None,
) -> ResultadoLista | None:
    """Descarga todos los elementos de una lista ya detectada."""
    lista = leer_info_lista(info, url)
    carpeta = resolver_carpeta_lista(destino, lista)
    try:
        preparar_carpeta_lista(carpeta, lista)
    except OSError as e:
        log.error("No se pudo preparar la carpeta de la lista: %s", e)
        return None

    registro = archivo_registro(carpeta, modo)
    antes = leer_registro(registro)
    log.info("Lista: %s (%d videos)", lista.titulo, lista.total)
    log.info("Carpeta de la lista: %s", carpeta)
    if antes is None:
        log.warning(
            "No se pudo leer el registro %s; no se sabe que videos ya estan "
            "descargados.",
            registro,
        )
    else:
        ya = sum(1 for e in lista.entradas if e.id in antes)
        log.info(
            "Se omitiran %d de %d videos por estar ya descargados (registro %s).",
            ya,
            lista.total,
            registro.name,
        )

    seg = _Seguimiento(progreso, cancelar, en_lista=True)
    resultado = ResultadoLista(carpeta=carpeta, titulo=lista.titulo, total=lista.total)
    try:
        with yt_dlp.YoutubeDL(construir(seg, carpeta, registro)) as ydl:
            seg.comprobar_cancelacion()
            # Reutiliza la extraccion ligera: yt-dlp resuelve cada entrada al
            # descargarla, sin volver a pedir la lista.
            ydl.process_ie_result(info, download=True)
    except DownloadCancelled:
        seg.limpiar_temporales()
        log.info("Descarga de la lista cancelada por el usuario.")
        resultado.cancelado = True
    except Exception as e:
        log.error("No se pudo descargar la lista: %s", e)
        return None

    despues = leer_registro(registro)
    descargadas, omitidas, fallidas = resumir_lista(lista.entradas, antes, despues)
    resultado.rutas = list(seg.rutas)
    resultado.omitidos = len(omitidas)
    if despues is None:
        log.warning("No se pudo leer el registro %s al terminar.", registro)
        resultado.descargados = len(seg.rutas)
    else:
        resultado.descargados = len(descargadas)
    # Tras una cancelacion lo pendiente no fallo: simplemente no se intento.
    if not resultado.cancelado:
        resultado.fallidos = fallidas

    for e in resultado.fallidos:
        log.warning("No disponible o fallido: %s", e.descripcion())
    log.info(
        "Resumen de la lista%s: %d descargados, %d omitidos por el registro, "
        "%d no disponibles o fallidos (total: %d).",
        " (cancelada)" if resultado.cancelado else "",
        resultado.descargados,
        resultado.omitidos,
        len(resultado.fallidos),
        resultado.total,
    )
    log.info("Carpeta de la lista: %s", carpeta)
    if progreso is not None and not resultado.cancelado:
        progreso(Progreso("finalizado", 100.0, "Completado"))
    return resultado


# ---------------------------------------------------------------------------
# Descargas
# ---------------------------------------------------------------------------


def descargar_video(
    url: str,
    carpeta: Path,
    video: OpcionesVideo | None = None,
    progreso: ProgresoCallback | None = None,
    cancelar: threading.Event | None = None,
    lista: bool = False,
) -> Path | ResultadoLista | None:
    """
    Descarga un video fusionado a MP4.

    Por defecto descarga solo el video, aunque la URL traiga ``list=``, y
    devuelve la ruta del archivo final. Con ``lista=True``, si la URL
    corresponde a una lista se descarga completa y se devuelve un
    ResultadoLista. Devuelve None si fallo o se cancelo.
    """
    video = video or OpcionesVideo()
    ffmpeg = verificar_ffmpeg()
    if not ffmpeg:
        log.warning(
            "Sin FFmpeg no se pueden fusionar video y audio; "
            "se descargara el mejor formato ya combinado."
        )

    if lista:
        info_lista = _detectar_lista(url)
        if info_lista is not None:
            log.info("Descargando en calidad: %s", video.calidad)
            return _descargar_lista(
                url,
                info_lista,
                carpeta,
                "video",
                lambda s, c, r: opciones_video(s, c, video, ffmpeg, r),
                progreso,
                cancelar,
            )

    seg = _Seguimiento(progreso, cancelar)
    opciones = opciones_video(seg, carpeta, video, ffmpeg)

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
    lista: bool = False,
) -> Path | ResultadoLista | None:
    """
    Descarga solo el audio.

    Igual que descargar_video(): individual por defecto (devuelve la ruta del
    archivo final) y, con ``lista=True`` y una URL de lista, la lista completa
    (devuelve un ResultadoLista). Devuelve None si fallo o se cancelo.
    """
    audio = audio or OpcionesAudio()
    ffmpeg = verificar_ffmpeg()
    if not ffmpeg:
        log.warning(
            "Sin FFmpeg no se puede convertir ni embeber caratula; "
            "se descargara el audio original sin procesar."
        )
    destino = audio.codec.upper() if ffmpeg else "original"

    if lista:
        info_lista = _detectar_lista(url)
        if info_lista is not None:
            log.info("Descargando audio (%s)...", destino)
            return _descargar_lista(
                url,
                info_lista,
                carpeta,
                "audio",
                lambda s, c, r: opciones_audio(s, c, audio, ffmpeg, r),
                progreso,
                cancelar,
            )

    seg = _Seguimiento(progreso, cancelar)
    opciones = opciones_audio(seg, carpeta, audio, ffmpeg)

    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
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
# Informacion (video, audio o, si se pide, lista)
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


def formatear_info_lista(lista: InfoLista) -> str:
    """Bloque de texto con los datos de una lista y sus titulos numerados."""
    lineas = [
        "Tipo:      Lista de reproduccion",
        f"Titulo:    {lista.titulo}",
    ]
    if lista.autor:
        lineas.append(f"Autor:     {lista.autor}")
    lineas.append(f"Videos:    {lista.total}")
    ancho = len(str(lista.entradas[-1].posicion)) if lista.entradas else 1
    lineas.extend(
        f"  {e.posicion:>{ancho}}. {e.titulo or e.id or 'Sin titulo'}"
        for e in lista.entradas
    )
    return "\n".join(lineas)


def _registrar_bloque(texto: str) -> None:
    for linea in texto.splitlines():
        log.info(linea)


def mostrar_info(url: str, lista: bool = False) -> dict | None:
    """
    Muestra en el registro la informacion de la URL, sin descargar.

    Con ``lista=True`` y una URL de lista se muestran sus datos y la lista
    numerada de titulos (extraccion ligera); con un video individual se
    muestra la informacion habitual precedida de un aviso.
    """
    if lista:
        info = extraer_ligero(url)
        if info is not None:
            if es_lista(info):
                _registrar_bloque(formatear_info_lista(leer_info_lista(info, url)))
            else:
                log.info(MENSAJE_VIDEO_INDIVIDUAL)
                _registrar_bloque(formatear_info(info))
            return info
        log.warning("Se intentara obtener la informacion como video individual.")

    info = obtener_info(url)
    if info is None:
        return None
    _registrar_bloque(formatear_info(info))
    return info
