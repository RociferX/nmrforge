"""二维谱查看器控件(独立于项目管理 GUI):轮廓(contour)、峰标记、缩放/平移、长宽比。

交互习惯(nmrDraw/Poky):
- 左键拖拽框选放大;中键拖拽平移;滚轮缩放;
- 强度滑块控制轮廓起始水平;级数滑块控制轮廓密度(默认 8 级);
- 正峰黑/负峰红;峰为半透明圆点,点击选中放大并显示标签;
- 支持锁定显示长宽比(1:1 / 2:1 / 4:1 / 自由)。
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGraphicsRectItem,
    QGraphicsSceneMouseEvent,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.qc.peak_detection import snap_to_peak_top
from viewer.contour_layer import ContourLayer
from viewer.nmr_viewbox import NMRViewBox
from viewer.spectrum import Spectrum, Spectrum1D, SpectrumAxis

_COLORS = ("#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf")
_DEFAULT_LEVELS = 8


class _BoxSelectOverlay(QWidget):
    """框选虚线覆盖层:绘在 plot viewport 之上,拖动时只重绘本层。

    0.2.199-补29ax:不在场景内加/移 item(避免大谱等高线整场景重绘卡死,
    以及场景事件处理中途增删 item 的不稳定)。
    """

    def __init__(self, parent: QWidget, viewer: SpectrumViewer) -> None:
        super().__init__(parent)
        self._viewer = viewer
        self._scene_p0: QPointF | None = None
        self._scene_p1: QPointF | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_rect(self, p0: QPointF, p1: QPointF) -> None:
        self._scene_p0 = p0
        self._scene_p1 = p1
        self.update()

    def has_rect(self) -> bool:
        return self._scene_p0 is not None and self._scene_p1 is not None

    def clear(self) -> None:
        self._scene_p0 = None
        self._scene_p1 = None
        self.update()

    def paintEvent(self, event) -> None:
        if not self.has_rect():
            return
        plot = self._viewer.plot
        try:
            p0 = plot.mapFromScene(self._scene_p0)
            p1 = plot.mapFromScene(self._scene_p1)
        except Exception:  # noqa: BLE001
            return
        rect = QRect(p0, p1).normalized()
        painter = QPainter(self)
        try:
            painter.fillRect(rect, QColor(14, 99, 156, 40))
            painter.setPen(QPen(QColor("#0e639c"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(rect)
        finally:
            painter.end()


class SpectrumViewer(QWidget):
    """支持多谱叠加的二维谱查看器。"""

    peak_clicked = pyqtSignal(int)  # 峰行号
    manual_peak_requested = pyqtSignal(dict)  # 点击加峰:峰行 dict(已吸附峰顶)
    delete_peak_requested = pyqtSignal(float, float)
    peaks_box_selected = pyqtSignal(list)  # 框选峰:行号列表(选择模式)

    def __init__(
        self,
        parent: QWidget | None = None,
        level_count: int = _DEFAULT_LEVELS,
        contour_zoom: float = 2.0,
    ) -> None:
        super().__init__(parent)
        self._peaks: list[dict] = []
        self._selected_peak: int | None = None
        self._click_mode = "select"  # select / add / delete
        # 0.2.199-补29at:选择模式(左键拖动框选峰)
        self._box_select_enabled = False
        self._box_selecting = False
        self._box_press_scene: QPointF | None = None
        self._box_overlay: _BoxSelectOverlay | None = None
        self._box_selected_rows: set[int] = set()
        # 0.2.199-补29ay:峰数据坐标缓存(框选只做范围比对,不再逐峰换算)
        self._peak_data_xy: list[tuple[float, float]] = []
        self._suppress_click = False  # 框选释放不当作单击
        self._mode_1d = False
        self._primary_1d: Spectrum1D | None = None
        self._plot_1d: pg.PlotDataItem | None = None
        self._peaks_visible = True

        self.plot = pg.PlotWidget(viewBox=NMRViewBox())
        self.plot.setBackground("w")
        self.plot.setMenuEnabled(False)
        self.plot.getViewBox().setMouseMode(pg.ViewBox.RectMode)
        # 显示约定(用户 0.2.59 反馈修正):1H 高 ppm 在左、15N 高 ppm 在下;
        # 数据列 0 = 高 ppm(x 列 0 在左);contour 不翻转(view y = 数据行),
        # invertY(False) 下 view y 增大=屏幕向上 → 行 0(高 ppm)显示在底部。
        self.plot.getViewBox().invertY(False)

        self.layers: list[ContourLayer] = []
        self.layer_names: list[str] = []
        self.layer_spectra: list[Spectrum] = []
        self._primary: Spectrum | None = None
        self._level_count = max(5, int(level_count))
        self._contour_zoom = max(1.0, float(contour_zoom))

        # 0.2.199-补29az:峰标记 Poky 风格 ×,数据坐标尺寸随谱图缩放
        self._peak_size = 8.0
        self.peak_item = pg.ScatterPlotItem(
            pen=pg.mkPen("#8b0000", width=1.5),
            brush=pg.mkBrush(255, 70, 70, 150),
            size=self._peak_size,
            symbol="x",
            pxMode=False,
        )
        self.peak_item.setZValue(20)
        self.peak_item.sigClicked.connect(self._on_peak_clicked)
        self.plot.addItem(self.peak_item)
        self.peak_label_items: list[pg.TextItem] = []

        self.layer_list = QListWidget()
        self.layer_list.setMaximumHeight(90)
        self.layer_list.itemChanged.connect(self._on_layer_toggle)
        self.layer_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.layer_list.customContextMenuRequested.connect(
            self._on_layer_context_menu
        )

        self.level_slider = QSlider(Qt.Orientation.Horizontal)
        self.level_slider.setFixedHeight(18)
        self.level_slider.setRange(1, 100)
        self.level_slider.setValue(31)
        self.level_slider.valueChanged.connect(self._update_levels_debounced)
        self.level_slider.sliderReleased.connect(self._update_levels)

        self.count_slider = QSlider(Qt.Orientation.Horizontal)
        self.count_slider.setFixedHeight(18)
        self.count_slider.setRange(5, 60)
        self.count_slider.setValue(self._level_count)
        self.count_slider.valueChanged.connect(self._on_level_count)

        self.reset_button = QPushButton("Full view")
        self.reset_button.clicked.connect(self.reset_view)
        # 0.2.150:谱图数据边界框随谱出现:不预创建,
        # 第一张 2D 谱加载时才创建并挂到 ViewBox
        # (plot.addItem ignoreBounds=True):数据坐标系君与谱一起
        # 缩放/平移,放大后自然离开视野,看到全谱
        # 即可见;且不参与自动缩放计算(无启动乱跳)。
        self._data_bounds_item: QGraphicsRectItem | None = None



        # 0.2.133: aspect ratio slider(0.2.147 移到控件行 0 并排)
        self.aspect_slider = QSlider(Qt.Orientation.Horizontal)
        self.aspect_slider.setRange(0, 400)
        self.aspect_slider.setValue(100)
        self.aspect_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.aspect_slider.setTickInterval(50)
        self.aspect_slider.valueChanged.connect(self._on_aspect_changed)

        # Show peaks 复选框(0.2.147 移到峰操作行,位于 Add peak 前)
        self.show_peaks_checkbox = QCheckBox("Show peaks")
        self.show_peaks_checkbox.setChecked(True)
        self.show_peaks_checkbox.toggled.connect(self.set_peaks_visible)
        # 0.2.148:数值可直接输入(标题后显示具体值);滑块与输入双向同步
        self.level_label = QDoubleSpinBox()
        self.level_label.setPrefix("Contour start ")
        self.level_label.setSuffix("%")
        self.level_label.setRange(0.0, 100.0)
        self.level_label.setDecimals(2)
        self.level_label.setValue(self._level_fraction() * 100.0)
        self.level_label.setFixedWidth(150)
        self.level_label.valueChanged.connect(self._on_level_spin_changed)

        self.count_label = QSpinBox()
        self.count_label.setPrefix("Levels ")
        self.count_label.setRange(5, 60)
        self.count_label.setValue(self._level_count)
        self.count_label.setFixedWidth(110)
        self.count_label.valueChanged.connect(self._on_count_spin_changed)

        self.aspect_label = QDoubleSpinBox()
        self.aspect_label.setPrefix("Aspect ")
        self.aspect_label.setSuffix("x")
        self.aspect_label.setRange(0.0, 4.0)
        self.aspect_label.setDecimals(2)
        self.aspect_label.setValue(1.00)
        self.aspect_label.setFixedWidth(130)
        self.aspect_label.setSpecialValueText("Aspect free")
        self.aspect_label.valueChanged.connect(self._on_aspect_spin_changed)


        # TopSpin 式 1D 条带开关(0.2.147 行 1: 与 Full view 并排)
        self.show_1d_button = QPushButton("1D")
        self.show_1d_button.setCheckable(True)
        self.show_1d_button.setToolTip(
            "开启后出现随鼠标十字线,点击显示该处两个一维谱(TopSpin 式)"
        )
        self.show_1d_button.toggled.connect(self.set_1d_mode)

        self.crosshair_label = QLabel("Move mouse to read ppm")
        self.crosshair_label.setWordWrap(True)
        self.peak_label = QLabel("")
        self.peak_label.setWordWrap(True)

        controls = QWidget()
        controls_layout = QGridLayout(controls)
        # 0.2.147:横向控件行间隔明显;Layers 列表移出到面板顶部行
        controls_layout.setContentsMargins(6, 4, 6, 4)
        controls_layout.setHorizontalSpacing(20)
        controls_layout.setVerticalSpacing(6)

        # 行 0:contour start / Levels / Aspect ratio 三组并排
        def _labeled_slider(spinbox, slider) -> QVBoxLayout:
            """标题+数值(可输入)一行,slider 在下方。"""
            box = QVBoxLayout()
            box.setSpacing(2)
            box.addWidget(spinbox)
            box.addWidget(slider)
            return box

        controls_layout.addLayout(
            _labeled_slider(self.level_label, self.level_slider),
            0, 0,
        )
        controls_layout.addLayout(
            _labeled_slider(self.count_label, self.count_slider),
            0, 1,
        )
        controls_layout.addLayout(
            _labeled_slider(self.aspect_label, self.aspect_slider),
            0, 2,
        )

        # 行 1:Full view + 1D
        row1 = QHBoxLayout()
        row1.setSpacing(16)
        row1.addWidget(self.reset_button)
        row1.addWidget(self.show_1d_button)
        row1.addStretch(1)
        controls_layout.addLayout(row1, 1, 0, 1, 3)

        # 行 2:读数信息
        row2 = QHBoxLayout()
        row2.setSpacing(20)
        row2.addWidget(self.crosshair_label, 1)
        row2.addWidget(self.peak_label, 1)
        controls_layout.addLayout(row2, 2, 0, 1, 3)

        # 行 3:p0/p1 相位面板 —— 单行,仅 1D 模式显示
        from viewer.phase_panel import PhasePanel

        self.phase_panel = PhasePanel()
        self.phase_panel.phase_changed.connect(self._on_phase_changed)
        controls_layout.addWidget(self.phase_panel, 3, 0, 1, 3)
        self.phase_panel.setVisible(False)

        self.controls_layout = controls_layout

        # TopSpin 式 1D 条带:上方行迹线(F2)、右侧列迹线(F1),与主谱联动
        self.strip_top = pg.PlotWidget()
        self.strip_top.setFixedHeight(110)
        self.strip_top.setMenuEnabled(False)
        self.strip_top.getViewBox().setXLink(self.plot.getViewBox())
        self.strip_top_curve = pg.PlotDataItem(pen=pg.mkPen("#1f77b4", width=1))
        self.strip_top.addItem(self.strip_top_curve)
        self.strip_top.hide()
        self.strip_right = pg.PlotWidget()
        self.strip_right.setFixedWidth(90)
        self.strip_right.setMenuEnabled(False)
        self.strip_right.getViewBox().setYLink(self.plot.getViewBox())
        self.strip_right.getViewBox().invertY(False)
        self.strip_right_curve = pg.PlotDataItem(pen=pg.mkPen("#d62728", width=1))
        self.strip_right.addItem(self.strip_right_curve)
        self.strip_right.hide()
        self._strips_active = False

        self._crosshair_v = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen("#888888", width=1, style=Qt.PenStyle.DashLine),
        )
        self._crosshair_h = pg.InfiniteLine(
            angle=0,
            movable=False,
            pen=pg.mkPen("#888888", width=1, style=Qt.PenStyle.DashLine),
        )
        self._crosshair_v.setZValue(30)
        self._crosshair_h.setZValue(30)
        self.plot.addItem(self._crosshair_v)
        self.plot.addItem(self._crosshair_h)
        self._crosshair_v.hide()
        self._crosshair_h.hide()

        plot_area = QWidget()
        plot_grid = QGridLayout(plot_area)
        plot_grid.setContentsMargins(0, 0, 0, 0)
        plot_grid.setSpacing(0)
        plot_grid.addWidget(self.strip_top, 0, 1)
        plot_grid.addWidget(self.strip_right, 1, 0)
        plot_grid.addWidget(self.plot, 1, 1)
        self.plot_area = plot_area

        # 上下布局:上方谱图区,下方控制面板;谱图默认 1:1 正方形
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(plot_area)
        splitter.addWidget(controls)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([620, 210])
        self.plot.setMinimumHeight(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        # 0.2.199-补29ax:框选虚线画在 viewport 覆盖层(拖动不触发场景重绘)
        self._box_overlay = _BoxSelectOverlay(self.plot.viewport(), self)
        self._box_overlay.setGeometry(self.plot.viewport().rect())
        self.plot.viewport().installEventFilter(self)
        self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.plot.scene().sigMouseClicked.connect(self._on_plot_clicked)
        self.set_aspect_ratio(1.0)  # 默认正方形(1:1 数据长宽比)
        # 0.2.133: contour state
        self._contour_states: dict[str, tuple[int, int]] = {}
        self._mouse_left_pressed = False
        self.plot.scene().installEventFilter(self)

    # ------------------------------------------------------------ layers

    def clear(self) -> None:
        self._mode_1d = False
        self._primary_1d = None
        if self._plot_1d is not None:
            self.plot.removeItem(self._plot_1d)
            self._plot_1d = None
        self.set_1d_mode(False)
        # 统一 2D 显示方向:行 0(高 ppm)在底部,1D 视图后不泄漏到后续 2D/3D
        self.plot.getViewBox().invertY(False)
        for layer in self.layers:
            self.plot.removeItem(layer)
        self.layers.clear()
        self.layer_names.clear()
        self.layer_spectra.clear()
        self.layer_list.clear()
        self._primary = None
        if self._data_bounds_item is not None:
            self.plot.removeItem(self._data_bounds_item)
            self._data_bounds_item = None
        self.phase_panel.set_available(False)
        self.set_peaks([])

    def add_spectrum(
        self,
        spectrum: Spectrum | Spectrum1D,
        name: str | None = None,
        color: str | None = None,
    ) -> str:
        """叠加一张谱;第一张成为主谱(提供 ppm 轴与峰坐标系)。

        一维谱(Spectrum1D/.fid/二维行/列切片)走 1D 迹线显示模式。
        """
        if spectrum.data.ndim == 1:
            return self._show_1d(spectrum, name or "")
        if self._mode_1d:
            self._restore_2d()
        color = color or _COLORS[len(self.layers) % len(_COLORS)]
        if name is None:
            name = (
                spectrum.source.stem
                if spectrum.source is not None
                else f"Spectrum {len(self.layers) + 1}"
            )
        layer = ContourLayer(
            spectrum.data,
            self._levels_for(spectrum),
            pg.mkPen(color, width=1),
            neg_pen=pg.mkPen("#e74c3c", width=1),
            zoom=self._contour_zoom,
        )
        self.plot.addItem(layer)
        self.layers.append(layer)
        self.layer_names.append(name)
        self.layer_spectra.append(spectrum)

        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self.layer_list.addItem(item)

        if self._primary is None:
            self._primary = spectrum
            self._setup_axes(spectrum)
            self.reset_view()
        self._update_data_bounds()
        self._refresh_phase_availability()
        return name

    def update_spectrum_data(
        self, spectrum: Spectrum, name: str | None = None
    ) -> bool:
        """原位更新主谱数据(3D 切片切换):不 clear/重建,避免闪烁与慢。

        仅当视图只有一张二维主谱(3D 切片场景)时生效;其它情况回退
        clear+add。返回 True 表示已原位更新。
        """
        if (
            spectrum.data.ndim != 2
            or len(self.layers) != 1
            or self._primary is None
        ):
            self.clear()
            self.add_spectrum(spectrum, name=name)
            return False
        layer = self.layers[0]
        layer.setData(spectrum.data, self._levels_for(spectrum))
        self._primary = spectrum
        self.layer_spectra[0] = spectrum
        if name is not None and self.layer_names:
            self.layer_names[0] = name
            if self.layer_list.count():
                self.layer_list.item(0).setText(name)
        self._setup_axes(spectrum)
        self._update_data_bounds()
        self._refresh_phase_availability()
        return True

    def _level_fraction(self) -> float:
        """滑块值 → 起点百分比(立方映射:前 10% 阈值占拖动条大部分,低阈值精细可调)。"""
        value = max(1, self.level_slider.value())
        return (value / 100.0) ** 3

    def _level_label_text(self) -> str:
        return f"Contour start {self._level_fraction() * 100:.2f}%"

    def _levels_for(self, spectrum: Spectrum) -> np.ndarray:
        """从起点(base)到最大值之间取 n 级对数间隔,含对称负级。

        二维 FID 等动态范围大的数据可用 ``robust_max``(高分位数)代替
        全局最大值作为基准,避免被个别尖峰淹没。
        """
        maximum = float(
            getattr(spectrum, "robust_max", 0.0) or spectrum.max_intensity
        )
        if maximum <= 0:
            maximum = abs(float(np.min(spectrum.data))) if spectrum.data.size else 0.0
        if maximum <= 0:
            return np.array([-1.0, 1.0])
        base = maximum * self._level_fraction()
        positive = np.geomspace(max(base, maximum * 1e-6), maximum, self._level_count)
        return np.concatenate([-positive[::-1], positive])

    def _update_levels_debounced(self) -> None:
        """拖动过程中仅刷新数值,避免每格都重建轮廓(性能)。"""
        self.level_label.setValue(self._level_fraction() * 100.0)

    def _update_levels(self) -> None:
        """松开滑块/级数变化时重建轮廓(复用已缓存插值数据)。"""
        self.level_label.setValue(self._level_fraction() * 100.0)
        for layer, spectrum in zip(self.layers, self.layer_spectra):
            layer.set_levels(self._levels_for(spectrum))

    def _on_level_count(self, value: int) -> None:
        self._level_count = value
        self.count_label.setValue(value)
        self._update_levels()

    def _on_level_spin_changed(self, percent: float) -> None:
        """输入轮廓起始百分比 -> 反推滑块值(立方映射取整)。"""
        v = int(round(100.0 * (max(percent, 0.0) / 100.0) ** (1.0 / 3.0)))
        self.level_slider.setValue(max(1, min(100, v)))

    def _on_count_spin_changed(self, value: int) -> None:
        self.count_slider.setValue(value)

    def _on_aspect_spin_changed(self, value: float) -> None:
        self.aspect_slider.setValue(int(round(value * 100.0)))

    @staticmethod
    def _axis_ticks(
        axis: SpectrumAxis, count: int
    ) -> tuple[list[tuple[int, str]], str]:
        """ppm 有效轴显示 ppm 刻度;时间域轴(如 FID 时点)显示点序号。

        0.2.133:刻度标签尽量取整(±0.1 ppm 内显示整数,否则 1 位
        小数),避免长小数标签在缩小时相互重叠。
        """
        if axis.sw_hz > 0 and axis.obs_mhz > 0:
            ticks = []
            for i in np.linspace(0, axis.size - 1, count):
                ppm = axis.ppm_at(int(i))
                if abs(ppm - round(ppm)) < 0.1:
                    ticks.append((int(i), f"{round(ppm):.0f}"))
                else:
                    ticks.append((int(i), f"{ppm:.1f}"))
            return ticks, f"{axis.label} (ppm)"
        ticks = [
            (int(i), str(int(i))) for i in np.linspace(0, axis.size - 1, 6)
        ]
        return ticks, axis.label

    def _setup_axes(self, spectrum: Spectrum) -> None:
        x_axis = spectrum.x_axis
        y_axis = spectrum.y_axis
        x_ticks, x_label = self._axis_ticks(x_axis, 10)
        y_ticks, y_label = self._axis_ticks(y_axis, 10)
        self.plot.getAxis("bottom").setTicks([x_ticks])
        self.plot.getAxis("left").setTicks([y_ticks])
        self.plot.setLabels(bottom=x_label, left=y_label)
        self._connect_axis_refresh()


    def _connect_axis_refresh(self) -> None:
        """缩放/平移后按当前可视范围重建刻度(0.2.133)。

        固定 ticks 在缩小时标签会挤在一起;这里随视角重选 5 个刻度,
        每个刻度标签尽量取整,避免重叠与长小数标签。
        """
        if getattr(self, "_axis_refresh_connected", False):
            return
        try:
            self.plot.getViewBox().sigRangeChanged.connect(
                self._refresh_visible_ticks
            )
            self._axis_refresh_connected = True
        except Exception:  # noqa: BLE001
            self._axis_refresh_connected = False

    def _refresh_visible_ticks(self, *_args) -> None:
        """按可视数据范围重建底轴/左轴刻度(每轴最多 5 个)。"""
        if not self.layers or self._mode_1d:
            return
        spectrum = self.layer_spectra[0] if self.layer_spectra else None
        if spectrum is None:
            return
        try:
            x_range, y_range = self.plot.getViewBox().viewRange()
        except Exception:  # noqa: BLE001
            return
        x_ticks, _ = self._visible_axis_ticks(spectrum.x_axis, x_range)
        y_ticks, _ = self._visible_axis_ticks(spectrum.y_axis, y_range)
        self.plot.getAxis("bottom").setTicks([x_ticks])
        self.plot.getAxis("left").setTicks([y_ticks])

    @staticmethod
    def _visible_axis_ticks(
        axis: SpectrumAxis, span: tuple[float, float]
    ) -> tuple[list[tuple[int, str]], str]:
        """可视跨度内选 5 个等距刻度(索引位置,标签取整 ppm/序号)。"""
        lo, hi = span
        lo = max(0.0, float(lo))
        hi = min(float(axis.size - 1), float(hi))
        if hi <= lo or axis.size <= 1:
            return [], ""
        indices = np.linspace(lo, hi, 5)
        ticks: list[tuple[int, str]] = []
        seen: set[int] = set()
        for idx in indices:
            i = int(round(idx))
            if i in seen:
                continue
            seen.add(i)
            if i < 0 or i >= axis.size:
                continue
            if axis.sw_hz > 0 and axis.obs_mhz > 0:
                ppm = axis.ppm_at(i)
                label = (
                    f"{round(ppm):.0f}"
                    if abs(ppm - round(ppm)) < 0.1
                    else f"{ppm:.1f}"
                )
            else:
                label = str(i)
            ticks.append((i, label))
        if len(ticks) <= 1 and axis.size > 1:
            mid = int(round((lo + hi) / 2.0))
            ticks.append((mid, str(mid)))
        return ticks, ""


    def _on_layer_toggle(self, item: QListWidgetItem) -> None:
        index = self.layer_list.row(item)
        if 0 <= index < len(self.layers):
            visible = item.checkState() == Qt.CheckState.Checked
            self.layers[index].setVisible(visible)


    # ------------------------------------------------------------ 1D

    def _show_1d(self, spectrum1d: Spectrum1D, name: str = "") -> str:
        """把一维谱(FID/二维行/列切片)显示为 1D 迹线,隐藏 2D 轮廓层。"""
        self._mode_1d = True
        self._primary_1d = spectrum1d
        if self._plot_1d is None:
            self._plot_1d = pg.PlotDataItem(pen=pg.mkPen("#1f77b4", width=1))
            self._plot_1d.setZValue(10)
            self.plot.addItem(self._plot_1d)
        x = spectrum1d.x_values()
        self._plot_1d.setData(x, spectrum1d.data)
        for layer in self.layers:
            layer.setVisible(False)
        axis = spectrum1d.axis
        if spectrum1d.ppm_valid:
            ticks = []
            for i in np.linspace(0, axis.size - 1, 10):
                ppm = axis.ppm_at(int(i))
                if abs(ppm - round(ppm)) < 0.1:
                    ticks.append((int(i), f"{round(ppm):.0f}"))
                else:
                    ticks.append((int(i), f"{ppm:.1f}"))
            self.plot.getAxis("bottom").setTicks([ticks])
            self.plot.setLabels(bottom=f"{axis.label} (ppm)", left="Intensity")
        else:
            ticks = [
                (int(i), str(int(i)))
                for i in np.linspace(0, axis.size - 1, 6)
            ]
            self.plot.getAxis("bottom").setTicks([ticks])
            self.plot.setLabels(bottom=axis.label, left="Intensity")
        self.plot.getViewBox().invertY(False)
        self.set_1d_mode(False)
        self.reset_view()
        self._connect_axis_refresh()
        self._refresh_phase_availability()
        return name or (
            spectrum1d.source.stem if spectrum1d.source is not None else "1D"
        )

    def _restore_2d(self) -> None:
        """退出 1D 视图,恢复 2D 轮廓层。"""
        if not self._mode_1d:
            return
        self._mode_1d = False
        self._primary_1d = None
        if self._plot_1d is not None:
            self.plot.removeItem(self._plot_1d)
            self._plot_1d = None
        for layer in self.layers:
            layer.setVisible(True)
        self.plot.getViewBox().invertY(False)
        if self._primary is not None:
            self._setup_axes(self._primary)
            self.reset_view()
            self._apply_peak_items()
        self._refresh_phase_availability()

    # ------------------------------------------------------------ phase
    def _refresh_phase_availability(self) -> None:
        """按当前显示启用相位面板:一维谱或 1D 条带时可用(仅显示,不改数据)。"""
        self.phase_panel.set_visible_1d_mode(self._phase_target() != "")

    def _phase_target(self) -> str:
        """当前可调相的 1D 目标:主一维迹线 / 1D 条带。"""
        if self._mode_1d and self._primary_1d is not None:
            return "1d"
        if self._strips_active and self._primary is not None:
            return "strips"
        return ""

    @staticmethod
    def _display_phase(real: np.ndarray, p0: float, p1: float) -> np.ndarray:
        """显示用相位旋转(不改变数据):解析信号(实部+Hilbert 虚部)旋转后取实部。

        P0/P1 仅影响显示,等价 nmrDraw 的肉眼看相;实数谱也可用。
        """
        try:
            from scipy.signal import hilbert
        except ImportError:  # pragma: no cover - scipy 为项目依赖
            return np.asarray(real, dtype=float)
        data = np.asarray(real, dtype=float)
        if data.ndim != 1 or data.size == 0:
            return data
        analytic = hilbert(data)
        n = data.shape[0]
        k = np.arange(n, dtype=float)
        angle = np.deg2rad(p0 + p1 * k / max(1, n - 1))
        return (analytic * np.exp(1j * angle)).real

    def _on_phase_changed(self, final: bool) -> None:
        """相位滑块变化:1D 实时更新显示;条带模式下同步两个一维迹线。"""
        target = self._phase_target()
        if target == "1d":
            self._update_phased_1d()
        elif target == "strips":
            self._refresh_strips_phase()


    # 0.2.133: 左键按住状态跟踪(1D 模式下十字虚线随动)
    # 0.2.145: 按住拖动期间直接处理 MouseMove
    # 0.2.148: 真实场景事件类型是 GraphicsSceneMouse*,
    #          与普通 MouseButtonPress/Move 均认为按住/移动
    @staticmethod
    def _mouse_event_kind(etype) -> str:
        if etype in (
            QEvent.Type.GraphicsSceneMousePress,
            QEvent.Type.MouseButtonPress,
        ):
            return "press"
        if etype in (
            QEvent.Type.GraphicsSceneMouseRelease,
            QEvent.Type.MouseButtonRelease,
        ):
            return "release"
        if etype in (
            QEvent.Type.GraphicsSceneMouseMove,
            QEvent.Type.MouseMove,
        ):
            return "move"
        return ""

    def eventFilter(self, obj, event) -> bool:
        if obj is self.plot.viewport() and event.type() == QEvent.Type.Resize:
            if self._box_overlay is not None:
                self._box_overlay.setGeometry(self.plot.viewport().rect())
            return super().eventFilter(obj, event)
        if obj is self.plot.scene():
            kind = self._mouse_event_kind(event.type())
            if kind == "press":
                if event.button() == Qt.MouseButton.LeftButton:
                    self._mouse_left_pressed = True
                    self._box_press_scene = self._box_scene_pos(event)
                    self._box_selecting = True
            elif kind == "release":
                if event.button() == Qt.MouseButton.LeftButton:
                    self._mouse_left_pressed = False
                    if (
                        self._box_select_enabled
                        and self._click_mode == "select"
                        and self._box_selecting
                    ):
                        pos = self._box_scene_pos(event)
                        if (
                            self._box_overlay is not None
                            and self._box_overlay.has_rect()
                            and pos is not None
                        ):
                            self._finish_box_select(pos)
                        else:
                            self._cancel_box_select()
            elif kind == "move" and self._mouse_left_pressed:
                if self._strips_active or self._mode_1d:
                    # 0.2.199-补10:场景事件可能是 QGraphicsSceneMouseEvent
                    # (取 scenePos)或普通 QMouseEvent(取 position);PyQt6 无
                    # scenePosition 属性
                    if isinstance(event, QGraphicsSceneMouseEvent):
                        self._follow_drag(event.scenePos())
                    else:
                        self._follow_drag(event.position())
                elif (
                    self._box_select_enabled
                    and self._click_mode == "select"
                    and self._box_selecting
                    and self._box_press_scene is not None
                ):
                    pos = self._box_scene_pos(event)
                    if pos is not None:
                        if (pos - self._box_press_scene).manhattanLength() > 6:
                            if self._box_overlay is not None:
                                self._box_overlay.set_rect(
                                    self._box_press_scene, pos
                                )
        return super().eventFilter(obj, event)

    def _box_scene_pos(self, event) -> QPointF | None:
        """场景事件取 scenePos;普通 QMouseEvent 由 plot 映射到场景坐标。"""
        if isinstance(event, QGraphicsSceneMouseEvent):
            return event.scenePos()
        try:
            return self.plot.mapToScene(event.position().toPoint())
        except Exception:  # noqa: BLE001
            return None

    def _cancel_box_select(self) -> None:
        self._box_selecting = False
        self._box_press_scene = None
        if self._box_overlay is not None:
            self._box_overlay.clear()

    def _finish_box_select(self, scene_pos) -> None:
        """框选结束:选中矩形内全部峰并联动峰表。"""
        if self._box_press_scene is None or self._primary is None:
            self._cancel_box_select()
            return
        rect = QRectF(self._box_press_scene, scene_pos).normalized()
        self._suppress_click = True  # 框选结束,释放不当作单击
        self._cancel_box_select()
        try:
            v0 = self.plot.getViewBox().mapSceneToView(rect.topLeft())
            v1 = self.plot.getViewBox().mapSceneToView(rect.bottomRight())
        except Exception:  # noqa: BLE001
            return
        xi0, yi0 = self._view_to_data(v0)
        xi1, yi1 = self._view_to_data(v1)
        if xi0 < 0 or xi1 < 0:
            return
        lo_x, hi_x = sorted((xi0, xi1))
        lo_y, hi_y = sorted((yi0, yi1))
        # 0.2.199-补29ay:只比对框范围与已缓存峰坐标,不做其它运算
        rows: list[int] = [
            row
            for row, (xi, yi) in enumerate(self._peak_data_xy)
            if lo_x <= xi <= hi_x and lo_y <= yi <= hi_y
        ]
        self._box_selected_rows = set(rows)
        self._selected_peak = None
        self._apply_peak_items()
        if rows:
            self.peaks_box_selected.emit(sorted(rows))


    def _follow_drag(self, scene_pos) -> None:
        """按住左键拖动:条带模式移动十字线+两个 1D 迹线;1D 数据模式更新读数。"""
        if self._primary is None:
            return
        try:
            point = self.plot.getViewBox().mapSceneToView(scene_pos)
        except Exception:  # noqa: BLE001
            return
        xi, yi = self._view_to_data(point)
        if xi < 0:
            return
        if self._strips_active:
            self._move_crosshair(float(point.x()), float(point.y()))
            self._update_strips(yi, xi)
        elif self._mode_1d and self._primary_1d is not None:
            axis = self._primary_1d.axis
            self.crosshair_label.setText(
                f"{axis.label} {axis.ppm_at(xi):.3f} ppm"
            )


    def _update_phased_1d(self) -> None:
        if self._plot_1d is None or self._primary_1d is None:
            return
        p0, p1 = self.phase_panel.values()
        phased = self._display_phase(self._primary_1d.data, p0, p1)
        self._plot_1d.setData(self._primary_1d.x_values(), phased)

    def _refresh_strips_phase(self) -> None:
        """按当前相位重算两个 1D 条带迹线(显示用)。"""
        pos = getattr(self, "_strip_pos", None)
        if pos is None or not self._strips_active or self._primary is None:
            return
        self._update_strips(pos[0], pos[1])

    def save_contour_state(self, key: str) -> None:
        """Save current contour state by key."""
        self._contour_states[key] = (self.level_slider.value(), self._level_count)

    def restore_contour_state(self, key: str) -> None:
        """Restore contour state; default to 3%, 8 levels."""
        state = self._contour_states.get(key)
        if state is not None:
            self.level_slider.setValue(state[0])
            self.count_slider.setValue(state[1])
        else:
            self.level_slider.setValue(31)
            self.count_slider.setValue(8)

    def set_1d_mode(self, active: bool) -> None:
        """开关一维谱显示(TopSpin 式):十字线 + 上/右 1D 条带。"""
        active = bool(active)
        if self._primary is None or self._mode_1d:
            active = False
        if active == self._strips_active:
            return
        self._strips_active = active
        if self.show_1d_button.isChecked() != active:
            self.show_1d_button.setChecked(active)
        self.strip_top.setVisible(active)
        self.strip_right.setVisible(active)
        self._crosshair_v.setVisible(active)
        self._crosshair_h.setVisible(active)
        if active:
            if self._data_bounds_item is not None:
                self._data_bounds_item.setVisible(False)
            self._setup_strip_axes()
            # 右侧 1D 条带方向与二维谱 Y 轴保持一致(谱+坐标轴一起翻正)
            self.strip_right.getViewBox().invertY(
                bool(self.plot.getViewBox().state.get("yInverted", True))
            )
            rows, cols = self._primary.data.shape
            self._update_strips(rows // 2, cols // 2)
            self._move_crosshair(cols // 2, rows // 2)
            # 0.2.133: 1D mode: PanMode (no RectMode zoom)
            self.plot.getViewBox().setMouseMode(pg.ViewBox.PanMode)
        else:
            self._restore_strips()
            # 0.2.133: exit 1D mode: restore RectMode
            self.plot.getViewBox().setMouseMode(pg.ViewBox.RectMode)
            self._update_data_bounds()
        self._refresh_phase_availability()

    def _setup_strip_axes(self) -> None:
        """给 1D 条带设置刻度(ppm 轴显示 ppm,时间域轴显示点序号)。"""
        if self._primary is None:
            return
        x_axis = self._primary.x_axis
        y_axis = self._primary.y_axis
        x_ticks, x_label = self._axis_ticks(x_axis, 6)
        y_ticks, y_label = self._axis_ticks(y_axis, 6)
        self.strip_top.getAxis("bottom").setTicks([x_ticks])
        self.strip_top.setLabels(bottom=x_label, left="Intensity")
        self.strip_right.getAxis("left").setTicks([y_ticks])
        self.strip_right.setLabels(left=y_label, bottom="Intensity")

    def _update_strips(self, row: int, col: int) -> None:
        """更新十字线处两个一维迹线(行=F2 迹线,列=F1 迹线;显示相位)。"""
        if self._primary is None or not self._strips_active:
            return
        rows, cols = self._primary.data.shape
        row = max(0, min(rows - 1, int(row)))
        col = max(0, min(cols - 1, int(col)))
        self._strip_pos = (row, col)
        p0, p1 = self.phase_panel.values()
        row_trace = self._display_phase(self._primary.data[row, :], p0, p1)
        col_trace = self._display_phase(self._primary.data[:, col], p0, p1)
        self.strip_top_curve.setData(np.arange(cols), row_trace)
        self.strip_right_curve.setData(col_trace, np.arange(rows))

    def _move_crosshair(self, x: float, y: float) -> None:
        """移动十字线到视图坐标 (x=列, y=view y;view y 即数据行)。"""
        if not self._strips_active:
            return
        self._crosshair_v.setPos(x)
        self._crosshair_h.setPos(y)

    def _restore_strips(self) -> None:
        """隐藏条带与十字线。"""
        self.strip_top.hide()
        self.strip_right.hide()
        self._crosshair_v.hide()
        self._crosshair_h.hide()

    # ------------------------------------------------------------ layers

    def _on_layer_context_menu(self, pos) -> None:
        """图层列表右键:删除该图层 / 删除全部图层。"""
        item = self.layer_list.itemAt(pos)
        if item is None:
            return
        index = self.layer_list.row(item)
        menu = QMenu(self)
        delete_action = menu.addAction("Delete layer")
        clear_action = menu.addAction("Delete all layers")
        chosen = menu.exec(self.layer_list.mapToGlobal(pos))
        if chosen is delete_action:
            self.remove_layer(index)
        elif chosen is clear_action:
            self.clear()

    def remove_layer(self, index: int) -> None:
        """删除指定图层的谱图(主谱被删时回退到剩余第一张)。"""
        if not (0 <= index < len(self.layers)):
            return
        removed = self.layer_spectra[index]
        self.plot.removeItem(self.layers[index])
        del self.layers[index]
        del self.layer_names[index]
        del self.layer_spectra[index]
        self.layer_list.takeItem(index)
        if self._primary is removed:
            self._primary = self.layer_spectra[0] if self.layer_spectra else None
            if self._primary is not None:
                self._setup_axes(self._primary)
                self.reset_view()
            self._apply_peak_items()

    # ------------------------------------------------------------- view

    def reset_view(self) -> None:
        """恢复显示完整谱图范围(1D 迹线或所有叠加谱的联合范围)。"""
        if self._mode_1d and self._primary_1d is not None:
            x = self._primary_1d.x_values()
            y = self._primary_1d.data
            if y.size == 0:
                return
            self.plot.getViewBox().set_full_range(
                (float(np.min(x)), float(np.max(x))),
                (float(np.min(y)), float(np.max(y))),
                padding=0.02,
            )
            return
        if not self.layers:
            return
        x0 = min(layer.boundingRect().left() for layer in self.layers)
        y0 = min(layer.boundingRect().top() for layer in self.layers)
        x1 = max(layer.boundingRect().right() for layer in self.layers)
        y1 = max(layer.boundingRect().bottom() for layer in self.layers)
        self.plot.getViewBox().set_full_range(
            (x0, x1), (y0, y1), padding=0
        )

    def _aspect_ratio_from_slider(self, value: int) -> float | None:
        """Slider value (0-400) -> aspect ratio; 0 = None (free)."""
        if value <= 0:
            return None
        return max(0.25, min(4.0, value / 100.0))

    def _update_data_bounds(self) -> None:
        """根据当前 layer 数据大小绘制谱图数据边界矩形(随谱涉创建)。"""
        sx = max((s.x_axis.size for s in self.layer_spectra), default=0)
        sy = max((s.y_axis.size for s in self.layer_spectra), default=0)
        if not sx or not sy:
            if self._data_bounds_item is not None:
                self._data_bounds_item.setVisible(False)
            return
        if self._data_bounds_item is None:
            item = QGraphicsRectItem()
            item.setPen(QPen(QColor("#2e7d32"), 1, Qt.PenStyle.DashLine))
            item.setZValue(25)
            item.setVisible(False)
            # 挂到 ViewBox 数据坐标系,与谱同缩放/平移;
            # 放大后移出视野,全谱可见
            self.plot.addItem(item)
            self._data_bounds_item = item
        self._data_bounds_item.setRect(
            QRectF(-0.5, -0.5, float(sx), float(sy))
        )
        self._data_bounds_item.setVisible(not self._mode_1d)

    def _on_aspect_changed(self, value: int) -> None:
        """Aspect ratio slider callback."""
        ratio = self._aspect_ratio_from_slider(value)
        if ratio is None:
            self.aspect_label.setValue(0.0)  # SpecialValueText 显示 "Aspect free"
        else:
            self.aspect_label.setValue(ratio)
        self.set_aspect_ratio(ratio)


    def set_aspect_ratio(self, ratio: float | None) -> None:
        """锁定显示长宽比(数据单位 x/y);None 表示自由拉伸(默认)。"""
        vb = self.plot.getViewBox()
        if ratio is None:
            vb.setAspectLocked(False)
        else:
            vb.setAspectLocked(True, ratio=float(ratio))

    # ------------------------------------------------------------- peaks

    def set_peaks_visible(self, visible: bool) -> None:
        """显示/隐藏全部峰标记(勾选控件回调)。"""
        visible = bool(visible)
        if visible == self._peaks_visible:
            return
        self._peaks_visible = visible
        if self.show_peaks_checkbox.isChecked() != visible:
            self.show_peaks_checkbox.setChecked(visible)
        self._apply_peak_items()

    def set_peaks(self, peaks: list[dict], show_labels: bool = True) -> None:
        """叠加峰标记:dict 支持 H_shift/N_shift 或 x_ppm/y_ppm,可选 label。"""
        self._peaks = list(peaks)
        self._selected_peak = None
        self._box_selected_rows.clear()
        self._apply_peak_items(show_labels=show_labels)

    def highlight_peak(self, row: int) -> None:
        self._selected_peak = row if 0 <= row < len(self._peaks) else None
        self._apply_peak_items()

    def set_peak_size(self, size: float) -> None:
        """峰标记大小(数据坐标单位,随谱图缩放);0.2.199-补29az。"""
        self._peak_size = max(0.5, float(size))
        self._apply_peak_items()

    def set_peak_click_mode(self, mode: str) -> None:
        """左键单击行为:select=选中峰 / add=加峰 / delete=删峰。"""
        self._click_mode = mode if mode in ("select", "add", "delete") else "select"
        if mode != "select":
            self._cancel_box_select()
            self._box_selected_rows.clear()
            self._suppress_click = False
            self._apply_peak_items()

    def set_box_select_mode(self, enabled: bool) -> None:
        """选择模式开关:开启后左键拖动框选峰(不缩放);关闭恢复框选缩放。"""
        enabled = bool(enabled)
        if enabled == self._box_select_enabled:
            return
        self._box_select_enabled = enabled
        self.plot.getViewBox().setMouseMode(
            pg.ViewBox.PanMode if enabled else pg.ViewBox.RectMode
        )
        if not enabled:
            self._cancel_box_select()
            self._box_selected_rows.clear()
            self._suppress_click = False
            self._apply_peak_items()

    def _peak_xy(self, peak: dict) -> tuple[float, float]:
        """把峰行映射到当前显示平面的 x/y ppm(按轴维序 F1/F2/F3)。

        轴标签可能为核名(H/N/C...),因此用轴在谱中的维序(F1=0/F2=1/F3=2)
        选峰表列;2D 峰表回退 H_shift/N_shift,其余情况回退 x_ppm/y_ppm。
        """
        x_axis = self._primary.x_axis if self._primary is not None else None
        y_axis = self._primary.y_axis if self._primary is not None else None

        def _dim_of(axis) -> int:
            if self._primary is None or axis is None:
                return -1
            dims = getattr(self._primary, "dim_indices", None)
            try:
                local = self._primary.axes.index(axis)
            except ValueError:
                return -1
            if dims is not None:
                try:
                    return int(dims[local])
                except (TypeError, IndexError):
                    return -1
            return local

        def _pick(dim: int, fallback: str, alt: str) -> float:
            if 0 <= dim <= 2:
                value = peak.get(f"F{dim + 1}_shift")
                if value is not None and str(value) != "":
                    return float(value)
            return float(peak.get(fallback, peak.get(alt, 0.0)))

        x_ppm = _pick(_dim_of(x_axis), "H_shift", "x_ppm")
        y_ppm = _pick(_dim_of(y_axis), "N_shift", "y_ppm")
        return x_ppm, y_ppm

    def _apply_peak_items(self, show_labels: bool = True) -> None:
        if not self._peaks_visible or self._mode_1d or self._primary is None:
            self.peak_item.setData(x=[], y=[])
            self._peak_data_xy = []
            for text_item in self.peak_label_items:
                self.plot.removeItem(text_item)
            self.peak_label_items.clear()
            return
        x_axis = self._primary.x_axis
        y_axis = self._primary.y_axis
        xs: list[float] = []
        ys: list[float] = []
        sizes: list[float] = []
        base = self._peak_size
        for row, peak in enumerate(self._peaks):
            x_ppm, y_ppm = self._peak_xy(peak)
            xs.append(float(x_axis.index_at(x_ppm)))
            # view y 即数据行:峰标记按 y 轴数据行放置,与 contour 对齐
            ys.append(float(y_axis.index_at(y_ppm)))
            sizes.append(
                base * 1.6
                if (row == self._selected_peak or row in self._box_selected_rows)
                else base
            )
        self._peak_data_xy = list(zip(xs, ys))
        self.peak_item.setData(x=xs, y=ys, size=sizes)

        for text_item in self.peak_label_items:
            self.plot.removeItem(text_item)
        self.peak_label_items.clear()
        if show_labels:
            for row, (peak, xi, yi) in enumerate(zip(self._peaks, xs, ys)):
                label = str(peak.get("label") or "").strip()
                if not label and row != self._selected_peak:
                    continue
                text = label or str(peak.get("Peak_ID", ""))
                if not text:
                    continue
                label_item = pg.TextItem(
                    text, color="#c0392b", anchor=(0.0, 0.5)
                )
                label_item.setPos(xi + 4.0, yi)
                label_item.setZValue(21)
                self.plot.addItem(label_item)
                self.peak_label_items.append(label_item)

    def _on_peak_clicked(self, _plot, points) -> None:
        if not points:
            return
        point = points[0]
        row = point.data().get("row", -1)
        if 0 <= row < len(self._peaks):
            self.highlight_peak(row)
            peak = self._peaks[row]
            x_ppm, y_ppm = self._peak_xy(peak)
            self.peak_label.setText(
                f"峰 {row + 1}: {x_ppm:.3f} / {y_ppm:.3f} ppm "
                f"({peak.get('label', '')})"
            )
            self.peak_clicked.emit(row)

    def _view_to_data(self, point) -> tuple[int, int]:
        """视图坐标 → 数据下标 (col, row);contour 不翻转,view y 即数据行。
        超出范围返回 (-1, -1)。"""
        if self._primary is None:
            return -1, -1
        x_axis = self._primary.x_axis
        y_axis = self._primary.y_axis
        xi = int(round(point.x()))
        yi = int(round(point.y()))
        if not (0 <= xi < x_axis.size and 0 <= yi < y_axis.size):
            return -1, -1
        return xi, yi

    def _on_plot_clicked(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if getattr(self, "_suppress_click", False):
            # 刚完成框选:该次释放不当作单击
            self._suppress_click = False
            return
        try:
            press = event.buttonDownScenePos(Qt.MouseButton.LeftButton)
        except AttributeError:
            # 0.2.199-补29aw:pyqtgraph MouseClickEvent 无 buttonDownScenePos
            # (那是 MouseDragEvent 的 API);用 eventFilter 记录的左键按下
            # 场景坐标判断是否拖拽
            press = getattr(self, "_box_press_scene", None)
        release = event.scenePos()
        if press is not None and (release - press).manhattanLength() > 6:
            return  # 拖拽是框选缩放,不当作单击
        if self._primary is None:
            return
        try:
            point = self.plot.getViewBox().mapSceneToView(event.scenePos())
        except Exception:  # noqa: BLE001
            return
        xi, yi = self._view_to_data(point)
        if xi < 0:
            return
        if self._strips_active:
            # 一维谱模式:点击定位十字线(视图坐标)并显示该处两个一维谱
            self._move_crosshair(float(point.x()), float(point.y()))
            self._update_strips(yi, xi)
            return
        x_axis = self._primary.x_axis
        y_axis = self._primary.y_axis
        x_ppm = float(x_axis.ppm_at(xi))
        y_ppm = float(y_axis.ppm_at(yi))
        if self._click_mode == "delete":
            self.delete_peak_requested.emit(x_ppm, y_ppm)
        elif self._click_mode == "add":
            # 0.2.199-补29ar:点击自动吸附到附近峰顶;找不到显著峰顶则用点击点
            snapped_row, snapped_col = snap_to_peak_top(
                self._primary.data, yi, xi
            )
            self.manual_peak_requested.emit(
                self._peak_from_data_point(snapped_col, snapped_row)
            )
        else:
            # select:选中距点击位置最近的峰(像素距离)
            row = self._nearest_peak(xi, yi)
            if row is not None:
                self.highlight_peak(row)
                self.peak_clicked.emit(row)

    def _peak_from_data_point(self, xi: int, yi: int) -> dict:
        """点击数据点 (col,row) → 峰行 dict。

        3D 切片平面按 dim_indices 映射到逻辑维(F1/F2/F3_shift),2D 用
        H_shift/N_shift(契约 §6);Intensity 取该点数据值。
        """
        if self._primary is None:
            return {"label": ""}
        x_axis = self._primary.x_axis
        y_axis = self._primary.y_axis
        x_ppm = float(x_axis.ppm_at(xi))
        y_ppm = float(y_axis.ppm_at(yi))
        peak: dict = {"label": ""}
        dims = getattr(self._primary, "dim_indices", None)
        if dims is not None:
            for axis, ppm in ((x_axis, x_ppm), (y_axis, y_ppm)):
                try:
                    local = self._primary.axes.index(axis)
                except ValueError:
                    continue
                dim = int(dims[local]) if local < len(dims) else local
                if 0 <= dim <= 2:
                    peak[f"F{dim + 1}_shift"] = ppm
        else:
            peak["H_shift"] = x_ppm
            peak["N_shift"] = y_ppm
        try:
            peak["Intensity"] = float(self._primary.data[yi, xi])
        except Exception:  # noqa: BLE001 - 强度缺失不阻断加峰
            pass
        return peak

    def _nearest_peak(self, xi: int, yi: int) -> int | None:
        if self._primary is None or not self._peaks:
            return None
        x_axis = self._primary.x_axis
        y_axis = self._primary.y_axis
        best: tuple[float, int | None] = (12.0, None)  # 数据点半径阈值
        for row, peak in enumerate(self._peaks):
            x_ppm, y_ppm = self._peak_xy(peak)
            dx = x_axis.index_at(x_ppm) - xi
            dy = y_axis.index_at(y_ppm) - yi
            dist = float(np.hypot(dx, dy))
            if dist < best[0]:
                best = (dist, row)
        return best[1]

    def _on_mouse_moved(self, pos) -> None:
        if self._primary is None:
            return
        try:
            point = self.plot.getViewBox().mapSceneToView(pos)
        except Exception:  # noqa: BLE001
            return
        xi, yi = self._view_to_data(point)
        if xi < 0:
            return
        if self._strips_active:
            # 0.2.133: 1D crosshair only on left button hold
            if self._mouse_left_pressed:
                self._move_crosshair(float(point.x()), float(point.y()))
                self._update_strips(yi, xi)
            x_axis = self._primary.x_axis
            y_axis = self._primary.y_axis
            self.crosshair_label.setText(
                f"{x_axis.label} {x_axis.ppm_at(xi):.3f} ppm | "
                f"{y_axis.label} {y_axis.ppm_at(yi):.3f} ppm"
            )
        elif self._mode_1d and self._primary_1d is not None:
            axis = self._primary_1d.axis
            self.crosshair_label.setText(
                f"{axis.label} {axis.ppm_at(xi):.3f} ppm"
            )
        else:
            # 0.2.148:普通 2D(含 3D 切片)鼠标移动实时刷新 ppm 读数
            x_axis = self._primary.x_axis
            y_axis = self._primary.y_axis
            self.crosshair_label.setText(
                f"{x_axis.label} {x_axis.ppm_at(xi):.3f} ppm | "
                f"{y_axis.label} {y_axis.ppm_at(yi):.3f} ppm"
            )

    # ------------------------------------------------------------- misc

    def add_control_panel(self, panel: QWidget) -> None:
        """把任意控制面板挂到查看器控制区。"""
        self.controls_layout.addWidget(panel)
