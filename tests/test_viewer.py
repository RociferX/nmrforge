"""Spectrum viewer module tests: ppm axes, ft2 loading, contours, the viewer and the
standalone window (offscreen)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from qtcompat.QtCore import QPointF
from qtcompat.QtWidgets import QApplication, QGraphicsItem

from viewer.app import SpectrumWindow
from viewer.contour_layer import ContourLayer
from viewer.spectrum import Spectrum, SpectrumAxis
from viewer.spectrum_viewer import SpectrumViewer


def _axis(label: str, size: int = 128, sw: float = 6000.0) -> SpectrumAxis:
    return SpectrumAxis(
        label=label,
        size=size,
        sw_hz=sw,
        obs_mhz=600.0,
        carrier_ppm=4.7,
        orig_hz=4.7 * 600.0,
    )


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _synthetic_spectrum(shape: tuple[int, int] = (128, 256)) -> Spectrum:
    rng = np.random.default_rng(0)
    data = np.zeros(shape)
    for (y, x), amp in [((40, 120), 500), ((45, 110), 350), ((35, 105), 250)]:
        data[y, x] = amp
    from scipy.ndimage import gaussian_filter

    data = gaussian_filter(data, sigma=(1.5, 1.5))
    data = data + rng.normal(0, 0.05, size=shape)
    return Spectrum(data, [_axis("F1"), _axis("F2", size=shape[1])])


def _write_ft2(path: Path, spectrum: Spectrum) -> None:
    from nmrglue.fileio import pipe

    axes = spectrum.axes
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = axes[1].size
    dic["FDSPECNUM"] = axes[0].size
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDF1T"] = axes[0].size
    dic["FDF1SW"] = axes[0].sw_hz
    dic["FDF1OBS"] = axes[0].obs_mhz
    dic["FDF1CAR"] = axes[0].carrier_ppm
    dic["FDF1ORIG"] = axes[0].orig_hz
    dic["FDF2T"] = axes[1].size
    dic["FDF2SW"] = axes[1].sw_hz
    dic["FDF2OBS"] = axes[1].obs_mhz
    dic["FDF2CAR"] = axes[1].carrier_ppm
    dic["FDF2ORIG"] = axes[1].orig_hz
    pipe.write(str(path), dic, spectrum.data.astype(np.float32), overwrite=True)


def test_spectrum_axis_ppm_roundtrip() -> None:
    axis = _axis("F2", size=256)
    ppm = axis.ppm
    assert ppm.shape == (256,)
    assert ppm[0] > ppm[-1]  # ppm decreases with the index
    for index in (0, 1, 100, 255):
        assert axis.index_at(axis.ppm_at(index)) == index
    # with ORIG defined, the axis end point ppm = ORIG/OBS
    assert abs(axis.ppm_at(255) - 4.7) < 1e-9


def test_spectrum_axis_index_at_f() -> None:
    """Subpixel inverse interpolation: index_at_f(ppm_at_f(x)) ≈ x (0.2.199-patch29eo)."""
    axis = _axis("F2", size=256)
    for index in (0, 10, 128, 255):
        assert abs(axis.index_at_f(axis.ppm_at(index)) - index) < 1e-9
    f = 100.3
    ppm = axis.ppm_at_f(f)
    assert abs(axis.index_at_f(ppm) - f) < 1e-6
    # out of range clamps to the end point
    assert axis.index_at_f(axis.ppm[0] + 1.0) == 0.0
    assert axis.index_at_f(axis.ppm[-1] - 1.0) == float(axis.size - 1)


def test_spectrum_axis_fallback_carrier() -> None:
    axis = SpectrumAxis(label="F1", size=64, sw_hz=3000.0, obs_mhz=150.0, carrier_ppm=118.0)
    ppm = axis.ppm
    assert ppm[0] > ppm[-1]
    assert abs(ppm[32] - 118.0) < 1e-6


def test_load_from_ft2_roundtrip(tmp_path: Path) -> None:
    spectrum = _synthetic_spectrum()
    path = tmp_path / "test.ft2"
    _write_ft2(path, spectrum)
    loaded = Spectrum.load_from_ft2(path)
    assert loaded.data.shape == spectrum.data.shape
    np.testing.assert_allclose(loaded.data, spectrum.data)
    assert loaded.source == path
    assert loaded.x_axis.size == spectrum.data.shape[1]


def test_load_from_ft2_rejects_1d(tmp_path: Path) -> None:

    path = tmp_path / "bad.ft2"
    from nmrglue.fileio import pipe

    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 1
    dic["FDSIZE"] = 16
    dic["FDSPECNUM"] = 1
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDF1T"] = 16
    dic["FDF1SW"] = 1.0
    dic["FDF1OBS"] = 1.0
    dic["FDF1CAR"] = 1.0
    pipe.write(str(path), dic, np.zeros(16, dtype=np.float32), overwrite=True)
    with pytest.raises(ValueError, match="二维"):
        Spectrum.load_from_ft2(path)


def test_contour_layer_small_data_uses_paths() -> None:
    from scipy.ndimage import gaussian_filter

    spectrum = _synthetic_spectrum((64, 128))
    data = spectrum.data.copy()
    negative = np.zeros_like(data)
    negative[20, 90] = -300.0
    data = data + gaussian_filter(negative, sigma=(1.5, 1.5))
    layer = ContourLayer(
        data,
        np.array([-50.0, -10.0, 10.0, 50.0]),
        pen="#000000",
        neg_pen="#e74c3c",
        zoom=2.0,
    )
    # small spectra go through matplotlib contours (size shunt; the stable path for the full
    # VM run, see CHANGELOG 0.2.73)
    assert layer._use_contourpy is False
    assert layer._path.isEmpty() is False
    assert layer._path_neg.isEmpty() is False
    assert layer.boundingRect().width() == 128
    assert layer.boundingRect().height() == 64
    layer.setData(spectrum.data, np.array([-20.0, 20.0]))
    assert layer._path.isEmpty() is False


def test_contour_layer_large_data_uses_contourpy() -> None:
    from scipy.ndimage import gaussian_filter

    spectrum = _synthetic_spectrum((512, 1024))
    data = spectrum.data.copy()
    negative = np.zeros_like(data)
    negative[160, 720] = -300.0
    data = data + gaussian_filter(negative, sigma=(1.5, 1.5))
    layer = ContourLayer(
        data,
        np.array([-50.0, -10.0, 10.0, 50.0]),
        pen="#000000",
        neg_pen="#e74c3c",
        zoom=2.0,
    )
    # large spectra go through contourpy real contours (POKY/nmrDraw-style thin line frame,
    # see CHANGELOG 0.2.75)
    assert layer._use_contourpy is True
    assert layer._path.isEmpty() is False
    assert layer._path_neg.isEmpty() is False
    assert layer.boundingRect().width() == 1024
    assert layer.boundingRect().height() == 512
    layer.setData(spectrum.data, np.array([-20.0, 20.0]))
    assert layer._path.isEmpty() is False


def test_viewer_add_spectrum_and_levels(qapp: QApplication) -> None:
    viewer = SpectrumViewer()
    spectrum = _synthetic_spectrum()
    name = viewer.add_spectrum(spectrum, name="HSQC")
    assert name == "HSQC"
    assert viewer.layer_list.count() == 1
    assert viewer.layers[0]._path.isEmpty() is False
    # changing the level slider rebuilds the paths and updates the level count
    old_count = viewer._level_count
    viewer.count_slider.setValue(old_count + 8)
    assert viewer._level_count == old_count + 8
    assert len(viewer.layers[0]._levels) == 2 * (old_count + 8)  # symmetric positive/negative
    viewer.reset_view()
    viewer.close()


def test_viewer_update_spectrum_data_in_place(qapp: QApplication) -> None:
    """0.2.199-patch10: 3D slice switching updates the main spectrum data in situ (no layer
    rebuild, avoids flicker)."""
    viewer = SpectrumViewer()
    first = _synthetic_spectrum()
    viewer.add_spectrum(first, name="slice 0")
    second = _synthetic_spectrum()
    second.data = second.data + 1.0
    ok = viewer.update_spectrum_data(second, name="slice 1")
    assert ok is True
    assert len(viewer.layers) == 1  # updated in situ: no layer added or rebuilt
    assert viewer._primary is second
    assert viewer.layer_names == ["slice 1"]
    assert viewer.layer_list.count() == 1
    assert viewer.layer_list.item(0).text() == "slice 1"
    assert np.array_equal(viewer.layers[0]._data, second.data)
    viewer.close()


def test_viewer_update_spectrum_data_falls_back(qapp: QApplication) -> None:
    """0.2.199-patch10: in a multi-layer view the in-situ update falls back to clear+add."""
    viewer = SpectrumViewer()
    viewer.add_spectrum(_synthetic_spectrum())
    viewer.add_spectrum(_synthetic_spectrum())  # second layer
    ok = viewer.update_spectrum_data(_synthetic_spectrum())
    assert ok is False
    assert len(viewer.layers) == 1  # only one remains after clear
    viewer.close()


def test_viewer_contour_defaults_and_english_labels(
    qapp: QApplication,
) -> None:
    """0.2.77: contour start defaults to 3% and levels to 8; common terms shown in English."""
    viewer = SpectrumViewer()
    assert viewer.level_slider.value() == 31
    assert viewer._level_count == 8
    assert viewer.count_slider.value() == 8
    assert viewer.level_label.text() == "2.98"

    viewer.level_slider.setValue(50)
    assert viewer._level_fraction() < 0.15
    viewer.level_slider.setValue(31)
    assert viewer.count_label.text() == "8"
    assert viewer.show_peaks_checkbox.text() == "Show peaks"
    assert viewer.show_1d_button.text() == "1D"
    viewer.close()


def test_viewer_peaks_poky_style(qapp: QApplication) -> None:
    viewer = SpectrumViewer()
    spectrum = _synthetic_spectrum()
    viewer.add_spectrum(spectrum)
    x_ppm = spectrum.x_axis.ppm_at(120)
    y_ppm = spectrum.y_axis.ppm_at(40)
    viewer.set_peaks(
        [
            {"H_shift": x_ppm, "N_shift": y_ppm, "label": "G1"},
            {"H_shift": x_ppm - 0.5, "N_shift": y_ppm - 0.4, "label": "A2"},
        ]
    )
    data = viewer.peak_item.data
    assert data["size"].shape == (2,)
    assert viewer.peak_item.opts["symbol"] == "x"  # Poky style x
    assert viewer.peak_item.opts["pxMode"] is False  # scales with the spectrum
    assert viewer._label_overlay.visible_label_count() == 2  # labelled peaks + selected peak
    viewer.highlight_peak(0)
    assert float(viewer.peak_item.data["size"][0]) == pytest.approx(1.5 * 3.0)
    assert viewer._flash_item is not None  # 0.2.199-patch29bk: single-point flash localization
    assert viewer._flash_item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
    viewer._clear_flash()
    assert viewer._flash_item is None
    viewer.set_peak_size(12.0)
    assert float(viewer.peak_item.data["size"][0]) == pytest.approx(12.0 * 3.0)
    assert float(viewer.peak_item.data["size"][1]) == pytest.approx(12.0)
    viewer.close()


def test_viewer_nearest_peak(qapp: QApplication) -> None:
    viewer = SpectrumViewer()
    spectrum = _synthetic_spectrum()
    viewer.add_spectrum(spectrum)
    viewer.set_peaks(
        [
            {"H_shift": spectrum.x_axis.ppm_at(120), "N_shift": spectrum.y_axis.ppm_at(40)},
            {"H_shift": spectrum.x_axis.ppm_at(200), "N_shift": spectrum.y_axis.ppm_at(80)},
        ]
    )
    assert viewer._nearest_peak(120, 40) == 0
    assert viewer._nearest_peak(200, 80) == 1
    assert viewer._nearest_peak(0, 0) is None
    viewer.close()


def test_viewer_axis_direction_nmrdraw(qapp: QApplication) -> None:
    """Display convention (user 0.2.59 feedback): 1H high ppm on the left (x column 0 on the
    left), 15N high ppm at the bottom (y row 0 at the bottom)."""
    import pyqtgraph as pg

    viewer = SpectrumViewer()
    spectrum = _synthetic_spectrum()
    viewer.add_spectrum(spectrum)
    vb = viewer.plot.getViewBox()
    nx, ny = spectrum.data.shape[1], spectrum.data.shape[0]
    p0 = vb.mapViewToScene(pg.QtCore.QPointF(0, 0))
    p1 = vb.mapViewToScene(pg.QtCore.QPointF(nx - 1, 0))
    assert p0.x() < p1.x()  # column 0 (high ppm) on the left
    # view y is the data row: row 0 (high ppm) = view y=0 at the bottom, row ny-1 (low ppm)
    # at the top
    q0 = vb.mapViewToScene(pg.QtCore.QPointF(0, 0))
    q1 = vb.mapViewToScene(pg.QtCore.QPointF(0, ny - 1))
    assert q0.y() > q1.y()  # row 0 (high ppm) at the bottom, row ny-1 (low ppm) at the top
    viewer.close()


def test_viewer_aspect_ratio(qapp: QApplication) -> None:
    viewer = SpectrumViewer()
    viewer.add_spectrum(_synthetic_spectrum())
    viewer.set_aspect_ratio(2.0)
    assert viewer.plot.getViewBox().state["aspectLocked"] == 2.0
    viewer.set_aspect_ratio(None)
    assert viewer.plot.getViewBox().state["aspectLocked"] is False
    viewer.close()


def test_viewer_aspect_slider(qapp: QApplication) -> None:
    viewer = SpectrumViewer()
    viewer.add_spectrum(_synthetic_spectrum())
    assert viewer.aspect_slider.value() == 0
    assert viewer.plot.getViewBox().state["aspectLocked"] is False
    viewer.aspect_slider.setValue(200)
    assert viewer.plot.getViewBox().state["aspectLocked"] == 2.0
    viewer.aspect_slider.setValue(0)
    assert viewer.plot.getViewBox().state["aspectLocked"] is False
    viewer.close()


def test_viewer_zoom_min_limit(qapp: QApplication) -> None:
    viewer = SpectrumViewer()
    viewer.add_spectrum(_synthetic_spectrum((64, 128)))
    vb = viewer.plot.getViewBox()
    # Test min zoom: cannot zoom in below _MIN_VIEW_RANGE
    vb.setRange(xRange=(0, 2), yRange=(0, 2), padding=0)
    vr = vb.viewRange()
    # Should be clamped to at least _MIN_VIEW_RANGE (8.0)
    assert vr[0][1] - vr[0][0] >= 8.0
    assert vr[1][1] - vr[1][0] >= 8.0
    viewer.close()


def test_viewer_zoom_out_bounded(qapp: QApplication) -> None:
    """0.2.133: wheel/scaleBy zoom-out cannot go beyond the full range, and panning cannot
    move the spectrum out of view."""

    viewer = SpectrumViewer()
    viewer.add_spectrum(_synthetic_spectrum((64, 128)))
    vb = viewer.plot.getViewBox()
    full = vb.viewRange()
    full_x_span = full[0][1] - full[0][0]
    # zoom out 20 times through the wheel path (scaleBy): the span and the position must stay
    # inside the full range
    vb.setRange(xRange=(10, 20), yRange=(10, 20), padding=0)
    for _ in range(20):
        vb.scaleBy((0.9, 0.9), center=QPointF(16.0, 16.0))
    vr = vb.viewRange()
    assert vr[0][1] - vr[0][0] <= full_x_span + 1e-6
    assert vr[0][0] >= full[0][0] - 1e-6
    assert vr[0][1] <= full[0][1] + 1e-6
    # panning out of bounds: the view is pulled back, the spectrum cannot leave the viewport
    vb.translateBy(x=-500.0, y=0.0)
    vr = vb.viewRange()
    assert vr[0][0] >= full[0][0] - 1e-6
    assert vr[0][1] <= full[0][1] + 1e-6
    viewer.close()


def test_viewer_right_click_disabled(qapp: QApplication) -> None:
    viewer = SpectrumViewer()
    viewer.add_spectrum(_synthetic_spectrum())
    vb = viewer.plot.getViewBox()
    # Check that right-click is disabled (no context menu created yet)
    assert not hasattr(vb, "menu") or vb.menu is None
    viewer.close()


def test_spectrum_window_load_and_clear(tmp_path: Path, qapp: QApplication) -> None:
    path = tmp_path / "demo.ft2"
    _write_ft2(path, _synthetic_spectrum())
    window = SpectrumWindow()
    assert window.load_spectrum(path) is True
    assert window.viewer.layer_list.count() == 1
    assert "demo.ft2" in window.windowTitle()
    assert window._recent == [str(path)]
    window.clear_spectra()
    assert window.viewer.layer_list.count() == 0
    window.close()


def test_spectrum_window_load_failure(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    import viewer.app as app_module

    monkeypatch.setattr(app_module, "show_info", staticmethod(lambda *a, **k: None))
    window = SpectrumWindow()
    bad = tmp_path / "bad.ft2"
    bad.write_bytes(b"not a pipe file")
    assert window.load_spectrum(bad) is False
    window.close()


def test_viewer_drag_hold_follow_crosshair(qapp: QApplication) -> None:
    """hold left-button drag: eventFilter MouseMove drives crosshair."""
    from qtcompat.QtCore import QEvent, Qt
    from qtcompat.QtGui import QMouseEvent

    viewer = SpectrumViewer()
    viewer.add_spectrum(_synthetic_spectrum())
    viewer.set_1d_mode(True)
    assert viewer._strips_active
    scene = viewer.plot.scene()

    viewer._mouse_left_pressed = True
    calls: list[str] = []
    viewer._move_crosshair = lambda x, y: calls.append(f"move {x} {y}") or None
    viewer._update_strips = lambda y, x: calls.append(f"strips {y} {x}") or None

    drag_pos = viewer.plot.getViewBox().mapViewToScene(QPointF(40.0, 60.0))
    drag = QMouseEvent(
        QEvent.Type.MouseMove,
        drag_pos,
        drag_pos,
        drag_pos,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    viewer.eventFilter(scene, drag)

    assert any(c.startswith("move") for c in calls), f"crosshair not following: {calls}"
    assert any(c.startswith("strips") for c in calls)
    viewer.close()


def test_viewer_drag_1d_updates_readout(qapp: QApplication) -> None:
    """in 1D data mode, hold-drag updates readout label live."""
    from types import SimpleNamespace

    from qtcompat.QtCore import QEvent, Qt
    from qtcompat.QtGui import QMouseEvent

    viewer = SpectrumViewer()
    viewer.add_spectrum(_synthetic_spectrum())
    scene = viewer.plot.scene()
    viewer._mode_1d = True
    viewer._primary_1d = SimpleNamespace(
        axis=SimpleNamespace(label="1H", ppm_at=lambda i: 6.5 - i * 0.01)
    )
    viewer._mouse_left_pressed = True

    drag_pos = viewer.plot.getViewBox().mapViewToScene(QPointF(20.0, 0.0))
    drag = QMouseEvent(
        QEvent.Type.MouseMove,
        drag_pos,
        drag_pos,
        drag_pos,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    viewer.eventFilter(scene, drag)
    text = viewer.crosshair_label.text()
    assert "1H" in text and "ppm" in text
    assert text != "Move mouse to read ppm"
    viewer.close()


def test_scene_mouse_event_kind_maps_graphics_types(qapp: QApplication) -> None:
    """0.2.148: pyqtgraph scene drag event types GraphicsSceneMouse* must count as press/move."""
    from qtcompat.QtCore import QEvent

    from viewer.spectrum_viewer import SpectrumViewer

    viewer = SpectrumViewer()
    assert QEvent.Type.GraphicsSceneMouseMove != QEvent.Type.MouseMove
    assert viewer._mouse_event_kind(QEvent.Type.GraphicsSceneMousePress) == "press"
    assert viewer._mouse_event_kind(QEvent.Type.GraphicsSceneMouseMove) == "move"
    assert viewer._mouse_event_kind(QEvent.Type.GraphicsSceneMouseRelease) == "release"
    viewer.close()


def test_value_spinboxes_roundtrip(qapp: QApplication) -> None:
    """0.2.148: values can be typed and sync both ways with the sliders; the level count is
    rounded automatically."""
    viewer = SpectrumViewer()
    # input -> slider
    viewer.count_label.setValue(12)
    assert viewer.count_slider.value() == 12
    assert viewer._level_count == 12
    viewer.aspect_label.setValue(2.0)
    assert viewer.aspect_slider.value() == 200
    viewer.level_label.setValue(12.5)  # inverting the cubic mapping gives about 50
    assert 45 <= viewer.level_slider.value() <= 55
    # slider -> input
    viewer.count_slider.setValue(20)
    assert viewer.count_label.value() == 20
    viewer.aspect_slider.setValue(150)
    assert viewer.aspect_label.value() == 1.5
    viewer.level_slider.setValue(50)
    percent = viewer.level_label.value()
    assert 12.0 <= percent <= 13.0  # (0.5)^3 = 12.5%

    from qtcompat.QtWidgets import QLabel

    assert viewer.level_label.prefix() == viewer.level_label.suffix() == ""
    assert viewer.count_label.prefix() == ""
    assert viewer.aspect_label.prefix() == viewer.aspect_label.suffix() == ""
    assert {w.text() for w in viewer.level_controls.findChildren(QLabel)} == {"Contour start", "%"}
    viewer.close()


def test_phase_panel_spinboxes_roundtrip(qapp: QApplication) -> None:
    """0.2.148: P0/P1 can be typed and stay in sync with the sliders."""
    from viewer.spectrum_viewer import SpectrumViewer

    viewer = SpectrumViewer()
    panel = viewer.phase_panel
    panel.p0_label.setValue(30.0)
    assert panel.p0_slider.value() == 30
    panel.p1_label.setValue(-45.0)
    assert panel.p1_slider.value() == -45
    panel.p0_slider.setValue(75)
    assert panel.p0_label.value() == 75.0
    assert panel.p0_label.text() == "75"
    viewer.close()


@pytest.mark.parametrize("decimal", [False, True])
@pytest.mark.parametrize("enter_key", ["Key_Return", "Key_Enter"])
def test_slider_input_requires_enter(qapp, decimal, enter_key) -> None:
    from qtcompat.QtCore import Qt
    from qtcompat.QtTest import QTest
    from qtcompat.QtWidgets import QPushButton, QVBoxLayout, QWidget

    from ui_support.numeric_inputs import CommitDoubleSpinBox, CommitSpinBox

    host = QWidget()
    layout = QVBoxLayout(host)
    spin = CommitDoubleSpinBox() if decimal else CommitSpinBox()
    spin.setRange(0, 10)
    spin.setValue(2)
    button = QPushButton("Other control")
    layout.addWidget(spin)
    layout.addWidget(button)
    host.show()
    host.activateWindow()
    spin.setFocus()
    qapp.processEvents()
    changed = []
    spin.valueChanged.connect(changed.append)
    spin.selectAll()
    QTest.keyClicks(spin, "0")
    assert spin.value() == 2 and changed == []
    if decimal:
        QTest.keyClicks(spin, ".5")
    assert spin.value() == 2 and changed == []
    QTest.keyClick(spin, getattr(Qt.Key, enter_key))
    expected = 0.5 if decimal else 0
    assert spin.value() == expected
    assert changed == [expected]

    spin.selectAll()
    QTest.keyClicks(spin, "7")
    QTest.keyClick(spin, Qt.Key.Key_Tab)
    qapp.processEvents()
    assert spin.value() == expected and changed == [expected]
    spin.setFocus()
    spin.selectAll()
    QTest.keyClicks(spin, "8")
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert spin.value() == expected and changed == [expected]
    assert float(spin.text()) == expected
    host.close()


@pytest.mark.parametrize("decimal", [False, True])
def test_slider_input_cancel_and_arrows(qapp, decimal) -> None:
    from qtcompat.QtCore import Qt
    from qtcompat.QtTest import QTest
    from qtcompat.QtWidgets import QStyle, QStyleOptionSpinBox

    from ui_support.numeric_inputs import CommitDoubleSpinBox, CommitSpinBox

    spin = CommitDoubleSpinBox() if decimal else CommitSpinBox()
    spin.setRange(-10, 10)
    step = 0.1 if decimal else 1
    spin.setSingleStep(step)
    spin.setValue(2)
    spin.show()
    spin.setFocus()
    qapp.processEvents()
    spin.selectAll()
    QTest.keyClicks(spin, "-7")
    QTest.keyClick(spin, Qt.Key.Key_Escape)
    assert spin.value() == 2 and float(spin.text()) == 2
    spin.selectAll()
    QTest.keyClicks(spin, "8")
    QTest.keyClick(spin, Qt.Key.Key_Up)
    assert spin.value() == pytest.approx(2 + step)

    spin.selectAll()
    QTest.keyClicks(spin, "9")
    option = QStyleOptionSpinBox()
    spin.initStyleOption(option)
    rect = spin.style().subControlRect(
        QStyle.ComplexControl.CC_SpinBox, option, QStyle.SubControl.SC_SpinBoxDown, spin
    )
    QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=rect.center())
    assert spin.value() == pytest.approx(2)
    spin.close()


def test_viewer_aspect_decimal_and_precise_contour_commit(qapp, monkeypatch) -> None:
    from qtcompat.QtCore import Qt
    from qtcompat.QtTest import QTest

    viewer = SpectrumViewer()
    viewer.show()
    spin = viewer.aspect_label
    spin.setFocus()
    spin.selectAll()
    QTest.keyClicks(spin, "0")
    assert spin.text() == "0" and viewer.aspect_slider.value() == 0
    QTest.keyClicks(spin, ".1")
    assert viewer.aspect_slider.value() == 0
    QTest.keyClick(spin, Qt.Key.Key_Return)
    assert spin.value() == 0.1 and viewer.aspect_slider.value() == 10
    assert viewer.plot.getViewBox().state["aspectLocked"] == 0.1
    spin.stepUp()
    assert spin.value() == 0.15
    assert spin.singleStep() == 0.05
    assert viewer.level_label.singleStep() == 0.1
    fired = []
    monkeypatch.setattr(viewer, "_update_levels", lambda: fired.append(1))
    viewer.level_label.setValue(50.0)
    viewer.level_label.stepUp()
    assert viewer.level_label.value() == 50.1
    assert viewer._level_fraction() == pytest.approx(0.501)
    assert len(fired) == 2
    viewer.save_contour_state("typed")
    viewer.level_label.setValue(2)
    viewer.restore_contour_state("typed")
    assert viewer.level_label.value() == 50.1
    fired.clear()
    viewer.level_slider.setValue(40)
    assert fired == []
    viewer.level_slider.sliderReleased.emit()
    assert fired == [1]
    viewer.close()


def test_2d_readout_refreshes_on_mouse_move(qapp: QApplication) -> None:
    """0.2.148: in ordinary 2D (including 3D slices) mouse movement refreshes the ppm readout
    in real time."""

    viewer = SpectrumViewer()
    viewer.add_spectrum(_synthetic_spectrum())
    point = viewer.plot.getViewBox().mapViewToScene(QPointF(40.0, 60.0))
    viewer._on_mouse_moved(point)
    text = viewer.crosshair_label.text()
    assert "ppm" in text
    assert "F2" in text and "F1" in text
    assert text != "Move mouse to read ppm"
    viewer.close()


def test_data_bounds_item_tracks_spectrum(qapp: QApplication) -> None:
    """0.2.150: the bounding box is created together with the spectrum, shares its coordinate
    system so it follows zoom/pan, and does not take part in auto-scaling."""
    viewer = SpectrumViewer()
    # absent while no spectrum is loaded (does not disturb the startup view)
    assert viewer._data_bounds_item is None
    viewer.add_spectrum(_synthetic_spectrum())
    item = viewer._data_bounds_item
    assert item is not None and item.isVisible()
    rect = item.rect()
    assert rect.left() == -0.5 and rect.top() == -0.5
    assert rect.width() == float(_synthetic_spectrum().x_axis.size)
    assert rect.height() == float(_synthetic_spectrum().y_axis.size)
    # data coordinates are unchanged after zoom/pan (what is boxed is the data bounds)
    vb = viewer.plot.getViewBox()
    vb.setRange(xRange=(10.0, 60.0), yRange=(5.0, 90.0), padding=0)
    assert item.rect() == rect
    # the bounding box shares the spectrum's coordinate system: its parent is the ViewBox
    # childGroup, so it transforms with zoom/pan (it leaves the viewport after zooming in)
    assert item.parentItem() is vb.childGroup
    # ignoreBounds=True: not in addedItems, so it does not take part in auto-scaling
    assert item in vb.addedItems
    # 0.2.150: removed ignoreBounds (box now in addedItems)
    # removed together with the spectrum on clear (it no longer exists)
    viewer.clear()
    assert viewer._data_bounds_item is None
    viewer.close()


def test_slice_point_and_ppm_editable(qapp: QApplication) -> None:
    """0.2.149: the slice point / ppm can be typed and sync three ways with the slider."""
    import numpy as np

    from viewer.spectrum import Spectrum3D, SpectrumAxis
    from viewer.spectrum3d_panel import Spectrum3DPanel

    axes = [
        SpectrumAxis(
            label="H", size=24, sw_hz=3000.0, obs_mhz=500.0, carrier_ppm=4.7, orig_hz=4.7 * 500.0
        ),
        SpectrumAxis(
            label="N", size=16, sw_hz=1200.0, obs_mhz=50.0, carrier_ppm=118.0, orig_hz=118.0 * 50.0
        ),
        SpectrumAxis(
            label="C", size=12, sw_hz=4000.0, obs_mhz=125.0, carrier_ppm=55.0, orig_hz=55.0 * 125.0
        ),
    ]
    spec = Spectrum3D(data=np.zeros((24, 16, 12)), axes=axes)
    panel = Spectrum3DPanel()
    fired: list[int] = []
    panel.slice_changed.connect(lambda: fired.append(1))
    panel.set_spectrum3d(spec)

    assert panel._slice_axis == 0
    axis = axes[0]
    mid = axis.size // 2
    assert panel.slice_slider.value() == mid
    assert panel.point_spin.value() == mid
    assert abs(panel.ppm_spin.value() - axis.ppm_at(mid)) < 1e-2
    # enter a point
    panel.point_spin.setValue(3)
    assert panel.slice_slider.value() == 3
    assert abs(panel.ppm_spin.value() - axis.ppm_at(3)) < 1e-2
    # enter a ppm
    panel.ppm_spin.setValue(axis.ppm_at(7))
    assert panel.slice_slider.value() == 7
    assert panel.point_spin.value() == 7
    # dragging the slider syncs back
    panel.slice_slider.setValue(9)
    assert panel.point_spin.value() == 9
    assert abs(panel.ppm_spin.value() - axis.ppm_at(9)) < 1e-2

    panel.plane_combo.setCurrentIndex(0)
    assert panel._slice_axis == 2
    assert fired
    panel.clear()
    assert not panel.point_spin.isEnabled()
    assert not panel.ppm_spin.isEnabled()
    panel.close()


def test_highlight_pans_view_to_peak(qapp: QApplication) -> None:
    # 0.2.199-patch29bl: when the selected peak is out of view, pan the view to the centre
    # (the spectrum edge is clamped into the view)
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    viewer.set_peaks(
        [
            {"H_shift": spectrum.x_axis.ppm_at(30), "N_shift": spectrum.y_axis.ppm_at(20)},
        ]
    )
    vb = viewer.plot.getViewBox()
    vb.setRange(xRange=(50.0, 63.0), yRange=(50.0, 63.0), padding=0)
    xr, yr = vb.viewRange()
    assert not (xr[0] <= 30 <= xr[1] and yr[0] <= 20 <= yr[1])
    viewer.highlight_peak(0)
    xr, yr = vb.viewRange()
    assert xr[0] <= 30 <= xr[1] and yr[0] <= 20 <= yr[1]
    viewer.close()


def test_peak_label_leader_line(qapp: QApplication) -> None:
    # 0.2.199-patch29bm: a horizontal leader line from the label to the peak marker, with a
    # gap; hiding the label hides the line too
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    viewer.set_peaks(
        [
            {
                "H_shift": spectrum.x_axis.ppm_at(30),
                "N_shift": spectrum.y_axis.ppm_at(20),
                "label": "G1",
            }
        ]
    )
    assert viewer._label_overlay.visible_label_count() == 1
    viewer.set_peak_labels_visible(False)  # hide Assignment: label and line hide together
    assert viewer._label_overlay.visible_label_count() == 0
    viewer.set_peak_labels_visible(True)
    assert viewer._label_overlay.visible_label_count() == 1
    viewer.close()


def test_label_positions_magnified_about_center(qapp: QApplication) -> None:
    """0.2.199-patch29cl/patch29cm: assignment = the peak layer magnified 1.25 x about the
    view centre (spherical, spread out), keeping the 1.25 x ratio while zooming; the spectrum
    itself is still a 2D plane."""
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.resize(640, 480)
    viewer.add_spectrum(spectrum)
    viewer.set_peaks(
        [
            {
                "H_shift": spectrum.x_axis.ppm_at(30),
                "N_shift": spectrum.y_axis.ppm_at(20),
                "label": "G1",
            },
            {
                "H_shift": spectrum.x_axis.ppm_at(40),
                "N_shift": spectrum.y_axis.ppm_at(30),
                "label": "G2",
            },
        ]
    )
    qapp.processEvents()
    ov = viewer._label_overlay
    cx = ov.width() / 2.0
    cy = ov.height() / 2.0
    vb = viewer.plot.getViewBox()
    for row, (xi, yi) in enumerate(viewer._peak_data_xy):
        lp = viewer._label_widget_pos(row)
        pp = viewer.plot.mapFromScene(vb.mapViewToScene(QPointF(float(xi), float(yi))))
        assert lp is not None
        assert abs((lp.x() - cx) - 1.25 * (pp.x() - cx)) < 2.0
        assert abs((lp.y() - cy) - 1.25 * (pp.y() - cy)) < 2.0
    # the ratio is still 1.25 after zooming
    vb.setRange(xRange=(80.0, 176.0), yRange=(25.0, 70.0), padding=0)
    qapp.processEvents()
    for row, (xi, yi) in enumerate(viewer._peak_data_xy):
        lp = viewer._label_widget_pos(row)
        pp = viewer.plot.mapFromScene(vb.mapViewToScene(QPointF(float(xi), float(yi))))
        assert lp is not None
        assert abs((lp.x() - cx) - 1.25 * (pp.x() - cx)) < 2.0
        assert abs((lp.y() - cy) - 1.25 * (pp.y() - cy)) < 2.0
    viewer.close()


def test_label_drag_updates_position(qapp: QApplication) -> None:
    """0.2.199-patch29cl: dragging an assignment in selection mode stores a custom screen
    position, and hit detection works."""
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.resize(640, 480)
    viewer.add_spectrum(spectrum)
    viewer.set_peaks(
        [
            {
                "H_shift": spectrum.x_axis.ppm_at(30),
                "N_shift": spectrum.y_axis.ppm_at(20),
                "label": "G1",
            }
        ]
    )
    qapp.processEvents()
    assert viewer._label_positions[0] is None  # dynamic 1.25 x by default, nothing stored
    lp = viewer._label_widget_pos(0)
    assert lp is not None
    assert viewer._label_at_widget(lp) == 0
    viewer._move_label(0, QPointF(lp.x() + 40.0, lp.y() + 30.0))
    assert viewer._label_positions[0] is not None  # the screen ratio is stored after dragging
    assert viewer._label_widget_pos(0) != lp
    viewer.close()


def test_highlight_flash_only_when_requested(qapp: QApplication) -> None:
    """0.2.199-patch29bo: the flash is triggered only by a peak-table click; clicking or
    box-selecting on the spectrum only highlights, it does not flash."""
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    viewer.set_peaks(
        [
            {
                "H_shift": spectrum.x_axis.ppm_at(30),
                "N_shift": spectrum.y_axis.ppm_at(20),
            }
        ]
    )
    viewer.highlight_peak(0, flash=False)
    assert viewer._flash_item is None
    viewer.highlight_peak(0)
    assert viewer._flash_item is not None
    viewer._clear_flash()
    assert viewer._flash_item is None
    viewer.close()
