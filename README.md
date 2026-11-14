# Descargador de Video y Audio

Descargador de video y audio basado en [yt-dlp](https://github.com/yt-dlp/yt-dlp), con interfaz gráfica en **PySide6 (Qt 6)** pensada para sesiones **Wayland**, e interfaz de texto como alternativa. Diseñado para Linux (openSUSE Tumbleweed) y Python 3.13.

Soporta YouTube, YouTube Music y la mayoría de sitios compatibles con yt-dlp. Por defecto cada operación trabaja sobre un único video o audio; con la casilla **Descargar lista completa** también descarga listas de reproducción completas (ver [Descarga de listas de reproducción](#descarga-de-listas-de-reproducción)).

## Características

- Interfaz gráfica Qt 6 nativa en Wayland (con `xcb` como alternativa en X11/XWayland)
  - Selector de carpeta, barra de progreso con velocidad y tiempo restante, registro en vivo con avisos y errores coloreados
  - Casilla **Descargar lista completa** (en los modos video, audio e información)
  - Botón **Cancelar**: interrumpe la descarga en curso y borra los archivos parciales (`.part`, `.ytdl`)
  - Botón **Pegar** y autorrelleno de la URL desde el portapapeles al abrir
  - **Abrir carpeta** del archivo descargado (o de la subcarpeta de la lista) y acceso directo al archivo de log
  - Recuerda entre sesiones el tamaño de la ventana, el modo, la carpeta de cada modo, las opciones de calidad, metadatos y carátula, y la casilla de lista (`QSettings`, en `~/.config/descargador/`)
  - Atajos: `Ctrl+Return` iniciar, `Esc` cancelar, `Ctrl+L` ir a la URL, `Ctrl+Q` salir
- Interfaz de texto (`--cli`) con progreso en línea; se usa también automáticamente si no hay sesión gráfica o si no se puede cargar PySide6
- Descarga de video con selección de calidad (best, 1080p, 720p, 480p, 360p, worst), fusionado a MP4, con metadatos y miniatura embebidos (opcional)
- Extracción de audio en MP3, Opus o M4A con bitrate configurable
- Incrustación de carátula y metadatos vía FFmpeg (si está disponible)
- Descarga de listas de reproducción completas, en video o en audio, en una subcarpeta por lista, numeradas en orden y sin repetir lo ya descargado
- Vista de información de un video o audio (tipo, título, duración, canal, fecha, vistas, me gusta, sitio, calidades disponibles, tamaño aproximado) o de una lista (título, autor, cantidad de videos y títulos numerados)
- Registro de actividad y errores en un archivo de log (con rotación)
- Comprobación y actualización automática de las dependencias de `requirements.txt` al iniciar (usando `uv`), desactivable con `--no-update`
- Respeta las variables de entorno `XDG_DOWNLOAD_DIR` (carpeta de descargas) y `XDG_STATE_HOME` (ubicación del log)

## Requisitos

- Python 3.13+
- [`uv`](https://github.com/astral-sh/uv) (gestor de paquetes recomendado)
- FFmpeg instalado a nivel de sistema (para fusionar, convertir y embeber carátulas)
- Para la GUI, las librerías de sistema que usan los plugins de Qt (normalmente ya presentes en un escritorio Wayland): `libwayland-client0`, `libxkbcommon0`, `libEGL1`, `libGL1`; para la alternativa `xcb` (X11/XWayland) también `libxcb-cursor0`

## Instalación

```bash
# Clonar el repositorio
git clone https://github.com/ejbarra/descargador.git
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

Pegue la URL, elija entre **Video / Audio / Información**, ajuste las opciones y pulse el botón de acción. La carpeta de destino se puede escribir directamente o elegir con "Examinar..."; se recuerda por separado para video y para audio.

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
  3. Ver informacion (video, audio o lista)
  4. Salir
```

Después de la URL, las tres opciones preguntan `Descargar la lista completa si la URL corresponde a una lista? (s/N)`, que equivale a la casilla de la GUI (por defecto, no).

Los archivos se guardan por defecto en `~/Descargas/` (o `$XDG_DOWNLOAD_DIR`), organizados en subcarpetas (`videos/`, `musica/`). En la CLI se puede indicar otra subcarpeta o una ruta absoluta.

## Descarga de listas de reproducción

La casilla **Descargar lista completa** (o la pregunta equivalente de la CLI) decide qué hacer con una URL de lista:

- **Desmarcada** (por defecto, comportamiento de siempre): se descarga solo el video de la URL, aunque traiga `list=`. Por ejemplo, de `https://www.youtube.com/watch?v=nwfMKjzTxq4&list=PLxQezqB73FXlvmynISIJDTcbO3sSSPMZW&index=1` se descarga únicamente `nwfMKjzTxq4`.
- **Marcada**:
  - Si la URL es una lista, o un video con `list=`, se descarga la lista completa desde el primer elemento (se ignora `index=`).
  - Si la URL es de un solo video, se descarga igual que siempre, en la carpeta de destino y sin subcarpeta, y el registro muestra: *"La URL corresponde a un solo video, no a una lista; se descargara de forma individual."*

Para distinguir ambos casos se hace primero una extracción ligera (`extract_flat="in_playlist"`), que obtiene los títulos e ids de la lista sin resolver cada video.

### Subcarpeta y nombres de archivo

Cada lista se guarda en una subcarpeta de la carpeta de destino con el título de la lista (saneado con `yt_dlp.utils.sanitize_filename`, así que `/`, `:` y similares se reemplazan por caracteres equivalentes). Los archivos se numeran con su posición en la lista para conservar el orden:

```
~/Descargas/musica/
└── Mi lista favorita/
    ├── .descargador_lista.json
    ├── .descargas_audio.txt
    ├── 001 - Primer tema.mp3
    ├── 002 - Segundo tema.mp3
    └── ...
```

### Archivos ocultos de la subcarpeta

| Archivo | Contenido |
|---|---|
| `.descargador_lista.json` | Id, título y URL de la lista. Identifica la carpeta aunque se renombre o se mueva. |
| `.descargas_video.txt` | Registro de yt-dlp (`download_archive`) con los videos ya descargados en modo video. |
| `.descargas_audio.txt` | Lo mismo para el modo audio. |

Los registros son distintos por modo: descargar la lista en audio no omite los videos ya bajados en modo video, ni al revés. Al volver a descargar la misma lista solo se bajan los elementos nuevos o que fallaron antes, y el registro de actividad informa cuántos se omiten por estar ya descargados. Para forzar una descarga completa de nuevo, borre el archivo `.descargas_*.txt` del modo correspondiente.

La carpeta de la lista se busca en este orden:

1. Si la carpeta de destino elegida ya es la subcarpeta de esa lista (contiene `.descargador_lista.json` con el mismo id), se usa directamente. Sirve si usted movió la carpeta y la elige como destino.
2. Si alguna subcarpeta directa del destino contiene ese `.json` con el mismo id, se usa esa subcarpeta, aunque usted la haya renombrado o el autor haya cambiado el título de la lista.
3. Si no existe ninguna, se crea una subcarpeta nueva con el título de la lista. Si ya hay una carpeta con ese nombre que pertenece a otra lista, se agrega el id al nombre (`Título [PL...]`).

Como toda la información vive dentro de la subcarpeta, la carpeta es portable: se puede mover a otro disco o equipo y seguir actualizándola.

### Errores, progreso y cancelación

- Un video borrado, privado o bloqueado no detiene la lista: se registra el error y se continúa con el siguiente. Al terminar se listan los fallidos con su posición y título, y un resumen con los descargados, los omitidos por el registro y los no disponibles o fallidos. La GUI muestra *"Lista completada: X de N"*, donde X cuenta los descargados ahora más los que ya estaban.
- Si el formato pedido no existe para algún video, la alternativa (`/best`) va dentro de la propia cadena de formato y yt-dlp la resuelve video por video.
- El texto de estado indica el elemento actual, por ejemplo *"Video 3 de 25 - Descargando 45.2% de 12.3MiB a 1.2MiB/s"*. La barra muestra el avance del **archivo en curso** y su etiqueta lleva el contador (`3/25 - 45%`): una barra global avanzaría a saltos por los videos omitidos, los fallidos y las dos descargas (video y audio) de cada elemento.
- Entre un video y el siguiente hay una pausa aleatoria de 1 a 3 segundos para reducir los errores 429 (demasiadas solicitudes) de YouTube.
- **Cancelar** detiene la lista completa: se conservan los archivos ya terminados (quedan anotados en el registro, así que la próxima vez se continúa desde ahí) y se borran los parciales del video en curso. **Abrir carpeta** abre la subcarpeta de la lista.

### Información de una lista

En modo **Información** con la casilla marcada, una URL de lista muestra en el registro el título, el autor (si lo hay), la cantidad de videos y la lista numerada de títulos, sin descargar nada. Si la URL es de un solo video, se muestra la información habitual junto con el aviso anterior.

### Wayland

La GUI usa el backend nativo `wayland` de Qt cuando detecta `WAYLAND_DISPLAY` (si `QT_QPA_PLATFORM` no está definida, el programa la fija a `wayland;xcb`). Para forzar un backend concreto:

```bash
QT_QPA_PLATFORM=wayland python Descargador.py
QT_QPA_PLATFORM=xcb python Descargador.py       # XWayland / X11
```

En Wayland el escritorio identifica la ventana por su `app_id` (`descargador`). Para que muestre el icono y el nombre correctos en el dock o la barra de tareas, instale la entrada de escritorio y el icono incluidos en `assets/`, ajustando antes la ruta del `Exec=`:

```bash
sed "s|/ruta/a/descargador|$PWD|" assets/descargador.desktop > ~/.local/share/applications/descargador.desktop
mkdir -p ~/.local/share/icons/hicolor/scalable/apps
cp assets/descargador.svg ~/.local/share/icons/hicolor/scalable/apps/
update-desktop-database ~/.local/share/applications
```

Si usa un venv, cambie `python3` en el `Exec=` por la ruta del intérprete del venv (por ejemplo `.venv/bin/python`).

Para que los diálogos de archivo sean los del escritorio (vía xdg-desktop-portal), instale el paquete de portal de su entorno y, si hace falta, exporte `QT_QPA_PLATFORMTHEME=xdgdesktopportal`.

## Estructura del código

| Archivo | Contenido |
|---|---|
| `Descargador.py` | Punto de entrada: argumentos y elección de GUI o CLI |
| `descargador/core.py` | Núcleo sin interfaz: logging, opciones, descargas individuales y de listas, cancelación, información, actualizaciones |
| `descargador/gui.py` | Interfaz gráfica PySide6 (hilo de trabajo, progreso, ajustes persistentes) |
| `descargador/cli.py` | Menú interactivo de texto |
| `assets/` | Icono SVG y entrada `.desktop` |
| `tests/` | Pruebas unitarias del núcleo (`pytest`, sin red; `YoutubeDL` se simula) |
| `pyrightconfig.json` | Configuración de la comprobación de tipos con pyright |

Para desarrollar:

```bash
uv pip install -r requirements-dev.txt
pytest -q
ruff format --check .
ruff check .
pyright        # si lo tiene instalado (no forma parte de requirements-dev.txt)
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
- Cancelar durante el postproceso (conversión con FFmpeg) no interrumpe FFmpeg; la cancelación actúa sobre la fase de descarga y, en una lista, también antes de empezar cada video.
- El log de actividad y errores se guarda en `~/.local/state/descargador/descargador.log` (o bajo `$XDG_STATE_HOME`).
- La actualización automática al iniciar también puede actualizar PySide6, que es un paquete grande; use `--no-update` si prefiere controlarlo a mano.
- Conviene actualizar yt-dlp periódicamente para mantener la compatibilidad con los sitios:

```bash
uv pip install --upgrade yt-dlp
```

## Licencia

MIT
