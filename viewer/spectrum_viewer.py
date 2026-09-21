"""2D spectrum viewer controls (independent of project management GUI): contour, peak mark,
Zoom/Pan, aspect ratio. Interaction habits (nmrDraw/Poky): - Left-click dragging to zoom in;
middle-click dragging to pan; wheel zoom; - Intensity slider controls the starting level of the
contour; series slider controls the contour density (default level 8); - Zhengfeng
black/negative peak red; Peaks are translucent dots, click to select to enlarge and display
labels; - Supports locked display aspect ratio (1:1 / 2:1 / 4:1 / free)."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from qtcompat.QtCore import QEvent, QPointF, QRect, QRectF, Qt
from qtcompat.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from qtcompat.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGraphicsEllipseItem,
    QGraphicsItem,
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
from qtcompat import Signal
from ui_support.i18n import tr
from viewer.contour_layer import ContourLayer
from viewer.nmr_viewbox import NMRViewBox
from viewer.spectrum import Spectrum, Spectrum1D, SpectrumAxis

_COLORS = ("#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf")
_DEFAULT_LEVELS = 8


class _BoxSelectOverlay(QWidget):
    """Frame selection dotted line overlay: Drawn on the plot viewport, only this layer is redrawn
    when dragging. 0.2.199-patch29ax:Not included in the scene/shift item (to avoid stuck
    redrawing of the entire scene of large-scale contour lines, and the instability of adding or
    deleting items during scene event processing)."""

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


def _label_text_box(pos: QPointF, tw: float, th: float) -> QRectF:
    """Label text box (centered on label position; shared with drawing and hit detection)."""
    return QRectF(pos.x() - tw / 2.0, pos.y() - th / 2.0, tw, th)


class _LabelOverlay(QWidget):
    """Peak identification label overlay: labels are placed as close as possible to the actual
    white space around the peak (data coordinates are fixed), Zoom/Pan without rearrangement,
    and the selection mode can be dragged and fine-tuned; the guide line is connected from the
    label to the peak."""

    def __init__(self, parent: QWidget, viewer: SpectrumViewer) -> None:
        super().__init__(parent)
        self._viewer = viewer
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def visible_label_count(self) -> int:
        """The current number of tags that should be displayed (test/For status)."""
        return len(self._collect_labels())

    def _collect_labels(self) -> list[tuple[float, float, str]]:
        """Collect the labels that should be displayed (data coordinates xi, yi, text)."""
        viewer = self._viewer
        if (
            viewer._primary is None
            or viewer._mode_1d
            or not viewer._peaks_visible
            or not viewer._show_peak_labels
        ):
            return []
        out: list[tuple[float, float, str]] = []
        for row, (xi, yi) in enumerate(viewer._peak_data_xy):
            if xi != xi or yi != yi:  # 0.2.199-Patch29da: non-plane peak.
                continue
            peak = viewer._peaks[row]
            label = str(peak.get("label") or "").strip()
            if not label and row != viewer._selected_peak:
                continue
            text = label or str(peak.get("Peak_ID", ""))
            if not text:
                continue
            out.append((xi, yi, text))
        return out

    def paintEvent(self, event) -> None:
        """Draw the peak identification label (0.2.199-patch29cf): the position is fixed at the
        data coordinates (nearest blank), Zoom/Pan without rearrangement; the guide line is
        connected from the label to the peak (a gap is left at the peak end), and the text is
        upright with the label as the centre."""
        viewer = self._viewer
        if (
            viewer._primary is None
            or viewer._mode_1d
            or not viewer._peaks_visible
            or not viewer._show_peak_labels
        ):
            return
        painter = QPainter(self)
        try:
            vb = viewer.plot.getViewBox()
            try:
                ppu = 1.0 / max(vb.viewPixelSize()[0], 1e-9)
            except Exception:  # noqa: BLE001
                ppu = 1.0
            # 0.2.199-patch29bp:assignment font size = peak marker x 3 (fixed ratio).
            font_px = max(6.0, min(60.0, viewer._peak_size * 3.0 * ppu))
            font = QFont()
            font.setPixelSize(int(round(font_px)))
            painter.setFont(font)
            # Leave a seam at the peak end and do not connect it to death.
            gap_peak = 6.0 + font_px * 0.25
            leader_pen = QPen(QColor("#888888"), 1)
            label_pen = QPen(QColor("#c0392b"), 1)
            for row, (xi, yi) in enumerate(viewer._peak_data_xy):
                if xi != xi or yi != yi:  # 0.2.199-Patch29da: non-plane peak.
                    continue
                peak = viewer._peaks[row]
                label = str(peak.get("label") or "").strip()
                if not label and row != viewer._selected_peak:
                    continue
                text = label or str(peak.get("Peak_ID", ""))
                if not text:
                    continue
                try:
                    pp = QPointF(
                        viewer.plot.mapFromScene(
                            vb.mapViewToScene(QPointF(float(xi), float(yi)))
                        )
                    )
                except Exception:  # noqa: BLE001
                    continue
                lp = viewer._label_widget_pos(row)
                if lp is None or not QRectF(self.rect()).contains(pp):
                    continue
                dx = lp.x() - pp.x()
                dy = lp.y() - pp.y()
                d = max(float(np.hypot(dx, dy)), 1e-6)
                ex = pp.x() + dx / d * gap_peak
                ey = pp.y() + dy / d * gap_peak
                painter.setPen(leader_pen)
                painter.drawLine(lp, QPointF(ex, ey))
                painter.setPen(label_pen)
                fm = painter.fontMetrics()
                tw = float(fm.horizontalAdvance(text))
                th = float(fm.height())
                painter.drawText(
                    viewer._label_box(lp, tw, th),
                    int(Qt.AlignmentFlag.AlignCenter),
                    text,
                )
        finally:
            painter.end()


class SpectrumViewer(QWidget):
    """A 2D spectrum viewer that supports multi-spectrum overlay."""

    peak_clicked = Signal(int)  # Peak line number.
    manual_peak_requested = Signal(dict)  # Click to add a peak: peak row dict (adsorbed peak top).
    delete_peak_requested = Signal(float, float)
    peaks_box_selected = Signal(list)  # Frame peak selection: line number list (selection mode).

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
        # 0.2.199-patch29at: Selection mode (left-click and drag the frame to select peaks).
        self._box_select_enabled = False
        self._box_selecting = False
        self._box_press_scene: QPointF | None = None
        self._box_overlay: _BoxSelectOverlay | None = None
        self._box_selected_rows: set[int] = set()
        # 0.2.199-patch29ay: Peak data coordinate cache (box selection only performs range
        # comparison, no peak-by-peak conversion).
        self._peak_data_xy: list[tuple[float, float]] = []
        # 0.2.199-patch29da: 3D slice current plane visible peak rows (None = all visible).
        self._visible_peak_rows: set[int] | None = None
        # 0.2.199-patch29cb:Assignment fixed position (data coordinates, Zoom/Pan without
        # rearrangement, draggable).
        self._label_positions: list[tuple[float, float] | None] = []
        self._drag_label_row: int | None = None
        self._suppress_click = False  # Releasing the box selection is not treated as a click.
        self._mode_1d = False
        self._primary_1d: Spectrum1D | None = None
        self._plot_1d: pg.PlotDataItem | None = None
        self._peaks_visible = True

        self.plot = pg.PlotWidget(viewBox=NMRViewBox())
        self.plot.setBackground("w")
        self.plot.setMenuEnabled(False)
        self.plot.getViewBox().setMouseMode(pg.ViewBox.RectMode)
        # Display convention (user 0.2.59 feedback correction): 1H high ppm on the left, 15N high
        # ppm on the bottom; data column 0 = high ppm (x column 0 on the left); contour is not
        # inverted (view y = data row), invertY(False) lower view y increases = screen up -> row 0
        # (height ppm) is displayed at the bottom.
        self.plot.getViewBox().invertY(False)

        self.layers: list[ContourLayer] = []
        self.layer_names: list[str] = []
        self.layer_spectra: list[Spectrum] = []
        self._primary: Spectrum | None = None
        self._level_count = max(5, int(level_count))
        self._contour_zoom = max(1.0, float(contour_zoom))

        # 0.2.199-patch29az: Peak label Poky style x, data coordinate size scales with spectrum.
        self._peak_size = 1.5
        self._show_peak_labels = True  # 0.2.199-Patch29bf:Assignment header switch.
        self._flash_item: QGraphicsEllipseItem | None = None  # 0.2.199-Patch29bk.
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
        # 0.2.150: The spectrum data bounding box appears with the spectrum: it is not pre-created,
        # it is created when the first 2D spectrum is loaded and hung in the ViewBox (plot.addItem
        # ignoreBounds=True): the data coordinate system is together with the spectrum Zoom/Pan, and
        # naturally leaves the field of view after zooming in, and it is visible when you see the
        # full spectrum; and it does not participate in automatic scaling calculation (no startup
        # jump).
        self._data_bounds_item: QGraphicsRectItem | None = None



        # 0.2.133: aspect ratio slider(0.2.147 moves to control row 0 side by side).
        self.aspect_slider = QSlider(Qt.Orientation.Horizontal)
        self.aspect_slider.setRange(0, 400)
        self.aspect_slider.setValue(0)
        self.aspect_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.aspect_slider.setTickInterval(50)
        self.aspect_slider.valueChanged.connect(self._on_aspect_changed)

        # Show peaks checkbox (0.2.147 moved to the peak operation row, before Add peak).
        self.show_peaks_checkbox = QCheckBox("Show peaks")
        self.show_peaks_checkbox.setChecked(True)
        self.show_peaks_checkbox.toggled.connect(self.set_peaks_visible)
        # 0.2.148: Values can be input directly (the specific value is displayed after the title);
        # the slider and input are synchronized in both directions.
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
        self.aspect_label.setValue(0.0)
        self.aspect_label.setFixedWidth(130)
        self.aspect_label.setSpecialValueText("Aspect free")
        self.aspect_label.valueChanged.connect(self._on_aspect_spin_changed)


        # TopSpin Style 1D Strip Switch (0.2.147 row 1: side by side with Full view).
        self.show_1d_button = QPushButton("1D")
        self.show_1d_button.setCheckable(True)
        self.show_1d_button.setToolTip(
                tr(
                "After opening, a cross line appears with the mouse. Move the mouse to display two "
                "one-dimensional spectra (TopSpin "
                "type)",
            )
        )
        self.show_1d_button.toggled.connect(self.set_1d_mode)

        self.crosshair_label = QLabel("Move mouse to read ppm")
        self.crosshair_label.setWordWrap(True)
        self.peak_label = QLabel("")
        self.peak_label.setWordWrap(True)

        controls = QWidget()
        controls_layout = QGridLayout(controls)
        # 0.2.147: Horizontal control rows are clearly spaced; the Layers list moves out to the top
        # row of the panel.
        controls_layout.setContentsMargins(6, 4, 6, 4)
        controls_layout.setHorizontalSpacing(20)
        controls_layout.setVerticalSpacing(6)

        # Row 0: contour start / Levels / Aspect ratio three groups side by side.
        def _labeled_slider(spinbox, slider) -> QVBoxLayout:
            """Title + value (can be entered) in one line, slider below."""
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

        # Row 1: Full view + 1D.
        row1 = QHBoxLayout()
        row1.setSpacing(16)
        row1.addWidget(self.reset_button)
        row1.addWidget(self.show_1d_button)
        row1.addStretch(1)
        controls_layout.addLayout(row1, 1, 0, 1, 3)

        # Row 2: Reading information.
        row2 = QHBoxLayout()
        row2.setSpacing(20)
        row2.addWidget(self.crosshair_label, 1)
        row2.addWidget(self.peak_label, 1)
        controls_layout.addLayout(row2, 2, 0, 1, 3)

        # Row 3: p0/p1 phase panel -- Single row, only 1D mode display.
        from viewer.phase_panel import PhasePanel

        self.phase_panel = PhasePanel()
        self.phase_panel.phase_changed.connect(self._on_phase_changed)
        controls_layout.addWidget(self.phase_panel, 3, 0, 1, 3)
        self.phase_panel.setVisible(False)

        self.controls_layout = controls_layout

        # TopSpin style 1D strip: upper row trace (F2), right column trace (F1), linked to the main
        # spectrum.
        self.strip_top = pg.PlotWidget()
        self.strip_top.setFixedHeight(170)  # 0.2.199-Patch29bt:1D strip size increase.
        self.strip_top.setMenuEnabled(False)
        self.strip_top.getViewBox().setXLink(self.plot.getViewBox())
        self.strip_top_curve = pg.PlotDataItem(pen=pg.mkPen("#1f77b4", width=1))
        self.strip_top.addItem(self.strip_top_curve)
        self.strip_top.hide()
        self.strip_right = pg.PlotWidget()
        # Patch29bt: Vertical strips are enlarged first.
        self.strip_right.setFixedWidth(190)
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

        # Upper and lower layout: upper spectrum area, lower control panel; spectrum defaults to 1:1
        # square.
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(plot_area)
        splitter.addWidget(controls)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([620, 210])
        self.view_splitter = splitter  # 0.2.199-Patch29bd:1D fits content when switching.
        self.plot.setMinimumHeight(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        # 0.2.199-patch29ax: Frame selection dotted line is drawn on the viewport overlay (drag does
        # not trigger scene redrawing).
        self._box_overlay = _BoxSelectOverlay(self.plot.viewport(), self)
        self._box_overlay.setGeometry(self.plot.viewport().rect())
        self.plot.viewport().installEventFilter(self)
        # 0.2.199-patch29bn: The peak identification label is drawn on the viewport overlay (widget
        # coordinates, text upright; peripheral distribution guide lines do not cross); redraw when
        # the view changes.
        self._label_overlay = _LabelOverlay(self.plot.viewport(), self)
        self._label_overlay.setGeometry(self.plot.viewport().rect())
        self.plot.getViewBox().sigRangeChanged.connect(
            lambda *_: self._label_overlay.update()
        )
        self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.plot.scene().sigMouseClicked.connect(self._on_plot_clicked)
        self.set_aspect_ratio(None)  # Default free aspect ratio (free).
        # 0.2.133: contour state
        self._contour_states: dict[str, tuple[int, int]] = {}
        self._mouse_left_pressed = False
        self.plot.scene().installEventFilter(self)

    # ------------------------------------------------------------ layers

    def _set_2d_controls_visible(self, visible: bool) -> None:
        """Show/hide 2D contour/aspect related controls (1D view hidden, 2D/3D restored).
        0.2.199-patch29gj-Fixed: Previously, clear() was not restored after _show_1d was hidden,
        and _mode_1d was set to False, causing add_spectrum to skip _restore_2d, causing 2D/3D
        spectrum contour control to remain hidden."""
        self.show_1d_button.setVisible(visible)
        self.level_label.setVisible(visible)
        self.level_slider.setVisible(visible)
        self.count_label.setVisible(visible)
        self.count_slider.setVisible(visible)
        self.aspect_label.setVisible(visible)
        self.aspect_slider.setVisible(visible)

    def clear(self) -> None:
        self._mode_1d = False
        self._primary_1d = None
        if self._plot_1d is not None:
            self.plot.removeItem(self._plot_1d)
            self._plot_1d = None
        # 0.2.199-patch29gj-Fixed: Restore 2D contour/aspect control (clear is not restored after
        # _show_1d is hidden).
        self._set_2d_controls_visible(True)
        self.set_1d_mode(False)
        # Unify 2D display orientation: row 0 (height ppm) at bottom, 1D view does not leak to
        # subsequent 2D/3D.
        self.plot.getViewBox().invertY(False)
        self._label_overlay.update()
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
        """Superimpose one spectrum; the first one becomes the main spectrum (providing ppm axis
        and peak coordinate system). One-dimensional spectrum (Spectrum1D/.fid/2D row/column
        slicing) enters 1D trace display mode."""
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
        """Update main spectrum data in situ (3D slice switching): No clear/reconstruction to avoid
        flickering and slowness. It only takes effect when the view has only one 2D main
        spectrum (3D slicing scene); in other cases, fall back to clear+add. Return True to
        indicate that it has been updated in situ."""
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
        """Slider value -> Starting point percentage (cubic mapping: the first 10% threshold
        accounts for most of the drag bar, and the low threshold is finely adjustable)."""
        value = max(1, self.level_slider.value())
        return (value / 100.0) ** 3

    def _level_label_text(self) -> str:
        return f"Contour start {self._level_fraction() * 100:.2f}%"

    def _levels_for(self, spectrum: Spectrum) -> np.ndarray:
        """From the starting point to the maximum value of the slice, take n levels of logarithmic
        intervals, including symmetric negative levels. The benchmark is the maximum value of
        the current slice itself (the peak can be filled, not hollow); when the 3D slice zone is
        noise_floor (multiple of full-spectrum noise), levels lower than the lower limit are
        eliminated -- pure noise. The slice will not fill the screen with noise points. The
        return level increases monotonically."""
        maximum = float(spectrum.max_intensity)
        if maximum <= 0 and spectrum.data.size:
            maximum = abs(float(np.min(spectrum.data)))
        if maximum <= 0:
            return np.array([-1.0, 1.0])
        base = maximum * self._level_fraction()
        lo = max(base, maximum * 1e-6)
        if lo >= maximum:
            lo = maximum * 0.5
        positive = np.geomspace(lo, maximum, self._level_count)
        levels = np.concatenate([-positive[::-1], positive])
        floor = float(getattr(spectrum, "noise_floor", 0.0) or 0.0)
        if floor > 0:
            levels = levels[np.abs(levels) >= floor]
            if levels.size == 0:
                return np.array([-1.0, 1.0])
        return levels


    def _update_levels_debounced(self) -> None:
        """Only refresh the value during dragging to avoid rebuilding the outline for each grid
        (performance)."""
        self.level_label.setValue(self._level_fraction() * 100.0)

    def _update_levels(self) -> None:
        """Release the slider/Reconstruct contours when levels change (reuse cached interpolation
        data)."""
        self.level_label.setValue(self._level_fraction() * 100.0)
        for layer, spectrum in zip(self.layers, self.layer_spectra):
            layer.set_levels(self._levels_for(spectrum))
        # 0.2.199-patch29bw:contour starting point change -> blank area change, redraw label.
        if self._label_overlay is not None:
            self._label_overlay.update()

    def _on_level_count(self, value: int) -> None:
        self._level_count = value
        self.count_label.setValue(value)
        self._update_levels()

    def _on_level_spin_changed(self, percent: float) -> None:
        """Enter the starting percentage of the contour -> Invert the slider value (cubic mapping
        rounding)."""
        v = int(round(100.0 * (max(percent, 0.0) / 100.0) ** (1.0 / 3.0)))
        self.level_slider.setValue(max(1, min(100, v)))

    def _on_count_spin_changed(self, value: int) -> None:
        self.count_slider.setValue(value)

    def _on_aspect_spin_changed(self, value: float) -> None:
        if self._mode_1d:
            return  # 1D Always aspect free.
        self.aspect_slider.setValue(int(round(value * 100.0)))

    @staticmethod
    def _axis_ticks(
        axis: SpectrumAxis, count: int
    ) -> tuple[list[tuple[int, str]], str]:
        """Ppm The effective axis displays ppm scale; the time domain axis (such as FID time point)
        displays point serial number. 0.2.133: Scale labels are rounded as much as possible
        (integers are displayed within +/-0.1 ppm, otherwise 1 decimal place) to avoid long
        decimal labels overlapping each other when reducing."""
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
        """Zoom/After translation, reconstruct the scale according to the current visual
        range(0.2.133). Fixed ticks labels being squeezed together when zoomed out; here, 5
        ticks are reselected according to the viewing angle, and each tick label is rounded as
        much as possible to avoid overlapping and long decimal labels."""
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
        """Rebuild bottom axis by visible data range/left axis scale (max 5 per axis)."""
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
        """Select 5 equidistant ticks within the visible span (index position, label rounding
        ppm/serial number)."""
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
        """Display the one-dimensional spectrum (FID/2D row/column slicing) as a 1D trace and hide
        the 2D contour layer."""
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
        vb = self.plot.getViewBox()
        # 0.2.199-patch29gj - Modification: 1D trace x=ppm, y=intensity, the units are not
        # comparable -- Unlock the 2D aspect ratio, otherwise the y magnitude (1e13) will explode
        # the x-axis range and compress the spectrum into a line.
        self.set_aspect_ratio(None)
        # The 1D spectrum itself is already 1D, and the TopSpin strip button is meaningless and
        # hidden (user patch29gj).
        self.show_1d_button.setVisible(False)
        # When 1D is used, the contour/aspect control is meaningless and hidden; the phase panel is
        # displayed for phase modulation.
        self._set_2d_controls_visible(False)
        self.phase_panel.set_visible_1d_mode(True)
        if spectrum1d.ppm_valid:
            ticks = []
            for i in np.linspace(0, axis.size - 1, 10):
                ppm = float(axis.ppm_at(int(i)))
                if abs(ppm - round(ppm)) < 0.1:
                    ticks.append((ppm, f"{round(ppm):.0f}"))
                else:
                    ticks.append((ppm, f"{ppm:.1f}"))
            self.plot.getAxis("bottom").setTicks([ticks])
            self.plot.setLabels(bottom=f"{axis.label} (ppm)", left="Intensity")
            # NMR Convention: High ppm is on the left (consistent with 2D spectrum).
            vb.invertX(True)
        else:
            ticks = [
                (int(i), str(int(i)))
                for i in np.linspace(0, axis.size - 1, 6)
            ]
            self.plot.getAxis("bottom").setTicks([ticks])
            self.plot.setLabels(bottom=axis.label, left="Intensity")
            vb.invertX(False)
        vb.invertY(False)
        self.set_1d_mode(False)
        self.reset_view()
        self._connect_axis_refresh()
        self._refresh_phase_availability()
        return name or (
            spectrum1d.source.stem if spectrum1d.source is not None else "1D"
        )

    def _restore_2d(self) -> None:
        """Exit the 1D view and restore the 2D outline layer."""
        if not self._mode_1d:
            return
        self._mode_1d = False
        self._primary_1d = None
        if self._plot_1d is not None:
            self.plot.removeItem(self._plot_1d)
            self._plot_1d = None
        for layer in self.layers:
            layer.setVisible(True)
        vb = self.plot.getViewBox()
        vb.invertX(False)
        vb.invertY(False)
        self._set_2d_controls_visible(True)
        self.phase_panel.set_visible_1d_mode(False)
        # Restore 2D aspect ratio (press slider current value; temporarily unlocked when 1D is
        # entered).
        self.set_aspect_ratio(self._aspect_ratio_from_slider(self.aspect_slider.value()))
        if self._primary is not None:
            self._setup_axes(self._primary)
            self.reset_view()
            self._apply_peak_items()
        self._refresh_phase_availability()

    # ------------------------------------------------------------ phase
    def _refresh_phase_availability(self) -> None:
        """Enable the phase panel according to the current display: available for 1D spectra or 1D
        strips (only displays, does not change the data)."""
        self.phase_panel.set_visible_1d_mode(self._phase_target() != "")

    def _phase_target(self) -> str:
        """Current phaseable 1D targets: Primary 1D trace / 1D strip."""
        if self._mode_1d and self._primary_1d is not None:
            return "1d"
        if self._strips_active and self._primary is not None:
            return "strips"
        return ""

    @staticmethod
    def _display_phase(real: np.ndarray, p0: float, p1: float) -> np.ndarray:
        """The display uses phase rotation (without changing the data): the analytical signal (real
        part + Hilbert imaginary part) is rotated and the real part is taken. P0/P1 only affects
        the display, which is equivalent to the naked eye phase of nmrDraw; the real spectrum is
        also available."""
        try:
            from scipy.signal import hilbert
        except ImportError:  # pragma: no cover - scipy Depends on the project.
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
        """Phase slider changes: 1D real-time update display; synchronizes two 1D traces in strip
        mode."""
        target = self._phase_target()
        if target == "1d":
            self._update_phased_1d()
        elif target == "strips":
            self._refresh_strips_phase()


    # 0.2.133: Left button press and hold state tracking (cross dotted line follows in 1D mode)
    # 0.2.145: Directly handle MouseMove during press and hold drag 0.2.148: The real scene event
    # type is GraphicsSceneMouse*, which is the same as normal MouseButtonPress/Move are considered
    # to hold down/move.
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
            if self._label_overlay is not None:
                self._label_overlay.setGeometry(self.plot.viewport().rect())
            return super().eventFilter(obj, event)
        if obj is self.plot.scene():
            kind = self._mouse_event_kind(event.type())
            if kind == "press":
                if event.button() == Qt.MouseButton.LeftButton:
                    self._mouse_left_pressed = True
                    self._box_press_scene = self._box_scene_pos(event)
                    self._box_selecting = True
                    # 0.2.199-patch29cb: Press and hold assignment in selection mode to drag and
                    # fine-tune.
                    if self._box_select_enabled and self._click_mode == "select":
                        scene_pos = self._box_scene_pos(event)
                        if scene_pos is not None:
                            row = self._label_at_widget(self.plot.mapFromScene(scene_pos))
                            if row is not None:
                                self._drag_label_row = row
                                self._suppress_click = True
                                return True
            elif kind == "release":
                if event.button() == Qt.MouseButton.LeftButton:
                    self._mouse_left_pressed = False
                    if self._drag_label_row is not None:
                        self._drag_label_row = None
                        self._suppress_click = True
                        return True
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
                if self._drag_label_row is not None:
                    scene_pos = self._box_scene_pos(event)
                    if scene_pos is not None:
                        self._move_label(
                            self._drag_label_row,
                            self.plot.mapFromScene(scene_pos),
                        )
                    return True
                if self._strips_active or self._mode_1d:
                    # 0.2.199-patch10: The scene event may be QGraphicsSceneMouseEvent (take
                    # scenePos) or ordinary QMouseEvent (take position); Qt binding has no
                    # scenePosition attribute.
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
        """Scene events take scenePos; ordinary QMouseEvent is mapped to scene coordinates by
        plot."""
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
        """Frame selection ends: Select all peaks within the rectangle and link the peak table."""
        if self._box_press_scene is None or self._primary is None:
            self._cancel_box_select()
            return
        rect = QRectF(self._box_press_scene, scene_pos).normalized()
        # The box selection is over and releasing is not regarded as a click.
        self._suppress_click = True
        self._cancel_box_select()
        try:
            v0 = self.plot.getViewBox().mapSceneToView(rect.topLeft())
            v1 = self.plot.getViewBox().mapSceneToView(rect.bottomRight())
        except Exception:  # noqa: BLE001
            return
        if self._primary is None:
            return
        x_size = self._primary.x_axis.size
        y_size = self._primary.y_axis.size

        def _clamp(point) -> tuple[int, int]:
            # 0.2.199-patch29bf: When the box selection exceeds the spectrum area, it ends at the
            # edge of the spectrum.
            return (
                max(0, min(int(round(point.x())), x_size - 1)),
                max(0, min(int(round(point.y())), y_size - 1)),
            )

        ax0, ay0 = _clamp(v0)
        ax1, ay1 = _clamp(v1)
        lo_x, hi_x = sorted((ax0, ax1))
        lo_y, hi_y = sorted((ay0, ay1))
        # 0.2.199-patch29ay: Only compare the frame range and cached peak coordinates, no other
        # operations are performed.
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
        """Hold down the left button and drag: strip mode moves the crosshair + two 1D traces; 1D
        data mode updates the readings."""
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
        """Recalculate two 1D strip traces according to the current phase (for display)."""
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
        """Switch 1D spectrum display (TopSpin type): Crosshair + superior/right 1D strip."""
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
        # 0.2.199-patch29bd: The control area fits the content and removes the white space above and
        # below the ppm display line.
        if getattr(self, "view_splitter", None) is not None:
            controls = self.controls_layout.parentWidget()
            hint = max(1, controls.sizeHint().height())
            total = self.view_splitter.height()
            if total > 0:
                self.view_splitter.setSizes(
                    [max(1, total - hint), hint]
                )
        self._label_overlay.update()
        if active:
            if self._data_bounds_item is not None:
                self._data_bounds_item.setVisible(False)
            self._setup_strip_axes()
            # The direction of the 1D strip on the right is consistent with the Y-axis of the two-
            # dimensional spectrum (spectrum + coordinate axis are corrected together).
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
        """Set the scale for the 1D strip (ppm axis displays ppm, time domain axis displays point
        number)."""
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
        """Update two one-dimensional traces at the crosshairs (row=F2 trace, column=F1 trace; show
        phase)."""
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
        """Move the crosshair to the view coordinates (x=column, y=view y; view y is the data
        row)."""
        if not self._strips_active:
            return
        self._crosshair_v.setPos(x)
        self._crosshair_h.setPos(y)

    def _restore_strips(self) -> None:
        """Hide bars and crosshairs."""
        self.strip_top.hide()
        self.strip_right.hide()
        self._crosshair_v.hide()
        self._crosshair_h.hide()

    # ------------------------------------------------------------ layers

    def _on_layer_context_menu(self, pos) -> None:
        """Right click on the layer list: Delete this layer/Delete all layers."""
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
        """Delete the spectrum of the specified layer (when the main spectrum is deleted, it will
        fall back to the first remaining spectrum)."""
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
        """Resume displaying the full spectrum range (1D trace or the combined range of all stacked
        spectra)."""
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
        """Draws the spectrum data bounding rectangle (created with the spectrum) based on the
        current layer data size."""
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
            # Hang to the ViewBox data coordinate system, Scale with spectrum/Pan; after zooming in,
            # move out of the field of view, the full spectrum is visible.
            self.plot.addItem(item)
            self._data_bounds_item = item
        self._data_bounds_item.setRect(
            QRectF(-0.5, -0.5, float(sx), float(sy))
        )
        self._data_bounds_item.setVisible(not self._mode_1d)

    def _on_aspect_changed(self, value: int) -> None:
        """Aspect ratio slider callback."""
        if self._mode_1d:
            return  # 1D Always aspect free,Ignore recovery/external writeback(0.2.199-patch29gj).
        ratio = self._aspect_ratio_from_slider(value)
        if ratio is None:
            self.aspect_label.setValue(0.0)  # SpecialValueText Show "Aspect free".
        else:
            self.aspect_label.setValue(ratio)
        self.set_aspect_ratio(ratio)


    def set_aspect_ratio(self, ratio: float | None) -> None:
        """Lock the display aspect ratio (data unit x/y); None means free stretching (default)."""
        vb = self.plot.getViewBox()
        if ratio is None:
            vb.setAspectLocked(False)
        else:
            vb.setAspectLocked(True, ratio=float(ratio))

    # ------------------------------------------------------------- peaks

    def set_peaks_visible(self, visible: bool) -> None:
        """Show/Hide all peak markers(check control callback)."""
        visible = bool(visible)
        if visible == self._peaks_visible:
            return
        self._peaks_visible = visible
        if self.show_peaks_checkbox.isChecked() != visible:
            self.show_peaks_checkbox.setChecked(visible)
        self._apply_peak_items()

    def set_peaks(self, peaks: list[dict], show_labels: bool = True) -> None:
        """Overlay peak label: dict supports H_shift/N_shift or x_ppm/y_ppm, optional label."""
        self._peaks = list(peaks)
        self._selected_peak = None
        self._box_selected_rows.clear()
        self._drag_label_row = None
        self._apply_peak_items(show_labels=show_labels)
        # 0.2.199-patch29cl:assignment By default, press "centre Zoom 1.5 x" for dynamic projection;
        # save the screen position after dragging.
        self._label_positions = [None] * len(self._peaks)
        self._label_overlay.update()

    def apply_label_edit(self, row: int, text: str) -> None:
        """Immediately update the label on the graph after editing the peak table Assignment
        (0.2.199-patch29cn): only the peak label is changed and the label overlay is redrawn,
        the peak label is not rebuilt, and the dragged position is not moved."""
        if 0 <= row < len(self._peaks):
            self._peaks[row]["label"] = text
        self._label_overlay.update()

    def highlight_peak(self, row: int, flash: bool = True) -> None:
        """Selected peaks: only click on the peak table to trigger flashing positioning
        (0.2.199-patch29bk/patch29bo); click on spectrum, and box selection only highlights the
        mark and does not flash."""
        self._selected_peak = row if 0 <= row < len(self._peaks) else None
        self._apply_peak_items()
        if flash and 0 <= row < len(self._peak_data_xy):
            xi, yi = self._peak_data_xy[row]
            if xi == xi and yi == yi:  # Not NaN (current plane is visible).
                self._flash_at(xi, yi)

    def _ensure_peak_visible(self, xi: float, yi: float) -> None:
        """When the selected peak is out of view, pan the view: try to get to the centre, and move
        the edge of the spectrum into the view (NMRViewBox will clamp to the data boundary,
        0.2.199-patch29bl)."""
        if self._primary is None:
            return
        vb = self.plot.getViewBox()
        xr, yr = vb.viewRange()
        if xr[0] <= xi <= xr[1] and yr[0] <= yi <= yr[1]:
            return
        span_x = max(xr[1] - xr[0], 1.0)
        span_y = max(yr[1] - yr[0], 1.0)
        vb.setRange(
            xRange=(xi - span_x / 2.0, xi + span_x / 2.0),
            yRange=(yi - span_y / 2.0, yi + span_y / 2.0),
            padding=0,
        )

    def _flash_at(self, xi: float, yi: float) -> None:
        """Draw a flickering ring of fixed screen size at the peak position, which disappears after
        about 400ms (0.2.199-patch29bk/bl: does not scale with the spectrum; first translate and
        position outside the field of view)."""
        if self._primary is None:
            return
        self._ensure_peak_visible(xi, yi)
        radius = 24.0  # Fixed pixel size, does not scale with spectrum.
        if self._flash_item is not None:
            try:
                self.plot.removeItem(self._flash_item)
            except Exception:  # noqa: BLE001
                pass
            self._flash_item = None
        ring = QGraphicsEllipseItem(
            -radius, -radius, radius * 2.0, radius * 2.0
        )
        ring.setPen(pg.mkPen("#0e639c", width=2))
        ring.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        )
        ring.setPos(xi, yi)
        ring.setZValue(22)
        self.plot.addItem(ring)
        self._flash_item = ring
        from qtcompat.QtCore import QTimer

        QTimer.singleShot(400, self._clear_flash)

    def _clear_flash(self) -> None:
        """Remove flicker ring (timer trigger or external cleanup)."""
        if self._flash_item is not None:
            try:
                self.plot.removeItem(self._flash_item)
            except Exception:  # noqa: BLE001
                pass
            self._flash_item = None

    def set_peak_size(self, size: float) -> None:
        """Peak marker size (data coordinate units, scaled with spectrum); 0.2.199-patch29az."""
        self._peak_size = max(0.5, float(size))
        self._apply_peak_items()

    # ----------------------------------------------------------------
    # Public read-only/thin packaging interface (for external use such as gui/spectrum_panel)
    # 0.2.199-patch29hz: It turns out that external direct access to
    # _update_levels/_show_peak_labels/ _primary will silently fail once the internal name is
    # changed.
    # ----------------------------------------------------------------
    @property
    def peak_labels_visible(self) -> bool:
        """Whether the peak label (Assignment) is currently displayed."""
        return bool(self._show_peak_labels)

    @property
    def primary_spectrum(self):
        """The current main score (the first one in the overlay; not loaded is None)."""
        return self._primary

    def refresh_levels(self) -> None:
        """Press current contour starting point/Layer redraw contours."""
        self._update_levels()

    def set_peak_labels_visible(self, visible: bool) -> None:
        """Peak assignment label on the switch graph (Assignment column title click linkage,
        0.2.199-patch29bf)."""
        visible = bool(visible)
        if visible == self._show_peak_labels:
            return
        self._show_peak_labels = visible
        self._apply_peak_items()
        self._label_overlay.update()

    def set_peak_click_mode(self, mode: str) -> None:
        """Left-click behaviour: select=select peak/add=add peak/delete=delete peak."""
        self._click_mode = mode if mode in ("select", "add", "delete") else "select"
        if mode != "select":
            self._cancel_box_select()
            self._box_selected_rows.clear()
            self._suppress_click = False
            self._apply_peak_items()

    def set_box_select_mode(self, enabled: bool) -> None:
        """Selection mode switch: After turning it on, left-click and drag the frame to select the
        peak (without zooming); turn it off to restore the frame selection zoom."""
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
        """Map the peak rows to x/y ppm of the current display plane (in order of axis dimension
        F1/F2/F3). The axis label may be the core name (H/N/C...), so the dimensional order of
        the axis in the spectrum (F1=0/F2=1/F3=2) is used to select the peak table column; the
        2D peak table falls back to H_shift/N_shift, and in other cases it falls back to
        x_ppm/y_ppm."""
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

    def _label_widget_pos(self, row: int) -> QPointF | None:
        """Assignment screen position (0.2.199-patch29cl/patch29cm): Default = the peak layer is
        enlarged from the centre of the view 1.25 x (spherical mapping, spread out; Pan/Zoom
        automatically maintained 1.25 x ratio); user saves the viewport ratio after dragging."""
        pos = (
            self._label_positions[row]
            if 0 <= row < len(self._label_positions)
            else None
        )
        if pos is not None:
            ov = self._label_overlay
            return QPointF(
                float(pos[0]) * max(ov.width(), 1),
                float(pos[1]) * max(ov.height(), 1),
            )
        if 0 <= row < len(self._peak_data_xy):
            xi, yi = self._peak_data_xy[row]
            if xi != xi or yi != yi:  # 0.2.199-Patch29da: No label for non-local plane peaks.
                return None
            try:
                pp = self.plot.mapFromScene(
                    self.plot.getViewBox().mapViewToScene(
                        QPointF(float(xi), float(yi))
                    )
                )
            except Exception:  # noqa: BLE001
                return None
            ov = self._label_overlay
            cx = ov.width() / 2.0
            cy = ov.height() / 2.0
            return QPointF(
                cx + 1.25 * (float(pp.x()) - cx),
                cy + 1.25 * (float(pp.y()) - cy),
            )
        return None

    def _label_box(self, pos: QPointF, tw: float, th: float) -> QRectF:
        """Text box: Centered horizontally on the anchor point, text above the anchor point
        (0.2.199-patch29ci)."""
        return QRectF(pos.x() - tw / 2.0, pos.y() - th, tw, th)

    def _label_at_widget(self, widget_pos: QPointF) -> int | None:
        """Selection mode dragging: Hit the line containing the assignment text
        (0.2.199-patch29cb)."""
        if self._primary is None or self._mode_1d or not self._show_peak_labels:
            return None
        widget_pos = QPointF(widget_pos)
        try:
            ppu = 1.0 / max(self.plot.getViewBox().viewPixelSize()[0], 1e-9)
        except Exception:  # noqa: BLE001
            ppu = 1.0
        font_px = max(6.0, min(60.0, self._peak_size * 3.0 * ppu))
        font = QFont()
        font.setPixelSize(int(round(font_px)))
        fm = QFontMetrics(font)
        for row, _xy in enumerate(self._peak_data_xy):
            xi, yi = _xy
            if xi != xi or yi != yi:  # 0.2.199-Patch29da: non-plane peak.
                continue
            peak = self._peaks[row]
            label = str(peak.get("label") or "").strip()
            if not label and row != self._selected_peak:
                continue
            text = label or str(peak.get("Peak_ID", ""))
            if not text:
                continue
            p = self._label_widget_pos(row)
            if p is None:
                continue
            tw = float(fm.horizontalAdvance(text))
            th = float(fm.height())
            if self._label_box(p, tw, th).adjusted(-4, -4, 4, 4).contains(
                widget_pos
            ):
                return row
        return None

    def _move_label(self, row: int, widget_pos: QPointF) -> None:
        """Drag the assignment to the new screen location (save viewport scale, layer fixed;
        0.2.199-patch29cj)."""
        if not (0 <= row < len(self._label_positions)):
            return
        ov = self._label_overlay
        w = max(ov.width(), 1)
        h = max(ov.height(), 1)
        self._label_positions[row] = (
            float(widget_pos.x() / w),
            float(widget_pos.y() / h),
        )
        self._label_overlay.update()

    def _apply_peak_items(self, show_labels: bool = True) -> None:
        if not self._peaks_visible or self._mode_1d or self._primary is None:
            self.peak_item.setData(x=[], y=[])
            self._peak_data_xy = []
            self._label_overlay.update()
            return
        x_axis = self._primary.x_axis
        y_axis = self._primary.y_axis
        slice_axis = getattr(self._primary, "slice_axis", None)
        slice_ppm = getattr(self._primary, "slice_ppm", None)
        slice_step = getattr(self._primary, "slice_step_ppm", None)
        slice_ppm_min = getattr(self._primary, "slice_ppm_min", None)
        slice_ppm_max = getattr(self._primary, "slice_ppm_max", None)

        def _on_current_plane(peak: dict) -> bool:
            """3D slices only display peaks whose fixed axis coordinates fall within the current
            plane (0.2.199-patch29da). Peaks that lack fixed axis coordinates (such as 2D peak
            table) or whose coordinates exceed the entire fixed axis range (the peak table does
            not match the current spectral axis, such as the old peak selection results) cannot
            be filtered by plane and are displayed directly
            (0.2.199-patch29db/patch29de/patch29dj)."""
            if slice_axis is None or slice_ppm is None or not slice_step:
                return True
            try:
                value = float(peak.get(f"F{int(slice_axis) + 1}_shift"))
            except (TypeError, ValueError):
                return True
            if not value:
                # Wait for invalid coordinates: try your best to display, do not filter by plane.
                return True
            if abs(value - float(slice_ppm)) <= 0.5 * float(slice_step):
                return True
            # 0.2.199-patch29dj: The entire range of the fixed axis is used for out-of-bounds
            # judgment (the original "current slice +/-10 steps" will display almost all non-plane
            # peaks as out-of-bounds -> one slice can see all layer peaks).
            if (
                slice_ppm_min is not None
                and slice_ppm_max is not None
                and (
                    value < float(slice_ppm_min) - 0.5 * float(slice_step)
                    or value > float(slice_ppm_max) + 0.5 * float(slice_step)
                )
            ):
                return True
            return False

        visible = [_on_current_plane(peak) for peak in self._peaks]
        self._visible_peak_rows = (
            {row for row, ok in enumerate(visible) if ok}
            if any(not ok for ok in visible)
            else None
        )
        xs: list[float] = []
        ys: list[float] = []
        sizes: list[float] = []
        rows_data: list[dict] = []
        base = self._peak_size
        pens: list = []
        for row, peak in enumerate(self._peaks):
            if not visible[row]:
                continue
            x_ppm, y_ppm = self._peak_xy(peak)
            xs.append(float(x_axis.index_at_f(x_ppm)))
            # View y is the data row: the peak markers are placed according to the y-axis data row,
            # aligned with the contour; 0.2.199-patch29eo is indexed with decimals, and the markers
            # fall on the top of sub-pixel peaks.
            ys.append(float(y_axis.index_at_f(y_ppm)))
            selected = (
                row == self._selected_peak or row in self._box_selected_rows
            )
            # 0.2.199-patch29bj: Select peak 3 x size + blue, click on the peak table with clear
            # spectrum indication.
            sizes.append(base * 3.0 if selected else base)
            pens.append(
                pg.mkPen("#0e639c", width=1.5)
                if selected
                else pg.mkPen("#8b0000", width=1.5)
            )
            rows_data.append({"row": row})
        # The full line length list retains row index alignment (Label/selected/Frame selection);
        # hidden row coordinates are set to NaN.
        full_xy: list[tuple[float, float]] = []
        cursor = 0
        for row in range(len(self._peaks)):
            if visible[row]:
                full_xy.append((xs[cursor], ys[cursor]))
                cursor += 1
            else:
                full_xy.append((float("nan"), float("nan")))
        self._peak_data_xy = full_xy
        if len(self._label_positions) != len(self._peaks):
            self._label_positions = [None] * len(self._peaks)
        self.peak_item.setData(
            x=xs, y=ys, size=sizes, pen=pens, data=rows_data
        )

        # 0.2.199-patch29bn: The peak identification label and the guide line are drawn by
        # _LabelOverlay at the widget coordinates (the text is upright, peripherally distributed,
        # and the guide line does not cross). Only redrawing is triggered here.
        self._label_overlay.update()

    def _on_peak_clicked(self, _plot, points) -> None:
        if not points:
            return
        point = points[0]
        row = point.data().get("row", -1)
        if 0 <= row < len(self._peaks):
            self.highlight_peak(row, flash=False)
            peak = self._peaks[row]
            x_ppm, y_ppm = self._peak_xy(peak)
            self.peak_label.setText(
                tr(
                    "Peak {p0}: {p1:.3f} / {p2:.3f} ppm "
                    "({p3})",
                    p0=row + 1,
                    p1=x_ppm,
                    p2=y_ppm,
                    p3=peak.get("label", ""),
                )
            )
            self.peak_clicked.emit(row)

    def _view_to_data(self, point) -> tuple[int, int]:
        """View coordinates -> data index (col, row);contour is not flipped, view y is the data
        row. Out of range returns (-1, -1)."""
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
            # Just completed the box selection: this release is not regarded as a click.
            self._suppress_click = False
            return
        try:
            press = event.buttonDownScenePos(Qt.MouseButton.LeftButton)
        except AttributeError:
            # 0.2.199-patch29aw: pyqtgraph MouseClickEvent None buttonDownScenePos (that is API of
            # MouseDragEvent); use the left button pressed scene coordinates recorded by eventFilter
            # to determine whether to drag.
            press = getattr(self, "_box_press_scene", None)
        release = event.scenePos()
        if press is not None and (release - press).manhattanLength() > 6:
            return  # Dragging is a frame selection and zooming, not a click.
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
            # One-dimensional spectrum mode: Click the positioning crosshair (view coordinates) and
            # display two one-dimensional spectra there.
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
            # 0.2.199-patch29ar: Click to automatically adsorb to a nearby peak; if you cannot find
            # a significant peak, use the click point.
            snapped_row, snapped_col = snap_to_peak_top(
                self._primary.data, yi, xi
            )
            self.manual_peak_requested.emit(
                self._peak_from_data_point(snapped_col, snapped_row)
            )
        else:
            # Select: Select the peak closest to the click position (pixel distance).
            row = self._nearest_peak(xi, yi)
            if row is not None:
                self.highlight_peak(row, flash=False)
                self.peak_clicked.emit(row)

    def _peak_from_data_point(self, xi: int, yi: int) -> dict:
        """Click on the data point (col,row) -> peak row dict. The 3D slice plane is mapped to the
        logical dimension (F1/F2/F3_shift) according to dim_indices, and the 2D uses
        H_shift/N_shift (Contract §6); Intensity takes the data value of the point."""
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
        except Exception:  # noqa: BLE001 - Lack of intensity does not block peak addition.
            pass
        return peak

    def _nearest_peak(self, xi: int, yi: int) -> int | None:
        if self._primary is None or not self._peaks:
            return None
        x_axis = self._primary.x_axis
        y_axis = self._primary.y_axis
        best: tuple[float, int | None] = (12.0, None)  # Data point radius threshold.
        for row, peak in enumerate(self._peaks):
            if (
                self._visible_peak_rows is not None
                and row not in self._visible_peak_rows
            ):
                continue
            x_ppm, y_ppm = self._peak_xy(peak)
            dx = x_axis.index_at(x_ppm) - xi
            dy = y_axis.index_at(y_ppm) - yi
            dist = float(np.hypot(dx, dy))
            if dist < best[0]:
                best = (dist, row)
        return best[1]

    def _on_mouse_moved(self, pos) -> None:
        # 1D pure spectrum without 2D primary, still allows moving readings (only the horizontal
        # axis is displayed ppm).
        if self._primary is None and not (
            self._mode_1d and self._primary_1d is not None
        ):
            return
        try:
            point = self.plot.getViewBox().mapSceneToView(pos)
        except Exception:  # noqa: BLE001
            return
        if self._mode_1d and self._primary_1d is not None:
            # 1D: view x is ppm(data x), read the horizontal axis directly without rotating the data
            # index.
            axis = self._primary_1d.axis
            xv = float(point.x())
            if self._primary_1d.ppm_valid:
                self.crosshair_label.setText(
                    f"{axis.label} {xv:.3f} ppm"
                )
            else:
                self.crosshair_label.setText(
                    tr("{p0} point {p1}", p0=axis.label, p1=int(round(xv)))
                )
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
        else:
            # 0.2.148: Ordinary 2D (including 3D slices) mouse movement refreshes ppm readings in
            # real time.
            x_axis = self._primary.x_axis
            y_axis = self._primary.y_axis
            self.crosshair_label.setText(
                f"{x_axis.label} {x_axis.ppm_at(xi):.3f} ppm | "
                f"{y_axis.label} {y_axis.ppm_at(yi):.3f} ppm"
            )

    # ------------------------------------------------------------- misc

    def add_control_panel(self, panel: QWidget) -> None:
        """Hang any control panel into the viewer control area, occupying the entire row (across
        all columns). 0.2.199-patch29di: The original addWidget(panel) only puts it in column 0
        -- the right side of the 3D panel (flat drop down/slice slider/pt/ppm) (column 1/2) is
        left blank, and the minimum width of the panel is only pressed on column 0, making the
        column wider with the entire window; after spanning columns, the entire row width is
        used, and the slider can be extended to fill, no longer unnecessary width."""
        self.controls_layout.addWidget(
            panel,
            self.controls_layout.rowCount(),
            0,
            1,
            self.controls_layout.columnCount(),
        )
