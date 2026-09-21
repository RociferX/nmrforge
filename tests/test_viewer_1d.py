"""One-dimensional spectrum/FID View tests: Spectrum1D, TopSpin-style 1D striping, layer removal,
peak switching."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from qtcompat.QtCore import QPointF, Qt
from qtcompat.QtWidgets import QApplication

from viewer.app import SpectrumWindow
from viewer.spectrum import Spectrum, Spectrum1D, SpectrumAxis
from viewer.spectrum_viewer import SpectrumViewer


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _axis(
    label: str, size: int, sw: float = 6000.0, car: float = 4.7
) -> SpectrumAxis:
    return SpectrumAxis(
        label=label,
        size=size,
        sw_hz=sw,
        obs_mhz=600.0,
        carrier_ppm=car,
        orig_hz=car * 600.0,
    )


def _synthetic_spectrum(shape: tuple[int, int] = (64, 128)) -> Spectrum:
    rng = np.random.default_rng(0)
    data = np.zeros(shape)
    data[shape[0] * 5 // 8, shape[1] * 15 // 16] = 500.0
    data[shape[0] * 5 // 8 + 5, shape[1] * 13 // 16] = 350.0
    from scipy.ndimage import gaussian_filter

    data = gaussian_filter(data, sigma=(1.5, 1.5))
    data = data + rng.normal(0, 0.05, size=shape)
    return Spectrum(
        data, [_axis("F1", shape[0]), _axis("F2", shape[1])]
    )


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
    for prefix, axis in zip(("FDF1", "FDF2"), axes):
        dic[prefix + "T"] = axis.size
        dic[prefix + "SW"] = axis.sw_hz
        dic[prefix + "OBS"] = axis.obs_mhz
        dic[prefix + "CAR"] = axis.carrier_ppm
        dic[prefix + "ORIG"] = axis.orig_hz
    pipe.write(
        str(path), dic, spectrum.data.astype(np.float32), overwrite=True
    )


def _write_fid(path: Path, data: np.ndarray) -> None:
    from nmrglue.fileio import pipe

    data = np.asarray(data)
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 1
    dic["FDSIZE"] = data.shape[-1]
    dic["FDQUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDF2SW"] = 6000.0
    dic["FDF2OBS"] = 600.0
    dic["FDF2CAR"] = 4.7
    dic["FDF2ORIG"] = 4.7 * 600.0
    pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)


def _write_fid2d(path: Path, data: np.ndarray) -> None:
    """Write two-dimensional time domain FID(FDDIMCOUNT=2: row = indirect dimension increment,
    column = direct dimension time point)."""
    from nmrglue.fileio import pipe

    data = np.asarray(data)
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = data.shape[-1]
    dic["FDSPECNUM"] = data.shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDF1TDSIZE"] = data.shape[0]
    dic["FDF2TDSIZE"] = data.shape[1]
    dic["FDF2SW"] = 6000.0
    dic["FDF2OBS"] = 600.0
    dic["FDF2CAR"] = 4.7
    dic["FDF2ORIG"] = 4.7 * 600.0
    pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)


def test_spectrum1d_load_from_fid_roundtrip(tmp_path: Path) -> None:
    data = np.linspace(0.0, 1.0, 64)
    path = tmp_path / "test.fid"
    _write_fid(path, data)
    loaded = Spectrum1D.load_from_fid(path)
    assert loaded.data.shape == (64,)
    np.testing.assert_allclose(loaded.data, data)
    assert loaded.axis.size == 64
    assert not loaded.ppm_valid  # Time domain FID without ppm axis.
    np.testing.assert_allclose(loaded.x_values(), np.arange(64))
    assert loaded.source == path


def test_spectrum1d_load_from_fid_2d_returns_timedomain_spectrum(
    tmp_path: Path,
) -> None:
    """0.2.78: Two-dimensional FID displays the entire time domain graph in nmrDraw format (rows =
    each FID, columns = time points)."""
    rng = np.random.default_rng(1)
    data = rng.normal(size=(16, 32))
    data[0, 3] += 10000.0  # Individual spikes: do not affect quantile benchmark.
    path = tmp_path / "raw.fid"
    _write_fid2d(path, data)
    loaded = Spectrum1D.load_from_fid(path)
    assert isinstance(loaded, Spectrum)
    assert loaded.data.shape == (16, 32)
    np.testing.assert_allclose(loaded.data, data)
    assert loaded.axes[0].label == "FID"
    assert loaded.axes[1].label == "Points"
    assert loaded.axes[0].size == 16
    assert loaded.axes[1].size == 32
    assert loaded.source == path
    # The default baseline for contour lines uses high quantiles to avoid being overwhelmed by
    # spikes.
    assert 0 < loaded.robust_max < loaded.max_intensity


def test_viewer_fid_2d_window_load(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.78: After opening the two-dimensional FID, it is displayed as a 2D time domain diagram
    instead of the 1D trace of the first FID."""
    rng = np.random.default_rng(2)
    data = rng.normal(size=(16, 64))
    data[0, :] = np.exp(-np.arange(64) / 10.0) * 5000.0
    path = tmp_path / "2d.fid"
    _write_fid2d(path, data)
    window = SpectrumWindow()
    assert window.load_spectrum(path) is True
    assert not window.viewer._mode_1d
    assert len(window.viewer.layers) == 1
    assert isinstance(window.viewer._primary, Spectrum)
    assert window.viewer._primary.data.shape == (16, 64)
    assert "FID" in window.statusBar().currentMessage()
    window.close()


