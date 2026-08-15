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
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from viewer.contour_layer import ContourLayer
from viewer.nmr_viewbox import NMRViewBox
from viewer.spectrum import Spectrum, Spectrum1D, SpectrumAxis

_COLORS = ("#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf")
_DEFAULT_LEVELS = 8


class SpectrumViewer(QWidget):
    """支持多谱叠加的二维谱查看器。"""

    peak_clicked = pyqtSignal(int)  # 峰行号
    manual_peak_requested = pyqtSignal(float, float)  # (x ppm, y ppm)
    delete_peak_requested = pyqtSignal(float, float)

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

        self.peak_item = pg.ScatterPlotItem(
            pen=pg.mkPen("#8b0000", width=1),
            brush=pg.mkBrush(255, 70, 70, 150),
            size=10,
            symbol="o",
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
        self.level_label = QLabel(self._level_label_text())

        self.count_slider = QSlider(Qt.Orientation.Horizontal)
        self.count_slider.setFixedHeight(18)
        self.count_slider.setRange(5, 60)
        self.count_slider.setValue(self._level_count)
        self.count_slider.valueChanged.connect(self._on_level_count)
        self.count_label = QLabel(f"Levels {self._level_count}")

        self.reset_button = QPushButton("Full view")
        self.reset_button.clicked.connect(self.reset_view)

        self.crosshair_label = QLabel("Move mouse to read ppm")
        self.crosshair_label.setWordWrap(True)
        self.peak_label = QLabel("")
        self.peak_label.setWordWrap(True)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(4, 2, 4, 2)
        controls_layout.setSpacing(2)
        controls_layout.addWidget(QLabel("Layers"))
        controls_layout.addWidget(self.layer_list, 1)
        controls_layout.addWidget(QLabel("Contour start (%)"))
        controls_layout.addWidget(self.level_slider)
        controls_layout.addWidget(self.level_label)
        controls_layout.addWidget(QLabel("Levels"))
        controls_layout.addWidget(self.count_slider)
        controls_layout.addWidget(self.count_label)
        controls_layout.addWidget(self.reset_button)
        self.show_1d_button = QPushButton("1D")
        self.show_1d_button.setCheckable(True)
        self.show_1d_button.setToolTip(
            "开启后出现随鼠标十字线,点击显示该处两个一维谱(TopSpin 式)"
        )
        self.show_1d_button.toggled.connect(self.set_1d_mode)
        controls_layout.addWidget(self.show_1d_button)
        controls_layout.addWidget(self.crosshair_label)
        controls_layout.addWidget(self.peak_label)
        self.show_peaks_checkbox = QCheckBox("Show peaks")
        self.show_peaks_checkbox.setChecked(True)
        self.show_peaks_checkbox.toggled.connect(self.set_peaks_visible)
        controls_layout.addWidget(self.show_peaks_checkbox)
        from viewer.phase_panel import PhasePanel

        self.phase_panel = PhasePanel()
        self.phase_panel.phase_changed.connect(self._on_phase_changed)
        controls_layout.addWidget(self.phase_panel)
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

        self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.plot.scene().sigMouseClicked.connect(self._on_plot_clicked)
        self.set_aspect_ratio(1.0)  # 默认正方形(1:1 数据长宽比)

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
        self._refresh_phase_availability()
        return name

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
        """拖动过程中仅刷新标签,避免每格都重建轮廓(性能)。"""
        self.level_label.setText(self._level_label_text())

    def _update_levels(self) -> None:
        """松开滑块/级数变化时重建轮廓(复用已缓存插值数据)。"""
        self.level_label.setText(self._level_label_text())
        for layer, spectrum in zip(self.layers, self.layer_spectra):
            layer.set_levels(self._levels_for(spectrum))

    def _on_level_count(self, value: int) -> None:
        self._level_count = value
        self.count_label.setText(f"Levels {value}")
        self._update_levels()

    @staticmethod
    def _axis_ticks(
        axis: SpectrumAxis, count: int
    ) -> tuple[list[tuple[int, str]], str]:
        """ppm 有效轴显示 ppm 刻度;时间域轴(如 FID 时点)显示点序号。"""
        if axis.sw_hz > 0 and axis.obs_mhz > 0:
            ticks = [
                (int(i), f"{axis.ppm_at(int(i)):.2f}")
                for i in np.linspace(0, axis.size - 1, count)
            ]
            return ticks, f"{axis.label} (ppm)"
        ticks = [
            (int(i), str(int(i))) for i in np.linspace(0, axis.size - 1, 6)
        ]
        return ticks, axis.label

    def _setup_axes(self, spectrum: Spectrum) -> None:
        x_axis = spectrum.x_axis
        y_axis = spectrum.y_axis
        x_ticks, x_label = self._axis_ticks(x_axis, 8)
        y_ticks, y_label = self._axis_ticks(y_axis, 8)
        self.plot.getAxis("bottom").setTicks([x_ticks])
        self.plot.getAxis("left").setTicks([y_ticks])
        self.plot.setLabels(bottom=x_label, left=y_label)

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
            ticks = [
                (int(i), f"{axis.ppm_at(int(i)):.2f}")
                for i in np.linspace(0, axis.size - 1, 8)
            ]
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
        """按当前谱图是否有复型数据启用/禁用相位面板。"""
        complex_data, _kind = self._complex_for_phase()
        self.phase_panel.set_available(complex_data is not None)

    def _complex_for_phase(self) -> tuple[np.ndarray | None, str]:
        """当前可用于交互调相的复型数据(1D FID 或二维时域 FID)。"""
        if self._mode_1d and self._primary_1d is not None:
            return getattr(self._primary_1d, "complex_data", None), "1d"
        if self._primary is not None:
            return getattr(self._primary, "complex_data", None), "2d"
        return None, ""

    def _on_phase_changed(self, final: bool) -> None:
        """相位滑块变化:1D 实时更新,2D 松手后重建轮廓。"""
        complex_data, kind = self._complex_for_phase()
        if complex_data is None:
            return
        if kind == "1d":
            self._update_phased_1d(complex_data)
        elif kind == "2d" and final:
            self._update_phased_2d(complex_data)

    @staticmethod
    def _phase_rotate(
        complex_data: np.ndarray, p0: float, p1: float, axis: int
    ) -> np.ndarray:
        """沿指定轴 FT 后按 P0/P1 旋转相位,返回实部显示数据。"""
        spec = np.fft.fft(complex_data, axis=axis)
        n = spec.shape[axis]
        k = np.arange(n, dtype=float)
        angle = np.deg2rad(p0 + p1 * k / max(1, n - 1))
        shape = [1] * spec.ndim
        shape[axis] = n
        return (spec * np.exp(1j * angle.reshape(shape))).real

    def _update_phased_1d(self, complex_data: np.ndarray) -> None:
        if self._plot_1d is None or self._primary_1d is None:
            return
        p0, p1 = self.phase_panel.values()
        phased = self._phase_rotate(np.asarray(complex_data), p0, p1, axis=0)
        self._plot_1d.setData(self._primary_1d.x_values(), phased)

    def _update_phased_2d(self, complex_data: np.ndarray) -> None:
        if self._primary is None or not self.layers:
            return
        p0, p1 = self.phase_panel.values()
        phased = self._phase_rotate(np.asarray(complex_data), p0, p1, axis=1)
        display = Spectrum(phased, self._primary.axes, source=self._primary.source)
        layer = ContourLayer(
            display.data,
            self._levels_for(display),
            pg.mkPen("#1f77b4", width=1),
            neg_pen=pg.mkPen("#e74c3c", width=1),
            zoom=self._contour_zoom,
        )
        self.plot.addItem(layer)
        old = self.layers[0]
        self.plot.removeItem(old)
        self.layers[0] = layer
        self.layer_spectra[0] = display
        self._apply_peak_items()

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
            self._setup_strip_axes()
            # 右侧 1D 条带方向与二维谱 Y 轴保持一致(谱+坐标轴一起翻正)
            self.strip_right.getViewBox().invertY(
                bool(self.plot.getViewBox().state.get("yInverted", True))
            )
            rows, cols = self._primary.data.shape
            self._update_strips(rows // 2, cols // 2)
            self._move_crosshair(cols // 2, rows // 2)
        else:
            self._restore_strips()

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
        """更新十字线处两个一维迹线(行=F2 迹线,列=F1 迹线)。"""
        if self._primary is None or not self._strips_active:
            return
        rows, cols = self._primary.data.shape
        row = max(0, min(rows - 1, int(row)))
        col = max(0, min(cols - 1, int(col)))
        self.strip_top_curve.setData(
            np.arange(cols), np.asarray(self._primary.data[row, :])
        )
        self.strip_right_curve.setData(
            np.asarray(self._primary.data[:, col]), np.arange(rows)
        )

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
            self.plot.getViewBox().setRange(
                xRange=(float(np.min(x)), float(np.max(x))),
                yRange=(float(np.min(y)), float(np.max(y))),
                padding=0.02,
            )
            return
        if not self.layers:
            return
        x0 = min(layer.boundingRect().left() for layer in self.layers)
        y0 = min(layer.boundingRect().top() for layer in self.layers)
        x1 = max(layer.boundingRect().right() for layer in self.layers)
        y1 = max(layer.boundingRect().bottom() for layer in self.layers)
        self.plot.getViewBox().setRange(
            xRange=(x0, x1),
            yRange=(y0, y1),
            padding=0,
        )

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
        self._apply_peak_items(show_labels=show_labels)

    def highlight_peak(self, row: int) -> None:
        self._selected_peak = row if 0 <= row < len(self._peaks) else None
        self._apply_peak_items()

    def set_peak_click_mode(self, mode: str) -> None:
        """左键单击行为:select=选中峰 / add=加峰 / delete=删峰。"""
        self._click_mode = mode if mode in ("select", "add", "delete") else "select"

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
            for text_item in self.peak_label_items:
                self.plot.removeItem(text_item)
            self.peak_label_items.clear()
            return
        x_axis = self._primary.x_axis
        y_axis = self._primary.y_axis
        xs: list[float] = []
        ys: list[float] = []
        sizes: list[float] = []
        for row, peak in enumerate(self._peaks):
            x_ppm, y_ppm = self._peak_xy(peak)
            xs.append(float(x_axis.index_at(x_ppm)))
            # view y 即数据行:峰标记按 y 轴数据行放置,与 contour 对齐
            ys.append(float(y_axis.index_at(y_ppm)))
            sizes.append(16.0 if row == self._selected_peak else 10.0)
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
        press = event.buttonDownScenePos(Qt.MouseButton.LeftButton)
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
            self.manual_peak_requested.emit(x_ppm, y_ppm)
        else:
            # select:选中距点击位置最近的峰(像素距离)
            row = self._nearest_peak(xi, yi)
            if row is not None:
                self.highlight_peak(row)
                self.peak_clicked.emit(row)

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
        if self._primary is None or self._mode_1d:
            return
        try:
            point = self.plot.getViewBox().mapSceneToView(pos)
        except Exception:  # noqa: BLE001
            return
        xi, yi = self._view_to_data(point)
        if xi < 0:
            return
        if self._strips_active:
            self._move_crosshair(float(point.x()), float(point.y()))
            self._update_strips(yi, xi)
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
