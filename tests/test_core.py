"""Pruebas unitarias del nucleo (sin red ni FFmpeg)."""

from __future__ import annotations

import copy
import json
import logging
import threading
from pathlib import Path
from typing import ClassVar, Self

import pytest
from yt_dlp.utils import DownloadCancelled

from descargador import core
from descargador.core import (
    ARCHIVO_LISTA,
    MENSAJE_VIDEO_INDIVIDUAL,
    PLANTILLA_LISTA,
    EntradaLista,
    InfoLista,
    OpcionesAudio,
    OpcionesVideo,
    Progreso,
    ResultadoLista,
    _Seguimiento,
    archivo_registro,
    con_alternativa,
    es_lista,
    es_solo_audio,
    fmt_duracion,
    formatear_info,
    formatear_info_lista,
    formato_video,
    leer_info_lista,
    leer_json_lista,
    leer_registro,
    nombre_carpeta_lista,
    normalizar_url,
    opciones_audio,
    opciones_video,
    paquetes_de_requirements,
    preparar_carpeta_lista,
    resolver_carpeta_lista,
    resumir_lista,
)

# -- utilidades ---------------------------------------------------------------


@pytest.mark.parametrize(
    "segundos, esperado",
    [
        (None, "N/A"),
        (0, "N/A"),
        (59, "0:59"),
        (61, "1:01"),
        (3600, "1:00:00"),
        (3725.9, "1:02:05"),
    ],
)
def test_fmt_duracion(segundos, esperado):
    assert fmt_duracion(segundos) == esperado


