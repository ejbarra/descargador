# Descargador de Video y Audio

Descargador interactivo de video y audio basado en [yt-dlp](https://github.com/yt-dlp/yt-dlp), diseñado para Linux.

Soporta YouTube, YouTube Music y la mayoría de sitios compatibles con yt-dlp.

## Características

- Descarga de video con selección de calidad (best, 720p, 480p, 360p, worst)
- Extracción de audio en MP3, Opus o M4A con bitrate configurable
- Descarga de playlists completas o selección de pistas (ej: `1,3,5-10`)
- Incrustación de carátula y metadatos vía FFmpeg
- Conversión automática de URLs de YouTube Music a YouTube
- Respeta `XDG_DOWNLOAD_DIR` para la carpeta de destino

## Requisitos

- Python 3.13+
- FFmpeg instalado a nivel de sistema (para conversión y carátulas)
- [`uv`](https://github.com/astral-sh/uv) (gestor de paquetes recomendado)

## Instalación

```bash
# Clonar el repositorio
git clone git@github.com:usuario/descargador.git
cd descargador

# Instalar dependencias
uv pip install -r requirements.txt

# Instalar FFmpeg (openSUSE — repositorio Packman recomendado para codecs completos)
sudo zypper install ffmpeg
```

## Uso

```bash
python Descargador.py
```

El menú interactivo guía el proceso:

```
Opciones:
  1. Descargar video
  2. Descargar audio (individual)
  3. Descargar playlist (audio)
  4. Ver información de video/playlist
  5. Salir
```

Los archivos se guardan en `~/Descargas/` organizados por subcarpeta (`videos/`, `musica/`, `playlists/`).

## Dependencias Python

| Paquete | Función |
|---|---|
| `yt-dlp` | Motor principal de descarga |
| `mutagen` | Lectura/escritura de metadatos de audio |
| `pycryptodomex` | Descifrado de algunos formatos |
| `brotli` | Compatibilidad con sitios que usan compresión Brotli |
| `certifi` | Certificados SSL actualizados |
| `websockets` | Soporte para streams en vivo |

## Notas

- FFmpeg debe instalarse desde el repositorio **Packman** en openSUSE para tener soporte completo de codecs (MP3, AAC, etc.).
- Si FFmpeg no está disponible, el audio se descarga sin conversión ni carátula.
- Actualizar yt-dlp periódicamente para mantener compatibilidad con los sitios:

```bash
uv pip install --upgrade yt-dlp
```

## Licencia

MIT
yolo
