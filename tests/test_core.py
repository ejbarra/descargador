"""Pruebas unitarias del nucleo (sin red ni FFmpeg)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadCancelled

from descargador import core
from descargador.core import (
    OpcionesAudio,
    OpcionesVideo,
    Progreso,
    _Seguimiento,
    es_solo_audio,
    fmt_duracion,
    formatear_info,
    formato_video,
    normalizar_url,
    paquetes_de_requirements,
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
