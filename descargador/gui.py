"""
Interfaz grafica del descargador (PySide6 / Qt 6).

Pensada para sesiones Wayland: usa el plugin nativo de Qt para Wayland (con
xcb como alternativa), fija el app_id mediante el nombre del archivo .desktop
y recuerda el tamano de la ventana y las ultimas opciones usadas (QSettings).
"""

from __future__ import annotations

import functools
import html
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__, core
from .core import (
    AUDIO_CODECS,
    BASE_DESCARGAS,
    CALIDADES_MP3,
    CALIDADES_VIDEO,
    RUTA_LOG,
    SUBCARPETAS,
    OpcionesAudio,
    OpcionesVideo,
    Progreso,
    log,
)

NOMBRE_APP = "descargador"
TITULO_APP = "Descargador de video y audio"
RUTA_ASSETS = Path(__file__).resolve().parent.parent / "assets"

# Tipo de las tareas que ejecuta el hilo de trabajo:
#   tarea(progreso_callback, evento_cancelar) -> resultado
Tarea = Callable[[core.ProgresoCallback, threading.Event], object]


# ---------------------------------------------------------------------------
# Puente logging -> Qt (thread-safe gracias a las senales)
# ---------------------------------------------------------------------------


class _PuenteLog(QObject):
    mensaje = Signal(int, str)  # (nivel, texto)


class _HandlerQt(logging.Handler):
    """Handler de logging que reenvia cada registro al hilo de la GUI."""

    def __init__(self, puente: _PuenteLog) -> None:
        super().__init__()
        self._puente = puente
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._puente.mensaje.emit(record.levelno, self.format(record))
        except RuntimeError:  # el QObject ya fue destruido al cerrar
            pass


# ---------------------------------------------------------------------------
# Hilo de trabajo
# ---------------------------------------------------------------------------


