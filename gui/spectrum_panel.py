"""右侧谱图面板:嵌入独立查看器 + 项目谱图文件列表 + 峰表编辑回写。

复用 viewer.SpectrumViewer(不重复实现谱图功能);列表扫描项目 spectra 目录,
点击 .ft2/.ft3 即在右侧打开。峰表支持添加/删除/编辑行并写回
data_dir(..., "peaks")/<exp>-<data>.list(契约 §6:峰文件即 Poky .list,
经 ProcessingController,登记 manual_peaks WorkflowRun)。GUI 不直接接触处理逻辑。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QItemSelectionModel, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
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
    status_message = pyqtSignal(str)  # 状态栏提示(主窗口接收)
    _ft3_ready = pyqtSignal(object, object)  # (path, Spectrum3D) 后台加载完成
    _ft3_failed = pyqtSignal(object, str)  # (path, message)

    # 0.2.89:超过该大小的 .ft3 后台线程加载,避免大文件读取卡死 UI
    _ASYNC_FT3_MIN_BYTES = 32 * 1024 * 1024

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
        self._viewer3d_state: dict[str, int] = {}
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
        self._ft3_ready.connect(self._on_ft3_ready)
        self._ft3_failed.connect(self._on_ft3_failed)
        self._spectrum3d_panel.slice_changed.connect(self._render_3d_view)
        self._spectrum3d_panel.plane_combo.currentIndexChanged.connect(
            self._save_3d_state
        )
        self.viewer.add_control_panel(self._spectrum3d_panel)

        self.file_list = QListWidget()
        self.file_list.setMaximumWidth(190)
        self.file_list.itemClicked.connect(self._on_file_clicked)

        self.peak_toolbar = QHBoxLayout()
        # 0.2.147:峰操作一行,列间间隔显明;Show peaks 位于 Add peak 前
        self.peak_toolbar.setSpacing(12)
        self.peak_toolbar.addWidget(self.viewer.show_peaks_checkbox)
        # 0.2.199-补29at:选择模式(左键拖动框选峰),与 1D/Add peak 互斥
        self.select_peaks_button = QPushButton("选择")
        self.select_peaks_button.setCheckable(True)
        self.select_peaks_button.setEnabled(False)
        self.select_peaks_button.setToolTip(
            "选择模式:按住左键拖动框选多个峰;与 1D 查看、Add peak 互斥"
        )
        self.select_peaks_button.toggled.connect(self._on_select_mode_toggled)
        self.peak_toolbar.addWidget(self.select_peaks_button)
        # 0.2.199-补29ar:Add peak 改为开关——开启后点击谱图加峰(吸附峰顶)
        self.add_peak_button = QPushButton("Add peak")
        self.add_peak_button.setCheckable(True)
        self.add_peak_button.setEnabled(False)
        self.add_peak_button.setToolTip(
            "开关:开启后点击谱图加峰(自动吸附到峰顶;找不到显著峰顶则用点击位置)"
        )
        self.add_peak_button.toggled.connect(self._on_add_peak_toggled)
        self.peak_toolbar.addWidget(self.add_peak_button)
        # 0.2.199-补29bb:峰操作分两行——第二行放 Delete/Import/Export/Save
        self.peak_toolbar2 = QHBoxLayout()
        self.peak_toolbar2.setSpacing(12)
        self.delete_peak_button = QPushButton("Delete selected")
        self.delete_peak_button.setEnabled(False)
        self.delete_peak_button.setToolTip("删除峰表中选中的行(自动/手动峰均可)")
        self.delete_peak_button.clicked.connect(self._on_delete_peak)
        self.peak_toolbar2.addWidget(self.delete_peak_button)
        self.import_poky_button = QPushButton("Import peaks")
        self.import_poky_button.setEnabled(False)
        self.import_poky_button.setToolTip("从 Poky/Sparky .list 导入峰表")
        self.import_poky_button.clicked.connect(self._on_import_poky)
        self.peak_toolbar2.addWidget(self.import_poky_button)
        self.export_poky_button = QPushButton("Export peaks")
        self.export_poky_button.setEnabled(False)
        self.export_poky_button.setToolTip("把当前峰表导出为 Poky/Sparky .list")
        self.export_poky_button.clicked.connect(self._export_peaks_poky)
        self.peak_toolbar2.addWidget(self.export_poky_button)
        self.save_peaks_button = QPushButton("Save peaks")
        self.save_peaks_button.setEnabled(False)
        self.save_peaks_button.setToolTip("把峰表写回 data/peaks/<exp>-<data>.list 并登记")
        self.save_peaks_button.clicked.connect(self._on_save_peaks)
        self.peak_toolbar2.addWidget(self.save_peaks_button)
        # 0.2.199-补29az:峰标记大小(数据坐标,随谱图缩放)
        self.peak_size_label = QLabel("标记尺寸")
        self.peak_size_spin = QDoubleSpinBox()
        self.peak_size_spin.setRange(0.5, 50.0)
        self.peak_size_spin.setSingleStep(0.5)
        self.peak_size_spin.setDecimals(1)
        self.peak_size_spin.setValue(8.0)
        self.peak_size_spin.setToolTip("标记尺寸(数据坐标单位,随谱图缩放)")
        self.peak_size_spin.setEnabled(False)
        self.peak_size_spin.valueChanged.connect(self.viewer.set_peak_size)
        self.peak_toolbar.addWidget(self.peak_size_label)
        self.peak_toolbar.addWidget(self.peak_size_spin)
        self.peak_toolbar.addStretch(1)

        self.peak_table = QTableWidget(0, 5)
        self.peak_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.peak_table.setHorizontalHeaderLabels(list(self._peak_keys))
        self.peak_table.horizontalHeader().setStretchLastSection(True)
        self.peak_table.setMaximumHeight(150)
        self.peak_table.itemSelectionChanged.connect(self._on_peak_row_selected)
        self.peak_table.itemChanged.connect(self._on_peak_cell_edited)
        self._peaks: list[dict] = []
        self._current_spectrum: Path | None = None
        self.placeholder = QLabel(
            "未打开项目\n\n从左侧选择项目下的实验,或点击谱图文件查看结果。"
        )
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet("color: #888;")


        # 上下布局:顶部文件/ Layers 行,中部查看器,下方峰操作+峰表
        # 0.2.147:文件列表与 Layers 列表并排一行,间隔明显
        self.lists_row_widget = QWidget()
        self.lists_row = QHBoxLayout(self.lists_row_widget)
        self.lists_row.setContentsMargins(0, 0, 0, 0)
        self.lists_row.setSpacing(16)
        files_box = QVBoxLayout()
        files_box.setSpacing(2)
        files_box.addWidget(QLabel("Files"))
        files_box.addWidget(self.file_list, 1)
        layers_box = QVBoxLayout()
        layers_box.setSpacing(2)
        layers_box.addWidget(QLabel("Layers"))
        layers_box.addWidget(self.viewer.layer_list, 1)
        self.lists_row.addLayout(files_box, 1)
        self.lists_row.addLayout(layers_box, 1)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.lists_row_widget)
        splitter.addWidget(self.viewer)
        self.peak_toolbar_widget = QWidget()
        _peak_rows = QVBoxLayout(self.peak_toolbar_widget)
        _peak_rows.setContentsMargins(0, 0, 0, 0)
        _peak_rows.setSpacing(4)
        _peak_rows.addLayout(self.peak_toolbar)
        _peak_rows.addLayout(self.peak_toolbar2)
        splitter.addWidget(self.peak_toolbar_widget)
        splitter.addWidget(self.peak_table)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setStretchFactor(3, 0)
        splitter.setSizes([90, 480, 40, 160])
        self.viewer.peak_clicked.connect(self._on_viewer_peak_clicked)
        self.viewer.manual_peak_requested.connect(self._on_manual_peak_added)
        self.viewer.peaks_box_selected.connect(self._on_peaks_box_selected)
        self.viewer.show_1d_button.toggled.connect(self._on_viewer_1d_toggled)
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
        """刷新谱图文件列表;无文件时隐藏列表(避免右下角空白)。

        0.2.88:不自动显示谱图——文件列表点击或 Pipeline「展示谱图」按钮打开;
        切换样品数据时清空旧谱,避免残留上一张谱。
        """
        self.file_list.clear()
        has_context = (
            self.manager.project is not None
            and bool(self._current_exp_id)
            and bool(self._current_data_id)
        )
        self.add_peak_button.setEnabled(has_context)
        self.select_peaks_button.setEnabled(has_context)
        self.peak_size_spin.setEnabled(has_context)
        self._update_delete_button()
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
        if not paths:
            self._current_spectrum = None
            self._spectrum3d_panel.clear()
            self.viewer.clear()  # 谱图文件夹无谱时右侧留空
            self._clear_peaks()
            return
        if self._current_spectrum is not None and self._current_spectrum not in paths:
            # 上下文已切换:不自动显示,清空旧谱(等用户点文件/「展示谱图」)
            self._current_spectrum = None
            self._spectrum3d_panel.clear()
            self.viewer.clear()
            self._clear_peaks()

    def load_current_spectrum(self) -> bool:
        """加载当前样品数据的主谱(投影文件由列表点击直接查看)。"""
        paths = self._spectrum_paths()
        if not paths:
            return False
        main = [
            p for p in paths if not self._is_projection_name(p.name)
        ] or paths
        first = main[0]
        if not self.open_spectrum(first):
            return False
        self._current_spectrum = first
        self._load_peaks(first)
        return True

    def _spectrum_paths(self) -> list[Path]:
        """当前实验/样品数据下的谱图文件(新布局优先,旧扁平路径回退)。

        新布局按数据级 spectra/ 目录内全部 .ft2/.ft3 列——后端终谱按
        dataset_id 命名(如 hsqc_2d.ft2),不假设 exp_id-data_id 前缀(0.2.112)。
        """
        paths: list[Path] = []
        exp_id = self._current_exp_id
        data_id = self._current_data_id
        if self.manager.project is None:
            return paths
        try:
            if data_id:
                spectra_dir = self.manager.data_dir(exp_id, data_id, "spectra")
                files = list(spectra_dir.glob("*.ft2")) + list(
                    spectra_dir.glob("*.ft3")
                )
                paths = sorted(files)
                if paths:
                    return paths
        except Exception:  # noqa: BLE001 - 新布局不可用回退旧路径
            pass
        # 旧扁平布局回退(项目根 spectra/,{exp_id}* 通配)
        spectra_dir = self.manager.dir_path("spectra")
        files = list(spectra_dir.glob(f"{exp_id}*.ft2")) + list(
            spectra_dir.glob(f"{exp_id}*.ft3")
        )
        return sorted(files)

    def open_spectrum(self, path: Path, name: str | None = None) -> bool:
        """加载谱图到查看器;失败返回 False(不弹窗,由调用方决定提示)。

        .ft3 走 3D 查看路径(契约 §10):绑定 Spectrum3D 并显示默认切片,
        3D 面板提供平面/切片/投影切换;.ft2 走二维叠加。
        """
        # 0.2.133: save/restore contour state per spectrum
        if self._current_spectrum is not None and self._current_spectrum != path:
            self.viewer.save_contour_state(str(self._current_spectrum))
        self.viewer.restore_contour_state(str(path))
        labels3d = self._axis_labels(3) or ("F1", "F2", "F3")
        labels2d = self._axis_labels(2) or ("F1", "F2")
        nuclei3d = self._axis_nuclei(3)
        nuclei2d = self._axis_nuclei(2)
        try:
            if path.suffix.lower() == ".ft3":
                from viewer.spectrum import Spectrum3D

                size = path.stat().st_size if path.is_file() else 0
                if size >= self._ASYNC_FT3_MIN_BYTES:
                    # 0.2.89:大 3D 谱后台加载,避免 UI 长时间无响应
                    self._current_spectrum = path
                    self.status_message.emit(
                        f"正在后台加载 3D 谱: {path.name} "
                        f"({size // (1024 * 1024)} MB)"
                    )
                    self._load_ft3_async(path, labels3d, nuclei3d)
                    return True
                self._current_spectrum = path
                # 在 set_spectrum3d(会重置平面/投影并触发保存)之前捕获记忆状态
                state = self._viewer3d_state.get(self._current_data_id)
                self._spectrum3d_panel.set_spectrum3d(
                    Spectrum3D.load_from_ft3(
                        path, labels=labels3d, nuclei=nuclei3d
                    )
                )
                self._spectrum3d_panel.setVisible(True)
                if state:
                    self._spectrum3d_panel.plane_combo.setCurrentIndex(state)
                self._render_3d_view()
                return True
            if path.suffix.lower() == ".fid":
                from viewer.spectrum import Spectrum1D

                self._current_spectrum = path
                self._spectrum3d_panel.clear()
                self.viewer.add_spectrum(
                    Spectrum1D.load_from_fid(path), name=path.stem
                )
                return True
            from viewer.spectrum import Spectrum

            if self._is_projection_name(path.name):
                proj_spec = self._load_projection_ft2(path)
                if proj_spec is None:
                    return False
                spectrum = proj_spec
            else:
                spectrum = Spectrum.load_from_ft2(
                    path, labels=labels2d, nuclei=nuclei2d
                )
        except Exception:  # noqa: BLE001 - 损坏文件统一由调用方提示
            return False
        self._spectrum3d_panel.clear()
        self.viewer.clear()
        self.viewer.add_spectrum(spectrum, name=name or path.stem)
        return True


    def _load_ft3_async(self, path: Path, labels3d, nuclei3d=None) -> None:
        """后台线程读取大 .ft3,完成后经信号回主线程绑定渲染。"""
        import threading

        def worker() -> None:
            try:
                from viewer.spectrum import Spectrum3D

                spectrum3d = Spectrum3D.load_from_ft3(
                    path, labels=labels3d, nuclei=nuclei3d
                )
                self._ft3_ready.emit(path, spectrum3d)
            except Exception as exc:  # noqa: BLE001 - 错误统一回主线程提示
                self._ft3_failed.emit(path, f"{type(exc).__name__}: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_ft3_ready(self, path, spectrum3d) -> None:
        """大 .ft3 加载完成(主线程):绑定 3D 面板并渲染;已切换则忽略。"""
        if path != self._current_spectrum:
            return
        state = self._viewer3d_state.get(self._current_data_id)
        self._spectrum3d_panel.set_spectrum3d(spectrum3d)
        self._spectrum3d_panel.setVisible(True)
        if state:
            self._spectrum3d_panel.plane_combo.setCurrentIndex(state)
        self._render_3d_view()
        self._load_peaks(path)
        self.status_message.emit(f"已加载 3D 谱: {path.name}")

    def _on_ft3_failed(self, path, message: str) -> None:
        """大 .ft3 加载失败(主线程)。"""
        if path != self._current_spectrum:
            return
        self._current_spectrum = None
        self.status_message.emit(f"3D 谱加载失败: {message}")

    def _axis_nuclei(self, required: int) -> list[str] | None:
        """按当前样品数据 metadata 返回逻辑轴核(F1/F2/F3 序);无则 None。"""
        from viewer.axis_labels import nuclei_from_metadata

        if self._manager is None or not (
            self._current_exp_id and self._current_data_id
        ):
            return None
        try:
            meta_path = self._manager.data_metadata_path(
                self._current_exp_id, self._current_data_id
            )
        except Exception:  # noqa: BLE001
            return None
        if meta_path is None or not meta_path.is_file():
            return None
        try:
            import json

            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        nuclei = nuclei_from_metadata(metadata)
        if not nuclei or len(nuclei) != required:
            return None
        return nuclei

    def _axis_labels(self, required: int) -> tuple[str, ...] | None:
        """按当前样品数据 metadata 的核信息生成轴名(F1/F2/F3→H/N/C)。
        维度数不符/无 metadata 时返回 None(调用方回退 F1/F2/F3)。"""
        from viewer.axis_labels import axis_labels_from_nuclei

        nuclei = self._axis_nuclei(required)
        if not nuclei:
            return None
        return axis_labels_from_nuclei(nuclei)

    def _is_projection_name(self, name: str) -> bool:
        """投影文件识别:新命名 {data_id}_{核A}-{核B}.ft2 或旧 *_proj_*.ft2。"""
        if "_proj_" in name:
            return True
        data_id = self._current_data_id or ""
        if data_id and name.startswith(f"{data_id}_") and name.endswith(".ft2"):
            body = name[len(data_id) + 1:-4]
            if "-" in body:
                return True
        return False

    def _load_projection_ft2(self, path: Path) -> object | None:
        """按文件名两核加载单个投影 .ft2(0.2.133,直接点击查看)。

        核从文件名解析;显示规则:横坐标优先级 H > N > C(0.2.153),
        必要时转置数据矩阵。
        轴参数优先取自已加载 3D 谱对应核的轴(SW/OBS/CAR/ORIG),
        否则用文件头槽位兜底。返回 Pydantic Spectrum;解析失败 None。
        """
        import re as _re
        from types import SimpleNamespace as _Sn

        import nmrglue as ng
        import numpy as np

        from viewer.axis_labels import nucleus_symbol
        from viewer.spectrum import Spectrum, SpectrumAxis

        def _norm(nuc: str) -> str:
            return _re.sub(r"[^A-Za-z0-9]", "", str(nuc or "")).upper()

        def _base_norm(nuc: str) -> str:
            """投影核名归一:去掉尾部 x/y/z 下标再比较(15Ny→15N)。"""
            t = _norm(nuc)
            if t and t[-1] in "XYZ":
                t = t[:-1]
            return t

        name = path.name
        data_id = self._current_data_id or ""
        nuclei = self._axis_nuclei(3) or []
        a = b = None
        # 0.2.199-补29x:仅 _proj_F{n} 命名能确定平面两轴与固定轴的
        # 逻辑映射(数据取向 (b,a)=(remaining[0],remaining[1]));
        # 核名命名的投影无法区分重复核,沿用旧同核/符号标签
        logical_mapped = False
        if data_id and name.startswith(f"{data_id}_") and name.endswith(".ft2"):
            body = name[len(data_id) + 1:-4]
            if "-" in body:
                parts = body.split("-", 1)
                a, b = parts[0], parts[1]
        if not (a and b):
            m = _re.search(r"_proj_F(\d)\.ft2$", name)
            if m and len(nuclei) == 3:
                fixed_axis = int(m.group(1)) - 1
                remaining = [i for i in range(3) if i != fixed_axis]
                a = nuclei[remaining[1]]
                b = nuclei[remaining[0]]
                logical_mapped = True
            else:
                # 旧名 _proj_NH 之类:按后缀符号对尝试
                m2 = _re.search(r"_proj_([A-Za-z0-9]{2,6})\.ft2$", name)
                if m2 and len(nuclei) == 3:
                    body = m2.group(1)
                    symbols = {nucleus_symbol(n).upper(): n for n in nuclei}
                    match = [symbols.get(c.upper()) for c in body]
                    if len(match) == 2 and all(match):
                        a, b = match[0], match[1]
                else:
                    # 最终回退:直接按文件头加载(尽力)
                    try:
                        return Spectrum.load_from_ft2(str(path))
                    except Exception:  # noqa: BLE001
                        return None
        if not (a and b):
            return None
        try:
            dic, data = ng.pipe.read(str(path))
        except Exception:  # noqa: BLE001
            return None
        data = np.asarray(data, dtype=float)
        if data.ndim != 2:
            return None
        na, nb = _base_norm(a), _base_norm(b)
        same_nucleus = na == nb
        fixed_axis = -1
        if len(nuclei) == 3:
            for i, nuc in enumerate(nuclei):
                if _base_norm(nuc) not in (na, nb):
                    fixed_axis = i
                    break
        s3d = getattr(self._spectrum3d_panel, "_spectrum3d", None)
        s3d_axes = list(getattr(s3d, "axes", []) or []) if s3d is not None else []
        # 0.2.199-补29w:重复核(HNN 双 15N)按逻辑轴下标区分——投影的核
        # 无法单靠核名区分是 F1 还是 F2 的 15N,用固定轴推导剩余两轴
        # 的逻辑下标(15Nx/15Ny)。
        labels3 = None
        if len(nuclei) == 3:
            from viewer.axis_labels import axis_labels_from_nuclei as _alfn

            labels3 = _alfn(nuclei)
            # 0.2.199-补29y:HNN 双 15N——Nx 对应 HSQC 的 N(酰胺 N(i),
            # 直接连 1H),按 HNN 惯例为 F2(t2);F1=N(i-1) 顺序 N 标 Ny
            if (
                len({_base_norm(n) for n in nuclei}) < len(nuclei)
                and _base_norm(nuclei[0]) == "15N"
                and _base_norm(nuclei[1]) == "15N"
            ):
                labels3 = ("Ny", "Nx", str(labels3[2]))
        x_params = y_params = None
        # 0.2.199-补29aj:参数优先按逻辑下标符号定位(15Ny→F1、15Nx→F2,
        # 同核轴也能区分),再按基础核名兜底;proj3D 同核平面文件头
        # FDF*LABEL 不可靠,最后才用文件头槽位。
        if len(s3d_axes) == 3:
            sym_a, sym_b = nucleus_symbol(a), nucleus_symbol(b)
            for i, sym in enumerate(labels3 or ()):
                if str(sym) == sym_a and x_params is None:
                    x_params = s3d_axes[i]
                if str(sym) == sym_b and y_params is None:
                    y_params = s3d_axes[i]
            if x_params is None or y_params is None:
                for i, nuc in enumerate(nuclei):
                    if _base_norm(nuc) == na and x_params is None:
                        x_params = s3d_axes[i]
                    if _base_norm(nuc) == nb and y_params is None:
                        y_params = s3d_axes[i]
        if x_params is None or y_params is None:
            # 轴参数缺失:用文件头槽位兜底(与 0.2.126 一致)
            x_params = _Sn(
                label=nucleus_symbol(a),
                size=int(data.shape[1]),
                sw_hz=float(dic.get("FDF2SW", 1.0) or 1.0),
                obs_mhz=float(dic.get("FDF2OBS", 1.0) or 1.0),
                carrier_ppm=float(dic.get("FDF2CAR", 0.0) or 0.0),
                orig_hz=float(dic.get("FDF2ORIG", 0.0) or 0.0),
            )
            y_params = _Sn(
                label=nucleus_symbol(b),
                size=int(data.shape[0]),
                sw_hz=float(dic.get("FDF1SW", 1.0) or 1.0),
                obs_mhz=float(dic.get("FDF1OBS", 1.0) or 1.0),
                carrier_ppm=float(dic.get("FDF1CAR", 0.0) or 0.0),
                orig_hz=float(dic.get("FDF1ORIG", 0.0) or 0.0),
            )
        # 0.2.153:横坐标优先级 H > N > C(必要时转置数据矩阵)
        _X_PRIORITY = {"1H": 0, "15N": 1, "13C": 2}
        if _X_PRIORITY.get(_base_norm(a), 100) > _X_PRIORITY.get(_base_norm(b), 100):
            data = data.T
            x_params, y_params = y_params, x_params
            a, b = b, a
            na, nb = nb, na
        if logical_mapped and labels3 is not None:
            remaining = [i for i in range(3) if i != fixed_axis]
            x_label = str(labels3[remaining[1]])
            y_label = str(labels3[remaining[0]])
        elif same_nucleus:
            # 同核投影平面无直接维语义:按显示轴取 x/y(x 轴得 x);
            # 已带下标(15Ny→Ny)直接显示,纯核名补下标(0.2.199-补29aj)
            from viewer.axis_labels import nucleus_symbol as _nsym

            sx, sy = _nsym(a), _nsym(b)
            x_label = sx if sx[-1:].upper() in ("X", "Y", "Z") else sx + "x"
            y_label = sy if sy[-1:].upper() in ("X", "Y", "Z") else sy + "y"
        else:
            x_label, y_label = nucleus_symbol(a), nucleus_symbol(b)
        # 0.2.199-补29x:HNN 等重复核投影——X 轴对应 HSQC 的 N(15N),
        # 15N-15N 平面把逻辑序较小的 Nx(F1)放 X;普通谱保持 H>N>C
        if logical_mapped and labels3 is not None:
            _remaining = [i for i in range(3) if i != fixed_axis]
            _dup = len({_base_norm(n) for n in nuclei}) < len(nuclei)
            if _dup and all(_base_norm(n) == "15N" for n in (a, b)):
                if not _norm(x_label).startswith("NX"):
                    data = data.T
                    x_label, y_label = y_label, x_label
                    x_params, y_params = y_params, x_params
            elif _dup and "15N" in (_base_norm(a), _base_norm(b)):
                if not _norm(x_label).startswith("N"):
                    data = data.T
                    x_label, y_label = y_label, x_label
                    x_params, y_params = y_params, x_params

        x_axis = SpectrumAxis(
            label=x_label,
            size=int(data.shape[1]),
            sw_hz=x_params.sw_hz,
            obs_mhz=x_params.obs_mhz,
            carrier_ppm=x_params.carrier_ppm,
            orig_hz=x_params.orig_hz,
        )
        y_axis = SpectrumAxis(
            label=y_label,
            size=int(data.shape[0]),
            sw_hz=y_params.sw_hz,
            obs_mhz=y_params.obs_mhz,
            carrier_ppm=y_params.carrier_ppm,
            orig_hz=y_params.orig_hz,
        )
        spectrum = Spectrum(data, [y_axis, x_axis], source=path)
        if fixed_axis >= 0:
            spectrum.dim_indices = tuple(
                i for i in range(3) if i != fixed_axis
            )
        return spectrum

    def _load_3d_projections(self) -> dict[int, object]:
        """兼容接口:扫描全部投影文件并按键=固定轴下标返回(0.2.133)。"""
        proj: dict[int, object] = {}
        if not (self._current_exp_id and self._current_data_id):
            return proj
        try:
            spectra_dir = self._manager.data_dir(
                self._current_exp_id, self._current_data_id, "spectra"
            )
        except Exception:  # noqa: BLE001
            return proj
        data_id = self._current_data_id
        candidates: list[Path] = []
        for p in sorted(spectra_dir.glob(f"{data_id}_*.ft2")):
            if p.name == f"{data_id}.ft2":
                continue
            if p not in candidates:
                candidates.append(p)
        for p in sorted(spectra_dir.glob("*_proj_*.ft2")):
            if p not in candidates:
                candidates.append(p)
        for path in candidates:
            try:
                spec = self._load_projection_ft2(path)
            except Exception:  # noqa: BLE001
                continue
            if spec is None:
                continue
            indices = getattr(spec, "dim_indices", ())
            if len(indices) == 2:
                fixed = next((i for i in range(3) if i not in indices), None)
                if fixed is not None:
                    proj[fixed] = spec
        return proj


    def _render_3d_view(self) -> None:
        """按 3D 面板当前平面/切片/投影渲染二维视图并重挂峰标记。"""
        spectrum = self._spectrum3d_panel.current_spectrum()
        if spectrum is None:
            return
        base = self._current_spectrum.stem if self._current_spectrum else "3D"
        # 0.2.199-补10:切片切换原位更新轮廓(不 clear/重建),避免闪烁与慢;
        # 换谱/换平面时 update_spectrum_data 自动回退 clear+add
        self.viewer.update_spectrum_data(
            spectrum,
            name=self._spectrum3d_panel.current_name(base),
        )
        if self._peaks:
            self.viewer.set_peaks(self._peaks)
    def _save_3d_state(self, *_args) -> None:
        """记忆当前样品数据的 3D 查看平面(0.2.133 仅 slice 模式)。"""
        if self._current_data_id:
            self._viewer3d_state[self._current_data_id] = (
                self._spectrum3d_panel.plane_combo.currentIndex()
            )

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
    def _peak_file_path(self, spectrum_path: Path) -> Path | None:
        """峰表文件:.list 优先(峰表即 list),旧 CSV 兼容回退。"""
        if self.manager.project is None:
            return None
        try:
            if self._current_data_id:
                peaks_dir = self.manager.data_dir(
                    self._current_exp_id, self._current_data_id, "peaks"
                )
                for suffix in (".list", ".csv"):
                    candidate = peaks_dir / (
                        f"{self._current_exp_id}-{self._current_data_id}{suffix}"
                    )
                    if candidate.is_file():
                        return candidate
        except Exception:  # noqa: BLE001
            pass
        for suffix in (".list", ".csv"):
            legacy = self.manager.dir_path("peaks") / f"{self._current_exp_id}{suffix}"
            if legacy.is_file():
                return legacy
        return None

    def _load_peaks(self, spectrum_path: Path) -> None:
        self._clear_peaks()
        peak_path = self._peak_file_path(spectrum_path)
        if peak_path is None:
            return
        if peak_path.suffix.lower() == ".list":
            peaks = import_peaks_poky(peak_path)
        else:
            peaks = load_peaks(peak_path)
        if not peaks:
            return
        self._peaks = self._assign_peak_ids(peaks)
        self._populate_peak_table()
        self.viewer.set_peaks(self._peaks)
        self.export_poky_button.setEnabled(True)
        self.save_peaks_button.setEnabled(True)
        self._update_delete_button()

    def _set_peak_columns(self, is_3d: bool) -> None:
        keys: list[str] = (
            ["Peak_ID", "label", "F1_shift", "F2_shift", "F3_shift", "Intensity", "SN"]
            if is_3d
            else ["Peak_ID", "label", "H_shift", "N_shift", "Intensity", "SN"]
        )
        # 0.2.162-补4:峰带可靠性注释时追加显示列
        if any(
            str(p.get("Reliability(%)", "")).strip() for p in self._peaks
        ) and "Reliability(%)" not in keys:
            keys.append("Reliability(%)")
        tuple_keys = tuple(keys)
        if tuple_keys == self._peak_keys:
            return
        self._peak_keys = tuple_keys
        self.peak_table.setColumnCount(len(tuple_keys))
        self.peak_table.setHorizontalHeaderLabels(
            ["Assignment" if k == "label" else k for k in tuple_keys]
        )

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

    def _update_delete_button(self) -> None:
        """删除峰按钮:有峰表(自动/手动)且选中数据时可用(0.2.199-补29ba)。"""
        has_context = bool(
            self.manager.project is not None
            and self._current_exp_id
            and self._current_data_id
        )
        self.delete_peak_button.setEnabled(
            has_context and self.peak_table.rowCount() > 0
        )

    def _sync_peaks_in_memory(self) -> None:
        """表格编辑后同步内存 _peaks(不立即重建 viewer,避免卡顿)。"""
        if self._loading_peaks:
            return
        self._peaks = self._table_peaks()
        self._update_delete_button()

    def _on_peak_cell_edited(self, _item) -> None:
        self._sync_peaks_in_memory()

    def _on_add_peak_toggled(self, checked: bool) -> None:
        """Add peak 开关:开启后点击谱图加峰(吸附峰顶);与 1D/选择互斥。"""
        if checked:
            self.select_peaks_button.setChecked(False)
            self.viewer.show_1d_button.setChecked(False)
            self.viewer.set_box_select_mode(False)
        self.viewer.set_peak_click_mode("add" if checked else "select")
        self.add_peak_button.setText("Add peak: ON" if checked else "Add peak")

    def _on_select_mode_toggled(self, checked: bool) -> None:
        """选择模式:左键拖动框选峰;与 1D/Add peak 互斥。"""
        if checked:
            self.add_peak_button.setChecked(False)
            self.viewer.show_1d_button.setChecked(False)
            self.viewer.set_peak_click_mode("select")
        self.viewer.set_box_select_mode(checked)
        self.select_peaks_button.setText("选择: ON" if checked else "选择")

    def _on_viewer_1d_toggled(self, checked: bool) -> None:
        """1D 查看开启时关闭选择/加峰模式(互斥)。"""
        if checked:
            self.select_peaks_button.setChecked(False)
            self.add_peak_button.setChecked(False)
            self.viewer.set_box_select_mode(False)
            self.viewer.set_peak_click_mode("select")

    def _on_peaks_box_selected(self, rows: list[int]) -> None:
        """框选峰:联动峰表多选。"""
        model = self.peak_table.selectionModel()
        if model is None:
            return
        model.clearSelection()
        for row in rows:
            if 0 <= row < self.peak_table.rowCount():
                model.select(
                    self.peak_table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )

    def _on_manual_peak_added(self, peak: dict) -> None:
        """点击谱图加峰:吸附后追加到峰表(自动编号)并立即显示。"""
        if not (self.manager.project is not None and self._current_exp_id):
            return
        next_id = (
            max((int(p.get("Peak_ID", 0) or 0) for p in self._peaks), default=0) + 1
        )
        peak["Peak_ID"] = next_id
        self._peaks.append(peak)
        self._populate_peak_table()
        self.viewer.set_peaks(self._peaks)
        self._update_delete_button()
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
        self._peaks = self._assign_peak_ids(peaks)
        self._populate_peak_table()
        self.viewer.set_peaks(peaks)
        self.export_poky_button.setEnabled(True)
        self.save_peaks_button.setEnabled(True)
        self._update_delete_button()
        # 替换峰表关联关系(不覆盖文件):导入仅更新内存峰表,
        # 点「保存峰表」时以 Poky .list 写盘
        InfoDialog.show_info(
            self,
            "导入完成",
            f"已用 Poky 峰表替换当前峰表关联({len(peaks)} 个峰)\n"
            "(点「保存峰表」写回 .list 文件)",
        )

    def _on_save_peaks(self) -> None:
        """峰表写回 data/peaks/<exp>-<data>.list 并登记 manual_peaks 运行。"""
        if self.manager.project is None or not self._current_exp_id:
            InfoDialog.show_info(self, "提示", "请先选中样品数据")
            return
        if not self._current_data_id:
            InfoDialog.show_info(self, "提示", "请先选中样品数据节点")
            return
        peaks = self._table_peaks()
        try:
            self.controller.set_manager(self.manager)
            list_path = self.controller.save_peaks_manual(
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
        InfoDialog.show_info(self, "保存完成", f"峰表已写入:\n{list_path}")

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
        if self.add_peak_button.isChecked():
            self.add_peak_button.setChecked(False)
        if self.select_peaks_button.isChecked():
            self.select_peaks_button.setChecked(False)
        self.viewer.set_box_select_mode(False)
        self.viewer.set_peak_click_mode("select")

    @staticmethod
    def _assign_peak_ids(peaks: list[dict]) -> list[dict]:
        """给缺少 Peak_ID 的行按行序编号(Poky .list 无 ID 列)。"""
        for i, peak in enumerate(peaks, start=1):
            if not (peak.get("Peak_ID") or ""):
                peak["Peak_ID"] = i
        return peaks

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
