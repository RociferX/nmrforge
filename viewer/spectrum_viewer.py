"""二维谱查看器控件(独立于项目管理 GUI):轮廓(contour)、峰标记、缩放/平移、长宽比。

交互习惯(nmrDraw/Poky):
- 左键拖拽框选放大;中键拖拽平移;滚轮缩放;
- 强度滑块控制轮廓起始水平;级数滑块控制轮廓密度(默认 36 级);
- 正峰黑/负峰红;峰为半透明圆点,点击选中放大并显示标签;
- 支持锁定显示长宽比(1:1 / 2:1 / 4:1 / 自由)。
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from viewer.contour_layer import ContourLayer
from viewer.nmr_viewbox import NMRViewBox
from viewer.spectrum import Spectrum

_COLORS = ("#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf")
_DEFAULT_LEVELS = 36


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

        self.plot = pg.PlotWidget(viewBox=NMRViewBox())
        self.plot.setBackground("w")
        self.plot.setMenuEnabled(False)
        self.plot.getViewBox().setMouseMode(pg.ViewBox.RectMode)
        # nmrDraw/Poky 显示约定:1H 高 ppm 在左、15N 高 ppm 在下
        # 数据列 0 = 高 ppm(ppm 随索引递减):默认 x 轴列 0 在左即满足;
        # 因此只反转 y(行 0 = 高 ppm 放到下方),不反转 x(否则高 ppm 会到右)。
        self.plot.getViewBox().invertY(True)

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
        self.layer_list.itemChanged.connect(self._on_layer_toggle)

        self.level_slider = QSlider(Qt.Orientation.Horizontal)
        self.level_slider.setRange(1, 100)
        self.level_slider.setValue(8)
        self.level_slider.valueChanged.connect(self._update_levels)
        self.level_label = QLabel(self._level_label_text())

        self.count_slider = QSlider(Qt.Orientation.Horizontal)
        self.count_slider.setRange(5, 60)
        self.count_slider.setValue(self._level_count)
        self.count_slider.valueChanged.connect(self._on_level_count)
        self.count_label = QLabel(f"级数 {self._level_count}")

        self.reset_button = QPushButton("全谱视图")
        self.reset_button.clicked.connect(self.reset_view)

        self.crosshair_label = QLabel("移动鼠标读取 ppm 坐标")
        self.crosshair_label.setWordWrap(True)
        self.peak_label = QLabel("")
        self.peak_label.setWordWrap(True)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.addWidget(QLabel("谱图层"))
        controls_layout.addWidget(self.layer_list, 1)
        controls_layout.addWidget(QLabel("轮廓起点(%)"))
        controls_layout.addWidget(self.level_slider)
        controls_layout.addWidget(self.level_label)
        controls_layout.addWidget(QLabel("轮廓级数"))
        controls_layout.addWidget(self.count_slider)
        controls_layout.addWidget(self.count_label)
        controls_layout.addWidget(self.reset_button)
        controls_layout.addWidget(self.crosshair_label)
        controls_layout.addWidget(self.peak_label)
        self.controls_layout = controls_layout

        # 左右布局:左侧谱图,右侧控制面板(分隔条可左右拖动,更紧凑)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.plot)
        splitter.addWidget(controls)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([720, 220])
        # 默认接近正方形的谱图区(随可用宽度)
        self.plot.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.plot.scene().sigMouseClicked.connect(self._on_plot_clicked)

    # ------------------------------------------------------------ layers

    def clear(self) -> None:
        for layer in self.layers:
            self.plot.removeItem(layer)
        self.layers.clear()
        self.layer_names.clear()
        self.layer_spectra.clear()
        self.layer_list.clear()
        self._primary = None
        self.set_peaks([])

    def add_spectrum(
        self,
        spectrum: Spectrum,
        name: str | None = None,
        color: str | None = None,
    ) -> str:
        """叠加一张谱;第一张成为主谱(提供 ppm 轴与峰坐标系)。"""
        color = color or _COLORS[len(self.layers) % len(_COLORS)]
        if name is None:
            name = (
                spectrum.source.stem
                if spectrum.source is not None
                else f"谱图 {len(self.layers) + 1}"
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
        return name

    def _level_fraction(self) -> float:
        """滑块 → 起点百分比(平方映射:前 10% 精细可调)。"""
        value = max(1, self.level_slider.value())
        return (value / 100.0) ** 2

    def _level_label_text(self) -> str:
        return f"轮廓起点 {self._level_fraction() * 100:.2f}%"

    def _levels_for(self, spectrum: Spectrum) -> np.ndarray:
        """从起点(base)到最大值之间取 n 级对数间隔,含对称负级。"""
        maximum = spectrum.max_intensity
        if maximum <= 0:
            maximum = abs(float(np.min(spectrum.data))) if spectrum.data.size else 0.0
        if maximum <= 0:
            return np.array([-1.0, 1.0])
        base = maximum * self._level_fraction()
        positive = np.geomspace(max(base, maximum * 1e-6), maximum, self._level_count)
        return np.concatenate([-positive[::-1], positive])

    def _update_levels(self) -> None:
        for layer, spectrum in zip(self.layers, self.layer_spectra):
            layer.setData(spectrum.data, self._levels_for(spectrum))
        self.level_label.setText(self._level_label_text())

    def _on_level_count(self, value: int) -> None:
        self._level_count = value
        self.count_label.setText(f"级数 {value}")
        self._update_levels()

    def _setup_axes(self, spectrum: Spectrum) -> None:
        x_axis = spectrum.x_axis
        y_axis = spectrum.y_axis
        x_ticks = [
            (int(i), f"{x_axis.ppm_at(int(i)):.2f}")
            for i in np.linspace(0, x_axis.size - 1, 8)
        ]
        y_ticks = [
            (int(i), f"{y_axis.ppm_at(int(i)):.2f}")
            for i in np.linspace(0, y_axis.size - 1, 8)
        ]
        self.plot.getAxis("bottom").setTicks([x_ticks])
        self.plot.getAxis("left").setTicks([y_ticks])
        self.plot.setLabels(
            bottom=f"{x_axis.label} (ppm)", left=f"{y_axis.label} (ppm)"
        )

    def _on_layer_toggle(self, item: QListWidgetItem) -> None:
        index = self.layer_list.row(item)
        if 0 <= index < len(self.layers):
            visible = item.checkState() == Qt.CheckState.Checked
            self.layers[index].setVisible(visible)

    # ------------------------------------------------------------- view

    def reset_view(self) -> None:
        """恢复显示完整谱图范围。"""
        if self._primary is None:
            return
        self.plot.getViewBox().setRange(
            xRange=(0, self._primary.data.shape[1]),
            yRange=(0, self._primary.data.shape[0]),
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
        x_ppm = float(peak.get("H_shift", peak.get("x_ppm", 0.0)))
        y_ppm = float(peak.get("N_shift", peak.get("y_ppm", 0.0)))
        return x_ppm, y_ppm

    def _apply_peak_items(self, show_labels: bool = True) -> None:
        if self._primary is None:
            return
        x_axis = self._primary.x_axis
        y_axis = self._primary.y_axis
        xs: list[float] = []
        ys: list[float] = []
        sizes: list[float] = []
        for row, peak in enumerate(self._peaks):
            x_ppm, y_ppm = self._peak_xy(peak)
            xs.append(float(x_axis.index_at(x_ppm)))
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
        x_axis = self._primary.x_axis
        y_axis = self._primary.y_axis
        xi = round(point.x())
        yi = round(point.y())
        if not (0 <= xi < x_axis.size and 0 <= yi < y_axis.size):
            return
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
        if self._primary is None:
            return
        try:
            point = self.plot.getViewBox().mapSceneToView(pos)
        except Exception:  # noqa: BLE001
            return
        x_axis = self._primary.x_axis
        y_axis = self._primary.y_axis
        xi = round(point.x())
        yi = round(point.y())
        if 0 <= xi < x_axis.size and 0 <= yi < y_axis.size:
            self.crosshair_label.setText(
                f"{x_axis.label} {x_axis.ppm_at(xi):.3f} ppm | "
                f"{y_axis.label} {y_axis.ppm_at(yi):.3f} ppm"
            )

    # ------------------------------------------------------------- misc

    def add_control_panel(self, panel: QWidget) -> None:
        """把任意控制面板挂到查看器控制区。"""
        self.controls_layout.addWidget(panel)
