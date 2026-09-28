"""3D spectrum viewing tests (contract §10): Spectrum3D model + standalone window/panel 3D mode."""

from __future__ import annotations

import logging
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager
from gui.spectrum_panel import SpectrumPanel
from viewer.app import SpectrumWindow
from viewer.spectrum import Spectrum, Spectrum3D, SpectrumAxis
from viewer.spectrum_viewer import SpectrumViewer


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _axis3(label: str, size: int, carrier: float) -> SpectrumAxis:
    return SpectrumAxis(
        label=label,
        size=size,
        sw_hz=6000.0,
        obs_mhz=600.0,
        carrier_ppm=carrier,
        orig_hz=carrier * 600.0,
    )


def _synthetic3d(shape: tuple[int, int, int] = (4, 6, 8)) -> Spectrum3D:
    """Synthetic 3D spectrum: peak at (1, 2, 3), axis labels F1/F2/F3."""
    data = np.zeros(shape, dtype=np.float32)
    data[1, 2, 3] = 500.0
    from scipy.ndimage import gaussian_filter

    data = gaussian_filter(data, sigma=(0.7, 0.7, 0.7))
    axes = [
        _axis3("F1", shape[0], 60.0),
        _axis3("F2", shape[1], 118.0),
        _axis3("F3", shape[2], 4.7),
    ]
    return Spectrum3D(data, axes)


def _write_ft3(path: Path, spectrum3d: Spectrum3D, stream: bool = True) -> None:
    """Write an NMRPipe 3D spectrum: stream file (FDPIPEFLAG=1) or a single
    non-stream file (FDPIPEFLAG=0)."""
    from nmrglue.fileio import pipe

    axes = spectrum3d.axes
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 3
    dic["FDPIPEFLAG"] = 1 if stream else 0
    dic["FDSIZE"] = axes[2].size
    # Stream file: FDSPECNUM = F2 (plane count given by FDF3SIZE); non-stream stores F1*F2
    dic["FDSPECNUM"] = axes[1].size if stream else axes[0].size * axes[1].size
    dic["FDF3SIZE"] = axes[0].size
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDF3QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    for index, prefix in enumerate(("FDF1", "FDF2", "FDF3")):
        axis = axes[index]
        dic[prefix + "T"] = axis.size
        dic[prefix + "SW"] = axis.sw_hz
        dic[prefix + "OBS"] = axis.obs_mhz
        dic[prefix + "CAR"] = axis.carrier_ppm
        dic[prefix + "ORIG"] = axis.orig_hz
    pipe.write(
        str(path), dic, spectrum3d.data.astype(np.float32), overwrite=True
    )


def _write_ft3_ordered(
    path: Path, data: np.ndarray, fddimorder: list[float]
) -> None:
    """Write a 3D stream file with FDDIMORDER.

    data is the natural array in the shape nmrglue reads back
    (FDF3SIZE, FDSPECNUM, FDSIZE) (axis order = reverse storage order;
    FDDIMORDER records the logical dimension number of each axis). FDF1/FDF2/
    FDF3 are the parameter blocks for logical dimensions 1/2/3.
    """
    from nmrglue.fileio import pipe

    nz, ny, nx = data.shape
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 3
    dic["FDPIPEFLAG"] = 1
    dic["FDSIZE"] = nx
    dic["FDSPECNUM"] = ny
    dic["FDF3SIZE"] = nz
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDF3QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDDIMORDER"] = [float(v) for v in fddimorder] + [4.0]
    for i, v in enumerate(fddimorder, start=1):
        dic[f"FDDIMORDER{i}"] = float(v)
    blocks = {  # logical dimension number -> (nucleus, size, SW, OBS, CAR, ORIG)
        1: ("15N", nz, 2189.0, 60.8, 118.0, 100.0 * 60.8),
        2: ("1H", nx, 3000.0, 600.0, 4.7, 6.0 * 600.0),
        3: ("13C", ny, 11300.0, 150.9, 45.0, 40.0 * 150.9),
    }
    for dim in (1, 2, 3):
        prefix = f"FDF{dim}"
        lab, size, sw, obs, car, orig = blocks[dim]
        dic[prefix + "T"] = size
        dic[prefix + "SW"] = sw
        dic[prefix + "OBS"] = obs
        dic[prefix + "CAR"] = car
        dic[prefix + "ORIG"] = orig
        dic[prefix + "LABEL"] = lab
    pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)


def test_load_from_ft3_honors_fddimorder_order_231(tmp_path: Path) -> None:
    """0.2.151: ORDER 2 3 1 files (storage F2,F3,F1) map correctly to logical order.

    Real NMRPipe 3D outputs such as 28.ft3/61.ft3 are ORDER 2 3 1 (natural
    array axis order (F1,F3,F2)); before the fix the viewer paired
    FDF1/FDF2/FDF3 by position, so data and axes were mismatched (parameter
    blocks for axes 1/2 swapped, without warning).
    """
    # Natural array (F1=15N, F3=13C, F2=1H): P[z,y,x] = z*100 + y*10 + x
    nz, ny, nx = 2, 4, 6
    P = np.zeros((nz, ny, nx), dtype=np.float32)
    for z in range(nz):
        for y in range(ny):
            for x in range(nx):
                P[z, y, x] = z * 100 + y * 10 + x
    path = tmp_path / "order231.ft3"
    _write_ft3_ordered(path, P, [2.0, 3.0, 1.0])
    loaded = Spectrum3D.load_from_ft3(
        path, labels=("N", "H", "C"), nuclei=["15N", "1H", "13C"]
    )
    # Logical order (F1=15N, F2=1H, F3=13C): shape (2, 6, 4), data[i,j,k]=P[i,k,j]
    assert loaded.data.shape == (nz, nx, ny)
    assert loaded.data[1, 2, 3] == P[1, 3, 2]
    assert [ax.label for ax in loaded.axes] == ["N", "H", "C"]
    assert [ax.size for ax in loaded.axes] == [nz, nx, ny]
    assert loaded.axes[0].obs_mhz == pytest.approx(60.8)  # 15N
    assert loaded.axes[1].obs_mhz == pytest.approx(600.0)  # 1H
    assert loaded.axes[2].obs_mhz == pytest.approx(150.9)  # 13C
    # Slice: fix F3 (13C) → plane (15N, 1H); data matches logical order
    sl = loaded.slice(2, 1)
    assert sl.data.shape == (nz, nx)
    assert sl.y_axis.label == "N" and sl.x_axis.label == "H"
    np.testing.assert_allclose(sl.data, loaded.data[:, :, 1])


