"""右侧谱图面板:嵌入独立查看器 + 项目谱图文件列表。

复用 viewer.SpectrumViewer(不重复实现谱图功能);列表扫描项目 spectra 目录,
点击 .ft2/.ft3 即在右侧打开。GUI 不直接接触处理逻辑,只展示产物。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QLabel,
    QListWidget,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.project import ProjectManager
from viewer.spectrum_viewer import SpectrumViewer


class SpectrumPanel(QWidget):
    """谱图面板:左侧产物文件列表,右侧查看器。"""

    def __init__(
        self, manager: ProjectManager | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.manager = manager or ProjectManager()
        self._current_exp_id: str = ""
        self._current_data_id: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = SpectrumViewer()

        self.file_list = QListWidget()
        self.file_list.setMaximumWidth(190)
        self.file_list.itemClicked.connect(self._on_file_clicked)
        self.placeholder = QLabel("未打开项目\n\n从左侧选择实验,或点击下方谱图文件查看结果。")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet("color: #888;")

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.file_list)
        splitter.addWidget(self.viewer)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([170, 560])
        layout.addWidget(splitter)
        self.refresh()

    def set_context(self, exp_id: str, data_id: str = "") -> None:
        self._current_exp_id = exp_id or ""
        self._current_data_id = data_id or ""
        self.refresh()

    def refresh(self) -> None:
        """刷新谱图文件列表(schema 1.3 data_dir 新布局,旧扁平路径回退)。"""
        self.file_list.clear()
        if self.manager.project is None or not self._current_exp_id:
            return
        for path in self._spectrum_paths():
            self.file_list.addItem(path.name)

    def _spectrum_paths(self) -> list[Path]:
        """当前实验/数据下的谱图文件(新布局优先,旧扁平路径回退)。"""
        paths: list[Path] = []
        exp_id = self._current_exp_id
        data_id = self._current_data_id
        if self.manager.project is None:
            return paths
        try:
            if data_id:
                spectra_dir = self.manager.data_dir(exp_id, data_id, "spectra")
                for ext in (".ft2", ".ft3"):
                    paths.extend(
                        sorted(spectra_dir.glob(f"{exp_id}-{data_id}*{ext}"))
                    )
                if paths:
                    return paths
        except Exception:  # noqa: BLE001 - 新布局不可用回退旧路径
            pass
        # 旧扁平布局回退(项目根 spectra/,{exp_id}* 通配)
        spectra_dir = self.manager.dir_path("spectra")
        for ext in (".ft2", ".ft3"):
            paths.extend(sorted(spectra_dir.glob(f"{exp_id}*{ext}")))
        return paths

    def open_spectrum(self, path: Path, name: str | None = None) -> bool:
        """加载谱图到查看器;失败返回 False(不弹窗,由调用方决定提示)。"""
        try:
            from viewer.spectrum import Spectrum

            spectrum = Spectrum.load_from_ft2(path)
        except Exception:  # noqa: BLE001 - 损坏文件统一由调用方提示
            return False
        self.viewer.clear()
        self.viewer.add_spectrum(spectrum, name=name or path.stem)
        return True

    def _on_file_clicked(self, item) -> None:
        paths = [p for p in self._spectrum_paths() if p.name == item.text()]
        if not paths:
            return
        if not self.open_spectrum(paths[0]):
            self.viewer.clear()

