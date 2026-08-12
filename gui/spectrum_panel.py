"""右侧谱图面板:嵌入独立查看器 + 项目谱图文件列表。

复用 viewer.SpectrumViewer(不重复实现谱图功能);列表扫描项目 spectra 目录,
点击 .ft2/.ft3 即在右侧打开。GUI 不直接接触处理逻辑,只展示产物。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.project import ProjectManager
from gui.peaks_io import export_peaks_poky, load_peaks
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
        self.peak_toolbar = QHBoxLayout()
        self.export_poky_button = QPushButton("导出 Poky")
        self.export_poky_button.setEnabled(False)
        self.export_poky_button.setToolTip("把当前峰表导出为 Poky/Sparky .list")
        self.export_poky_button.clicked.connect(self._export_peaks_poky)
        self.peak_toolbar.addWidget(self.export_poky_button)
        self.peak_toolbar.addStretch(1)
        self.peak_table = QTableWidget(0, 5)
        self.peak_table.setHorizontalHeaderLabels(
            ["Peak_ID", "H_shift", "N_shift", "Intensity", "SN"]
        )
        self.peak_table.horizontalHeader().setStretchLastSection(True)
        self.peak_table.setMaximumHeight(150)
        self.peak_table.itemSelectionChanged.connect(self._on_peak_row_selected)
        self._peaks: list[dict] = []
        self.placeholder = QLabel("未打开项目\n\n从左侧选择实验,或点击下方谱图文件查看结果。")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet("color: #888;")

        # 上下布局:上方查看器,中部文件列表,下方峰表
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.viewer)
        splitter.addWidget(self.file_list)
        toolbar_widget = QWidget()
        toolbar_widget.setLayout(self.peak_toolbar)
        splitter.addWidget(toolbar_widget)
        splitter.addWidget(self.peak_table)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([500, 80, 120])
        self.viewer.peak_clicked.connect(self._on_viewer_peak_clicked)
        self.file_list.setMaximumWidth(16777215)  # 取消横向宽度限制
        layout.addWidget(splitter)
        self.refresh()

    def set_context(self, exp_id: str, data_id: str = "") -> None:
        self._current_exp_id = exp_id or ""
        self._current_data_id = data_id or ""
        self.refresh()

    def refresh(self) -> None:
        """刷新谱图文件列表;无文件时隐藏列表(避免右下角空白)。"""
        self.file_list.clear()
        if self.manager.project is None or not self._current_exp_id:
            self.file_list.setVisible(False)
            self.peak_table.setVisible(False)
            return
        paths = self._spectrum_paths()
        for path in paths:
            self.file_list.addItem(path.name)
        self.file_list.setVisible(bool(paths))
        self.peak_table.setVisible(bool(paths))
        self.export_poky_button.setEnabled(False)
        if paths:
            self._load_peaks(paths[0])
        else:
            self._clear_peaks()

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
        self._load_peaks(paths[0])

    # ------------------------------------------------------------------
    # 峰表(Peak CSV)与谱图双向联动
    # ------------------------------------------------------------------
    def _peak_csv_path(self, spectrum_path: Path) -> Path | None:
        if self.manager.project is None:
            return None
        try:
            if self._current_data_id:
                peaks_dir = self.manager.data_dir(
                    self._current_exp_id, self._current_data_id, "peaks"
                )
                candidate = peaks_dir / f"{self._current_exp_id}-{self._current_data_id}.csv"
                if candidate.is_file():
                    return candidate
        except Exception:  # noqa: BLE001
            pass
        legacy = self.manager.dir_path("peaks") / f"{self._current_exp_id}.csv"
        return legacy if legacy.is_file() else None

    def _load_peaks(self, spectrum_path: Path) -> None:
        self._clear_peaks()
        csv_path = self._peak_csv_path(spectrum_path)
        if csv_path is None:
            return
        peaks = load_peaks(csv_path)
        if not peaks:
            return
        self._peaks = peaks
        self.peak_table.setRowCount(len(peaks))
        for row, peak in enumerate(peaks):
            for col, key in enumerate(("Peak_ID", "H_shift", "N_shift", "Intensity", "SN")):
                self.peak_table.setItem(row, col, QTableWidgetItem(str(peak.get(key, ""))))
            self.peak_table.item(row, 0).setData(0x0100, row)
        self.viewer.set_peaks(peaks)
        self.export_poky_button.setEnabled(True)

    def _export_peaks_poky(self) -> None:
        """导出当前峰表为 Poky .list;无峰表/谱图时禁用。"""
        if not self._peaks or self.manager.project is None:
            return
        default = None
        try:
            default = (
                self.manager.data_dir(
                    self._current_exp_id, self._current_data_id, "peaks"
                )
                / f"{self._current_exp_id}-{self._current_data_id}.list"
            )
        except Exception:  # noqa: BLE001
            default = None
        start = str(default.parent) if default is not None else str(Path.home())
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 Poky 峰表", str(default) if default else start,
            "Poky 峰表 (*.list);;所有文件 (*)",
        )
        if not path:
            return
        try:
            export_peaks_poky(path, self._peaks, ndim=2)
            from gui.dialogs import InfoDialog

            InfoDialog.show_info(self, "导出完成", f"已导出 Poky 峰表: {path}")
        except Exception as exc:  # noqa: BLE001
            from gui.dialogs import InfoDialog

            InfoDialog.show_info(self, "导出失败", str(exc))

    def _clear_peaks(self) -> None:
        self._peaks = []
        self.peak_table.setRowCount(0)
        self.viewer.set_peaks([])
        self.export_poky_button.setEnabled(False)

    def _on_viewer_peak_clicked(self, row: int) -> None:
        if 0 <= row < self.peak_table.rowCount():
            self.peak_table.selectRow(row)

    def _on_peak_row_selected(self) -> None:
        rows = self.peak_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        if 0 <= row < len(self._peaks):
            self.viewer.highlight_peak(row)

