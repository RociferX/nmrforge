"""独立谱图查看窗口:与项目管理 GUI 解耦,可直接打开 .ft2 查看。"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QTimer
from PyQt6.QtGui import QAction, QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QVBoxLayout,
)

from viewer.spectrum import Spectrum, Spectrum1D, Spectrum3D
from viewer.spectrum3d_panel import Spectrum3DPanel
from viewer.spectrum_viewer import SpectrumViewer


class InfoDialog(QDialog):
    """替代 QMessageBox(避免 Qt6 Windows 下模态弹窗的鼠标 grab 警告)。"""

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
    ("自由", None),
    ("1:1", 1.0),
    ("2:1", 2.0),
    ("4:1", 4.0),
)


class SpectrumWindow(QMainWindow):
    """独立谱图窗口:打开/叠加谱、长宽比、峰模式、重置视图。"""

    def __init__(self, parent=None, start_dir: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("NMRForge 谱图查看器")
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
        self.statusBar().showMessage("打开 .ft2/.ft3 谱图开始(支持拖放)")

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("文件(&F)")
        open_action = QAction("打开谱图...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_spectrum)
        file_menu.addAction(open_action)
        self.recent_menu = file_menu.addMenu("最近文件")
        file_menu.addSeparator()
        file_menu.addAction("清空谱图", self.clear_spectra)
        file_menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("视图(&V)")
        reset_action = QAction("全谱视图", self)
        reset_action.setShortcut("Home")
        reset_action.triggered.connect(self.viewer.reset_view)
        view_menu.addAction(reset_action)
        aspect_menu = view_menu.addMenu("显示长宽比")
        self._aspect_actions: list[QAction] = []
        for label, ratio in _ASPECT_CHOICES:
            action = QAction(label, self, checkable=True)
            action.triggered.connect(
                lambda _checked=False, r=ratio: self._set_aspect(r)
            )
            aspect_menu.addAction(action)
            self._aspect_actions.append(action)
        self._aspect_actions[1].setChecked(True)  # 默认 1:1 正方形

        peak_menu = self.menuBar().addMenu("峰(&P)")
        self._peak_actions: list[QAction] = []
        for label, mode in (("选中峰", "select"), ("添加峰", "add"), ("删除峰", "delete")):
            action = QAction(label, self, checkable=True)
            action.triggered.connect(
                lambda _checked=False, m=mode: self._set_peak_mode(m)
            )
            peak_menu.addAction(action)
            self._peak_actions.append(action)
        self._peak_actions[0].setChecked(True)

        help_menu = self.menuBar().addMenu("帮助(&H)")
        help_menu.addAction("操作说明", self._show_help)

    # ------------------------------------------------------------- files

    def open_spectrum(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "打开 NMRPipe 谱图",
            self.start_dir or "",
            "NMRPipe 谱 (*.ft2 *.ft3 *.ft1 *.fid);;所有文件 (*)",
        )
        if path:
            self.load_spectrum(Path(path))

    def load_spectrum(self, path: Path, name: str | None = None) -> bool:
        """加载谱图(.ft2/.ft3)并显示;失败时弹窗并返回 False。"""
        # 0.2.199-补29dh(用户):轴序/标签只以 .ft3 头部为准,不向加载器
        # 传 metadata 核/标签(软件处理有轴重排,metadata 采集序不可作兜底)
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
        except Exception as exc:  # noqa: BLE001 - 文件损坏等统一提示
            show_info(self, "打开失败", f"{path}\n{exc}")
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
            status_text = f"已加载 3D 谱: {path}"
        elif spectrum1d is not None:
            status_text = f"已加载 FID: {path}"
        else:
            status_text = f"已加载: {path}"
        self.statusBar().showMessage(status_text)
        self.setWindowTitle(f"NMRForge 谱图查看器 - {path.name}")
        return True

    def _enter_3d_mode(self, spectrum3d: Spectrum3D, name: str) -> None:
        """进入 3D 查看模式:绑定面板(自动渲染默认 F1-F2 中间切片)。"""
        self._spectrum3d_active = True
        self._spectrum3d_name = name
        self._spectrum3d_panel.set_spectrum3d(spectrum3d)
        self._spectrum3d_panel.setVisible(True)

    def _render_3d(self) -> None:
        """按面板当前平面/切片/投影模式渲染二维视图(复用 2D 绘制)。"""
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
        self.setWindowTitle("NMRForge 谱图查看器")
        self.statusBar().showMessage("谱图已清空")

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
        choices = (("选中峰", "select"), ("添加峰", "add"), ("删除峰", "delete"))
        for action, (_, m) in zip(self._peak_actions, choices):
            action.setChecked(m == mode)

    def _show_help(self) -> None:
        show_info(
            self,
            "操作说明",
            "左键拖拽:框选放大\n"
            "中键拖拽:平移\n"
            "滚轮:缩放\n"
            "Home / 全谱视图:恢复完整范围\n"
            "视图菜单:锁定显示长宽比\n"
            "峰菜单:切换单击行为(选中/添加/删除峰)\n"
            "3D 谱(.ft3):右侧面板选择查看平面(F1-F2/F1-F3/F2-F3),\n"
            "  拖动切片滑块逐平面查看,或切换 MIP/求和投影\n"
            ".fid:一维显示 1D 迹线;二维按 nmrDraw 式显示整块时域图\n"
            "  (行=各 FID,列=直接维时点,开 1D 条带可逐条查看)\n"
            "二维谱:右键谱图提取 1D 行/列切片;图层列表右键删除图层",
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
    """独立查看器:QDialog 显示时自动居中到所在屏幕(与主应用一致,0.2.112)。"""
    from PyQt6.QtWidgets import QApplication

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
    """命令行入口:nmrforge-viewer [spectrum.ft2 ...]"""
    from PyQt6.QtWidgets import QApplication

    args = argv if argv is not None else sys.argv[1:]
    paths = [Path(a) for a in args if Path(a).suffix in (".ft2", ".ft3", ".ft1", ".fid")]
    app = QApplication(sys.argv[:1] + args)
    _install_dialog_centering(app)
    from gui.theme import app_icon, apply_dark_theme

    apply_dark_theme(app)
    _icon = app_icon()
    if _icon is not None:
        app.setWindowIcon(_icon)
    window = SpectrumWindow()
    window.show()
    for path in paths:
        window.load_spectrum(path)
    return app.exec()