def test_display_phase_math() -> None:
    """0.2.87: Display phase (only display without changing data): 0° restoration, 180° inversion,
    90° change."""
    from viewer.spectrum_viewer import SpectrumViewer

    rng = np.random.default_rng(3)
    real = rng.normal(size=32)
    p0_0 = SpectrumViewer._display_phase(real, 0.0, 0.0)
    np.testing.assert_allclose(p0_0, real)
    p0_180 = SpectrumViewer._display_phase(real, 180.0, 0.0)
    np.testing.assert_allclose(p0_180, -real, atol=1e-9)
    p0_90 = SpectrumViewer._display_phase(real, 90.0, 0.0)
    assert not np.allclose(p0_90, real)


def test_viewer_phase_panel_display_only_real_data(
    qapp: QApplication,
) -> None:
    """0.2.87: The phase panel is available for real 1D spectra. It only displays the phase
    modulation and does not change the data."""
    from viewer.spectrum import SpectrumAxis

    n = 64
    time = np.arange(n)
    real = np.exp(-time / 20.0) * np.cos(2 * np.pi * time / 8.0)
    spectrum1d = Spectrum1D(real, SpectrumAxis("1H", n, 6000.0, 600.0, 4.7))
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum1d, name="1d")
    assert viewer.phase_panel.p0_slider.isEnabled()
    viewer._on_phase_changed(True)  # Create display baseline (P0=P1=0).
    _x0, y0 = viewer._plot_1d.getData()
    viewer.phase_panel.set_values(90, 0)
    _x1, y1 = viewer._plot_1d.getData()
    assert not np.allclose(np.asarray(y0), np.asarray(y1))
    # Only phase modulation is displayed: the original data remains unchanged.
    np.testing.assert_allclose(viewer._primary_1d.data, real)
    viewer.phase_panel.set_values(0, 0)
    _x2, y2 = viewer._plot_1d.getData()
    np.testing.assert_allclose(np.asarray(y2), np.asarray(y0))
    viewer.close()


def test_viewer_1d_hide_then_clear_restore_2d_controls(
    qapp: QApplication,
) -> None:
    """0.2.199-patch29gj-Fixed: After the 1D view hides the contour/aspect control, clear() should
    be restored; otherwise the contour control will remain hidden when switching back to 2D/3D."""
    from viewer.spectrum import SpectrumAxis

    n = 64
    time = np.arange(n)
    real = np.exp(-time / 20.0) * np.cos(2 * np.pi * time / 8.0)
    spectrum1d = Spectrum1D(real, SpectrumAxis("1H", n, 6000.0, 600.0, 4.7))
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum1d, name="1d")
    assert viewer._mode_1d
    assert viewer.level_slider.isHidden()
    assert viewer.aspect_slider.isHidden()
    # Simulate clear() before switching back to 2D/3D (the contour control was not restored before,
    # regression).
    viewer.clear()
    assert not viewer.level_slider.isHidden()
    assert not viewer.aspect_slider.isHidden()
    assert not viewer.count_slider.isHidden()
    assert not viewer.level_label.isHidden()
    viewer.add_spectrum(_synthetic_spectrum(), name="2d")
    assert not viewer.level_slider.isHidden()
    viewer.close()