def test_load_from_ft3_without_metadata_reorders_and_labels(
    tmp_path: Path,
) -> None:
    """0.2.152: opening with no metadata reorders by FDDIMORDER and derives labels."""
    nz, ny, nx = 2, 4, 6
    P = np.arange(nz * ny * nx, dtype=np.float32).reshape(nz, ny, nx)
    path = tmp_path / "order231_nomd.ft3"
    _write_ft3_ordered(path, P, [2.0, 3.0, 1.0])
    loaded = Spectrum3D.load_from_ft3(path)
    # ORDER 2 3 1 → logical order (F1=15N, F2=1H, F3=13C), labels from header nuclei
    assert loaded.data.shape == (nz, nx, ny)
    assert [ax.label for ax in loaded.axes] == ["N", "H", "C"]
    assert loaded.axes[0].obs_mhz == pytest.approx(60.8)
    assert loaded.axes[1].obs_mhz == pytest.approx(600.0)
    assert loaded.axes[2].obs_mhz == pytest.approx(150.9)

def _write_ft2(path: Path, data: np.ndarray) -> None:
    """Write a synthetic 2D spectrum (for the "ft3 rejects 2D files" assertion)."""
    from nmrglue.fileio import pipe

    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = data.shape[1]
    dic["FDSPECNUM"] = data.shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    for prefix in ("FDF1", "FDF2"):
        dic[prefix + "SW"] = "6000.0"
        dic[prefix + "OBS"] = "600.0"
        dic[prefix + "CAR"] = "4.7"
        dic[prefix + "ORIG"] = "1000.0"
    pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)


# ----------------------------------------------------------------------
# Spectrum3D model (contract §10.1)
# ----------------------------------------------------------------------
def test_slices_carry_noise_floor(tmp_path: Path) -> None:
    """0.2.199-patch29fw: 3D slice contours are leveled by the slice's own max
    (solid peaks); noise_floor = whole-spectrum noise (same source for lazy and
    full loads), used by the viewer to filter pure noise."""
    rng = np.random.default_rng(7)
    data = rng.normal(0, 1.0, (4, 32, 32)).astype(np.float32)
    data[1, 12:18, 12:18] += 500.0
    axes = [
        _axis3("15N", 4, 118.0),
        _axis3("1H", 32, 4.7),
        _axis3("13C", 32, 40.0),
    ]
    spec = Spectrum3D(data, axes)
    path = tmp_path / "slice_base.ft3"
    _write_ft3(path, spec, stream=True)

    full = Spectrum3D.load_from_ft3(path)
    noise = full._compute_global_noise()
    assert noise is not None and noise > 0
    s0 = full.slice(2, 0)
    s1 = full.slice(2, 1)
    assert abs(s0.noise_floor - 3.0 * noise) < 1e-6
    assert abs(s1.noise_floor - 3.0 * noise) < 1e-6

    lazy = Spectrum3D.load_from_ft3(path, lazy=True)
    noise_l = lazy._compute_global_noise()
    assert noise_l is not None and noise_l > 0
    sl = lazy.slice(2, 0)
    assert abs(sl.noise_floor - 3.0 * noise_l) < 1e-6


def test_load_from_ft3_roundtrip(tmp_path: Path) -> None:
    spectrum3d = _synthetic3d()
    path = tmp_path / "test.ft3"
    _write_ft3(path, spectrum3d)
    loaded = Spectrum3D.load_from_ft3(path)
    assert loaded.data.shape == spectrum3d.data.shape
    np.testing.assert_allclose(loaded.data, spectrum3d.data)
    assert loaded.source == path
    # 0.2.152: without metadata, labels derive from header nuclei (the synthetic
    # file has OBS all 1H → same-nucleus indices); 0.2.199-patch29ah: direct
    # dimensions F3→Hx, F2→Hy, F1→Hz)
    assert [axis.label for axis in loaded.axes] == ["Hz", "Hy", "Hx"]
    assert loaded.max_intensity > 0


def test_load_from_ft3_reshapes_non_stream(tmp_path: Path) -> None:
    """Reshape a non-stream single file (nmgrue reads it back as 2D storage)
    by FDF3SIZE/FDSPECNUM/FDSIZE."""
    spectrum3d = _synthetic3d()
    path = tmp_path / "nostream.ft3"
    _write_ft3(path, spectrum3d, stream=False)
    loaded = Spectrum3D.load_from_ft3(path)
    assert loaded.data.shape == spectrum3d.data.shape
    np.testing.assert_allclose(loaded.data, spectrum3d.data)


def test_load_from_ft3_rejects_2d(tmp_path: Path) -> None:
    path = tmp_path / "two.ft2"
    _write_ft2(path, np.zeros((16, 32)))
    with pytest.raises(ValueError, match="三维"):
        Spectrum3D.load_from_ft3(path)


