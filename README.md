# Descargador de Video y Audio

Descargador de video y audio basado en [yt-dlp](https://github.com/yt-dlp/yt-dlp), con interfaz gráfica en **PySide6 (Qt 6)** pensada para sesiones **Wayland**, e interfaz de texto como alternativa. Diseñado para Linux (openSUSE Tumbleweed) y Python 3.13.

Soporta YouTube, YouTube Music y la mayoría de sitios compatibles con yt-dlp. Cada operación trabaja sobre un único video o audio (no playlists).

## Características

- Interfaz gráfica Qt 6 nativa en Wayland (con `xcb` como alternativa en X11/XWayland)
  - Selector de carpeta, barra de progreso con velocidad y tiempo restante, registro en vivo con avisos y errores coloreados
  - Botón **Cancelar**: interrumpe la descarga en curso y borra los archivos parciales (`.part`, `.ytdl`)
  - Botón **Pegar** y autorrelleno de la URL desde el portapapeles al abrir
  - **Abrir carpeta** del archivo descargado y acceso directo al archivo de log
  - Recuerda entre sesiones el tamaño de la ventana, el modo, la carpeta de cada modo y las opciones de calidad (`QSettings`, en `~/.config/descargador/`)
  - Atajos: `Ctrl+Return` iniciar, `Esc` cancelar, `Ctrl+L` ir a la URL, `Ctrl+Q` salir
- Interfaz de texto (`--cli`) con progreso en línea, o automáticamente si no hay sesión gráfica ni PySide6
- Descarga de video con selección de calidad (best, 1080p, 720p, 480p, 360p, worst), fusionado a MP4, con metadatos y miniatura embebidos (opcional)
- Extracción de audio en MP3, Opus o M4A con bitrate configurable
- Incrustación de carátula y metadatos vía FFmpeg (si está disponible)
- Vista de información de un video o audio (título, duración, canal, fecha, vistas, me gusta, sitio, calidades disponibles, tamaño aproximado)
- Registro de actividad y errores en un archivo de log (con rotación)
- Comprobación y actualización automática de las dependencias de `requirements.txt` al iniciar (usando `uv`), desactivable con `--no-update`
- Respeta `XDG_DOWNLOAD_DIR` (carpeta de descargas) y `XDG_STATE_HOME` (ubicación del log)

## Requisitos

- Python 3.13+
- [`uv`](https://github.com/astral-sh/uv) (gestor de paquetes recomendado)
- FFmpeg instalado a nivel de sistema (para fusionar, convertir y embeber carátulas)
- Para la GUI, las librerías de sistema que usan los plugins de Qt (normalmente ya presentes en un escritorio Wayland): `libwayland-client0`, `libxkbcommon0`, `libEGL1`, `libGL1`; para la alternativa `xcb` (X11/XWayland) también `libxcb-cursor0`

## Instalación

```bash
# Clonar el repositorio
git clone git@github.com:usuario/descargador.git
cd descargador

# Crear el entorno e instalar dependencias (incluye PySide6-Essentials)
uv venv
uv pip install -r requirements.txt

# Instalar FFmpeg (openSUSE — repositorio Packman recomendado para codecs completos)
sudo zypper install ffmpeg
```

## Uso

```bash
python Descargador.py                 # abre la GUI
python Descargador.py "https://..."   # abre la GUI con la URL ya cargada
```

Pega la URL, elige entre **Video / Audio / Información**, ajusta las opciones y pulsa el botón de acción. La carpeta de destino se puede escribir directamente o elegir con "Examinar..."; se recuerda por separado para video y para audio.

Opciones de línea de comandos:

```bash
python Descargador.py --cli         # usar la interfaz de texto en vez de la GUI
python Descargador.py --no-update   # omitir la comprobación de actualizaciones al iniciar
python Descargador.py --version
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

### Wayland

La GUI usa el backend nativo `wayland` de Qt cuando detecta `WAYLAND_DISPLAY` (si `QT_QPA_PLATFORM` no está definida, el programa la fija a `wayland;xcb`). Para forzar un backend concreto:

```bash
QT_QPA_PLATFORM=wayland python Descargador.py
QT_QPA_PLATFORM=xcb python Descargador.py       # XWayland / X11
```

En Wayland el escritorio identifica la ventana por su `app_id` (`descargador`). Para que muestre el icono y el nombre correctos en el dock o la barra de tareas, instala la entrada de escritorio y el icono incluidos en `assets/`, ajustando antes la ruta del `Exec=`:

```bash
sed "s|/ruta/a/descargador|$PWD|" assets/descargador.desktop > ~/.local/share/applications/descargador.desktop
mkdir -p ~/.local/share/icons/hicolor/scalable/apps
cp assets/descargador.svg ~/.local/share/icons/hicolor/scalable/apps/
update-desktop-database ~/.local/share/applications
```

Si usas un venv, cambia `python3` en el `Exec=` por la ruta del intérprete del venv (por ejemplo `.venv/bin/python`).

Para que los diálogos de archivo sean los del escritorio (vía xdg-desktop-portal), instala el paquete de portal de tu entorno y, si hace falta, exporta `QT_QPA_PLATFORMTHEME=xdgdesktopportal`.

## Estructura del código

| Archivo | Contenido |
|---|---|
| `Descargador.py` | Punto de entrada: argumentos y elección de GUI o CLI |
| `descargador/core.py` | Núcleo sin interfaz: logging, opciones, descargas, cancelación, información, actualizaciones |
| `descargador/gui.py` | Interfaz gráfica PySide6 (hilo de trabajo, progreso, ajustes persistentes) |
| `descargador/cli.py` | Menú interactivo de texto |
| `assets/` | Icono SVG y entrada `.desktop` |
| `tests/` | Pruebas unitarias del núcleo (`pytest`, sin red) |

Para desarrollar:

```bash
uv pip install -r requirements-dev.txt
pytest -q
ruff check . && ruff format --check .
```

## Dependencias Python

| Paquete | Función |
|---|---|
| `yt-dlp` | Motor principal de descarga |
| `PySide6-Essentials` | Interfaz gráfica Qt 6 (QtCore, QtGui, QtWidgets, QtSvg; incluye los plugins `wayland` y `xcb`) |
| `mutagen` | Lectura/escritura de metadatos de audio y carátulas |
| `pycryptodomex` | Descifrado de algunos formatos |
| `brotli` | Compatibilidad con sitios que usan compresión Brotli |
| `certifi` | Certificados SSL actualizados |
| `websockets` | Soporte para streams en vivo |

## Notas

- FFmpeg debe instalarse desde el repositorio **Packman** en openSUSE para tener soporte completo de codecs (MP3, AAC, Opus, etc.).
- Si FFmpeg no está disponible, el video se descarga en el mejor formato ya combinado y el audio sin conversión ni carátula.
- Cancelar durante el postproceso (conversión con FFmpeg) no interrumpe FFmpeg; la cancelación actúa sobre la fase de descarga.
- El log de actividad y errores se guarda en `~/.local/state/descargador/descargador.log` (o bajo `$XDG_STATE_HOME`).
- La actualización automática al iniciar también puede actualizar PySide6, que es un paquete grande; usa `--no-update` si prefieres controlarlo a mano.
- Actualizar yt-dlp periódicamente para mantener compatibilidad con los sitios:

```bash
uv pip install --upgrade yt-dlp
```

## Licencia

MIT