def test_viewer_1d_strips_toggle_and_update(qapp: QApplication) -> None:
    """One-dimensional spectrum switch: crosshair + superior/right strip, click the position to
    display two one-dimensional spectra."""
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    assert viewer.show_1d_button.text() == "1D"
    assert not viewer.show_1d_button.isChecked()
    viewer.set_1d_mode(True)
    assert viewer._strips_active
    assert viewer.show_1d_button.isChecked()
    assert not viewer.strip_top.isHidden()
    assert not viewer.strip_right.isHidden()
    # 0.2.199-patch29bt:Horizontal strip 170 high, vertical strip 190 wide.
    assert viewer.strip_top.minimumHeight() == viewer.strip_top.maximumHeight() == 170
    assert (
        viewer.strip_right.minimumWidth() == viewer.strip_right.maximumWidth() == 190
    )
    # Update crosshair position -> two 1D traces.
    viewer._update_strips(40, 120)
    _xt, yt = viewer.strip_top_curve.getData()
    np.testing.assert_allclose(np.asarray(yt), spectrum.data[40, :])
    xr, _yr = viewer.strip_right_curve.getData()
    np.testing.assert_allclose(np.asarray(xr), spectrum.data[:, 120])
    # 0.2.133: 1D mode cross dotted line needs to hold down the left button to follow; simulate
    # pressing first and then move.
    viewer._mouse_left_pressed = True
    scene_pt = viewer.plot.getViewBox().mapViewToScene(QPointF(120.0, 40))
    viewer._on_mouse_moved(scene_pt)
    # The crosshair no longer follows the new position after releasing the left button.
    viewer._mouse_left_pressed = False
    scene_pt2 = viewer.plot.getViewBox().mapViewToScene(QPointF(90.0, 30))
    viewer._on_mouse_moved(scene_pt2)
    _xt, yt = viewer.strip_top_curve.getData()
    np.testing.assert_allclose(np.asarray(yt), spectrum.data[40, :])
    assert viewer._crosshair_v.pos().x() == pytest.approx(120.0)
    _xt, yt = viewer.strip_top_curve.getData()
    np.testing.assert_allclose(np.asarray(yt), spectrum.data[40, :])
    xr, _yr = viewer.strip_right_curve.getData()
    np.testing.assert_allclose(np.asarray(xr), spectrum.data[:, 120])
    assert viewer._crosshair_v.pos().x() == pytest.approx(120.0)
    assert viewer._crosshair_h.pos().y() == pytest.approx(40)
    # Off: Strips and crosshairs hidden.
    viewer.set_1d_mode(False)
    assert not viewer._strips_active
    assert viewer.strip_top.isHidden()
    viewer.close()


def test_viewer_fid_window_load(tmp_path: Path, qapp: QApplication) -> None:
    fid_path = tmp_path / "1d.fid"
    _write_fid(fid_path, np.linspace(0.0, 1.0, 64))
    window = SpectrumWindow()
    assert window.load_spectrum(fid_path) is True
    assert window.viewer._mode_1d
    assert "FID" in window.statusBar().currentMessage()
    window.close()


def test_viewer_layer_delete(qapp: QApplication) -> None:
    viewer = SpectrumViewer()
    viewer.add_spectrum(_synthetic_spectrum(), name="A")
    viewer.add_spectrum(_synthetic_spectrum((48, 96)), name="B")
    assert viewer.layer_list.count() == 2
    viewer.remove_layer(0)
    assert viewer.layer_list.count() == 1
    assert viewer.layer_names == ["B"]
    assert len(viewer.layers) == 1
    viewer.remove_layer(0)
    assert viewer.layer_list.count() == 0
    assert viewer._primary is None
    viewer.close()