class Trabajador(QThread):
    """Ejecuta una tarea del nucleo fuera del hilo de la GUI."""

    progreso = Signal(object)  # core.Progreso
    terminado = Signal(object)  # Path | dict | None

    def __init__(self, tarea: Tarea, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tarea = tarea
        self.cancelar = threading.Event()
        self._ultimo_envio = 0.0

    def _notificar(self, p: Progreso) -> None:
        # Limita la frecuencia de actualizacion durante la descarga.
        ahora = time.monotonic()
        en_curso = (
            p.fase == "descarga" and p.porcentaje is not None and p.porcentaje < 100
        )
        if en_curso and ahora - self._ultimo_envio < 0.1:
            return
        self._ultimo_envio = ahora
        self.progreso.emit(p)

    def run(self) -> None:
        try:
            resultado = self._tarea(self._notificar, self.cancelar)
        except Exception as e:  # el nucleo ya captura casi todo; por si acaso
            log.error("Error inesperado: %s", e)
            resultado = None
        self.terminado.emit(resultado)


# ---------------------------------------------------------------------------
# Ventana principal
# ---------------------------------------------------------------------------


class VentanaPrincipal(QMainWindow):
    _COLORES: ClassVar[dict[int, str]] = {
        logging.WARNING: "#d9a400",
        logging.ERROR: "#e0554d",
        logging.CRITICAL: "#e0554d",
    }

    def __init__(self, url_inicial: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle(TITULO_APP)
        self.setMinimumSize(640, 520)
        self.resize(820, 640)

        self._ajustes = QSettings()
        self._hilo: Trabajador | None = None
        self._ultima_ruta: Path | None = None
        self._modo_actual = "audio"
        self._calidad_mp3 = CALIDADES_MP3[0]
        self._carpetas = {
            modo: str(BASE_DESCARGAS / sub) for modo, sub in SUBCARPETAS.items()
        }

        self._puente = _PuenteLog(self)
        self._puente.mensaje.connect(self._agregar_log)
        self._handler = _HandlerQt(self._puente)
        log.addHandler(self._handler)

        self._construir_ui()
        self._crear_acciones()
        self._restaurar_ajustes()
        self._cambiar_modo(self._modo_actual, inicial=True)
        self._cambiar_codec()

        if url_inicial:
            self.ed_url.setText(core.normalizar_url(url_inicial))
        else:
            QTimer.singleShot(300, self._sugerir_url_portapapeles)

    # -- construccion de la interfaz ---------------------------------------

    def _construir_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        raiz = QVBoxLayout(central)
        raiz.setContentsMargins(12, 12, 12, 12)
        raiz.setSpacing(8)

        # URL y destino
        rejilla = QGridLayout()
        rejilla.setColumnStretch(1, 1)

        rejilla.addWidget(QLabel("URL:"), 0, 0)
        self.ed_url = QLineEdit()
        self.ed_url.setPlaceholderText("https://www.youtube.com/watch?v=...")
        self.ed_url.setClearButtonEnabled(True)
        self.ed_url.returnPressed.connect(self._iniciar)
        rejilla.addWidget(self.ed_url, 0, 1)
        self.btn_pegar = QPushButton("Pegar")
        self.btn_pegar.setToolTip("Pegar la URL del portapapeles")
        self.btn_pegar.clicked.connect(self._pegar)
        rejilla.addWidget(self.btn_pegar, 0, 2)

        self.lbl_carpeta = QLabel("Destino:")
        rejilla.addWidget(self.lbl_carpeta, 1, 0)
        self.ed_carpeta = QLineEdit()
        rejilla.addWidget(self.ed_carpeta, 1, 1)
        self.btn_examinar = QPushButton("Examinar...")
        self.btn_examinar.clicked.connect(self._examinar)
        rejilla.addWidget(self.btn_examinar, 1, 2)
        raiz.addLayout(rejilla)

        # Modo
        caja_modo = QGroupBox("Tipo de descarga")
        fila_modo = QHBoxLayout(caja_modo)
        self._grupo_modo = QButtonGroup(self)
        self._radios: dict[str, QRadioButton] = {}
        for texto, modo in (
            ("Video", "video"),
            ("Audio", "audio"),
            ("Informacion", "info"),
        ):
            rb = QRadioButton(texto)
            self._radios[modo] = rb
            self._grupo_modo.addButton(rb)
            fila_modo.addWidget(rb)
            rb.toggled.connect(lambda activo, m=modo: activo and self._cambiar_modo(m))
        fila_modo.addStretch(1)
        raiz.addWidget(caja_modo)

        # Opciones de video
        self.caja_video = QGroupBox("Opciones de video")
        fila_v = QHBoxLayout(self.caja_video)
        fila_v.addWidget(QLabel("Calidad:"))
        self.cb_calidad = QComboBox()
        self.cb_calidad.addItems(CALIDADES_VIDEO)
        fila_v.addWidget(self.cb_calidad)
        fila_v.addSpacing(16)
        self.chk_meta_video = QCheckBox("Embeber metadatos y miniatura")
        self.chk_meta_video.setChecked(True)
        fila_v.addWidget(self.chk_meta_video)
        fila_v.addStretch(1)
        raiz.addWidget(self.caja_video)

        # Opciones de audio
        self.caja_audio = QGroupBox("Opciones de audio")
        fila_a = QHBoxLayout(self.caja_audio)
        fila_a.addWidget(QLabel("Formato:"))
        self.cb_codec = QComboBox()
        self.cb_codec.addItems(list(AUDIO_CODECS))
        self.cb_codec.currentTextChanged.connect(self._cambiar_codec)
        fila_a.addWidget(self.cb_codec)
        fila_a.addSpacing(16)
        fila_a.addWidget(QLabel("Calidad:"))
        self.cb_aq = QComboBox()
        self.cb_aq.addItems(CALIDADES_MP3)
        self.cb_aq.currentTextChanged.connect(self._recordar_calidad_mp3)
        fila_a.addWidget(self.cb_aq)
        fila_a.addWidget(QLabel("kbps"))
        fila_a.addSpacing(16)
        self.chk_caratula = QCheckBox("Embeber caratula y metadatos")
        self.chk_caratula.setChecked(True)
        fila_a.addWidget(self.chk_caratula)
        fila_a.addStretch(1)
        raiz.addWidget(self.caja_audio)

        # Progreso y estado
        self.barra = QProgressBar()
        self.barra.setRange(0, 100)
        self.barra.setValue(0)
        self.barra.setTextVisible(True)
        raiz.addWidget(self.barra)
        self.lbl_estado = QLabel("Listo.")
        raiz.addWidget(self.lbl_estado)

        # Registro
        caja_log = QGroupBox("Registro")
        col_log = QVBoxLayout(caja_log)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(3000)
        self.txt_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        col_log.addWidget(self.txt_log, 1)
        fila_log = QHBoxLayout()
        fila_log.addStretch(1)
        btn_limpiar = QPushButton("Limpiar")
        btn_limpiar.clicked.connect(self.txt_log.clear)
        fila_log.addWidget(btn_limpiar)
        btn_ver_log = QPushButton("Abrir archivo de log")
        btn_ver_log.clicked.connect(lambda: self._abrir_ruta(RUTA_LOG))
        fila_log.addWidget(btn_ver_log)
        col_log.addLayout(fila_log)
        raiz.addWidget(caja_log, 1)

        # Botones de accion
        fila_btn = QHBoxLayout()
        self.btn_abrir = QPushButton("Abrir carpeta")
        self.btn_abrir.setToolTip("Abrir la carpeta de destino en el explorador")
        self.btn_abrir.clicked.connect(self._abrir_carpeta)
        fila_btn.addWidget(self.btn_abrir)
        fila_btn.addStretch(1)
        self.btn_cancelar = QPushButton("Cancelar")
        self.btn_cancelar.setEnabled(False)
        self.btn_cancelar.clicked.connect(self._cancelar)
        fila_btn.addWidget(self.btn_cancelar)
        self.btn_accion = QPushButton("Descargar")
        self.btn_accion.setDefault(True)
        self.btn_accion.setMinimumWidth(140)
        self.btn_accion.clicked.connect(self._iniciar)
        fila_btn.addWidget(self.btn_accion)
        raiz.addLayout(fila_btn)

        # Barra de estado
        ffmpeg = "detectado" if core.verificar_ffmpeg() else "NO detectado"
        self.statusBar().showMessage(
            f"yt-dlp {core.version_yt_dlp()}   |   FFmpeg {ffmpeg}   |   v{__version__}"
        )

    def _crear_acciones(self) -> None:
        def accion(texto: str, atajo: str, fn: Callable[[], None]) -> None:
            a = QAction(texto, self)
            a.setShortcut(QKeySequence(atajo))
            a.triggered.connect(fn)
            self.addAction(a)

        accion("Salir", "Ctrl+Q", self.close)
        accion("Ir a la URL", "Ctrl+L", self._enfocar_url)
        accion("Iniciar", "Ctrl+Return", self._iniciar)
        accion("Cancelar", "Escape", self._cancelar)

    # -- ajustes persistentes -----------------------------------------------

    def _restaurar_ajustes(self) -> None:
        a = self._ajustes
        geom = a.value("ventana/geometria")
        if geom is not None:
            self.restoreGeometry(geom)
        for modo in self._carpetas:
            self._carpetas[modo] = str(a.value(f"carpeta/{modo}", self._carpetas[modo]))
        modo = str(a.value("modo", "audio"))
        self._modo_actual = modo if modo in self._radios else "audio"
        self._radios[self._modo_actual].setChecked(True)

        calidad = str(a.value("video/calidad", "best"))
        if calidad in CALIDADES_VIDEO:
            self.cb_calidad.setCurrentText(calidad)
        self.chk_meta_video.setChecked(_a_bool(a.value("video/metadatos", True)))

        codec = str(a.value("audio/codec", "mp3"))
        if codec in AUDIO_CODECS:
            self.cb_codec.setCurrentText(codec)
        aq = str(a.value("audio/calidad", CALIDADES_MP3[0]))
        if aq in CALIDADES_MP3:
            self._calidad_mp3 = aq
        self.chk_caratula.setChecked(_a_bool(a.value("audio/caratula", True)))

    def _guardar_ajustes(self) -> None:
        a = self._ajustes
        a.setValue("ventana/geometria", self.saveGeometry())
        if self._modo_actual in self._carpetas:
            self._carpetas[self._modo_actual] = self.ed_carpeta.text().strip()
        for modo, carpeta in self._carpetas.items():
            a.setValue(f"carpeta/{modo}", carpeta)
        a.setValue("modo", self._modo_actual)
        a.setValue("video/calidad", self.cb_calidad.currentText())
        a.setValue("video/metadatos", self.chk_meta_video.isChecked())
        a.setValue("audio/codec", self.cb_codec.currentText())
        a.setValue("audio/calidad", self._calidad_mp3)
        a.setValue("audio/caratula", self.chk_caratula.isChecked())
        a.sync()

    # -- reacciones de la interfaz -----------------------------------------

    def _cambiar_modo(self, modo: str, inicial: bool = False) -> None:
        # Recuerda la carpeta del modo que se abandona (si el campo no esta vacio).
        anterior = self.ed_carpeta.text().strip()
        if not inicial and anterior and self._modo_actual in self._carpetas:
            self._carpetas[self._modo_actual] = anterior
        self._modo_actual = modo

        self.caja_video.setVisible(modo == "video")
        self.caja_audio.setVisible(modo == "audio")
        if modo in self._carpetas:
            self.ed_carpeta.setText(self._carpetas[modo])
        self._aplicar_estado_destino()
        self.btn_accion.setText("Ver informacion" if modo == "info" else "Descargar")

    def _aplicar_estado_destino(self) -> None:
        """El destino solo tiene sentido al descargar, no al pedir informacion."""
        activo = self._modo_actual != "info"
        for w in (self.lbl_carpeta, self.ed_carpeta, self.btn_examinar):
            w.setEnabled(activo)

    def _cambiar_codec(self) -> None:
        # La eleccion de bitrate solo aplica a MP3; el resto usa su valor fijo.
        codec = self.cb_codec.currentText()
        if codec == "mp3":
            self.cb_aq.setEnabled(True)
            self.cb_aq.setCurrentText(self._calidad_mp3)
        else:
            self.cb_aq.setEnabled(False)
            self.cb_aq.setCurrentText(AUDIO_CODECS[codec])

    def _recordar_calidad_mp3(self, texto: str) -> None:
        # Solo se recuerda lo elegido con MP3 activo; los valores fijos de los
        # otros codecs no deben pisar la preferencia del usuario.
        if self.cb_codec.currentText() == "mp3" and texto in CALIDADES_MP3:
            self._calidad_mp3 = texto

    def _enfocar_url(self) -> None:
        self.ed_url.setFocus()
        self.ed_url.selectAll()

    def _pegar(self) -> None:
        texto = QGuiApplication.clipboard().text().strip()
        if texto:
            self.ed_url.setText(texto)
            self.ed_url.setFocus()

    def _sugerir_url_portapapeles(self) -> None:
        """Al abrir, rellena la URL si el portapapeles contiene un enlace."""
        if self.ed_url.text().strip():
            return
        texto = QGuiApplication.clipboard().text().strip()
        if texto.startswith(("http://", "https://")) and " " not in texto:
            self.ed_url.setText(texto)

    def _examinar(self) -> None:
        inicio = self.ed_carpeta.text().strip() or str(BASE_DESCARGAS)
        if not Path(inicio).expanduser().is_dir():
            inicio = str(BASE_DESCARGAS)
        elegido = QFileDialog.getExistingDirectory(self, "Carpeta de descarga", inicio)
        if elegido:
            self.ed_carpeta.setText(elegido)

    def _carpeta_destino(self) -> Path:
        texto = self.ed_carpeta.text().strip()
        if not texto:
            texto = self._carpetas.get(self._modo_actual, str(BASE_DESCARGAS))
            self.ed_carpeta.setText(texto)
        return Path(texto).expanduser()

    def _abrir_carpeta(self) -> None:
        if self._ultima_ruta is not None and self._ultima_ruta.parent.is_dir():
            self._abrir_ruta(self._ultima_ruta.parent)
            return
        carpeta = self._carpeta_destino()
        if carpeta.is_dir():
            self._abrir_ruta(carpeta)
        else:
            self.lbl_estado.setText(f"La carpeta no existe: {carpeta}")

    def _abrir_ruta(self, ruta: Path) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(ruta))):
            log.warning("No se pudo abrir: %s", ruta)

    # -- ejecucion ----------------------------------------------------------

    def _iniciar(self) -> None:
        if self._hilo is not None:
            return
        url = core.normalizar_url(self.ed_url.text())
        if not url:
            self.lbl_estado.setText("Ingresa una URL.")
            self._enfocar_url()
            return
        self.ed_url.setText(url)

        modo = self._modo_actual
        if modo == "info":
            tarea: Tarea = functools.partial(_tarea_info, url)
        else:
            carpeta = self._carpeta_destino()
            try:
                carpeta.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                QMessageBox.warning(
                    self, TITULO_APP, f"No se pudo crear la carpeta:\n{e}"
                )
                return
            if modo == "video":
                video = OpcionesVideo(
                    calidad=self.cb_calidad.currentText(),
                    metadatos=self.chk_meta_video.isChecked(),
                )
                tarea = functools.partial(_tarea_video, url, carpeta, video)
            else:
                audio = OpcionesAudio(
                    codec=self.cb_codec.currentText(),
                    calidad=self.cb_aq.currentText(),
                    caratula=self.chk_caratula.isChecked(),
                )
                tarea = functools.partial(_tarea_audio, url, carpeta, audio)

        self._guardar_ajustes()
        self._ultima_ruta = None
        self._set_ocupado(True)
        self.barra.setRange(0, 100)
        self.barra.setValue(0)
        self.lbl_estado.setText("Trabajando...")

        self._hilo = Trabajador(tarea, self)
        self._hilo.progreso.connect(self._actualizar_progreso)
        self._hilo.terminado.connect(self._terminado)
        self._hilo.start()

    def _cancelar(self) -> None:
        if self._hilo is None or self._hilo.cancelar.is_set():
            return
        self._hilo.cancelar.set()
        self.btn_cancelar.setEnabled(False)
        self.lbl_estado.setText("Cancelando...")

    def _set_ocupado(self, ocupado: bool) -> None:
        for w in (
            self.btn_accion,
            self.btn_examinar,
            self.btn_pegar,
            self.ed_url,
            self.ed_carpeta,
            self.caja_video,
            self.caja_audio,
        ):
            w.setEnabled(not ocupado)
        for rb in self._radios.values():
            rb.setEnabled(not ocupado)
        self.btn_cancelar.setEnabled(ocupado)
        if not ocupado:
            self._aplicar_estado_destino()

    @Slot(object)
    def _actualizar_progreso(self, p: Progreso) -> None:
        if p.porcentaje is None:
            self.barra.setRange(0, 0)  # indeterminado
        else:
            self.barra.setRange(0, 100)
            self.barra.setValue(int(p.porcentaje))
        self.lbl_estado.setText(p.texto)

    @Slot(object)
    def _terminado(self, resultado: object) -> None:
        hilo = self._hilo
        self._hilo = None
        cancelado = hilo is not None and hilo.cancelar.is_set()
        if hilo is not None:
            hilo.wait()
            hilo.deleteLater()

        self.barra.setRange(0, 100)
        if isinstance(resultado, Path):
            self._ultima_ruta = resultado
            self.barra.setValue(100)
            self.lbl_estado.setText(f"Completado: {resultado.name}")
        elif isinstance(resultado, dict):
            self.barra.setValue(100)
            self.lbl_estado.setText("Informacion mostrada en el registro.")
        elif cancelado:
            self.barra.setValue(0)
            self.lbl_estado.setText("Cancelado.")
        else:
            self.barra.setValue(0)
            self.lbl_estado.setText("No se pudo completar (revisa el registro).")
        self._set_ocupado(False)

    @Slot(int, str)
    def _agregar_log(self, nivel: int, texto: str) -> None:
        color = self._COLORES.get(nivel)
        if color:
            self.txt_log.appendHtml(
                f'<span style="color:{color}">{html.escape(texto)}</span>'
            )
        else:
            self.txt_log.appendPlainText(texto)

    # -- cierre --------------------------------------------------------------

    def closeEvent(self, evento) -> None:  # nombre impuesto por Qt
        if self._hilo is not None:
            respuesta = QMessageBox.question(
                self,
                TITULO_APP,
                "Hay una descarga en curso. Salir de todos modos?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if respuesta != QMessageBox.StandardButton.Yes:
                evento.ignore()
                return
            self._hilo.cancelar.set()
            if not self._hilo.wait(5000):
                self._hilo.terminate()
                self._hilo.wait(1000)
            self._hilo = None
        self._guardar_ajustes()
        log.removeHandler(self._handler)
        super().closeEvent(evento)


# Tareas que ejecuta el hilo de trabajo (reciben el callback de progreso y el
# evento de cancelacion como ultimos argumentos).


def _tarea_info(url: str, _prog: core.ProgresoCallback, _canc: threading.Event):
    return core.mostrar_info(url)


def _tarea_video(
    url: str,
    carpeta: Path,
    video: OpcionesVideo,
    prog: core.ProgresoCallback,
    canc: threading.Event,
):
    return core.descargar_video(url, carpeta, video, progreso=prog, cancelar=canc)


def _tarea_audio(
    url: str,
    carpeta: Path,
    audio: OpcionesAudio,
    prog: core.ProgresoCallback,
    canc: threading.Event,
):
    return core.descargar_audio(url, carpeta, audio, progreso=prog, cancelar=canc)


def _a_bool(valor: object) -> bool:
    """QSettings puede devolver bool o la cadena 'true'/'false'."""
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ("1", "true", "yes", "si")


def _icono_app() -> QIcon:
    svg = RUTA_ASSETS / "descargador.svg"
    icono = QIcon(str(svg)) if svg.exists() else QIcon()
    if icono.isNull():
        icono = QIcon.fromTheme("folder-download")
    return icono


# ---------------------------------------------------------------------------
# Punto de entrada de la GUI
# ---------------------------------------------------------------------------


def lanzar_gui(
    ruta_requirements: Path, comprobar: bool = True, url_inicial: str | None = None
) -> int:
    """Crea la aplicacion Qt y muestra la ventana. Devuelve el codigo de salida."""
    # Prefiere el backend nativo de Wayland cuando hay sesion Wayland y el
    # usuario no ha fijado el plugin; xcb queda como alternativa (XWayland/X11).
    if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"

    QApplication.setApplicationName(NOMBRE_APP)
    QApplication.setOrganizationName(NOMBRE_APP)
    QApplication.setApplicationDisplayName(TITULO_APP)
    QApplication.setApplicationVersion(__version__)
    # En Wayland el app_id de la ventana sale de aqui; debe coincidir con
    # assets/descargador.desktop para que el escritorio asocie icono y nombre.
    QApplication.setDesktopFileName(NOMBRE_APP)

    app = QApplication.instance() or QApplication([NOMBRE_APP])
    app.setWindowIcon(_icono_app())

    ventana = VentanaPrincipal(url_inicial)
    ventana.show()

    log.info("Carpeta base: %s", BASE_DESCARGAS)
    log.info("Log: %s", RUTA_LOG)
    log.info("Backend grafico: %s", app.platformName())

    if comprobar:
        threading.Thread(
            target=core.comprobar_actualizaciones,
            args=(ruta_requirements,),
            daemon=True,
        ).start()

    return app.exec()