def test_slice_returns_2d_with_correct_axes() -> None:
    spectrum3d = _synthetic3d()
    # Fix F3 (index 3) → plane F1-F2 (y=F1, x=F2)
    sl = spectrum3d.slice(2, 3)
    assert sl.data.shape == (4, 6)
    assert sl.y_axis.label == "F1" and sl.x_axis.label == "F2"
    assert sl.data[1, 2] > 0  # peak at (1, 2, 3)
    np.testing.assert_allclose(sl.data, spectrum3d.data[:, :, 3])
    # Fix F1 → plane F2-F3
    sl = spectrum3d.slice(0, 1)
    assert sl.data.shape == (6, 8)
    assert sl.y_axis.label == "F2" and sl.x_axis.label == "F3"
    np.testing.assert_allclose(sl.data, spectrum3d.data[1, :, :])
    # Fix F2 → plane F1-F3
    sl = spectrum3d.slice(1, 2)
    assert sl.data.shape == (4, 8)
    assert sl.y_axis.label == "F1" and sl.x_axis.label == "F3"
    np.testing.assert_allclose(sl.data, spectrum3d.data[:, 2, :])


def test_slice_out_of_range_raises() -> None:
    spectrum3d = _synthetic3d()
    with pytest.raises(IndexError):
        spectrum3d.slice(2, 8)
    with pytest.raises(IndexError):
        spectrum3d.slice(0, -1)


def test_project_nmrpipe_thresholded_sum() -> None:
    """0.2.89: nmrPipe projZ projection: zero below threshold, then sum along the axis."""
    spectrum3d = _synthetic3d()
    thresh = 0.5
    proj = spectrum3d.project_nmrpipe(2, thresh)
    assert proj.data.shape == (4, 6)
    expected = np.where(
        np.abs(spectrum3d.data) < thresh, 0.0, spectrum3d.data
    ).sum(axis=2)
    np.testing.assert_allclose(proj.data, expected)


def test_project_modes() -> None:
    spectrum3d = _synthetic3d()
    # MIP along F3 → plane F1-F2
    proj = spectrum3d.project(2, "max")
    assert proj.data.shape == (4, 6)
    assert proj.y_axis.label == "F1" and proj.x_axis.label == "F2"
    np.testing.assert_allclose(proj.data, np.max(spectrum3d.data, axis=2))
    # Sum along F3
    proj = spectrum3d.project(2, "sum")
    np.testing.assert_allclose(proj.data, np.sum(spectrum3d.data, axis=2))
    # Along F1 → plane F2-F3
    proj = spectrum3d.project(0, "max")
    assert proj.data.shape == (6, 8)
    np.testing.assert_allclose(proj.data, np.max(spectrum3d.data, axis=0))


def test_index_at_by_ppm() -> None:
    spectrum3d = _synthetic3d()
    for index in (0, 1, 3):
        assert spectrum3d.index_at(0, spectrum3d.axes[0].ppm_at(index)) == index
    assert spectrum3d.index_at(2, spectrum3d.axes[2].ppm_at(5)) == 5


# ----------------------------------------------------------------------
# 3D control panel
# ----------------------------------------------------------------------
def test_spectrum3d_panel_widget(qapp: QApplication) -> None:
    from viewer.spectrum3d_panel import Spectrum3DPanel

    panel = Spectrum3DPanel()
    panel.set_spectrum3d(_synthetic3d())
    assert panel.slice_axis_label() == "F3"  # default F1-F2 plane, F3 fixed
    # 0.2.133: slice mode only (default), no mode_combo
    assert panel._mode == "slice"
    assert not hasattr(panel, "mode_combo")
    spectrum = panel.current_spectrum()
    assert spectrum is not None and spectrum.data.shape == (4, 6)
    assert panel.slice_slider.isEnabled() is True
    # 0.2.199-patch29da: the slice carries its fixed axis/position for plane filtering
    assert spectrum.slice_axis == 2
    assert spectrum.slice_ppm is not None
    assert spectrum.slice_step_ppm > 0
    # 0.2.199-patch29dj: full fixed-axis range (used for out-of-range checks)
    assert spectrum.slice_ppm_min is not None
    assert spectrum.slice_ppm_max is not None
    assert spectrum.slice_ppm_min <= spectrum.slice_ppm <= spectrum.slice_ppm_max
    panel.close()


def test_viewer_slice_filters_peaks_to_plane(qapp: QApplication) -> None:
    """0.2.199-patch29da: a 3D slice shows only peaks whose fixed-axis
    coordinate lies in the current plane."""
    from viewer.spectrum3d_panel import Spectrum3DPanel
    from viewer.spectrum_viewer import SpectrumViewer

    panel = Spectrum3DPanel()
    panel.set_spectrum3d(_synthetic3d())
    spectrum = panel.current_spectrum()
    on_ppm = float(spectrum.slice_ppm)
    step = float(spectrum.slice_step_ppm)
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    x_ppm = float(spectrum.x_axis.ppm_at(2))
    y_ppm = float(spectrum.y_axis.ppm_at(3))
    viewer.set_peaks(
        [
            {"F1_shift": y_ppm, "F2_shift": x_ppm, "F3_shift": on_ppm},
            {"F1_shift": y_ppm, "F2_shift": x_ppm, "F3_shift": on_ppm + 3.0 * step},
            {"F1_shift": y_ppm, "F2_shift": x_ppm, "F3_shift": on_ppm},
        ]
    )
    assert viewer._visible_peak_rows == {0, 2}
    xy = viewer._peak_data_xy
    assert xy[0][0] == xy[0][0] and xy[2][0] == xy[2][0]  # visible in this plane
    assert xy[1][0] != xy[1][0]  # peaks in other planes hidden (NaN)
    assert viewer._nearest_peak(int(xy[0][0]), int(xy[0][1])) == 0
    viewer.close()
    panel.close()