def test_viewer_view_to_data_maps_view_y_to_row(qapp: QApplication) -> None:
    """View coordinates are data index:view y=row number, out of bounds return (-1,-1)."""
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    rows, cols = spectrum.data.shape
    assert viewer._view_to_data(QPointF(120.0, 40)) == (120, 40)
    assert viewer._view_to_data(QPointF(0.0, 0)) == (0, 0)
    assert viewer._view_to_data(QPointF(0.0, rows)) == (-1, -1)
    assert viewer._view_to_data(QPointF(cols + 5, 40)) == (-1, -1)
    viewer.close()


def test_viewer_peaks_aligned_with_contour(qapp: QApplication) -> None:
    """Peak markers view y are data rows (consistent with contour)."""
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    viewer.set_peaks(
        [
            {
                "H_shift": spectrum.x_axis.ppm_at(120),
                "N_shift": spectrum.y_axis.ppm_at(40),
            }
        ]
    )
    assert float(viewer.peak_item.data["x"][0]) == 120.0
    assert float(viewer.peak_item.data["y"][0]) == 40
    viewer.close()


class _FakeClickEvent:
    """Minimise mouse event stub: left click (not drag)."""

    def __init__(self, scene_pos: QPointF) -> None:
        self._pos = scene_pos

    def button(self) -> Qt.MouseButton:
        return Qt.MouseButton.LeftButton

    def scenePos(self) -> QPointF:
        return self._pos

    def buttonDownScenePos(self, _button) -> QPointF:
        return self._pos


def test_viewer_plot_click_strips_and_select(qapp: QApplication) -> None:
    """Click the screen position of data (120, 40): Strips showing correct rows/List, and the peak
    can be selected."""
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    scene_pt = viewer.plot.getViewBox().mapViewToScene(QPointF(120.0, 40))
    viewer.set_peaks(
        [
            {
                "H_shift": spectrum.x_axis.ppm_at(120),
                "N_shift": spectrum.y_axis.ppm_at(40),
            }
        ]
    )
    viewer.set_1d_mode(True)
    viewer._on_plot_clicked(_FakeClickEvent(scene_pt))
    _xt, yt = viewer.strip_top_curve.getData()
    np.testing.assert_allclose(np.asarray(yt), spectrum.data[40, :])
    xr, _yr = viewer.strip_right_curve.getData()
    np.testing.assert_allclose(np.asarray(xr), spectrum.data[:, 120])
    viewer.set_1d_mode(False)
    viewer._on_plot_clicked(_FakeClickEvent(scene_pt))
    assert viewer._selected_peak == 0
    viewer.close()


def test_viewer_strip_right_y_direction_and_link(qapp: QApplication) -> None:
    """The direction of the 1D strip on the right is consistent with the Y-axis of the two-
    dimensional spectrum, and the y range is linked when the main image is zoomed."""
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    viewer.set_1d_mode(True)
    main_inv = viewer.plot.getViewBox().state["yInverted"]
    right_inv = viewer.strip_right.getViewBox().state["yInverted"]
    assert right_inv == main_inv
    # Main image Y scale -> right strip yRange follow(Moving with the two-dimensional spectral
    # coordinate axis/Zoom sync).
    viewer.plot.getViewBox().setRange(yRange=(10.0, 40.0), padding=0)
    np.testing.assert_allclose(
        viewer.strip_right.getViewBox().viewRange()[1],
        viewer.plot.getViewBox().viewRange()[1],
    )
    # The right strip data line 0 is in the same bottom direction as the main picture (consistent
    # with the Y-axis of the two-dimensional spectrum).
    right_s0 = viewer.strip_right.getViewBox().mapViewToScene(
        QPointF(0.0, 0.0)
    )
    right_s1 = viewer.strip_right.getViewBox().mapViewToScene(
        QPointF(0.0, spectrum.data.shape[0] - 1)
    )
    main_s0 = viewer.plot.getViewBox().mapViewToScene(QPointF(0.0, 0.0))
    main_s1 = viewer.plot.getViewBox().mapViewToScene(
        QPointF(0.0, spectrum.data.shape[0] - 1)
    )
    assert (right_s0.y() < right_s1.y()) == (main_s0.y() < main_s1.y())
    viewer.close()


