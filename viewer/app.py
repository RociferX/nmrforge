"""Independent spectrum viewing window: Decoupled from project management GUI,.ft2 can be opened
directly for viewing."""

from __future__ import annotations

import sys
from pathlib import Path

from qtcompat.QtCore import QEvent, QObject, QTimer
from qtcompat.QtGui import QAction, QDragEnterEvent, QDropEvent
from qtcompat.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QVBoxLayout,
)

from ui_support.i18n import tr
from viewer.spectrum import Spectrum, Spectrum1D, Spectrum3D
from viewer.spectrum3d_panel import Spectrum3DPanel
from viewer.spectrum_viewer import SpectrumViewer


class InfoDialog(QDialog):
    """Replaces QMessageBox (avoids mouse grab warning of modal pop-ups under Qt6 Windows)."""

    def __init__(self, parent, title: str, text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


def show_info(parent, title: str, text: str) -> None:
    InfoDialog(parent, title, text).exec()


_ASPECT_CHOICES = (
    (tr("free"), None),
    ("1:1", 1.0),
    ("2:1", 2.0),
    ("4:1", 4.0),
)


class SpectrumWindow(QMainWindow):
    """Independent spectrum window:Open/stacked spectrum, aspect ratio, peak mode, reset view."""

    def __init__(self, parent=None, start_dir: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("NMRForge spectrum viewer"))
        self.resize(1080, 720)
        self.start_dir = start_dir
        self.viewer = SpectrumViewer()
        self.setCentralWidget(self.viewer)
        self._recent: list[str] = []
        self._spectrum3d_active = False
        self._spectrum3d_name = ""
        self._spectrum3d_panel = Spectrum3DPanel()
        self._spectrum3d_panel.setVisible(False)
        self._spectrum3d_panel.slice_changed.connect(self._render_3d)
        self.viewer.add_control_panel(self._spectrum3d_panel)
        self._build_menus()
        self.setAcceptDrops(True)
        self.statusBar().showMessage(tr("Open.ft2/.ft3 spectrum to start (supports drag and drop)"))

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu(tr("&File"))
        open_action = QAction(tr("Open spectrum..."), self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_spectrum)
        file_menu.addAction(open_action)
        self.recent_menu = file_menu.addMenu(tr("recent files"))
        file_menu.addSeparator()
        file_menu.addAction(tr("clear spectrum"), self.clear_spectra)
        file_menu.addSeparator()
        quit_action = QAction(tr("quit"), self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu(tr("&View"))
        reset_action = QAction(tr("full spectrum view"), self)
        reset_action.setShortcut("Home")
        reset_action.triggered.connect(self.viewer.reset_view)
        view_menu.addAction(reset_action)
        aspect_menu = view_menu.addMenu(tr("display aspect ratio"))
        self._aspect_actions: list[QAction] = []
        for label, ratio in _ASPECT_CHOICES:
            action = QAction(label, self, checkable=True)
            action.triggered.connect(
                lambda _checked=False, r=ratio: self._set_aspect(r)
            )
            aspect_menu.addAction(action)
            self._aspect_actions.append(action)
        self._aspect_actions[0].setChecked(True)  # Default free.

        peak_menu = self.menuBar().addMenu(tr("&Peaks"))
        self._peak_actions: list[QAction] = []
        for label, mode in ((
            tr(
            "Select "
            "peak",
        ), "select"), (tr(
            "Add peak",
        ), "add"), (tr(
            "delete "
            "peak",
        ), "delete")):
            action = QAction(label, self, checkable=True)
            action.triggered.connect(
                lambda _checked=False, m=mode: self._set_peak_mode(m)
            )
            peak_menu.addAction(action)
            self._peak_actions.append(action)
        self._peak_actions[0].setChecked(True)

        help_menu = self.menuBar().addMenu(tr("&Help"))
        help_menu.addAction(tr("Operating Instructions"), self._show_help)

    # ------------------------------------------------------------- files

    def open_spectrum(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("Open NMRPipe spectrum"),
            self.start_dir or "",
            tr("NMRPipe spectrum (*.ft2 *.ft3 *.ft1 *.fid);; all files (*)"),
        )
        if path:
            self.load_spectrum(Path(path))

    def load_spectrum(self, path: Path, name: str | None = None) -> bool:
        """Load spectrum (.ft2/.ft3) and display; pop up a window on failure and return False."""
        # 0.2.199-patch29dh(user):axis sequence/Tags only start with The.ft3 header shall prevail,
        # and metadata will not be transmitted to the loader nuclear/Label (the software handles
        # axis rearrangement, and the metadata collection sequence cannot be divulged).
        try:
            if path.suffix.lower() == ".ft3":
                spectrum3d = Spectrum3D.load_from_ft3(path, lazy=True)
                spectrum = None
                spectrum1d = None
            elif path.suffix.lower() == ".fid":
                spectrum1d = Spectrum1D.load_from_fid(path)
                spectrum3d = None
                spectrum = None
            else:
                spectrum3d = None
                spectrum = Spectrum.load_from_ft2(path)
                spectrum1d = None
        except Exception as exc:  # noqa: BLE001 - Unified prompts such as file damage.
            show_info(self, tr("Open failed"), f"{path}\n{exc}")
            return False
        if spectrum3d is not None:
            self._enter_3d_mode(spectrum3d, name or path.stem)
        else:
            if self._spectrum3d_active:
                self._spectrum3d_active = False
                self._spectrum3d_panel.clear()
                self.viewer.clear()
            self.viewer.add_spectrum(
                spectrum1d if spectrum1d is not None else spectrum,
                name=name or path.stem,
            )
        self._recent = [str(path), *[p for p in self._recent if p != str(path)]][:8]
        self._refresh_recent()
        if spectrum3d is not None:
            status_text = tr("Loaded 3D spectrum: {p0}", p0=path)
        elif spectrum1d is not None:
            status_text = tr("Loaded FID: {p0}", p0=path)
        else:
            status_text = tr("Loaded: {p0}", p0=path)
        self.statusBar().showMessage(status_text)
        self.setWindowTitle(tr("NMRForge spectrum viewer - {p0}", p0=path.name))
        return True

    def _enter_3d_mode(self, spectrum3d: Spectrum3D, name: str) -> None:
        """Enter 3D viewing mode: Bind panel (automatically renders the default F1-F2 middle
        slice)."""
        self._spectrum3d_active = True
        self._spectrum3d_name = name
        self._spectrum3d_panel.set_spectrum3d(spectrum3d)
        self._spectrum3d_panel.setVisible(True)

    def _render_3d(self) -> None:
        """Press the current plane of the panel/slice/Rendering a 2D view in projection mode (reuse
        2D drawing)."""
        spectrum = self._spectrum3d_panel.current_spectrum()
        if spectrum is None:
            return
        self.viewer.clear()
        self.viewer.add_spectrum(
            spectrum,
            name=self._spectrum3d_panel.current_name(self._spectrum3d_name),
        )

    def clear_spectra(self) -> None:
        self._spectrum3d_active = False
        self._spectrum3d_panel.clear()
        self.viewer.clear()
        self.setWindowTitle(tr("NMRForge spectrum viewer"))
        self.statusBar().showMessage(tr("spectrum cleared"))

    def _refresh_recent(self) -> None:
        self.recent_menu.clear()
        for path in self._recent:
            action = self.recent_menu.addAction(path)
            action.triggered.connect(
                lambda _checked=False, p=path: self.load_spectrum(Path(p))
            )

    # ------------------------------------------------------------- view

    def _set_aspect(self, ratio: float | None) -> None:
        self.viewer.set_aspect_ratio(ratio)
        for action, (_, r) in zip(self._aspect_actions, _ASPECT_CHOICES):
            action.setChecked(r == ratio)

    def _set_peak_mode(self, mode: str) -> None:
        self.viewer.set_peak_click_mode(mode)
        choices = ((tr(
            "Select "
            "peak",
        ), "select"), (tr(
            "Add peak",
        ), "add"), (tr(
            "delete "
            "peak",
        ), "delete"))
        for action, (_, m) in zip(self._peak_actions, choices):
            action.setChecked(m == mode)

    def _show_help(self) -> None:
        show_info(
            self,
            tr("Operating Instructions"),
            tr(
                "Left drag: box zoom\nMiddle drag: pan\nWheel: zoom\nHome / full-spectrum view: "
                "restore the full range\nView menu: lock the display aspect ratio\nPeak menu: "
                "switch the single-click behaviour (select / add / delete peaks)\n3D spectra "
                "(.ft3): pick the plane to view in the right-hand panel (F1-F2 / F1-F3 / F2-F3),\n "
                " drag the slice slider to step through the planes, or switch to the MIP / summed "
                "projection\n.fid: one dimension shows the 1D trace; two dimensions show the whole "
                "time-domain block nmrDraw style\n  (rows = individual FIDs, columns = "
                "direct-dimension time points; turn on the 1D strip to inspect them one by "
                "one)\n2D spectra: right-click the spectrum to extract a 1D row/column slice; "
                "right-click a layer in the list to delete "
                "it",
            ),
        )

    # ------------------------------------------------------------- drops

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix in (".ft2", ".ft3", ".ft1", ".fid"):
                self.load_spectrum(path)


def _install_dialog_centering(app) -> None:
    """Independent viewer: QDialog is automatically centered on the screen when displayed
    (consistent with the main application, 0.2.112)."""
    from qtcompat.QtWidgets import QApplication

    def _center(dialog: QDialog) -> None:
        parent = dialog.parentWidget()
        screen = None
        if parent is not None and parent.window() is not None:
            screen = QApplication.screenAt(parent.window().frameGeometry().center())
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        dialog.move(
            geo.left() + max(0, (geo.width() - dialog.width()) // 2),
            geo.top() + max(0, (geo.height() - dialog.height()) // 2),
        )

    class _DialogCenterFilter(QObject):
        def eventFilter(self, obj, event) -> bool:
            if isinstance(obj, QDialog) and event.type() == QEvent.Type.Show:
                QTimer.singleShot(0, lambda d=obj: _center(d))
            return super().eventFilter(obj, event)

    app._dialog_centering_filter = _DialogCenterFilter(app)
    app.installEventFilter(app._dialog_centering_filter)


def main(argv: list[str] | None = None) -> int:
    """Command line entry: nmrforge-viewer [spectrum.ft2...]."""
    from qtcompat.QtWidgets import QApplication

    args = argv if argv is not None else sys.argv[1:]
    paths = [Path(a) for a in args if Path(a).suffix in (".ft2", ".ft3", ".ft1", ".fid")]
    app = QApplication(sys.argv[:1] + args)
    _install_dialog_centering(app)
    from ui_support.theme import app_icon, apply_dark_theme

    apply_dark_theme(app)
    _icon = app_icon()
    if _icon is not None:
        app.setWindowIcon(_icon)
    app.setApplicationName("NMRForge")
    try:
        app.setDesktopFileName("NMRForge")
    except AttributeError:
        pass
    window = SpectrumWindow()
    window.show()
    for path in paths:
        window.load_spectrum(path)
    return app.exec()