def test_viewer_slice_shows_peaks_without_axis_coord(
    qapp: QApplication,
) -> None:
    """0.2.199-patch29db: peaks without a fixed-axis coordinate (e.g. a 2D peak
    table) still show in the 3D slice."""
    from viewer.spectrum3d_panel import Spectrum3DPanel
    from viewer.spectrum_viewer import SpectrumViewer

    panel = Spectrum3DPanel()
    panel.set_spectrum3d(_synthetic3d())
    spectrum = panel.current_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    x_ppm = float(spectrum.x_axis.ppm_at(2))
    y_ppm = float(spectrum.y_axis.ppm_at(3))
    # 2D peak table (no F3_shift): cannot filter by plane, so show rather than hide
    viewer.set_peaks(
        [
            {"H_shift": x_ppm, "N_shift": y_ppm, "label": "G1"},
            {"H_shift": x_ppm, "N_shift": y_ppm, "label": "A2"},
        ]
    )
    assert viewer._visible_peak_rows is None or len(viewer._visible_peak_rows) == 2
    assert len(viewer.peak_item.data["x"]) == 2
    assert all(x == x for x in viewer.peak_item.data["x"])
    viewer.close()
    panel.close()


def test_peak_table_click_jumps_3d_slice(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.199-patch29dc: clicking a peak in the 3D peak table jumps to its slice."""
    from core.project import ProjectManager
    from gui.spectrum_panel import SpectrumPanel

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/fake/1")
    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data.id)
    s3d = _synthetic3d()
    panel._spectrum3d_panel.set_spectrum3d(s3d)
    panel._spectrum3d_panel.setVisible(True)
    panel._render_3d_view()
    target = 5
    panel._peaks = [
        {
            "F1_shift": float(s3d.axes[0].ppm_at(1)),
            "F2_shift": float(s3d.axes[1].ppm_at(2)),
            "F3_shift": float(s3d.axes[2].ppm_at(target)),
            "Intensity": 1,
            "SN": 1,
        }
    ]
    panel._populate_peak_table()
    panel.peak_table.selectRow(0)
    panel._on_peak_row_selected()
    assert panel._spectrum3d_panel.slice_slider.value() == target
    # after the jump the peak lies in the current slice plane, visible and flashing
    assert panel.viewer._visible_peak_rows is None or 0 in panel.viewer._visible_peak_rows
    assert panel.viewer._flash_item is not None
    panel.close()


def test_peak_table_click_skips_out_of_range_peak(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.199-patch29de: peaks whose fixed-axis coordinate is out of range or
    zero do not jump or flash, and a log message is emitted."""
    from core.project import ProjectManager
    from gui.spectrum_panel import SpectrumPanel

    manager = ProjectManager.create_project(tmp_path / "proj2", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/fake/1")
    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data.id)
    s3d = _synthetic3d()
    panel._spectrum3d_panel.set_spectrum3d(s3d)
    panel._spectrum3d_panel.setVisible(True)
    panel._render_3d_view()
    logs: list[str] = []
    panel.log_message.connect(logs.append)
    before = panel._spectrum3d_panel.slice_slider.value()
    panel._peaks = [
        {
            "F1_shift": float(s3d.axes[0].ppm_at(1)),
            "F2_shift": float(s3d.axes[1].ppm_at(2)),
            "F3_shift": 0.0,  # invalid fixed-axis (F3) coordinate
        }
    ]
    panel._populate_peak_table()
    panel.peak_table.selectRow(0)
    panel._on_peak_row_selected()
    assert panel._spectrum3d_panel.slice_slider.value() == before
    assert any("无法定位切面" in line or "不在当前谱轴范围" in line for line in logs)
    panel.close()


def test_viewer_slice_shows_out_of_range_peaks(qapp: QApplication) -> None:
    """0.2.199-patch29de: peaks with an out-of-range fixed-axis coordinate are still shown."""
    from viewer.spectrum3d_panel import Spectrum3DPanel
    from viewer.spectrum_viewer import SpectrumViewer

    panel = Spectrum3DPanel()
    panel.set_spectrum3d(_synthetic3d())
    spectrum = panel.current_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    x_ppm = float(spectrum.x_axis.ppm_at(2))
    y_ppm = float(spectrum.y_axis.ppm_at(3))
    viewer.set_peaks(
        [
            {"F1_shift": y_ppm, "F2_shift": x_ppm, "F3_shift": 99999.0},
            {"F1_shift": y_ppm, "F2_shift": x_ppm, "F3_shift": 0.0},
        ]
    )
    assert len(viewer.peak_item.data["x"]) == 2
    assert all(x == x for x in viewer.peak_item.data["x"])
    viewer.close()
    panel.close()


def test_load_from_ft3_lazy_matches_full(tmp_path: Path) -> None:
    """0.2.199-patch29dd: lazy loading (reads only 2D slices) matches full loading."""
    nz, ny, nx = 2, 4, 6
    P = np.zeros((nz, ny, nx), dtype=np.float32)
    for z in range(nz):
        for y in range(ny):
            for x in range(nx):
                P[z, y, x] = z * 100 + y * 10 + x
    path = tmp_path / "o231.ft3"
    _write_ft3_ordered(path, P, [2.0, 3.0, 1.0])  # ORDER 2 3 1 (common in real data)
    full = Spectrum3D.load_from_ft3(path)
    lazy = Spectrum3D.load_from_ft3(path, lazy=True)
    assert getattr(lazy, "_lazy", False) is True
    assert lazy.axes[0].size == full.axes[0].size
    for axis in range(3):
        for index in range(lazy.axes[axis].size):
            a = lazy.slice(axis, index).data
            b = full.slice(axis, index).data
            assert np.allclose(a, b), (axis, index)
    # the lazy object does not read everything: data is a streaming lazy object, not an ndarray
    assert type(lazy.data).__name__ != "ndarray"
    # lazy noise/max-intensity estimates are available
    assert lazy.estimate_noise() >= 0.0
    assert lazy.max_intensity > 0.0


def test_load_from_ft3_lazy_non_stream_falls_back(tmp_path: Path) -> None:
    """0.2.199-patch29dd: non-stream or incomplete-header files fall back to a full load."""
    path = tmp_path / "ns.ft3"
    _write_ft3(path, _synthetic3d(), stream=False)
    loaded = Spectrum3D.load_from_ft3(path, lazy=True)
    assert getattr(loaded, "_lazy", False) is False
    assert loaded.slice(2, 1).data.shape == (4, 6)


# ----------------------------------------------------------------------
# Standalone viewer / spectrum panel
# ----------------------------------------------------------------------
def test_spectrum_window_3d_mode(
    tmp_path: Path, qapp: QApplication
) -> None:
    path = tmp_path / "3d.ft3"
    _write_ft3(path, _synthetic3d())
    window = SpectrumWindow()
    assert window.load_spectrum(path) is True
    assert window._spectrum3d_active is True
    assert not window._spectrum3d_panel.isHidden()
    # 0.2.133: slice mode only (default): the spectrum appears on open
    assert window._spectrum3d_panel._mode == "slice"
    assert window.viewer.layer_list.count() == 1
    # Switch to the F2-F3 plane
    window._spectrum3d_panel.plane_combo.setCurrentIndex(2)
    assert window.viewer.layer_list.count() == 1
    # Moving the slider refreshes the slice
    assert window._spectrum3d_panel.slice_slider.isEnabled() is True
    window._spectrum3d_panel.slice_slider.setValue(5)
    window._spectrum3d_panel.refresh()
    assert window.viewer.layer_list.count() == 1
    # Loading a 2D spectrum exits 3D mode
    ft2 = tmp_path / "2d.ft2"
    _write_ft2(ft2, np.zeros((16, 32)))
    assert window.load_spectrum(ft2) is True
    assert window._spectrum3d_active is False
    assert window._spectrum3d_panel.isHidden()
    window.close()


def test_spectrum_panel_opens_ft3(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("3D")
    data = manager.import_data(entry.id, "/fake/3d")
    spectra = manager.data_dir(entry.id, data.id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    ft3 = spectra / f"{data.id}.ft3"
    _write_ft3(ft3, _synthetic3d())
    # Task E: three projection files (proj3D output, prefix d_001) for the panel to load
    for logical in ("F1", "F2", "F3"):
        _write_ft2(
            spectra / f"{data.id}_proj_{logical}.ft2",
            np.zeros((4, 6)),
        )
    manager.save()

    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data.id)
    assert panel.load_current_spectrum() is True
    assert panel._current_spectrum == ft3
    assert panel.viewer.layer_list.count() == 1
    assert not panel._spectrum3d_panel.isHidden()
    # 0.2.133: 3D defaults to slice mode (projection files open directly from the list)
    assert panel._spectrum3d_panel._mode == "slice"
    # 3D peak-table column linkage
    peaks = manager.data_dir(entry.id, data.id, "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / f"{entry.id}-{data.id}.csv").write_text(
        "Peak_ID,F1_shift,F2_shift,F3_shift,Intensity,SN,label\n"
        "1,60.0,118.0,4.7,100,20,G1\n",
        encoding="utf-8",
    )
    panel._load_peaks(ft3)
    assert panel.peak_table.rowCount() == 1
    headers = [
        panel.peak_table.horizontalHeaderItem(i).text()
        for i in range(panel.peak_table.columnCount())
    ]
    # 0.2.199-patch29df/patch29dg: 3D column names now use nucleus names
    # (N_shift style) instead of F1/F2/F3
    shift_cols = [h for h in headers if h.endswith("_shift")]
    assert len(shift_cols) == 3
    assert "F1_shift" not in headers
    assert len(panel.viewer._peaks) == 1
    panel.close()


def test_spectrum_panel_open_corrupt_ft3_returns_false(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    manager.import_data(entry.id, "/fake")
    panel = SpectrumPanel(manager)
    bad = tmp_path / "bad.ft3"
    bad.write_bytes(b"not a pipe file")
    assert panel.open_spectrum(bad) is False
    panel.close()


def test_viewer_peak_xy_3d_mapping(qapp: QApplication) -> None:
    """A 3D peak table (F1/F2/F3_shift) maps by the current slice's axis
    labels; the 2D H/N fallback is unchanged."""
    spectrum3d = _synthetic3d()
    viewer = SpectrumViewer()
    sl = spectrum3d.slice(2, 3)  # plane F1-F2
    sl.dim_indices = (0, 1)  # matches spectrum3d_panel.current_spectrum
    viewer.add_spectrum(sl)
    x_ppm, y_ppm = viewer._peak_xy(
        {
            "F1_shift": sl.y_axis.ppm_at(1),
            "F2_shift": sl.x_axis.ppm_at(2),
        }
    )
    assert sl.x_axis.index_at(x_ppm) == 2
    assert sl.y_axis.index_at(y_ppm) == 1
    # 2D peak table H/N fallback
    x_ppm, y_ppm = viewer._peak_xy(
        {
            "H_shift": sl.x_axis.ppm_at(2),
            "N_shift": sl.y_axis.ppm_at(1),
        }
    )
    assert sl.x_axis.index_at(x_ppm) == 2
    assert sl.y_axis.index_at(y_ppm) == 1
    # F2-F3 plane
    viewer2 = SpectrumViewer()
    sl2 = spectrum3d.slice(0, 1)
    sl2.dim_indices = (1, 2)  # matches spectrum3d_panel.current_spectrum
    viewer2.add_spectrum(sl2)
    x2, y2 = viewer2._peak_xy(
        {
            "F2_shift": sl2.y_axis.ppm_at(2),
            "F3_shift": sl2.x_axis.ppm_at(3),
        }
    )
    assert sl2.x_axis.index_at(x2) == 3
    assert sl2.y_axis.index_at(y2) == 2
    viewer.close()
    viewer2.close()


def _misordered_dic_and_data() -> tuple[dict, np.ndarray]:
    """ft3 header and data with storage axis order (15N, 1H, 13C) (logical F2/F3/F1)."""
    data = np.zeros((20, 40, 30), dtype=np.float32)
    dic = {
        "FDDIMCOUNT": 3,
        "FDF1T": 20, "FDF1SW": 1703.0, "FDF1OBS": 81.1, "FDF1CAR": 117.5,
        "FDF1ORIG": 117.5 * 81.1, "FDF1LABEL": "N15",
        "FDF2T": 40, "FDF2SW": 6000.0, "FDF2OBS": 600.1, "FDF2CAR": 4.7,
        "FDF2ORIG": 4.7 * 600.1, "FDF2LABEL": "H1",
        "FDF3T": 30, "FDF3SW": 3000.0, "FDF3OBS": 150.9, "FDF3CAR": 117.0,
        "FDF3ORIG": 117.0 * 150.9, "FDF3LABEL": "C13",
    }
    return dic, data


def test_load_from_ft3_without_fddimorder_keeps_storage_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """0.2.199-patch29dh: without FDDIMORDER in the header, storage order is
    kept and labels come from header nuclei; metadata no longer reorders as a
    fallback (user: the software has an axis-reordering step, so the metadata
    acquisition order cannot serve as a fallback)."""
    dic, data = _misordered_dic_and_data()
    monkeypatch.setattr("nmrglue.pipe.read", lambda path: (dic, data))
    with caplog.at_level(logging.INFO, logger="nmrforge.viewer.spectrum"):
        spec = Spectrum3D.load_from_ft3(
            tmp_path / "61.ft3", labels=("C", "N", "H"),
            nuclei=["13C", "15N", "1H"],
        )
    # storage order (FDF1=15N, FDF2=1H, FDF3=13C): no reordering, labels from header nuclei N,H,C
    assert [a.label for a in spec.axes] == ["N", "H", "C"]
    assert spec.data.shape == (20, 40, 30)
    assert round(spec.axes[0].obs_mhz, 1) == 81.1
    assert round(spec.axes[1].obs_mhz, 1) == 600.1
    assert round(spec.axes[2].obs_mhz, 1) == 150.9


def test_load_from_ft3_warns_ppm_range_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """0.2.122: self-check warning when nucleus/ppm ranges disagree (storage
    order is kept without metadata)."""
    data = np.zeros((8, 8, 8), dtype=np.float32)
    dic = {
        "FDDIMCOUNT": 3,
        "FDF1T": 8, "FDF1SW": 600.0, "FDF1OBS": 81.0, "FDF1CAR": 4.7,
        "FDF1ORIG": 4.7 * 81.0, "FDF1LABEL": "N15",
        "FDF2T": 8, "FDF2SW": 6000.0, "FDF2OBS": 600.0, "FDF2CAR": 4.7,
        "FDF2ORIG": 4.7 * 600.0, "FDF2LABEL": "H1",
        "FDF3T": 8, "FDF3SW": 6000.0, "FDF3OBS": 600.0, "FDF3CAR": 4.7,
        "FDF3ORIG": 4.7 * 600.0, "FDF3LABEL": "H1",
    }
    monkeypatch.setattr("nmrglue.pipe.read", lambda path: (dic, data))
    with caplog.at_level(logging.WARNING, logger="nmrforge.viewer.spectrum"):
        Spectrum3D.load_from_ft3(tmp_path / "x.ft3")
    assert "轴序/引用自检" in caplog.text


def test_load_from_ft2_without_fddimorder_keeps_storage_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """0.2.199-patch29dh: without FDDIMORDER in a 2D header, storage order is
    kept and labels come from header nuclei; metadata no longer reorders as a
    fallback (user decision)."""
    data = np.zeros((40, 20), dtype=np.float32)
    dic = {
        "FDDIMCOUNT": 2,
        "FDF1T": 40, "FDF1SW": 6000.0, "FDF1OBS": 600.0, "FDF1CAR": 4.7,
        "FDF1ORIG": 4.7 * 600.0, "FDF1LABEL": "H1",
        "FDF2T": 20, "FDF2SW": 1703.0, "FDF2OBS": 60.8, "FDF2CAR": 117.0,
        "FDF2ORIG": 117.0 * 60.8, "FDF2LABEL": "N15",
    }
    monkeypatch.setattr("nmrglue.pipe.read", lambda path: (dic, data))
    with caplog.at_level(logging.INFO, logger="nmrforge.viewer.spectrum"):
        spec = Spectrum.load_from_ft2(
            tmp_path / "x.ft2", labels=("N", "H"), nuclei=["15N", "1H"]
        )
    # storage order (FDF1=1H, FDF2=15N): no reordering; load_from_ft2 does no
    # display orientation, so the raw axis order is x=F2(15N), y=F1(1H); the
    # display layer transposes via orient_x_priority
    assert spec.data.shape == (40, 20)
    assert spec.y_axis.label == "H"
    assert spec.x_axis.label == "N"
    assert round(spec.y_axis.obs_mhz, 1) == 600.0

def test_3d_panel_slice_only() -> None:
    '''0.2.133: the 3D panel keeps slices only; no MIP/Sum/projection modes.'''
    from viewer.spectrum3d_panel import Spectrum3DPanel

    panel = Spectrum3DPanel()
    assert panel._mode == "slice"
    assert not hasattr(panel, "mode_combo")
    assert not hasattr(panel, "set_projections")
    axes = [_axis3("C", 8, 40.0), _axis3("N", 8, 118.0), _axis3("H", 8, 4.7)]
    spec3d = Spectrum3D(np.zeros((8, 8, 8), dtype=float), axes)
    panel.set_spectrum3d(spec3d)
    out = panel.current_spectrum()
    assert out is not None
    assert out.data.shape == (8, 8)
    panel.close()


def test_load_projections_new_naming(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.133: projections load under the new {data_id}_{nucleusA}-{nucleusB}.ft2
    naming, with nuclei parsed from the file name."""
    import numpy as np

    from core.project import ProjectManager
    from gui.spectrum_panel import SpectrumPanel

    def _write_ft2(path, data):
        from nmrglue.fileio import pipe
        dic = {k: '0' for k in pipe.fdata_dic}
        dic['FDMAGIC'] = 9.2330230000000007e14
        dic['FDDIMCOUNT'] = 2
        dic['FDSIZE'] = data.shape[1]
        dic['FDSPECNUM'] = data.shape[0]
        dic['FDQUADFLAG'] = 1
        dic['FDF1QUADFLAG'] = 1
        dic['FDF2QUADFLAG'] = 1
        for i, prefix in enumerate(('FDF1', 'FDF2')):
            dic[prefix + 'SW'] = 6000.0
            dic[prefix + 'OBS'] = 600.0
            dic[prefix + 'CAR'] = 4.7
            dic[prefix + 'ORIG'] = 4.7 * 600.0
            dic[prefix + 'LABEL'] = '1H'
        pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)

    manager = ProjectManager.create_project(tmp_path / 'proj_proj', 'demo')
    entry = manager.create_experiment('3D')
    data = manager.import_data(entry.id, '/fake/3d')
    spectra = manager.data_dir(entry.id, data.id, 'spectra')
    spectra.mkdir(parents=True, exist_ok=True)

    meta_path = manager.data_metadata_path(entry.id, data.id)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        '{"dataset": {"dimensions": ['
        '{"logical_axis": "F1", "nucleus": "13C", "sf": 150.9},'
        '{"logical_axis": "F2", "nucleus": "15N", "sf": 60.8},'
        '{"logical_axis": "F3", "nucleus": "1H", "sf": 600.1}'
        ']}}', encoding='utf-8')

    # file name = {data_id}_{nucleusA}-{nucleusB}.ft2 (A = X axis/column, B = Y axis/row)
    _write_ft2(spectra / f'{data.id}_15N-1H.ft2', np.zeros((8, 16)))
    _write_ft2(spectra / f'{data.id}_13C-1H.ft2', np.zeros((16, 8)))
    _write_ft2(spectra / f'{data.id}_13C-15N.ft2', np.zeros((8, 8)))

    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data.id)
    proj = panel._load_3d_projections()
    assert len(proj) == 3, f'expected 3, got {len(proj)}: {list(proj.keys())}'
    # 0.2.133: the nucleus with the smaller ppm goes on the x axis (carrier/
    # reference ordering, transposed when needed)
    # 15N-1H plane: fixed axis = 13C (F1, index 0); x=1H(H) < 15N(N)
    s0 = proj[0]
    assert s0.x_axis.label == 'H', f'x label {s0.x_axis.label}'
    assert s0.y_axis.label == 'N', f'y label {s0.y_axis.label}'
    assert s0.data.shape == (16, 8)
    # 13C-1H plane: fixed axis = 15N (F2, index 1); x=1H(H) < 13C(C)
    s1 = proj[1]
    assert s1.x_axis.label == 'H', f'x label {s1.x_axis.label}'
    assert s1.y_axis.label == 'C', f'y label {s1.y_axis.label}'
    assert s1.data.shape == (8, 16)
    # 13C-15N plane: fixed axis = 1H (F3, index 2); since 0.2.153 the x-axis
    # priority is H > N > C → x=15N(N) > 13C(C)
    s2 = proj[2]
    assert s2.x_axis.label == 'N'
    assert s2.y_axis.label == 'C'
    assert s2.data.shape == (8, 8)
    panel.close()


def test_contour_state_memory(monkeypatch, tmp_path: Path, qapp: QApplication) -> None:
    """0.2.133: contour state persists per spectrum path."""
    from viewer.spectrum_viewer import SpectrumViewer

    viewer = SpectrumViewer()
    assert viewer.level_slider.value() == 31
    assert viewer._level_count == 8
    viewer.level_slider.setValue(50)
    viewer.count_slider.setValue(16)
    viewer.save_contour_state('spec1')
    viewer.restore_contour_state('spec2')
    assert viewer.level_slider.value() == 31
    assert viewer._level_count == 8
    viewer.restore_contour_state('spec1')
    assert viewer.level_slider.value() == 50
    assert viewer._level_count == 16
    viewer.close()



def test_slice_orientation_x_priority_h_n_c(tmp_path: Path) -> None:
    """0.2.153: 3D slice x axes are oriented H > N > C (transposed when needed)."""
    nz, ny, nx = 2, 4, 6
    P = np.zeros((nz, ny, nx), dtype=np.float32)
    for z in range(nz):
        for y in range(ny):
            for x in range(nx):
                P[z, y, x] = z * 100 + y * 10 + x
    path = tmp_path / "orient.ft3"
    _write_ft3_ordered(path, P, [2.0, 3.0, 1.0])
    loaded = Spectrum3D.load_from_ft3(
        path, labels=("N", "H", "C"), nuclei=["15N", "1H", "13C"]
    )
    # F1-F2 plane (F3 fixed): (N, H) → H is already on x, no transpose
    sl = loaded.slice(2, 1)
    assert sl.y_axis.label == "N" and sl.x_axis.label == "H"
    np.testing.assert_allclose(sl.data, loaded.data[:, :, 1])
    # F1-F3 plane (F2 fixed): (N, C) → N > C, so N is on x after transposing
    sl = loaded.slice(1, 2)
    assert sl.y_axis.label == "C" and sl.x_axis.label == "N"
    np.testing.assert_allclose(sl.data, loaded.data[:, 2, :].T)
    # F2-F3 plane (F1 fixed): (H, C) → H > C, so H is on x after transposing
    sl = loaded.slice(0, 1)
    assert sl.y_axis.label == "C" and sl.x_axis.label == "H"
    np.testing.assert_allclose(sl.data, loaded.data[1, :, :].T)


def test_load_from_ft3_prefers_header_order_over_metadata(
    tmp_path: Path,
) -> None:
    """0.2.199-patch29dh: a complete header wins for reordering; conflicting
    metadata does not override it.

    Reproduces a real HNCA: metadata (Bruker acquisition order) gives
    F1=13C/F2=15N/F3=1H=CNH, while the .ft3 header ORDER 2 3 1 gives NHC --
    the viewer must show N,H,C, consistent with the peak table (same source as
    pick_peaks).
    """
    nz, ny, nx = 2, 4, 6
    P = np.arange(nz * ny * nx, dtype=np.float32).reshape(nz, ny, nx)
    path = tmp_path / "conflict.ft3"
    _write_ft3_ordered(path, P, [2.0, 3.0, 1.0])
    loaded = Spectrum3D.load_from_ft3(
        path, labels=("C", "N", "H"), nuclei=["13C", "15N", "1H"]
    )
    # header ORDER 2 3 1 → logical (F1=15N, F2=1H, F3=13C); metadata CNH has no effect
    assert [ax.label for ax in loaded.axes] == ["N", "H", "C"]
    assert loaded.axes[0].obs_mhz == pytest.approx(60.8)
    assert loaded.axes[1].obs_mhz == pytest.approx(600.0)
    assert loaded.axes[2].obs_mhz == pytest.approx(150.9)
    assert loaded.data.shape == (nz, nx, ny)

def test_control_panel_spans_full_row(qapp: QApplication) -> None:
    """0.2.199-patch29di: add_control_panel spans the full control row, leaving no gap."""
    from qtcompat.QtWidgets import QWidget

    from viewer.spectrum_viewer import SpectrumViewer

    viewer = SpectrumViewer()
    panel = QWidget()
    viewer.add_control_panel(panel)
    index = viewer.controls_layout.indexOf(panel)
    assert index >= 0
    _row, _col, _row_span, col_span = viewer.controls_layout.getItemPosition(
        index
    )
    # The control area is a 3-column grid; the panel must span the whole row
    # (the old implementation used column 0 only, leaving the right side empty)
    assert viewer.controls_layout.columnCount() >= 3
    assert col_span == viewer.controls_layout.columnCount()
    viewer.close()

def test_viewer_slice_hides_peaks_far_in_axis_range(
    qapp: QApplication,
) -> None:
    """0.2.199-patch29dj: peaks whose fixed-axis coordinate is inside the axis
    range but far from the current slice must be hidden.

    The old "show out-of-range peaks anyway" rule used the current slice ±10
    steps, so almost every peak outside this plane counted as out of range (one
    slice showed peaks from all layers); with the full fixed-axis range, only
    peaks beyond the whole axis (stale picks/bad values) are shown anyway.
    """
    from viewer.spectrum import Spectrum, SpectrumAxis
    from viewer.spectrum_viewer import SpectrumViewer

    x_axis = SpectrumAxis(
        label="H", size=256, sw_hz=3000.0, obs_mhz=600.0,
        carrier_ppm=8.0, orig_hz=8.0 * 600.0,
    )
    y_axis = SpectrumAxis(
        label="C", size=256, sw_hz=11300.0, obs_mhz=150.9,
        carrier_ppm=45.0, orig_hz=45.0 * 150.9,
    )
    import numpy as np

    spec = Spectrum(np.zeros((256, 256)), [y_axis, x_axis], source="x.ft3")
    spec.slice_axis = 0  # F1 (15N) fixed
    spec.slice_ppm = 117.0
    spec.slice_step_ppm = 0.05
    spec.slice_ppm_min = 105.0
    spec.slice_ppm_max = 129.0
    viewer = SpectrumViewer()
    viewer.add_spectrum(spec)
    x_ppm = float(x_axis.ppm_at(100))
    y_ppm = float(y_axis.ppm_at(100))
    viewer.set_peaks(
        [
            {"F1_shift": 117.0, "F2_shift": x_ppm, "F3_shift": y_ppm},  # this plane
            {"F1_shift": 125.0, "F2_shift": x_ppm, "F3_shift": y_ppm},  # far within the axis
            {"F1_shift": 50.0, "F2_shift": x_ppm, "F3_shift": y_ppm},  # beyond the whole axis
        ]
    )
    assert viewer._visible_peak_rows == {0, 2}
    xy = viewer._peak_data_xy
    assert xy[0][0] == xy[0][0] and xy[2][0] == xy[2][0]  # in-plane + beyond-axis visible
    assert xy[1][0] != xy[1][0]  # far from the current slice within the axis: hidden (NaN)
    viewer.close()

