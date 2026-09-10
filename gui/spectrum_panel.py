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
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.project import ProjectManager
from gui.dialogs import InfoDialog
from gui.peaks_io import (
    export_peaks_poky,
    import_peaks_poky,
    load_peaks,
    normalize_poky_label,
)
from gui.processing import ProcessingController
from gui.theme import TEXT_MUTED
from viewer.spectrum3d_panel import Spectrum3DPanel
from viewer.spectrum_viewer import SpectrumViewer

# 0.2.199-补29dc:Assignment 编辑组件只给可视行(±缓冲)创建,数千行峰表
# 逐行建 QWidget(每行 2-3 个输入框)是打开 3D 谱卡顿的主因。
_ASSIGNMENT_WIDGET_BUFFER = 12


def _axis_step(axis) -> float:
    """轴 ppm/点(相邻正步长中位数);异常返回 0。"""
    ppm = getattr(axis, "ppm", None)
    if ppm is None:
        return 0.0
    import numpy as np

    arr = np.asarray(ppm, dtype=float)
    if arr.size < 2:
        return 0.0
    diff = np.abs(np.diff(arr))
    diff = diff[diff > 0]
    return float(np.median(diff)) if diff.size else 0.0


class _AssignmentCell(QWidget):
    """峰表 Assignment 单元格:固定连字符 + 段输入框(2D 两段/3D 三段,默认 ?)。

    0.2.199-补29cp(用户):"-" 固定显示,前后各一个输入框;编辑逐段 Poky
    规范化后合并为 label(如 G1H-G1N、G1H-G1N-G1CA)。"""

    edited = pyqtSignal(int)  # row

    def __init__(self, ndim: int, row: int, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self.ndim = max(1, int(ndim))
        self.row = int(row)
        self.lines: list[QLineEdit] = []
        segs = [s.strip() for s in str(text or "").split("-")]
        while len(segs) < self.ndim:
            segs.append("?")
        segs = segs[: self.ndim]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.setStyleSheet("QWidget { background: transparent; }")
        for i in range(self.ndim):
            if i:
                dash = QLabel("-")
                dash.setStyleSheet("color: #c8c8c8; background: transparent;")
                dash.setFixedWidth(10)
                lay.addWidget(dash)
            le = QLineEdit()
            le.setAlignment(Qt.AlignmentFlag.AlignCenter)
            le.setFixedWidth(38)
            le.setMaxLength(12)
            # 0.2.199-补29cq:未指认段以占位符 "?" 显示(输入即替换,不追加残留)
            if segs[i] in ("", "?"):
                le.setPlaceholderText("?")
            else:
                le.setText(segs[i])
            le.setStyleSheet(
                "QLineEdit { color: #e8e8e8; } "
                "QLineEdit::placeholder { color: #8a8a8a; }"
            )
            self.lines.append(le)
            lay.addWidget(le)
        for le in self.lines:
            le.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self, *_args) -> None:
        self.edited.emit(self.row)

    def merged_text(self) -> str:
        """当前各段合并的 label(逐段 Poky 规范化,空段 → ?)。"""
        segs = [
            normalize_poky_label((le.text() or "").strip(), ndim=1) or "?"
            for le in self.lines
        ]
        return "-".join(segs)


def _nearest_smile_confidence(
    peak: dict,
    rel_peaks: list[dict],
    keys: tuple[str, ...],
    tol: dict[str, float],
) -> float | None:
    """SMILE 可靠性峰中找坐标最近的峰,返回 confidence(0-100)或 None。

    0.2.199-补29cy:峰 ppm 与 smile_reliability 的 shifts 各轴容差内取
    归一化距离最近者;无匹配返回 None(峰表该格留空)。
    """
    best_conf: float | None = None
    best_dist: float | None = None
    for rel_peak in rel_peaks:
        shifts = rel_peak.get("shifts") or {}
        dist = 0.0
        ok = True
        for key in keys:
            try:
                a = float(peak.get(key))
                b = float(shifts.get(key))
            except (TypeError, ValueError):
                ok = False
                break
            if not (a == a and b == b):  # NaN
                ok = False
                break
            t = tol.get(key, 0.5)
            if abs(a - b) > t:
                ok = False
                break
            dist += abs(a - b) / max(t, 1e-9)
        if not ok:
            continue
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_conf = float(rel_peak.get("confidence", 0.0) or 0.0)
    return best_conf