def test_viewer_clear_restores_2d_direction(qapp: QApplication) -> None:
    """Clear() unifies the main image 2D display direction (row 0=high ppm at bottom)."""
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    viewer.plot.getViewBox().invertY(True)  # Simulate 1D view post state.
    viewer.clear()
    assert viewer.plot.getViewBox().state["yInverted"] is False
    viewer.close()


def test_viewer_peaks_toggle(qapp: QApplication) -> None:
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    viewer.set_peaks(
        [
            {"H_shift": spectrum.x_axis.ppm_at(120), "N_shift": spectrum.y_axis.ppm_at(40)},
            {"H_shift": spectrum.x_axis.ppm_at(110), "N_shift": spectrum.y_axis.ppm_at(45)},
        ]
    )
    assert viewer.peak_item.data["x"].shape == (2,)
    assert viewer.show_peaks_checkbox.isChecked()
    viewer.set_peaks_visible(False)
    assert not viewer.show_peaks_checkbox.isChecked()
    assert viewer.peak_item.data["x"].shape == (0,)
    viewer.set_peaks_visible(True)
    assert viewer.peak_item.data["x"].shape == (2,)
    viewer.close()



class _FakeClickEventNoPress(_FakeClickEvent):
    """Simulate pyqtgraph MouseClickEvent: None buttonDownScenePos(0.2.199-patch29aw)."""

    def buttonDownScenePos(self, _button):
        raise AttributeError("MouseClickEvent has no attribute 'buttonDownScenePos'")


def test_viewer_plot_click_without_button_down_pos(qapp: QApplication) -> None:
    # 0.2.199-patch29aw: True MouseClickEvent None buttonDownScenePos No more errors reported.
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    viewer.set_peaks(
        [
            {
                'H_shift': spectrum.x_axis.ppm_at(60),
                'N_shift': spectrum.y_axis.ppm_at(30),
            }
        ]
    )
    scene_pt = viewer.plot.getViewBox().mapViewToScene(QPointF(60.0, 30))
    viewer._on_plot_clicked(_FakeClickEventNoPress(scene_pt))
    assert viewer._selected_peak == 0
    viewer.close()



def test_box_select_uses_peak_coords_only(qapp: QApplication) -> None:
    # 0.2.199-patch29ay: Frame selection only compares the frame range and cache peak coordinates,
    # and does not perform other operations.
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    viewer.set_peaks(
        [
            {'H_shift': spectrum.x_axis.ppm_at(30), 'N_shift': spectrum.y_axis.ppm_at(20)},
            {'H_shift': spectrum.x_axis.ppm_at(100), 'N_shift': spectrum.y_axis.ppm_at(50)},
            {'H_shift': spectrum.x_axis.ppm_at(80), 'N_shift': spectrum.y_axis.ppm_at(10)},
        ]
    )
    viewer._apply_peak_items()
    assert len(viewer._peak_data_xy) == 3
    vb = viewer.plot.getViewBox()
    viewer._box_press_scene = vb.mapViewToScene(QPointF(25.0, 15.0))
    viewer._finish_box_select(vb.mapViewToScene(QPointF(110.0, 55.0)))
    assert viewer._box_selected_rows == {0, 1}
    viewer.close()



def test_box_select_clamps_to_spectrum_edges(qapp: QApplication) -> None:
    # 0.2.199-patch29bf: When the box selection exceeds the spectrum area, it can still be selected
    # until the edge of the spectrum.
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    viewer.set_peaks(
        [
            {'H_shift': spectrum.x_axis.ppm_at(30), 'N_shift': spectrum.y_axis.ppm_at(20)},
            {'H_shift': spectrum.x_axis.ppm_at(100), 'N_shift': spectrum.y_axis.ppm_at(50)},
        ]
    )
    vb = viewer.plot.getViewBox()
    viewer._box_press_scene = vb.mapViewToScene(QPointF(10.0, 10.0))
    # The release point is far beyond the spectrum range (upper right) and should be cut off to the
    # edge of the spectrum.
    viewer._finish_box_select(vb.mapViewToScene(QPointF(1e6, 1e6)))
    assert viewer._box_selected_rows == {0, 1}
    viewer.close()
