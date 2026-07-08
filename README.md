# Descargador de Video y Audio

Descargador de video y audio basado en [yt-dlp](https://github.com/yt-dlp/yt-dlp), con interfaz gráfica (tkinter) e interfaz de texto como alternativa. Diseñado para Linux (openSUSE Tumbleweed) y Python 3.13.

Soporta YouTube, YouTube Music y la mayoría de sitios compatibles con yt-dlp. Cada operación trabaja sobre un único video o audio (no playlists).

## Características

- Interfaz gráfica (tkinter) con selector de carpeta, barra de progreso y registro en vivo
- Interfaz de texto (`--cli`) como alternativa, o si tkinter no está instalado
- Descarga de video con selección de calidad (best, 720p, 480p, 360p, worst), fusionado a MP4
- Extracción de audio en MP3, Opus o M4A con bitrate configurable
- Incrustación de carátula y metadatos vía FFmpeg (si está disponible)
- Vista de información de un video o audio (título, duración, vistas, canal, sitio, calidad máxima)
- Registro de actividad y errores en un archivo de log (con rotación)
- Comprobación y actualización automática de las dependencias de `requirements.txt` al iniciar (usando `uv`), desactivable con `--no-update`
- Respeta `XDG_DOWNLOAD_DIR` (carpeta de descargas) y `XDG_STATE_HOME` (ubicación del log)

## Requisitos

- Python 3.13+
- tkinter para la GUI: `sudo zypper install python313-tk` (opcional; sin él se usa la interfaz de texto)
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

Por defecto se abre la interfaz gráfica: pega la URL, elige entre Video/Audio/Información, ajusta las opciones y pulsa el botón de acción. La carpeta de destino se puede escribir directamente o elegir con "Examinar...".

Opciones de línea de comandos:

```bash
python Descargador.py --cli         # usar la interfaz de texto en vez de la GUI
python Descargador.py --no-update   # omitir la comprobación de actualizaciones al iniciar
```

Con `--cli` se muestra un menú interactivo:

```
Opciones:
  1. Descargar video
  2. Descargar audio
  3. Ver informacion (video o audio)
  4. Salir
```

Los archivos se guardan por defecto en `~/Descargas/` (o `$XDG_DOWNLOAD_DIR`), organizados en subcarpetas (`videos/`, `musica/`).

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
- El log de actividad y errores se guarda en `~/.local/state/descargador/descargador.log` (o bajo `$XDG_STATE_HOME`).
- Actualizar yt-dlp periódicamente para mantener compatibilidad con los sitios:

```bash
uv pip install --upgrade yt-dlp
```

## Licencia

MIT
yolo