@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("  https://youtu.be/abc  ", "https://youtu.be/abc"),
        ("youtu.be/abc", "https://youtu.be/abc"),
        ("http://example.com", "http://example.com"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalizar_url(entrada, esperado):
    assert normalizar_url(entrada) == esperado


def test_formato_video():
    assert formato_video("best").startswith("bestvideo[ext=mp4]+bestaudio[ext=m4a]")
    assert formato_video("worst") == "worstvideo+worstaudio/worst"
    sel = formato_video("720p")
    assert "height<=720" in sel and "720p" not in sel


def test_paquetes_de_requirements(tmp_path: Path):
    req = tmp_path / "requirements.txt"
    req.write_text(
        "# comentario\n"
        "yt-dlp>=2025.1.1   # principal\n"
        "PySide6_Essentials>=6.7\n"
        "-r otro.txt\n"
        "\n"
        "mutagen\n",
        encoding="utf-8",
    )
    assert paquetes_de_requirements(req) == {"yt-dlp", "pyside6-essentials", "mutagen"}
    assert paquetes_de_requirements(tmp_path / "no_existe.txt") == set()


def test_resolver_carpeta_relativa_y_absoluta(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(core, "BASE_DESCARGAS", tmp_path)
    assert core.resolver_carpeta("", "musica") == tmp_path / "musica"
    assert (tmp_path / "musica").is_dir()
    absoluta = tmp_path / "otra"
    assert core.resolver_carpeta(str(absoluta), "musica") == absoluta
    assert absoluta.is_dir()


# -- opciones ------------------------------------------------------------------


def test_postprocessors_audio_orden_metadatos_antes_de_caratula():
    keys = [
        pp["key"]
        for pp in OpcionesAudio(codec="opus", caratula=True).postprocessors(True)
    ]
    assert keys == ["FFmpegExtractAudio", "FFmpegMetadata", "EmbedThumbnail"]


def test_postprocessors_audio_sin_ffmpeg_o_sin_caratula():
    assert [pp["key"] for pp in OpcionesAudio(caratula=True).postprocessors(False)] == [
        "FFmpegExtractAudio"
    ]
    assert [pp["key"] for pp in OpcionesAudio(caratula=False).postprocessors(True)] == [
        "FFmpegExtractAudio"
    ]


def test_postprocessors_video():
    assert OpcionesVideo(metadatos=False).postprocessors(True) == []
    assert OpcionesVideo(metadatos=True).postprocessors(False) == []
    keys = [pp["key"] for pp in OpcionesVideo(metadatos=True).postprocessors(True)]
    assert keys == ["FFmpegMetadata", "EmbedThumbnail"]


# -- informacion ---------------------------------------------------------------


def test_es_solo_audio():
    assert es_solo_audio({"formats": [{"vcodec": "none"}, {"vcodec": None}]})
    assert not es_solo_audio({"formats": [{"vcodec": "none"}, {"vcodec": "avc1"}]})
    # vcodec desconocido (extractor generico): no se afirma que sea solo audio
    assert not es_solo_audio({"formats": [{"vcodec": None}]})
    assert not es_solo_audio({"formats": [{"vcodec": None, "height": 720}]})
    assert not es_solo_audio({"formats": []})
    assert not es_solo_audio({})


def test_formatear_info_video():
    info = {
        "title": "Prueba",
        "duration": 65,
        "uploader": "Canal",
        "upload_date": "20240131",
        "view_count": 1234567,
        "like_count": None,
        "extractor": "youtube",
        "formats": [
            {"height": 360, "vcodec": "avc1"},
            {"height": 1080, "vcodec": "avc1"},
        ],
        "filesize_approx": 10 * 1024 * 1024,
    }
    texto = formatear_info(info)
    assert "Tipo:      Video" in texto
    assert "Duracion:  1:05" in texto
    assert "Subido:    31/01/2024" in texto
    assert "Vistas:    1.234.567" in texto
    assert "Me gusta:  N/A" in texto
    assert "Maxima:    1080p" in texto
    assert "Calidades: 1080p, 360p" in texto
    assert "Tamano:" in texto


def test_formatear_info_audio():
    texto = formatear_info({"title": "Solo audio", "formats": [{"vcodec": "none"}]})
    assert "Audio (sin video)" in texto
    assert "Maxima" not in texto


# -- seguimiento: progreso y cancelacion --------------------------------------


def test_seguimiento_progreso_descarga():
    recibidos: list[Progreso] = []
    seg = _Seguimiento(recibidos.append, None)
    seg.hook_descarga(
        {
            "status": "downloading",
            "downloaded_bytes": 50,
            "total_bytes": 200,
            "speed": 1024.0,
            "eta": 3,
            "tmpfilename": "x.mp4.part",
        }
    )
    seg.hook_descarga({"status": "finished"})
    # yt-dlp notifica cada postprocesador por duplicado
    seg.hook_postproceso({"status": "started", "postprocessor": "ExtractAudio"})
    seg.hook_postproceso({"status": "started", "postprocessor": "ExtractAudio"})
    seg.hook_postproceso({"status": "finished", "postprocessor": "ExtractAudio"})
    seg.hook_postproceso({"status": "started", "postprocessor": "MoveFiles"})
    seg.post_hook("/tmp/final.mp3")

    assert recibidos[0].fase == "descarga" and recibidos[0].porcentaje == 25.0
    assert "25.0%" in recibidos[0].texto and "/s" in recibidos[0].texto
    assert recibidos[1].porcentaje == 100.0
    assert recibidos[2].fase == "postproceso" and recibidos[2].porcentaje is None
    assert recibidos[2].texto == "Convirtiendo audio..."
    assert len(recibidos) == 3  # ni el duplicado, ni "finished", ni MoveFiles emiten
    assert seg.ruta_final == Path("/tmp/final.mp3")


def test_seguimiento_cancelacion_y_limpieza(tmp_path: Path):
    cancelar = threading.Event()
    seg = _Seguimiento(None, cancelar)
    parcial = tmp_path / "video.mp4.part"
    parcial.write_bytes(b"x")
    ytdl = tmp_path / "video.mp4.ytdl"
    ytdl.write_bytes(b"x")
    intacto = tmp_path / "video.mp4"
    intacto.write_bytes(b"x")

    seg.hook_descarga({"status": "downloading", "tmpfilename": str(parcial)})
    cancelar.set()
    with pytest.raises(DownloadCancelled):
        seg.hook_descarga({"status": "downloading", "tmpfilename": str(parcial)})
    seg.limpiar_temporales()

    assert not parcial.exists()
    assert not ytdl.exists()
    assert intacto.exists()


# -- listas de reproduccion: utilidades ---------------------------------------


def _info_lista(n: int = 3, id_lista: str = "PL123", titulo: str = "Mi lista") -> dict:
    """Resultado tipico de una extraccion ligera (extract_flat) de una lista."""
    return {
        "_type": "playlist",
        "id": id_lista,
        "title": titulo,
        "uploader": "Autor",
        "webpage_url": f"https://www.youtube.com/playlist?list={id_lista}",
        "entries": [
            {"_type": "url", "ie_key": "Youtube", "id": f"v{i}", "title": f"Tema {i}"}
            for i in range(1, n + 1)
        ],
    }


def _lista(id_lista: str = "PL123", titulo: str = "Mi lista") -> InfoLista:
    return leer_info_lista(_info_lista(id_lista=id_lista, titulo=titulo))


def test_es_lista():
    assert es_lista({"_type": "playlist"})
    assert es_lista({"_type": "multi_video"})
    assert not es_lista({"_type": "video", "id": "x"})
    assert not es_lista({"id": "x"})  # sin _type: video individual
    assert not es_lista(None)


def test_leer_info_lista():
    info = _info_lista()
    info["entries"].insert(1, None)  # entrada vacia: conserva la numeracion
    lista = leer_info_lista(info)
    assert (lista.id, lista.titulo, lista.autor) == ("PL123", "Mi lista", "Autor")
    assert lista.url.endswith("list=PL123")
    assert [e.posicion for e in lista.entradas] == [1, 3, 4]
    assert lista.entradas[0] == EntradaLista(1, "v1", "Tema 1")
    assert lista.total == 3


@pytest.mark.parametrize(
    "titulo, id_lista, esperado",
    [
        ("Mi lista", "PL1", "Mi lista"),
        ("  .oculta  ", "PL1", "oculta"),
        ("   ", "PL1", "PL1"),
        ("", "", "lista"),
    ],
)
def test_nombre_carpeta_lista(titulo, id_lista, esperado):
    assert nombre_carpeta_lista(titulo, id_lista) == esperado


def test_nombre_carpeta_lista_sanea_caracteres_de_ruta():
    nombre = nombre_carpeta_lista("Rock: 80/90 <mejores>?")
    assert "/" not in nombre and ":" not in nombre and "?" not in nombre
    assert nombre.startswith("Rock")


def test_archivo_registro_segun_modo(tmp_path: Path):
    assert archivo_registro(tmp_path, "video") == tmp_path / ".descargas_video.txt"
    assert archivo_registro(tmp_path, "audio") == tmp_path / ".descargas_audio.txt"
    with pytest.raises(ValueError):
        archivo_registro(tmp_path, "info")


def test_leer_registro(tmp_path: Path):
    ruta = tmp_path / ".descargas_video.txt"
    assert leer_registro(ruta) == set()  # aun no existe
    ruta.write_text("youtube abc\n\nyoutube def\nbasura\n", encoding="utf-8")
    assert leer_registro(ruta) == {"abc", "def"}
    assert leer_registro(tmp_path) is None  # existe pero no se puede leer


def test_resolver_carpeta_lista_carpeta_nueva(tmp_path: Path):
    carpeta = resolver_carpeta_lista(tmp_path, _lista(titulo="Rock: 80/90"))
    assert carpeta.parent == tmp_path
    assert carpeta.name == nombre_carpeta_lista("Rock: 80/90")
    assert not carpeta.exists()  # solo decide la ruta; no crea nada


def test_resolver_carpeta_lista_destino_es_la_subcarpeta(tmp_path: Path):
    lista = _lista()
    movida = tmp_path / "otro sitio"
    preparar_carpeta_lista(movida, lista)
    assert resolver_carpeta_lista(movida, lista) == movida


def test_resolver_carpeta_lista_subcarpeta_renombrada(tmp_path: Path):
    lista = _lista()
    (tmp_path / "Mi lista").mkdir()  # carpeta sin .json: no cuenta
    renombrada = tmp_path / "Favoritos"
    preparar_carpeta_lista(renombrada, lista)
    # El titulo cambio en YouTube, pero el id sigue siendo el mismo.
    assert resolver_carpeta_lista(tmp_path, _lista(titulo="Nuevo")) == renombrada


def test_resolver_carpeta_lista_mismo_titulo_otra_lista(tmp_path: Path):
    preparar_carpeta_lista(tmp_path / "Mi lista", _lista(id_lista="PLotra"))
    carpeta = resolver_carpeta_lista(tmp_path, _lista(id_lista="PL123"))
    assert carpeta == tmp_path / "Mi lista [PL123]"


def test_preparar_carpeta_lista_escribe_json(tmp_path: Path):
    carpeta = tmp_path / "Mi lista"
    preparar_carpeta_lista(carpeta, _lista())
    datos = json.loads((carpeta / ARCHIVO_LISTA).read_text(encoding="utf-8"))
    assert datos == {
        "id": "PL123",
        "titulo": "Mi lista",
        "url": "https://www.youtube.com/playlist?list=PL123",
    }
    assert leer_json_lista(carpeta) == datos
    assert leer_json_lista(tmp_path) is None


def test_resumir_lista():
    entradas = [EntradaLista(i, f"v{i}", f"Tema {i}") for i in range(1, 5)]
    descargadas, omitidas, fallidas = resumir_lista(
        entradas, antes={"v1"}, despues={"v1", "v2", "v4"}
    )
    assert [e.id for e in omitidas] == ["v1"]
    assert [e.id for e in descargadas] == ["v2", "v4"]
    assert [e.id for e in fallidas] == ["v3"]
    assert fallidas[0].descripcion() == "3 - Tema 3"
    # Registro posterior ilegible: no se puede saber que fallo.
    assert resumir_lista(entradas, {"v1"}, None) == ([], [entradas[0]], [])
    # Registro previo ilegible: nada se cuenta como omitido.
    assert resumir_lista(entradas, None, {"v1"})[1] == []


def test_descripcion_entrada_sin_titulo():
    assert EntradaLista(7, "abc", None).descripcion() == "7 - abc"
    assert EntradaLista(7, None, None).descripcion() == "7 - Sin titulo"


def test_con_alternativa():
    assert con_alternativa("bestaudio/best") == "bestaudio/best"
    assert con_alternativa("best") == "best"
    assert con_alternativa("worstvideo+worstaudio/worst") == (
        "worstvideo+worstaudio/worst/best"
    )


# -- listas de reproduccion: armado de opciones --------------------------------


def test_opciones_video_individual(tmp_path: Path):
    seg = _Seguimiento(None, None)
    op = opciones_video(seg, tmp_path, OpcionesVideo(calidad="720p"), True)
    assert op["noplaylist"] is True
    assert op["ignoreerrors"] is False
    assert op["format"] == formato_video("720p")
    assert op["outtmpl"] == str(tmp_path / "%(title)s.%(ext)s")
    for clave in ("download_archive", "sleep_interval", "match_filter"):
        assert clave not in op


def test_opciones_video_con_lista(tmp_path: Path):
    seg = _Seguimiento(None, None, en_lista=True)
    carpeta = tmp_path / "Mi lista"
    registro = carpeta / ".descargas_video.txt"
    op = opciones_video(seg, carpeta, OpcionesVideo(calidad="worst"), True, registro)
    assert op["noplaylist"] is False
    assert op["ignoreerrors"] == "only_download"
    assert op["outtmpl"] == str(carpeta.resolve() / PLANTILLA_LISTA)
    assert Path(op["outtmpl"]).is_absolute()
    assert op["download_archive"] == str(registro.resolve())
    assert (op["sleep_interval"], op["max_sleep_interval"]) == (1, 3)
    assert op["allow_playlist_files"] is False
    assert op["format"].endswith("/best")
    assert op["merge_output_format"] == "mp4"


def test_opciones_audio_con_y_sin_lista(tmp_path: Path):
    seg = _Seguimiento(None, None)
    individual = opciones_audio(seg, tmp_path, OpcionesAudio(), True)
    assert individual["noplaylist"] is True and "download_archive" not in individual
    carpeta = tmp_path / "Exitos 100%"
    registro = carpeta / ".descargas_audio.txt"
    op = opciones_audio(seg, carpeta, OpcionesAudio(), True, registro)
    assert op["format"] == "bestaudio/best"
    assert op["download_archive"].endswith(".descargas_audio.txt")
    # Un '%' en la ruta no debe interpretarse como campo de la plantilla.
    assert "Exitos 100%%" in op["outtmpl"]
    assert op["outtmpl"].endswith(PLANTILLA_LISTA)


# -- listas de reproduccion: progreso y cancelacion ----------------------------


def test_seguimiento_lista_antepone_posicion():
    recibidos: list[Progreso] = []
    seg = _Seguimiento(recibidos.append, None, en_lista=True)
    info = {"playlist_index": 3, "n_entries": 25}
    seg.hook_descarga(
        {
            "status": "downloading",
            "downloaded_bytes": 50,
            "total_bytes": 100,
            "info_dict": info,
        }
    )
    seg.hook_postproceso(
        {"status": "started", "postprocessor": "Merger", "info_dict": info}
    )
    seg.hook_descarga(
        {"status": "finished", "info_dict": {"playlist_index": 4, "playlist_count": 9}}
    )
    assert recibidos[0].texto.startswith("Video 3 de 25 - Descargando 50.0%")
    assert (recibidos[0].elemento, recibidos[0].total) == (3, 25)
    assert recibidos[0].porcentaje == 50.0  # avance del archivo en curso
    assert recibidos[1].texto == "Video 3 de 25 - Fusionando video y audio..."
    assert recibidos[2].texto.startswith("Video 4 de 9 - ")


def test_seguimiento_individual_sin_posicion():
    recibidos: list[Progreso] = []
    seg = _Seguimiento(recibidos.append, None)
    seg.hook_descarga(
        {"status": "finished", "info_dict": {"playlist_index": 3, "n_entries": 5}}
    )
    assert recibidos[0].texto == "Descarga completada, procesando..."
    assert recibidos[0].elemento is None


def test_seguimiento_filtro_cancelacion():
    cancelar = threading.Event()
    seg = _Seguimiento(None, cancelar, en_lista=True)
    assert seg.filtro_cancelacion({"id": "x"}, incomplete=True) is None
    cancelar.set()
    with pytest.raises(DownloadCancelled):
        seg.filtro_cancelacion({"id": "x"}, incomplete=True)


# -- listas de reproduccion: flujo completo con YoutubeDL simulado -------------


class _YDLSimulado:
    """
    Sustituto de yt_dlp.YoutubeDL para las pruebas (sin red).

    ``respuesta`` es lo que devuelve extract_info. Al descargar una lista se
    imitan los efectos de yt-dlp: hooks de progreso, post_hooks y escritura
    del registro (download_archive) para cada entrada que no este en ``fallan``.
    """

    respuesta: ClassVar[dict] = {}
    fallan: ClassVar[set[str]] = set()
    cancelar_tras: ClassVar[int | None] = None
    instancias: ClassVar[list[_YDLSimulado]] = []

    def __init__(self, params: dict) -> None:
        self.params = params
        self.procesados: list[dict] = []
        _YDLSimulado.instancias.append(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False) -> dict:
        return copy.deepcopy(self.respuesta)

    def process_ie_result(self, info: dict, download: bool = True) -> dict:
        self.procesados.append(info)
        if info.get("_type") != "playlist":
            self.params["post_hooks"][0](f"/destino/{info.get('title')}.mp4")
            return info
        archivo = self.params.get("download_archive")
        previos = leer_registro(Path(archivo)) if archivo else set()
        entradas = info["entries"]
        for i, e in enumerate(entradas, 1):
            if previos and e["id"] in previos:
                continue
            if self.cancelar_tras is not None and i > self.cancelar_tras:
                raise DownloadCancelled("Cancelado por el usuario")
            if e["id"] in self.fallan:
                continue  # con ignoreerrors yt-dlp registra el error y sigue
            dato = {"playlist_index": i, "n_entries": len(entradas)}
            for hook in self.params["progress_hooks"]:
                hook({"status": "finished", "info_dict": dato})
            self.params["post_hooks"][0](f"{i:03d} - {e['title']}.mp4")
            if archivo:
                with open(archivo, "a", encoding="utf-8") as f:
                    f.write(f"youtube {e['id']}\n")
        return info

    def download(self, urls: list[str]) -> int:
        # descargar_audio() usa download(): equivale a extraer y procesar.
        self.process_ie_result(self.extract_info(urls[0]), download=True)
        return 0


@pytest.fixture
def ydl_simulado(monkeypatch):
    _YDLSimulado.respuesta = {}
    _YDLSimulado.fallan = set()
    _YDLSimulado.cancelar_tras = None
    _YDLSimulado.instancias = []
    monkeypatch.setattr(core.yt_dlp, "YoutubeDL", _YDLSimulado)
    monkeypatch.setattr(core, "verificar_ffmpeg", lambda: True)
    return _YDLSimulado


def _mensajes(caplog, nivel: int | None = None) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "descargador" and (nivel is None or r.levelno == nivel)
    ]


def test_descarga_sin_casilla_no_comprueba_lista(ydl_simulado, tmp_path: Path):
    ydl_simulado.respuesta = {"id": "nwfMKjzTxq4", "title": "Solo"}
    url = (
        "https://www.youtube.com/watch?v=nwfMKjzTxq4"
        "&list=PLxQezqB73FXlvmynISIJDTcbO3sSSPMZW&index=1"
    )
    resultado = core.descargar_video(url, tmp_path)
    assert resultado == Path("/destino/Solo.mp4")
    assert len(ydl_simulado.instancias) == 1  # sin extraccion ligera
    params = ydl_simulado.instancias[0].params
    assert params["noplaylist"] is True and params["ignoreerrors"] is False


def test_descarga_con_casilla_y_video_individual(ydl_simulado, tmp_path: Path, caplog):
    caplog.set_level(logging.INFO, logger="descargador")
    ydl_simulado.respuesta = {"id": "abc", "title": "Solo"}
    resultado = core.descargar_audio(
        "https://youtu.be/abc", tmp_path, OpcionesAudio(), lista=True
    )
    assert resultado == Path("/destino/Solo.mp4")  # sigue devolviendo Path
    ligera, descarga = ydl_simulado.instancias
    assert ligera.params["extract_flat"] == "in_playlist"
    assert ligera.params["noplaylist"] is False
    assert descarga.params["noplaylist"] is True
    assert "download_archive" not in descarga.params
    assert MENSAJE_VIDEO_INDIVIDUAL in _mensajes(caplog, logging.INFO)
    assert list(tmp_path.iterdir()) == []  # sin subcarpeta ni registros


def test_descarga_lista_completa(ydl_simulado, tmp_path: Path, caplog):
    caplog.set_level(logging.INFO, logger="descargador")
    ydl_simulado.respuesta = _info_lista(n=4, titulo="Rock: 80/90")
    ydl_simulado.fallan = {"v3"}
    recibidos: list[Progreso] = []

    r = core.descargar_video(
        "https://www.youtube.com/watch?v=v1&list=PL123",
        tmp_path,
        progreso=recibidos.append,
        lista=True,
    )

    assert isinstance(r, ResultadoLista)
    assert r.carpeta == tmp_path / nombre_carpeta_lista("Rock: 80/90")
    assert (r.total, r.descargados, r.omitidos, r.disponibles) == (4, 3, 0, 3)
    assert [e.posicion for e in r.fallidos] == [3]
    assert [p.name for p in r.rutas] == [
        "001 - Tema 1.mp4",
        "002 - Tema 2.mp4",
        "004 - Tema 4.mp4",
    ]
    datos = leer_json_lista(r.carpeta)
    assert datos is not None and datos["id"] == "PL123"
    assert (r.carpeta / ".descargas_video.txt").exists()
    assert not (r.carpeta / ".descargas_audio.txt").exists()

    descarga = ydl_simulado.instancias[-1]
    assert descarga.params["download_archive"].endswith(".descargas_video.txt")
    # Descarga a partir de la extraccion ligera, sin reintento externo.
    assert descarga.procesados[0]["_type"] == "playlist"
    assert recibidos[0].texto.startswith("Video 1 de 4 - ")
    assert recibidos[-1].fase == "finalizado"

    avisos = _mensajes(caplog, logging.WARNING)
    assert "No disponible o fallido: 3 - Tema 3" in avisos
    resumen = [m for m in _mensajes(caplog) if m.startswith("Resumen de la lista")]
    assert resumen == [
        (
            "Resumen de la lista: 3 descargados, 0 omitidos por el registro, "
            "1 no disponibles o fallidos (total: 4)."
        )
    ]


def test_descarga_lista_omite_lo_registrado_por_modo(
    ydl_simulado, tmp_path: Path, caplog
):
    caplog.set_level(logging.INFO, logger="descargador")
    ydl_simulado.respuesta = _info_lista(n=3)
    ydl_simulado.fallan = {"v2"}
    primera = core.descargar_video("u", tmp_path, lista=True)
    assert isinstance(primera, ResultadoLista) and primera.descargados == 2

    # Segunda vez en video: se omiten los dos ya descargados.
    caplog.clear()
    ydl_simulado.fallan = set()
    segunda = core.descargar_video("u", tmp_path, lista=True)
    assert isinstance(segunda, ResultadoLista)
    assert segunda.carpeta == primera.carpeta
    assert (segunda.omitidos, segunda.descargados, segunda.fallidos) == (2, 1, [])
    assert any("Se omitiran 2 de 3" in m for m in _mensajes(caplog))

    # En audio el registro es otro: no se omite nada.
    audio = core.descargar_audio("u", tmp_path, OpcionesAudio(), lista=True)
    assert isinstance(audio, ResultadoLista)
    assert (audio.omitidos, audio.descargados) == (0, 3)
    assert (audio.carpeta / ".descargas_audio.txt").exists()


def test_descarga_lista_cancelada(ydl_simulado, tmp_path: Path):
    ydl_simulado.respuesta = _info_lista(n=5)
    ydl_simulado.cancelar_tras = 2
    r = core.descargar_video("u", tmp_path, lista=True)
    assert isinstance(r, ResultadoLista)
    assert r.cancelado
    assert (r.descargados, r.fallidos) == (2, [])  # lo pendiente no cuenta


def test_mostrar_info_lista(ydl_simulado, caplog):
    caplog.set_level(logging.INFO, logger="descargador")
    ydl_simulado.respuesta = _info_lista(n=3)
    assert core.mostrar_info("u", lista=True) is not None
    mensajes = _mensajes(caplog)
    assert "Tipo:      Lista de reproduccion" in mensajes
    assert "Autor:     Autor" in mensajes
    assert "Videos:    3" in mensajes
    assert "  1. Tema 1" in mensajes and "  3. Tema 3" in mensajes
    assert len(ydl_simulado.instancias) == 1  # solo la extraccion ligera
    assert ydl_simulado.instancias[0].params["extract_flat"] == "in_playlist"


def test_mostrar_info_video_individual_con_casilla(ydl_simulado, caplog):
    caplog.set_level(logging.INFO, logger="descargador")
    ydl_simulado.respuesta = {"id": "abc", "title": "Solo", "formats": []}
    core.mostrar_info("u", lista=True)
    mensajes = _mensajes(caplog, logging.INFO)
    assert MENSAJE_VIDEO_INDIVIDUAL in mensajes
    assert "Titulo:    Solo" in mensajes


def test_formatear_info_lista_sin_autor():
    lista = leer_info_lista({"_type": "playlist", "id": "PL", "title": "T"})
    texto = formatear_info_lista(lista)
    assert "Autor" not in texto and "Videos:    0" in texto
