"""右侧谱图面板:嵌入独立查看器 + 项目谱图文件列表 + 峰表编辑回写。

复用 viewer.SpectrumViewer(不重复实现谱图功能);列表扫描项目 spectra 目录,
点击 .ft2/.ft3 即在右侧打开。峰表支持添加/删除/编辑行并写回
data_dir(..., "peaks")/<exp>-<data>.csv(经 ProcessingController,登记
manual_peaks WorkflowRun);Poky .list 可导入/导出。GUI 不直接接触处理逻辑。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
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
from gui.dialogs import InfoDialog
from gui.peaks_io import export_peaks_poky, import_peaks_poky, load_peaks
from gui.processing import ProcessingController
from viewer.spectrum3d_panel import Spectrum3DPanel
from viewer.spectrum_viewer import SpectrumViewer


class SpectrumPanel(QWidget):
    """谱图面板:查看器 + 文件列表 + 峰表(加/删/改/存)。"""

    peaks_saved = pyqtSignal()  # 峰表写回后发出(主窗口刷新 Pipeline/日志)

    def __init__(
        self,
        manager: ProjectManager | None = None,
        controller: ProcessingController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager or ProjectManager()
        self.controller = controller or ProcessingController()
        self.controller.set_manager(self._manager)
        self._current_exp_id: str = ""
        self._current_data_id: str = ""
        self._loading_peaks = False
        self._peak_keys: tuple[str, ...] = (
            "Peak_ID",
            "H_shift",
            "N_shift",
            "Intensity",
            "SN",
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = SpectrumViewer()
        self._spectrum3d_panel = Spectrum3DPanel()
        self._spectrum3d_panel.setVisible(False)
        self._spectrum3d_panel.slice_changed.connect(self._render_3d_view)
        self.viewer.add_control_panel(self._spectrum3d_panel)

        self.file_list = QListWidget()
        self.file_list.setMaximumWidth(190)
        self.file_list.itemClicked.connect(self._on_file_clicked)

        self.peak_toolbar = QHBoxLayout()
        self.add_peak_button = QPushButton("添加峰")
        self.add_peak_button.setEnabled(False)
        self.add_peak_button.setToolTip("在峰表追加一行(保存后写回 CSV)")
        self.add_peak_button.clicked.connect(self._on_add_peak)
        self.peak_toolbar.addWidget(self.add_peak_button)
        self.delete_peak_button = QPushButton("删除选中")
        self.delete_peak_button.setEnabled(False)
        self.delete_peak_button.setToolTip("删除峰表中选中的行")
        self.delete_peak_button.clicked.connect(self._on_delete_peak)
        self.peak_toolbar.addWidget(self.delete_peak_button)
        self.import_poky_button = QPushButton("导入 Poky")
        self.import_poky_button.setEnabled(False)
        self.import_poky_button.setToolTip("从 Poky/Sparky .list 导入峰表")
        self.import_poky_button.clicked.connect(self._on_import_poky)
        self.peak_toolbar.addWidget(self.import_poky_button)
        self.export_poky_button = QPushButton("导出 Poky")
        self.export_poky_button.setEnabled(False)
        self.export_poky_button.setToolTip("把当前峰表导出为 Poky/Sparky .list")
        self.export_poky_button.clicked.connect(self._export_peaks_poky)
        self.peak_toolbar.addWidget(self.export_poky_button)
        self.save_peaks_button = QPushButton("保存峰表")
        self.save_peaks_button.setEnabled(False)
        self.save_peaks_button.setToolTip("把峰表写回 data/peaks/<exp>-<data>.csv 并登记")
        self.save_peaks_button.clicked.connect(self._on_save_peaks)
        self.peak_toolbar.addWidget(self.save_peaks_button)
        self.peak_toolbar.addStretch(1)

        self.peak_table = QTableWidget(0, 5)
        self.peak_table.setHorizontalHeaderLabels(list(self._peak_keys))
        self.peak_table.horizontalHeader().setStretchLastSection(True)
        self.peak_table.setMaximumHeight(150)
        self.peak_table.itemSelectionChanged.connect(self._on_peak_row_selected)
        self.peak_table.itemChanged.connect(self._on_peak_cell_edited)
        self._peaks: list[dict] = []
        self._current_spectrum: Path | None = None
        self.placeholder = QLabel("未打开项目\n\n从左侧选择实验,或点击下方谱图文件查看结果。")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet("color: #888;")

        # 上下布局:上方查看器,中部文件列表,下方峰表
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.viewer)
        splitter.addWidget(self.file_list)
        self.peak_toolbar_widget = QWidget()
        self.peak_toolbar_widget.setLayout(self.peak_toolbar)
        splitter.addWidget(self.peak_toolbar_widget)
        splitter.addWidget(self.peak_table)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([500, 80, 120])
        self.viewer.peak_clicked.connect(self._on_viewer_peak_clicked)
        self.file_list.setMaximumWidth(16777215)  # 取消横向宽度限制
        layout.addWidget(splitter)
        self.refresh()

    @property
    def manager(self) -> ProjectManager:
        return self._manager

    @manager.setter
    def manager(self, value: ProjectManager) -> None:
        self._manager = value
        self.controller.set_manager(value)

    def set_context(self, exp_id: str, data_id: str = "") -> None:
        self._current_exp_id = exp_id or ""
        self._current_data_id = data_id or ""
        self.refresh()

    def refresh(self) -> None:
        """刷新谱图文件列表;无文件时隐藏列表(避免右下角空白)。"""
        self.file_list.clear()
        has_context = (
            self.manager.project is not None
            and bool(self._current_exp_id)
            and bool(self._current_data_id)
        )
        self.add_peak_button.setEnabled(has_context)
        self.delete_peak_button.setEnabled(has_context and self.peak_table.rowCount() > 0)
        self.import_poky_button.setEnabled(has_context)
        if self.manager.project is None or not self._current_exp_id:
            self.file_list.setVisible(False)
            self.peak_table.setVisible(False)
            self.peak_toolbar_widget.setVisible(False)
            self._spectrum3d_panel.setVisible(False)
            return
        paths = self._spectrum_paths()
        for path in paths:
            self.file_list.addItem(path.name)
        self.file_list.setVisible(bool(paths))
        self.peak_table.setVisible(bool(paths))
        self.peak_toolbar_widget.setVisible(bool(paths))
        self.export_poky_button.setEnabled(False)
        self.save_peaks_button.setEnabled(False)
        if paths:
            first = paths[0]
            if self._current_spectrum != first:
                if self.open_spectrum(first):
                    self._current_spectrum = first
                else:
                    self._current_spectrum = None
            self._load_peaks(first)
        else:
            self._current_spectrum = None
            self._spectrum3d_panel.clear()
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
        """加载谱图到查看器;失败返回 False(不弹窗,由调用方决定提示)。

        .ft3 走 3D 查看路径(契约 §10):绑定 Spectrum3D 并显示默认切片,
        3D 面板提供平面/切片/投影切换;.ft2 走二维叠加。
        """
        try:
            if path.suffix.lower() == ".ft3":
                from viewer.spectrum import Spectrum3D

                self._current_spectrum = path
                self._spectrum3d_panel.set_spectrum3d(
                    Spectrum3D.load_from_ft3(path)
                )
                self._spectrum3d_panel.setVisible(True)
                self._render_3d_view()
                return True
            from viewer.spectrum import Spectrum

            spectrum = Spectrum.load_from_ft2(path)
        except Exception:  # noqa: BLE001 - 损坏文件统一由调用方提示
            return False
        self._spectrum3d_panel.clear()
        self.viewer.clear()
        self.viewer.add_spectrum(spectrum, name=name or path.stem)
        return True

    def _render_3d_view(self) -> None:
        """按 3D 面板当前平面/切片/投影渲染二维视图并重挂峰标记。"""
        spectrum = self._spectrum3d_panel.current_spectrum()
        if spectrum is None:
            return
        base = self._current_spectrum.stem if self._current_spectrum else "3D"
        self.viewer.clear()
        self.viewer.add_spectrum(
            spectrum,
            name=self._spectrum3d_panel.current_name(base),
        )
        if self._peaks:
            self.viewer.set_peaks(self._peaks)

    def _on_file_clicked(self, item) -> None:
        paths = [p for p in self._spectrum_paths() if p.name == item.text()]
        if not paths:
            return
        if not self.open_spectrum(paths[0]):
            self.viewer.clear()
        else:
            self._current_spectrum = paths[0]
        self._load_peaks(paths[0])

    # ------------------------------------------------------------------
    # 峰表(Peak CSV)与谱图双向联动 + 编辑回写
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
        self._populate_peak_table()
        self.viewer.set_peaks(peaks)
        self.export_poky_button.setEnabled(True)
        self.save_peaks_button.setEnabled(True)

    def _set_peak_columns(self, is_3d: bool) -> None:
        keys: tuple[str, ...] = (
            ("Peak_ID", "F1_shift", "F2_shift", "F3_shift", "Intensity", "SN")
            if is_3d
            else ("Peak_ID", "H_shift", "N_shift", "Intensity", "SN")
        )
        if keys == self._peak_keys:
            return
        self._peak_keys = keys
        self.peak_table.setColumnCount(len(keys))
        self.peak_table.setHorizontalHeaderLabels(list(keys))

    def _populate_peak_table(self) -> None:
        """把 self._peaks 写入表格(2D/3D 列自动切换)。"""
        is_3d = bool(self._peaks) and "F1_shift" in self._peaks[0]
        self._set_peak_columns(is_3d)
        self._loading_peaks = True
        try:
            self.peak_table.setRowCount(len(self._peaks))
            for row, peak in enumerate(self._peaks):
                for col, key in enumerate(self._peak_keys):
                    self.peak_table.setItem(
                        row, col, QTableWidgetItem(str(peak.get(key, "")))
                    )
                # 行首单元格保存完整峰 dict(label 等编辑外字段随行保留)
                self.peak_table.item(row, 0).setData(0x0100, dict(peak))
        finally:
            self._loading_peaks = False

    def _table_peaks(self) -> list[dict]:
        """把表格当前内容读回为峰 dict 列表(未编辑行为空串)。"""
        peaks: list[dict] = []
        for row in range(self.peak_table.rowCount()):
            peak: dict = {}
            row_item = self.peak_table.item(row, 0)
            if row_item is not None:
                stored = row_item.data(0x0100)
                if isinstance(stored, dict):
                    peak.update(stored)
            for col, key in enumerate(self._peak_keys):
                item = self.peak_table.item(row, col)
                peak[key] = item.text() if item is not None else ""
            peaks.append(peak)
        return peaks

    def _sync_peaks_in_memory(self) -> None:
        """表格编辑后同步内存 _peaks(不立即重建 viewer,避免卡顿)。"""
        if self._loading_peaks:
            return
        self._peaks = self._table_peaks()
        self.delete_peak_button.setEnabled(self.peak_table.rowCount() > 0)

    def _on_peak_cell_edited(self, _item) -> None:
        self._sync_peaks_in_memory()

    def _on_add_peak(self) -> None:
        if not (self.manager.project is not None and self._current_exp_id):
            return
        next_id = (
            max((int(p.get("Peak_ID", 0) or 0) for p in self._peaks), default=0) + 1
        )
        row = self.peak_table.rowCount()
        self.peak_table.insertRow(row)
        self._loading_peaks = True
        try:
            for col, key in enumerate(self._peak_keys):
                value = str(next_id) if key == "Peak_ID" else ""
                self.peak_table.setItem(row, col, QTableWidgetItem(value))
            self.peak_table.item(row, 0).setData(0x0100, {})
        finally:
            self._loading_peaks = False
        self._sync_peaks_in_memory()
        self.viewer.set_peaks(self._peaks)
        self.save_peaks_button.setEnabled(True)

    def _on_delete_peak(self) -> None:
        rows = sorted(
            {index.row() for index in self.peak_table.selectionModel().selectedRows()},
            reverse=True,
        )
        if not rows:
            return
        self._loading_peaks = True
        try:
            for row in rows:
                self.peak_table.removeRow(row)
        finally:
            self._loading_peaks = False
        self._sync_peaks_in_memory()
        self.viewer.set_peaks(self._peaks)
        self.save_peaks_button.setEnabled(bool(self._peaks))

    def _on_import_poky(self) -> None:
        if self.manager.project is None or not self._current_exp_id:
            return
        start = str(Path.home())
        try:
            start = str(
                self.manager.data_dir(
                    self._current_exp_id, self._current_data_id, "peaks"
                )
            )
        except Exception:  # noqa: BLE001
            pass
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 Poky 峰表", start, "Poky 峰表 (*.list);;所有文件 (*)"
        )
        if not path:
            return
        try:
            peaks = import_peaks_poky(path)
        except Exception as exc:  # noqa: BLE001
            InfoDialog.show_info(self, "导入失败", str(exc))
            return
        if not peaks:
            InfoDialog.show_info(self, "导入结果", "文件中没有可解析的峰行")
            return
        self._peaks = peaks
        self._populate_peak_table()
        self.viewer.set_peaks(peaks)
        self.export_poky_button.setEnabled(True)
        self.save_peaks_button.setEnabled(True)
        InfoDialog.show_info(
            self, "导入完成", f"已从 Poky 峰表导入 {len(peaks)} 个峰\n(点「保存峰表」写回 CSV)"
        )

    def _on_save_peaks(self) -> None:
        """峰表写回 data/peaks/<exp>-<data>.csv 并登记 manual_peaks 运行。"""
        if self.manager.project is None or not self._current_exp_id:
            InfoDialog.show_info(self, "提示", "请先选中数据")
            return
        if not self._current_data_id:
            InfoDialog.show_info(self, "提示", "请先选中数据节点")
            return
        peaks = self._table_peaks()
        try:
            self.controller.set_manager(self.manager)
            csv_path = self.controller.save_peaks_manual(
                None,
                peaks,
                exp_id=self._current_exp_id,
                data_id=self._current_data_id,
            )
        except Exception as exc:  # noqa: BLE001 - 错误统一提示
            InfoDialog.show_info(self, "保存失败", f"{type(exc).__name__}: {exc}")
            return
        self._peaks = peaks
        self.viewer.set_peaks(peaks)
        self.save_peaks_button.setEnabled(True)
        self.peaks_saved.emit()
        InfoDialog.show_info(self, "保存完成", f"峰表已写入:\n{csv_path}")

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
            InfoDialog.show_info(self, "导出完成", f"已导出 Poky 峰表: {path}")
        except Exception as exc:  # noqa: BLE001
            InfoDialog.show_info(self, "导出失败", str(exc))

    def _clear_peaks(self) -> None:
        self._peaks = []
        self.peak_table.setRowCount(0)
        self.viewer.set_peaks([])
        self.export_poky_button.setEnabled(False)
        self.save_peaks_button.setEnabled(False)
        self.delete_peak_button.setEnabled(False)

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