class SpectrumPanel(QWidget):
    """谱图面板:查看器 + 文件列表 + 峰表(加/删/改/存)。"""

    peaks_saved = pyqtSignal()  # 峰表写回后发出(主窗口刷新 Pipeline/日志)
    status_message = pyqtSignal(str)  # 状态栏提示(主窗口接收)
    log_message = pyqtSignal(str)  # 任务日志(主窗口 LogPanel 接收,0.2.199-补29cz)
    _ft3_ready = pyqtSignal(object, object)  # (path, Spectrum3D) 后台加载完成
    _ft3_failed = pyqtSignal(object, str)  # (path, message)
    # 0.2.199-补29bp:谱图放大/收起(主窗口收起左侧三部分)
    expand_requested = pyqtSignal(bool)

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
        self._applying_label_format = False  # 0.2.199-补29cn:规范化防递归
        self._viewer3d_state: dict[tuple[str, str], int] = {}
        # 0.2.199-补29fz(用户):谱图显示调节按数据隔离——
        # (exp_id, data_id) → {contour 滑块/级数/aspect/峰标记尺寸}。
        self._display_states: dict[tuple[str, str], dict] = {}
        self._peak_keys: tuple[str, ...] = (
            "Peak_ID",
            "H_shift",
            "N_shift",
            "Intensity",
            "SN",
        )

        # 0.2.199-补29hz-修2:分区卡片 + 标题栏
        self.setObjectName("PanelCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 2, 0)
        panel_title = QLabel("谱图")
        panel_title.setObjectName("PanelTitle")
        header.addWidget(panel_title)
        header.addStretch(1)
        layout.addLayout(header)
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
        self.file_list.setAutoFillBackground(False)
        self.viewer.layer_list.setAutoFillBackground(False)
        self.file_list.setMaximumWidth(190)
        self.file_list.itemClicked.connect(self._on_file_clicked)

        self.peak_toolbar = QHBoxLayout()
        # 0.2.147:峰操作一行,列间间隔显明;Show peaks 位于 Add peak 前
        self.peak_toolbar.setSpacing(12)
        self.peak_toolbar.addWidget(self.viewer.show_peaks_checkbox)
        # 0.2.199-补29at:选择模式(左键拖动框选峰),与 1D/Add peak 互斥
        self.select_peaks_button = QPushButton("Select mode")
        self.select_peaks_button.setCheckable(True)
        self.select_peaks_button.setEnabled(False)
        self.select_peaks_button.setToolTip(
            "选择模式:按住左键拖动框选多个峰;与 1D 查看、Add peak 互斥"
        )
        self.select_peaks_button.toggled.connect(self._on_select_mode_toggled)
        self.peak_toolbar.addWidget(self.select_peaks_button)
        # 0.2.199-补29ar:Add peak 改为开关——开启后点击谱图加峰(吸附峰顶)
        self.add_peak_button = QPushButton("Add peak mode")
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
        self.export_poky_button.setToolTip(
            "导出峰表:直接导出(原坐标)或对齐后导出(整体平移对齐到所选参考峰文件)"
        )
        self.export_menu = QMenu(self)
        self.export_direct_action = self.export_menu.addAction(
            "直接导出", self._export_peaks_poky
        )
        self.export_aligned_action = self.export_menu.addAction(
            "对齐后导出", self._export_peaks_aligned
        )
        self.export_poky_button.setMenu(self.export_menu)
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
        self.peak_size_spin.setValue(1.5)
        self.peak_size_spin.setToolTip("标记尺寸(数据坐标单位,随谱图缩放)")
        self.peak_size_spin.setEnabled(False)
        self.peak_size_spin.valueChanged.connect(self.viewer.set_peak_size)
        # 0.2.199-补29fz(用户):显示调节(contour start/levels/aspect/标记
        # 尺寸)每次变化即记入当前数据,切换数据时互不影响。
        self.viewer.level_slider.valueChanged.connect(
            self._save_display_state
        )
        self.viewer.count_slider.valueChanged.connect(
            self._save_display_state
        )
        self.viewer.aspect_slider.valueChanged.connect(
            self._save_display_state
        )
        self.peak_size_spin.valueChanged.connect(self._save_display_state)
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
        # 0.2.199-补29bo:谱图点选/框选引起的程序化选行,不触发单峰闪烁
        self._syncing_table_selection = False
        self.peak_table.itemSelectionChanged.connect(self._on_peak_row_selected)
        # 0.2.199-补29cm:已选中行再次点击不触发 selectionChanged,
        # 需另接 cellClicked 才能重复闪烁定位
        self.peak_table.cellClicked.connect(self._on_peak_cell_clicked)
        self.peak_table.itemChanged.connect(self._on_peak_cell_edited)
        # 0.2.199-补29bf:点击 Assignment 列标题开关图上指认标签
        self.peak_table.horizontalHeader().sectionClicked.connect(
            self._on_peak_header_clicked
        )
        # 0.2.199-补29dc:滚动时按需创建/销毁 Assignment 编辑组件
        self.peak_table.verticalScrollBar().valueChanged.connect(
            self._ensure_assignment_widgets
        )
        self._peaks: list[dict] = []
        self._current_spectrum: Path | None = None
        self._viewer_1d_active = False  # 0.2.199-补29bd:1D 开启隐藏峰控件
        self._projection_active = False  # 0.2.199-补29db:投影文件隐藏峰 UI
        self.placeholder = QLabel(
            "未打开项目\n\n从左侧选择项目下的实验,或点击谱图文件查看结果。"
        )
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet(f"color: {TEXT_MUTED};")


        # 上下布局:顶部文件/ Layers 行,中部查看器,下方峰操作+峰表
        # 0.2.147:文件列表与 Layers 列表并排一行,间隔明显
        self.lists_row_widget = QWidget()
        # 0.2.199-补29bs:限制 Files/Layers 行高度,放大时右侧列不被撑爆
        self.lists_row_widget.setMaximumHeight(110)
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
        # 0.2.199-补29bp:谱图放大按钮——收起左侧三部分,谱图占满窗口
        self.expand_button = QPushButton("放大")
        self.expand_button.setCheckable(True)
        self.expand_button.setToolTip(
            "放大:绘图区单独伸到左侧(覆盖项目树/Pipeline/Log),右侧保留按键;再点还原"
        )
        self.expand_button.toggled.connect(self._on_expand_toggled)
        # 0.2.199-补29bs:不留 Layers 与按钮之间的大空白
        self.lists_row.addWidget(self.expand_button)
        # 0.2.199-补29br:谱图查看器的「文件」「帮助」菜单移到放大按钮右边
        self.file_menu = QMenu(self)
        self.file_menu.addAction("打开谱图...", self._on_menu_open_spectrum)
        self.file_menu.addAction("清空谱图", self._on_menu_clear_spectrum)
        self.file_button = QPushButton("文件")
        self.file_button.setMenu(self.file_menu)
        self.help_menu = QMenu(self)
        self.help_menu.addAction("操作说明", self._on_menu_show_help)
        self.help_button = QPushButton("帮助")
        self.help_button.setMenu(self.help_menu)
        self.lists_row.addWidget(self.file_button)
        self.lists_row.addWidget(self.help_button)
        self.lists_row.addSpacing(12)  # 0.2.199-补29bq:不贴最右边框

        # 0.2.199-补29bq:放大模式——垂直 splitter(默认)与水平
        # [绘图区 | 右侧控件列] 之间切换,只有绘图区伸到左侧
        self._expanded = False
        self._expand_splitter: QSplitter | None = None
        self._expand_controls: QWidget | None = None
        self._expand_plot_area: QWidget | None = None
        self._expand_viewer_controls: QWidget | None = None
        self._collapsed_sizes: list[int] = []
        self._view_splitter_sizes: list[int] = []
        self._panel_splitter = QSplitter(Qt.Orientation.Vertical)
        self._panel_splitter.addWidget(self.lists_row_widget)
        self._panel_splitter.addWidget(self.viewer)
        self.peak_toolbar_widget = QWidget()
        # 0.2.199-补29bs:两行峰按钮(Show/Select/Add/尺寸 + Delete/Import/Export/Save)
        self.peak_toolbar_widget.setMinimumHeight(68)
        self.peak_toolbar_widget.setMaximumHeight(90)
        _peak_rows = QVBoxLayout(self.peak_toolbar_widget)
        _peak_rows.setContentsMargins(0, 0, 0, 0)
        _peak_rows.setSpacing(4)
        _peak_rows.addLayout(self.peak_toolbar)
        _peak_rows.addLayout(self.peak_toolbar2)
        self._panel_splitter.addWidget(self.peak_toolbar_widget)
        self._panel_splitter.addWidget(self.peak_table)
        self._panel_splitter.setStretchFactor(0, 0)
        self._panel_splitter.setStretchFactor(1, 1)
        self._panel_splitter.setStretchFactor(2, 0)
        self._panel_splitter.setStretchFactor(3, 0)
        self._panel_splitter.setSizes([90, 420, 76, 150])
        self.viewer.peak_clicked.connect(self._on_viewer_peak_clicked)
        self.viewer.manual_peak_requested.connect(self._on_manual_peak_added)
        self.viewer.peaks_box_selected.connect(self._on_peaks_box_selected)
        self.viewer.show_1d_button.toggled.connect(self._on_viewer_1d_toggled)
        self.file_list.setMaximumWidth(16777215)  # 取消横向宽度限制
        layout.addWidget(self._panel_splitter)
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

    def _sync_peak_ui_visibility(self) -> None:
        """峰相关 UI 可见性:1D 查看或投影文件打开时全部隐藏(0.2.199-补29db)。"""
        show = (
            self.manager.project is not None
            and not self._viewer_1d_active
            and not self._projection_active
        )
        self.peak_table.setVisible(show)
        self.peak_toolbar_widget.setVisible(show)
        self.viewer.peak_label.setVisible(show)

    def _display_key(self) -> tuple[str, str]:
        return (self._current_exp_id or "", self._current_data_id or "")

    def _save_display_state(self, *_args) -> None:
        """把当前显示调节即时记入当前数据(0.2.199-补29fz)。"""
        key = self._display_key()
        if not all(key):
            return
        try:
            self._display_states[key] = {
                "level_slider": int(self.viewer.level_slider.value()),
                "level_count": int(self.viewer.count_slider.value()),
                "aspect": int(self.viewer.aspect_slider.value()),
                "peak_size": float(self.peak_size_spin.value()),
            }
            # 0.2.199-补29ga:镜像到 d_xxx/ui_state.json
            from gui.per_data_records import update_ui_state

            update_ui_state(
                self.manager,
                self._current_exp_id,
                self._current_data_id,
                "spectrum",
                dict(self._display_states[key]),
            )
        except Exception:  # noqa: BLE001 - 记录/持久化失败不阻断调节
            pass

    def _restore_display_state(self) -> None:
        """谱图加载成功后按当前数据恢复显示调节;无记录用默认并落档
        (0.2.199-补29fz,用户:阈值/contour start 等调节都要数据隔离)。"""
        key = self._display_key()
        if not all(key):
            return
        state = self._display_states.get(key)
        if state is None:
            defaults = {
                "level_slider": 31,
                "level_count": 8,
                "aspect": 0,
                "peak_size": 1.5,
            }
            # 0.2.199-补29ga:重启后从 d_xxx/ui_state.json 恢复
            try:
                from gui.per_data_records import load_ui_state

                file_state = (
                    load_ui_state(
                        self.manager, self._current_exp_id,
                        self._current_data_id,
                    ).get("spectrum")
                    or {}
                )
                for field in defaults:
                    if file_state.get(field) is not None:
                        defaults[field] = file_state[field]
            except Exception:  # noqa: BLE001 - 读取失败用默认
                pass
            state = defaults
            self._display_states[key] = dict(state)
        try:
            self.viewer.level_slider.setValue(
                int(state.get("level_slider", 31))
            )
            self.viewer.count_slider.setValue(
                int(state.get("level_count", 8))
            )
            self.viewer.aspect_slider.setValue(
                int(state.get("aspect", 0))
            )
            self.viewer.refresh_levels()
            self.peak_size_spin.setValue(
                float(state.get("peak_size", 1.5))
            )
        except Exception:  # noqa: BLE001 - 恢复失败不阻断谱图显示
            pass

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
        self._sync_peak_ui_visibility()
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
        """当前实验/样品数据下的谱图文件(schema 1.4 数据级 spectra/)。

        数据级上下文只列该数据;实验级上下文汇总实验下(未删除)全部数据。
        后端终谱按 dataset_id 命名(如 hsqc_2d.ft2),不假设前缀(0.2.112)。
        """
        exp_id = self._current_exp_id
        data_id = self._current_data_id
        if self.manager.project is None or not exp_id:
            return []
        entry = self.manager.project.experiment(exp_id)
        if entry is None:
            return []
        data_ids = (
            [data_id]
            if data_id
            else [d.id for d in (entry.data or []) if not getattr(d, "trashed", False)]
        )
        files: list[Path] = []
        for did in data_ids:
            spectra_dir = self.manager.data_dir(exp_id, did, "spectra")
            for ext in ("ft1", "ft2", "ft3"):
                try:
                    files.extend(spectra_dir.glob(f"*.{ext}"))
                except OSError:  # noqa: PERF203 - 目录缺失/不可读时跳过
                    continue
        return sorted(set(files))


    def open_spectrum(self, path: Path, name: str | None = None) -> bool:
        """加载谱图到查看器;失败返回 False(不弹窗,由调用方决定提示)。

        .ft3 走 3D 查看路径(契约 §10):绑定 Spectrum3D 并显示默认切片,
        3D 面板提供平面/切片/投影切换;.ft2 走二维叠加。
        """
        # 0.2.199-补29fz:显示调节按数据隔离——每次调节已即时记入当前
        # 数据;谱图加载成功后 _restore_display_state() 恢复各自数值,
        # 不再按谱文件路径在打开前快照。
        # 0.2.199-补29db:投影文件隐藏一切峰相关 UI,不做峰关联/峰操作
        self._projection_active = (
            path.suffix.lower() == ".ft2"
            and self._is_projection_name(path.name)
        )
        # 0.2.199-补29dh(用户):轴序/标签只以 .ft3 头部为准,不向加载器
        # 传 metadata 核/标签(软件处理有轴重排,metadata 采集序不可作兜底)
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
                    self._load_ft3_async(path)
                    return True
                self._current_spectrum = path
                # 在 set_spectrum3d(会重置平面/投影并触发保存)之前捕获记忆状态
                state = self._viewer3d_state.get((self._current_exp_id, self._current_data_id))
                self._spectrum3d_panel.set_spectrum3d(
                    Spectrum3D.load_from_ft3(path, lazy=True)
                )
                self._spectrum3d_panel.setVisible(True)
                if state:
                    self._spectrum3d_panel.plane_combo.setCurrentIndex(state)
                self._render_3d_view()
                self._restore_display_state()
                return True
            if path.suffix.lower() == ".ft1":
                from viewer.spectrum import Spectrum1D

                self._current_spectrum = path
                self._spectrum3d_panel.clear()
                self.viewer.add_spectrum(
                    Spectrum1D.load_from_ft1(path), name=name or path.stem
                )
                self._viewer_1d_active = True
                self._sync_peak_ui_visibility()
                self._restore_display_state()
                return True
            if path.suffix.lower() == ".fid":
                from viewer.spectrum import Spectrum1D

                self._current_spectrum = path
                self._spectrum3d_panel.clear()
                self.viewer.add_spectrum(
                    Spectrum1D.load_from_fid(path), name=path.stem
                )
                self._viewer_1d_active = True
                self._sync_peak_ui_visibility()
                self._restore_display_state()
                return True
            from viewer.spectrum import Spectrum

            if self._is_projection_name(path.name):
                proj_spec = self._load_projection_ft2(path)
                if proj_spec is None:
                    return False
                spectrum = proj_spec
                # 0.2.199-补29db:投影文件不加载/关联峰,清空峰表与标记
                self._clear_peaks()
            else:
                spectrum = Spectrum.load_from_ft2(path)
        except Exception:  # noqa: BLE001 - 损坏文件统一由调用方提示
            return False
        self._spectrum3d_panel.clear()
        self.viewer.clear()
        self.viewer.add_spectrum(spectrum, name=name or path.stem)
        self._viewer_1d_active = False
        self._restore_display_state()
        self._sync_peak_ui_visibility()
        return True


    def open_with_peaks(self, path: Path, name: str | None = None) -> bool:
        """打开谱图并加载其峰表(供主窗口调用,避免外部访问私有成员)。"""
        target = Path(path)
        if not self.open_spectrum(target, name=name):
            return False
        self._current_spectrum = target
        self._load_peaks(target)
        return True

    def _load_ft3_async(self, path: Path) -> None:
        """后台线程读取大 .ft3,完成后经信号回主线程绑定渲染。"""
        import threading

        def worker() -> None:
            try:
                from viewer.spectrum import Spectrum3D

                spectrum3d = Spectrum3D.load_from_ft3(path, lazy=True)
                self._ft3_ready.emit(path, spectrum3d)
            except Exception as exc:  # noqa: BLE001 - 错误统一回主线程提示
                self._ft3_failed.emit(path, f"{type(exc).__name__}: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_ft3_ready(self, path, spectrum3d) -> None:
        """大 .ft3 加载完成(主线程):绑定 3D 面板并渲染;已切换则忽略。"""
        if path != self._current_spectrum:
            return
        state = self._viewer3d_state.get((self._current_exp_id, self._current_data_id))
        self._spectrum3d_panel.set_spectrum3d(spectrum3d)
        self._spectrum3d_panel.setVisible(True)
        if state:
            self._spectrum3d_panel.plane_combo.setCurrentIndex(state)
        self._render_3d_view()
        self._restore_display_state()
        self._load_peaks(path)
        self.status_message.emit(f"已加载 3D 谱: {path.name}")

    def _on_ft3_failed(self, path, message: str) -> None:
        """大 .ft3 加载失败(主线程)。"""
        if path != self._current_spectrum:
            return
        self._current_spectrum = None
        self.status_message.emit(f"3D 谱加载失败: {message}")
        # 0.2.199-补29hz:失败不能只留状态栏一行(常被忽略),
        # 同步写进任务日志面板。
        self.log_message.emit(f"3D 谱加载失败 {path.name}: {message}")

    def _current_3d_nuclei(self) -> list[str] | None:
        """当前已加载 3D 谱每 F 轴(F1/F2/F3)的完整核名;核不可知返回 None。

        0.2.199-补29dk(用户):.list/峰表显示按外部约定,内部按 F 逻辑解读——
        这里从已加载谱轴标签取核(仅文件头来源,不用 metadata)。
        """
        s3d = self._spectrum3d_panel.spectrum3d
        axes3 = getattr(s3d, "axes", None) if s3d is not None else None
        if not axes3 or len(axes3) != 3:
            return None
        symbols = {
            "H": "1H", "N": "15N", "C": "13C",
            "F": "19F", "P": "31P", "D": "2H",
        }
        full = {"1H", "2H", "13C", "15N", "19F", "31P", "23Na", "29Si"}
        nuclei: list[str] = []
        for axis in axes3:
            label = str(getattr(axis, "label", "") or "").strip()
            if len(label) > 1 and label[-1:].lower() in ("x", "y", "z"):
                label = label[:-1]
            nuc = symbols.get(label, label)
            nuclei.append(nuc if nuc in full else "")
        if not all(nuclei):
            return None
        return nuclei

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
        s3d = self._spectrum3d_panel.spectrum3d
        s3d_axes = list(getattr(s3d, "axes", []) or []) if s3d is not None else []
        # 0.2.199-补29hd:面板未加载 3D 时,从同目录 .ft3 懒读轴作兜底——
        # 投影 .ft2 文件头被 proj3D.tcl 复制为输入平面头(全 15N/1H),不可用于
        # 轴参数;直接双击投影而未先加载 .ft3 时,否则投影轴参数全乱。
        if len(s3d_axes) != 3:
            from viewer.spectrum import Spectrum3D  # noqa: PLC0415

            parent = path.parent
            ft3_candidates = []
            if data_id:
                ft3_candidates.append(parent / f"{data_id}.ft3")
            ft3_candidates += list(parent.glob("*.ft3"))
            for ft3 in ft3_candidates:
                if not ft3.is_file() or not ft3.name.endswith(".ft3"):
                    continue
                try:
                    s3d_axes = list(Spectrum3D.load_from_ft3(ft3, lazy=True).axes or [])
                except Exception:  # noqa: BLE001 - 兄弟谱读取失败回退空,走文件头
                    s3d_axes = []
                if len(s3d_axes) == 3:
                    break
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
            # 0.2.133 本意是「从已加载 3D 谱对应核的轴取参数」;原用 metadata
            # 核序(nuclei/labels3)按下标 i 索引 s3d_axes,二者顺序不一致时
            # (CANH metadata 为 Bruker 采集序 C,N,H,加载谱逻辑序 N,H,C)会
            # 错配。改为按已加载谱自身的轴标签匹配(含 15Ny/15Nx 下标)。
            s3d_syms = [str(ax.label) for ax in s3d_axes]
            for i, sym in enumerate(s3d_syms):
                if sym == sym_a and x_params is None:
                    x_params = s3d_axes[i]
                if sym == sym_b and y_params is None:
                    y_params = s3d_axes[i]
            if x_params is None or y_params is None:
                for i, ax in enumerate(s3d_axes):
                    if _base_norm(str(ax.label)) == na and x_params is None:
                        x_params = s3d_axes[i]
                    if _base_norm(str(ax.label)) == nb and y_params is None:
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
            self._viewer3d_state[(self._current_exp_id, self._current_data_id)] = (
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
    # 峰表(.list 为主,旧 CSV 兼容读取)与谱图双向联动 + 编辑回写
    # ------------------------------------------------------------------
    def _load_smile_reliability(self) -> list[dict]:
        """读取当前数据 SMILE 优化逐峰可靠性 JSON(未做优化/解析失败返回空)。"""
        if not (self._current_exp_id and self._current_data_id):
            return []
        import json

        out_dir = self.manager.data_dir(
            self._current_exp_id, self._current_data_id, "smile_optimized"
        )
        if not out_dir.is_dir():
            return []
        base = f"{self._current_exp_id}-{self._current_data_id}_smile_reliability"
        candidates = [out_dir / f"{base}.json"]
        candidates += sorted(out_dir.glob(f"{base}_top*.json"))
        for name in candidates:
            try:
                data = json.loads(Path(name).read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - 单个文件损坏不影响
                continue
            peaks = data.get("peaks") or []
            if peaks:
                return peaks
        return []

    @staticmethod
    def _axis_step_ppm(axis) -> float | None:
        """轴 ppm/点(相邻正步长中位数);无有效轴返回 None。"""
        import numpy as np

        ppm = getattr(axis, "ppm", None)
        if ppm is None:
            return None
        arr = np.asarray(ppm, dtype=float)
        if arr.size < 2:
            return None
        diff = np.abs(np.diff(arr))
        diff = diff[diff > 0]
        return float(np.median(diff)) if diff.size else None

    def _smile_match_tolerance(self) -> dict[str, float]:
        """峰匹配容差(ppm):有谱轴时取 ±4 点,否则固定(1H 0.1/15N,13C 0.5)。"""
        tol = {
            "H_shift": 0.1,
            "N_shift": 0.5,
            "F1_shift": 0.5,
            "F2_shift": 0.1,
            "F3_shift": 0.5,
        }
        primary = self.viewer.primary_spectrum
        axes = getattr(primary, "axes", None) if primary is not None else None
        if axes:
            if len(axes) > 1:
                step = self._axis_step_ppm(axes[1])
                if step:
                    tol["H_shift"] = 4.0 * step
            if len(axes) > 0:
                step = self._axis_step_ppm(axes[0])
                if step:
                    tol["N_shift"] = 4.0 * step
        s3d = self._spectrum3d_panel.spectrum3d
        axes3 = getattr(s3d, "axes", None) if s3d is not None else None
        if axes3:
            for index, key in enumerate(("F1_shift", "F2_shift", "F3_shift")):
                if index < len(axes3):
                    step = self._axis_step_ppm(axes3[index])
                    if step:
                        tol[key] = 4.0 * step
        return tol

    def _attach_smile_confidence(self) -> None:
        """把 SMILE 优化逐峰可信度匹配到当前峰表(0.2.199-补29cy/补29cz)。

        匹配后写入 peak["Reliability(%)"],峰表显示列「可信度」;该列不写
        .list(export_peaks_poky 固定列)。未做 SMILE 优化或无匹配留空;
        匹配结果输出到任务日志(导入峰表/自动选峰/打开谱图均可看到)。
        """
        if not self._peaks:
            return
        rel_peaks = self._load_smile_reliability()
        if not rel_peaks:
            self.log_message.emit(
                "可信度匹配:未找到 SMILE 优化可靠性数据(smile_optimized/),"
                "跳过"
            )
            return
        tol = self._smile_match_tolerance()
        keys = (
            ("H_shift", "N_shift")
            if "H_shift" in self._peaks[0]
            else ("F1_shift", "F2_shift", "F3_shift")
        )
        matched = 0
        for peak in self._peaks:
            confidence = _nearest_smile_confidence(peak, rel_peaks, keys, tol)
            if confidence is not None:
                peak["Reliability(%)"] = confidence
                matched += 1
        total = len(self._peaks)
        if matched:
            self.log_message.emit(
                f"可信度匹配: {matched}/{total} 个峰匹配到 SMILE 优化可信度"
                f"(未匹配 {total - matched} 个)"
            )
        else:
            self.log_message.emit(
                f"可信度匹配: {total} 个峰均未在 SMILE 优化结果中找到"
                "对应峰,可信度留空"
            )

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
        return None

    def _load_peaks(self, spectrum_path: Path) -> None:
        self._clear_peaks()
        if self._projection_active:
            # 0.2.199-补29db:投影文件不做任何峰关联/峰操作
            return
        peak_path = self._peak_file_path(spectrum_path)
        if peak_path is None:
            return
        if peak_path.suffix.lower() == ".list":
            # 0.2.199-补29dk:3D 按外部约定 w1=15N/w2=13C/w3=1H 解读,
            # 经当前谱核名映射回内部 F1/F2/F3
            peaks = import_peaks_poky(
                peak_path, nuclei=self._current_3d_nuclei()
            )
        else:
            peaks = load_peaks(peak_path)
        if not peaks:
            return
        self._peaks = self._assign_peak_ids(peaks)
        # 0.2.199-补29cy:有 SMILE 优化时把逐峰可信度匹配回填(不入 .list)
        self._attach_smile_confidence()
        self._populate_peak_table()
        self.viewer.set_peaks(self._peaks)
        self.export_poky_button.setEnabled(True)
        self.save_peaks_button.setEnabled(True)
        self._update_delete_button()
        self._sync_peak_ui_visibility()

    def _set_peak_columns(self, is_3d: bool) -> None:
        # 0.2.199-补29dk(用户):.list 与峰表显示都和外部 Poky 约定一致——
        # 3D w1=15N/w2=13C/w3=1H(N,C,H);内部行键仍 F1/F2/F3,按核名排列。
        keys: list[str]
        header_map: dict[str, str] = {}
        if is_3d:
            s3d = self._spectrum3d_panel.spectrum3d
            axes3 = getattr(s3d, "axes", None)
            nuclei3 = self._current_3d_nuclei()
            if (
                axes3
                and len(axes3) == 3
                and nuclei3
                and set(nuclei3) == {"15N", "13C", "1H"}
            ):
                order = [nuclei3.index(n) for n in ("15N", "13C", "1H")]
                keys = (
                    ["Peak_ID", "label"]
                    + [f"F{i + 1}_shift" for i in order]
                    + ["Intensity", "SN"]
                )
                header_map = {
                    f"F{i + 1}_shift": f"{axes3[i].label}_shift"
                    for i in order
                }
            else:
                keys = [
                    "Peak_ID", "label", "F1_shift", "F2_shift", "F3_shift",
                    "Intensity", "SN",
                ]
                if axes3 and len(axes3) == 3:
                    header_map = {
                        f"F{i + 1}_shift": f"{axes3[i].label}_shift"
                        for i in range(3)
                    }
        else:
            keys = ["Peak_ID", "label", "H_shift", "N_shift", "Intensity", "SN"]
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
        # 0.2.199-补29dj:未加载 3D 谱时不用 metadata 核名兜底,保持 F1/F2/F3
        # 中性名;谱加载后 _load_peaks 重新填充列名(axes3 标签)
        self.peak_table.setHorizontalHeaderLabels(
            [
                (
                    "Assignment ✓"
                    if self.viewer.peak_labels_visible
                    else "Assignment ✗"
                )
                if k == "label"
                else ("可信度" if k == "Reliability(%)" else header_map.get(k, k))
                for k in tuple_keys
            ]
        )

    def _ensure_assignment_widgets(self) -> None:
        """按需创建/销毁 Assignment 编辑组件:只给可视行(±缓冲)建 QWidget。

        0.2.199-补29dc:数千行 3D 峰表逐行建 _AssignmentCell(每行 2-3 个
        输入框)是打开卡顿主因;改为视口内懒创建、滚动维护。无组件的行
        以 label item 文本显示,读取/保存走文本回退。
        """
        table = self.peak_table
        n = table.rowCount()
        if n <= 0 or "label" not in self._peak_keys:
            return
        label_col = self._peak_keys.index("label")
        first = table.rowAt(0)
        last = table.rowAt(max(table.viewport().height() - 1, 0))
        if first < 0 and last < 0:
            return
        first = max(0, first - _ASSIGNMENT_WIDGET_BUFFER)
        last = min(n - 1, last + _ASSIGNMENT_WIDGET_BUFFER)
        is_3d = bool(self._peaks) and "F1_shift" in self._peaks[0]
        for row in range(n):
            has = table.cellWidget(row, label_col) is not None
            in_view = first <= row <= last
            if has and not in_view:
                # 移出视口:销毁组件,label 回写到 item 文本显示
                table.setCellWidget(row, label_col, None)
                item = table.item(row, label_col)
                if item is not None:
                    label = (
                        str(self._peaks[row].get("label", "") or "")
                        if 0 <= row < len(self._peaks)
                        else ""
                    )
                    item.setText(label)
            elif not has and in_view:
                label = (
                    str(self._peaks[row].get("label", "") or "")
                    if 0 <= row < len(self._peaks)
                    else ""
                )
                widget = _AssignmentCell(3 if is_3d else 2, row, label)
                widget.edited.connect(self._on_assignment_cell_edited)
                table.setCellWidget(row, label_col, widget)
                item = table.item(row, label_col)
                if item is not None:
                    item.setText("")

    def _populate_peak_table(self) -> None:
        """把 self._peaks 写入表格(2D/3D 列自动切换)。"""
        is_3d = bool(self._peaks) and "F1_shift" in self._peaks[0]
        self._set_peak_columns(is_3d)
        label_col = self._peak_keys.index("label")
        self._loading_peaks = True
        try:
            self.peak_table.setRowCount(len(self._peaks))
            for row, peak in enumerate(self._peaks):
                for col, key in enumerate(self._peak_keys):
                    self.peak_table.setItem(
                        row, col, QTableWidgetItem(str(peak.get(key, "")))
                    )
                # 0.2.199-补29dc:不再逐行创建 Assignment 编辑组件(数千行卡顿),
                # label 先以 item 文本显示,可视行由 _ensure_assignment_widgets
                # 按需替换为段输入框组件;读取/保存仍走 cellWidget/文本回退
                # 行首单元格保存完整峰 dict(label 等编辑外字段随行保留)
                self.peak_table.item(row, 0).setData(0x0100, dict(peak))
            self.peak_table.setColumnWidth(label_col, 142 if is_3d else 94)
        finally:
            self._loading_peaks = False
        self._ensure_assignment_widgets()

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
                if key == "label":
                    # 0.2.199-补29cr:label 从段输入框组件合并读取(item 文本已清空)
                    widget = self.peak_table.cellWidget(row, col)
                    if widget is not None and hasattr(widget, "merged_text"):
                        peak[key] = widget.merged_text()
                    else:
                        item = self.peak_table.item(row, col)
                        peak[key] = item.text() if item is not None else ""
                    continue
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

    def _on_peak_cell_edited(self, item) -> None:
        """峰表单元格编辑:内存同步;Assignment 列由段输入框组件管理
        (0.2.199-补29cp),不在此处理。"""
        if self._applying_label_format or self._loading_peaks:
            return
        if item is None:
            return
        if self.peak_table.column(item) == self._peak_keys.index("label"):
            return
        self._sync_peaks_in_memory()

    def _on_assignment_cell_edited(self, row: int) -> None:
        """Assignment 段输入框变化:逐段 Poky 规范化(空→?),合并为固定连字符
        label,立即生效到图上标签(0.2.199-补29cp)。"""
        if self._loading_peaks or self._applying_label_format:
            return
        widget = self.peak_table.cellWidget(
            row, self._peak_keys.index("label")
        )
        if widget is None or not hasattr(widget, "merged_text"):
            return
        label = widget.merged_text()
        # 0.2.199-补29cr:只更新内存与 viewer,不写 item 文本(避免重叠)
        if 0 <= row < len(self._peaks):
            self._peaks[row]["label"] = label
        self.viewer.apply_label_edit(row, label)

    def _on_add_peak_toggled(self, checked: bool) -> None:
        """Add peak 开关:开启后点击谱图加峰(吸附峰顶);与 1D/选择互斥。"""
        if checked:
            self.select_peaks_button.setChecked(False)
            self.viewer.show_1d_button.setChecked(False)
            self.viewer.set_box_select_mode(False)
        self.viewer.set_peak_click_mode("add" if checked else "select")
        self.add_peak_button.setText("Add peak mode: ON" if checked else "Add peak mode")

    def _on_select_mode_toggled(self, checked: bool) -> None:
        """选择模式:左键拖动框选峰;与 1D/Add peak 互斥。"""
        if checked:
            self.add_peak_button.setChecked(False)
            self.viewer.show_1d_button.setChecked(False)
            self.viewer.set_peak_click_mode("select")
        self.viewer.set_box_select_mode(checked)
        self.select_peaks_button.setText("Select mode: ON" if checked else "Select mode")

    def _on_viewer_1d_toggled(self, checked: bool) -> None:
        """1D 查看开启时关闭选择/加峰模式,并隐藏峰相关控件(0.2.199-补29bd)。"""
        if checked:
            self.select_peaks_button.setChecked(False)
            self.add_peak_button.setChecked(False)
            self.viewer.set_box_select_mode(False)
            self.viewer.set_peak_click_mode("select")
        self._viewer_1d_active = bool(checked)
        self.viewer.peak_label.setVisible(not checked)
        self.refresh()

    def _on_peaks_box_selected(self, rows: list[int]) -> None:
        """框选峰:联动峰表多选(程序化选行,不触发单峰闪烁)。"""
        model = self.peak_table.selectionModel()
        if model is None:
            return
        self._syncing_table_selection = True
        try:
            model.clearSelection()
            for row in rows:
                if 0 <= row < self.peak_table.rowCount():
                    model.select(
                        self.peak_table.model().index(row, 0),
                        QItemSelectionModel.SelectionFlag.Select
                        | QItemSelectionModel.SelectionFlag.Rows,
                    )
        finally:
            self._syncing_table_selection = False

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
        remove = set(rows)
        self._loading_peaks = True
        try:
            for row in rows:
                self.peak_table.removeRow(row)
        finally:
            self._loading_peaks = False
        # 0.2.199-补29bg:直接从内存峰列表删除,避免全表重读导致选择/删除卡顿
        self._peaks = [
            peak for i, peak in enumerate(self._peaks) if i not in remove
        ]
        self.viewer.set_peaks(self._peaks)
        self.save_peaks_button.setEnabled(bool(self._peaks))
        self._update_delete_button()

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
            peaks = import_peaks_poky(
                path, nuclei=self._current_3d_nuclei()
            )
        except Exception as exc:  # noqa: BLE001
            InfoDialog.show_info(self, "导入失败", str(exc))
            return
        if not peaks:
            InfoDialog.show_info(self, "导入结果", "文件中没有可解析的峰行")
            return
        self._peaks = self._assign_peak_ids(peaks)
        # 0.2.199-补29cy:导入峰表同样匹配 SMILE 可信度
        self._attach_smile_confidence()
        self._populate_peak_table()
        # 0.2.199-补29fu:viewer 与面板同源(补 Peak_ID/可信度后的列表)
        self.viewer.set_peaks(self._peaks)
        self.export_poky_button.setEnabled(True)
        self.save_peaks_button.setEnabled(True)
        self._update_delete_button()
        # 替换峰表关联关系(不覆盖文件):导入仅更新内存峰表,
        # 点「保存峰表」时以 Poky .list 写盘
        InfoDialog.show_info(
            self.import_poky_button,
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
                nuclei=self._current_3d_nuclei(),
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
            export_peaks_poky(
                path, self._peaks, nuclei=self._current_3d_nuclei()
            )
            InfoDialog.show_info(self, "导出完成", f"已导出 Poky 峰表: {path}")
        except Exception as exc:  # noqa: BLE001
            InfoDialog.show_info(self, "导出失败", str(exc))

    def _export_peaks_aligned(self) -> None:
        """对齐后导出:选参考 .list → 整体平移搜索 → 导出平移后的峰表。

        0.2.199-补29fw(用户):导出参考谱可以是任意 .list 文件,与选峰参考
        解耦;按整体平移把当前峰文件移动后输出,以参考为基准。
        """
        if not self._peaks or self.manager.project is None:
            return
        ref_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择导出参考峰文件(.list)",
            "",
            "Poky 峰表 (*.list);;所有文件 (*)",
        )
        if not ref_path:
            return
        from workflow.peak_align import (
            MIN_ACCEPTABLE_RATIO,
            align_peak_files,
            alignment_figure,
            shifted_rows,
        )

        try:
            # 0.2.199-补29fx:对齐容差可在软件设置里改(与线宽同处)
            from gui.settings import load_settings

            settings_tol = (
                load_settings().get("alignment_tolerance_ppm") or None
            )
            ref_path = Path(ref_path)
            ref_rows = import_peaks_poky(ref_path)
            if not ref_rows:
                InfoDialog.show_info(self, "对齐导出失败", "参考峰文件为空或无法解析")
                return
            # 0.2.199-补29fx:外部 3D .list 按 Poky 约定 w1=15N/w2=13C/w3=1H
            # (位置式导入即 F1=N/F2=C/F3=H);核名须显式传给对齐,否则 3D
            # 参考行无法解析共同核坐标(2D 参考自带 N_shift/H_shift 不受影响)。
            ref_nuclei_ref = None
            if ref_rows and "F1_shift" in ref_rows[0]:
                ref_nuclei_ref = ["15N", "13C", "1H"]
            is_3d = "F1_shift" in (self._peaks[0] if self._peaks else {})
            nuclei = self._current_3d_nuclei() if is_3d else None
            result = align_peak_files(
                self._peaks,
                ref_rows,
                cur_nuclei=nuclei,
                ref_nuclei=ref_nuclei_ref,
                tol_ppm=settings_tol,
            )
            if result["status"] == "no_common":
                InfoDialog.show_info(
                    self, "对齐导出失败", result["message"]
                )
                return
            if result["status"] == "low":
                InfoDialog.show_info(
                    self,
                    "对齐率偏低",
                    f"对齐率 {result['ratio']:.0%} < "
                    f"{MIN_ACCEPTABLE_RATIO:.0%},请检查参考谱是否与此谱相似",
                )
                return
            out_rows = shifted_rows(self._peaks, result["shift"], nuclei)
            default = None
            try:
                default = (
                    self.manager.data_dir(
                        self._current_exp_id, self._current_data_id, "peaks"
                    )
                    / f"{self._current_exp_id}-{self._current_data_id}"
                    "_refaligned.list"
                )
            except Exception:  # noqa: BLE001
                default = None
            start = (
                str(default.parent)
                if default is not None
                else str(Path.home())
            )
            path, _ = QFileDialog.getSaveFileName(
                self,
                "导出对齐后 Poky 峰表",
                str(default) if default is not None else start,
                "Poky 峰表 (*.list);;所有文件 (*)",
            )
            if not path:
                return
            export_peaks_poky(path, out_rows, nuclei=nuclei)
            # 对齐检查图 → 当前数据 figures/;文件名 = 当前峰表_aligned_参考峰表
            fig_lines: list[str] = []
            try:
                cur_path = None
                if self._current_spectrum:
                    try:
                        cur_path = self._peak_file_path(
                            Path(self._current_spectrum)
                        )
                    except Exception:
                        cur_path = None
                cur_name = (
                    Path(cur_path).stem
                    if cur_path
                    else f"{self._current_exp_id}-{self._current_data_id}"
                )
                ref_name = Path(ref_path).stem
                figures_dir = self.manager.data_dir(
                    self._current_exp_id, self._current_data_id, "figures"
                )
                fig_path = figures_dir / f"{cur_name}_aligned_{ref_name}.png"
                alignment_figure(
                    self._peaks,
                    ref_rows,
                    result["shift"],
                    fig_path,
                    cur_nuclei=nuclei,
                    ref_nuclei=ref_nuclei_ref,
                    tol_ppm=settings_tol,
                    cur_label=cur_name,
                    ref_label=ref_name,
                )
                fig_lines.append(f"对齐检查图(PNG): {fig_path}")
                fig_lines.append(
                    f"对齐检查图(SVG): {fig_path.with_suffix('.svg')}"
                )
            except Exception as exc:  # noqa: BLE001 - 图失败不阻断导出
                fig_lines.append(f"对齐检查图生成失败: {exc}")
            msg = (
                "已导出对齐后峰表: " + str(path) + "\n"
                f"(对齐率 {result['ratio']:.0%},偏移 {result['shift']})"
            )
            if fig_lines:
                msg += "\n" + "\n".join(fig_lines)
            InfoDialog.show_info(self, "导出完成", msg)
        except Exception as exc:  # noqa: BLE001
            InfoDialog.show_info(self, "对齐导出失败", str(exc))

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
            self._syncing_table_selection = True
            try:
                self.peak_table.selectRow(row)
            finally:
                self._syncing_table_selection = False

    def _on_menu_open_spectrum(self) -> None:
        """文件菜单:打开谱图文件到当前查看器(集成独立查看器入口)。"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "打开 NMRPipe 谱图",
            "",
            "NMRPipe 谱 (*.ft2 *.ft3 *.ft1 *.fid);;所有文件 (*)",
        )
        if not path:
            return
        target = Path(path)
        if self.open_spectrum(target):
            self._current_spectrum = target
            self._load_peaks(target)
            self.status_message.emit(f"已打开: {target.name}")

    def _on_menu_clear_spectrum(self) -> None:
        """文件菜单:清空当前查看器谱图与峰表。"""
        self._current_spectrum = None
        self._spectrum3d_panel.clear()
        self.viewer.clear()
        self._clear_peaks()

    def _on_menu_show_help(self) -> None:
        """帮助菜单:查看器操作说明。"""
        InfoDialog.show_info(
            self,
            "操作说明",
            "左键拖拽:框选放大;中键拖拽:平移;滚轮:缩放\n"
            "Home / 全谱视图:恢复完整范围\n"
            "选择模式:左键拖动框选峰;Add peak mode 开启后点击加峰(吸附峰顶)\n"
            "3D 谱(.ft3):右侧面板选择查看平面、切片滑块逐平面查看,\n"
            "  或切换 MIP/求和投影\n"
            "二维谱:右键谱图提取 1D 行/列切片;图层列表右键删除图层",
        )

    def _on_expand_toggled(self, expanded: bool) -> None:
        """谱图放大/收起:只有绘图区伸到左侧,右侧保留按键。"""
        self.expand_button.setText("收起" if expanded else "放大")
        if expanded:
            self._enter_expand_mode()
        else:
            self._exit_expand_mode()
        self.expand_requested.emit(expanded)

    def _enter_expand_mode(self) -> None:
        """放大:只把绘图区(plot_area)单独移到左侧覆盖原左三栏区域,
        右侧控件列保留全部按键;原小绘图区(viewer 容器)隐藏不显示。"""
        if self._expanded or self._expand_splitter is not None:
            return
        outer = self.layout()
        viewer = self.viewer
        self._collapsed_sizes = list(self._panel_splitter.sizes())
        self._view_splitter_sizes = list(viewer.view_splitter.sizes())
        plot_area = viewer.plot_area
        controls_widget = viewer.controls_layout.parentWidget()
        # 从 viewer 垂直 splitter 取出,避免两处同时显示
        # (QSplitter 无 removeWidget;setParent(None) 即从 splitter 移除)
        plot_area.setParent(None)
        controls_widget.setParent(None)
        self._expand_plot_area = plot_area
        self._expand_viewer_controls = controls_widget
        # 放大时峰表放开高度上限,吃右侧列剩余空间(更实用)
        self._peak_table_max = self.peak_table.maximumHeight()
        self.peak_table.setMaximumHeight(16777215)
        controls = QWidget()
        col = QVBoxLayout(controls)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        col.addWidget(self.lists_row_widget, 0)
        col.addWidget(controls_widget, 0)
        col.addWidget(self.peak_toolbar_widget, 0)
        col.addWidget(self.peak_table, 1)
        self._expand_controls = controls
        hsplit = QSplitter(Qt.Orientation.Horizontal)
        hsplit.addWidget(plot_area)
        hsplit.addWidget(controls)
        hsplit.setStretchFactor(0, 1)
        hsplit.setStretchFactor(1, 0)
        right_w = max(360, min(self.width(), 560)) if self.width() > 100 else 520
        hsplit.setSizes([max(400, self.width() - right_w), right_w])
        self._expand_splitter = hsplit
        outer.replaceWidget(self._panel_splitter, hsplit)
        self._panel_splitter.setVisible(False)
        viewer.setVisible(False)  # 原小绘图区所在容器不显示
        self._expanded = True

    def _exit_expand_mode(self) -> None:
        """还原:绘图区与控件面板回到 viewer,右侧控件回原位。"""
        if not self._expanded or self._expand_splitter is None:
            return
        outer = self.layout()
        viewer = self.viewer
        outer.replaceWidget(self._expand_splitter, self._panel_splitter)
        self._expand_splitter.setVisible(False)
        self._expand_splitter = None
        self._expand_controls = None
        if self._expand_plot_area is not None:
            viewer.view_splitter.addWidget(self._expand_plot_area)
        if self._expand_viewer_controls is not None:
            viewer.view_splitter.addWidget(self._expand_viewer_controls)
        if self._view_splitter_sizes:
            viewer.view_splitter.setSizes(self._view_splitter_sizes)
        self._expand_plot_area = None
        self._expand_viewer_controls = None
        if getattr(self, "_peak_table_max", None) is not None:
            self.peak_table.setMaximumHeight(self._peak_table_max)
        self._panel_splitter.addWidget(self.lists_row_widget)
        self._panel_splitter.addWidget(viewer)
        self._panel_splitter.addWidget(self.peak_toolbar_widget)
        self._panel_splitter.addWidget(self.peak_table)
        if self._collapsed_sizes:
            self._panel_splitter.setSizes(self._collapsed_sizes)
        viewer.setVisible(True)
        self._panel_splitter.setVisible(True)
        self._expanded = False

    def _on_peak_header_clicked(self, section: int) -> None:
        """点击 Assignment 列标题:开关图上峰指认标签(0.2.199-补29bf)。"""
        if section != 1:
            return
        new_state = not self.viewer.peak_labels_visible
        self.viewer.set_peak_labels_visible(new_state)
        header_item = self.peak_table.horizontalHeaderItem(1)
        if header_item is not None:
            header_item.setText(
                "Assignment ✓" if new_state else "Assignment ✗"
            )

    def _jump_3d_slice_to_peak(self, row: int) -> None:
        """3D 峰表点峰:把切片跳到该峰固定轴对应切面,再按 2D 逻辑显示
        (0.2.199-补29dc)。缺固定轴坐标或坐标越界的峰(2D 峰表/旧选峰结果)
        不跳转并提示(0.2.199-补29de:避免任意峰都跳到最后一个切面)。"""
        s3d_panel = self._spectrum3d_panel
        s3d = s3d_panel.spectrum3d
        primary = self.viewer.primary_spectrum
        if s3d is None or primary is None:
            return
        if getattr(primary, "slice_axis", None) is None:
            return  # 当前不是 3D 切片视图
        if not (0 <= row < len(self._peaks)):
            return
        slice_axis = getattr(s3d_panel, "_slice_axis", None)
        if slice_axis is None or not (0 <= int(slice_axis) < len(s3d.axes)):
            return
        peak = self._peaks[row]
        try:
            value = float(peak.get(f"F{int(slice_axis) + 1}_shift"))
        except (TypeError, ValueError):
            return  # 无固定轴坐标,无法跳切面
        if not value:
            self.log_message.emit(
                f"峰 {row + 1} 固定轴坐标缺失/为 0,无法定位切面"
            )
            return
        axis = s3d.axes[int(slice_axis)]
        index = int(axis.index_at(value))
        # 0.2.199-补29de:坐标越出轴范围(峰表与当前谱轴不匹配)不跳转
        if abs(value - float(axis.ppm[index])) > 3.0 * _axis_step(axis):
            self.log_message.emit(
                f"峰 {row + 1} 坐标 {axis.label} {value:.2f} ppm 不在当前"
                f"谱轴范围({float(axis.ppm[-1]):.1f}-{float(axis.ppm[0]):.1f}),"
                "可能为旧选峰结果,请重新选峰"
            )
            return
        if s3d_panel.slice_slider.value() != index:
            s3d_panel.slice_slider.setValue(index)
            s3d_panel.refresh()

    def _on_peak_row_selected(self) -> None:
        if self._syncing_table_selection:
            return  # 谱图点选/框选引起的程序化选行,只高亮不闪烁
        rows = self.peak_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        if 0 <= row < len(self._peaks):
            # 0.2.199-补29dc:3D 先跳到该峰对应切面,再按 2D 逻辑定位显示
            self._jump_3d_slice_to_peak(row)
            self.viewer.highlight_peak(row)

    def _on_peak_cell_clicked(self, row: int, column: int) -> None:
        """峰表单元格点击:已选中行再次点击同样触发闪烁定位
        (0.2.199-补29cm,selectionChanged 在已选中行上不触发)。"""
        if self._syncing_table_selection:
            return
        if 0 <= row < len(self._peaks):
            # 0.2.199-补29dc:3D 先跳到该峰对应切面,再定位显示
            self._jump_3d_slice_to_peak(row)
            self.viewer.highlight_peak(row)
