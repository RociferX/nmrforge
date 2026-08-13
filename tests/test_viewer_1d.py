"""一维谱/FID 查看测试:Spectrum1D、TopSpin 式 1D 条带、图层删除、峰开关。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtWidgets import QApplication

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


def test_spectrum1d_load_from_fid_roundtrip(tmp_path: Path) -> None:
    data = np.linspace(0.0, 1.0, 64)
    path = tmp_path / "test.fid"
    _write_fid(path, data)
    loaded = Spectrum1D.load_from_fid(path)
    assert loaded.data.shape == (64,)
    np.testing.assert_allclose(loaded.data, data)
    assert loaded.axis.size == 64
    assert not loaded.ppm_valid  # 时间域 FID 不用 ppm 轴
    np.testing.assert_allclose(loaded.x_values(), np.arange(64))
    assert loaded.source == path


def test_viewer_1d_strips_toggle_and_update(qapp: QApplication) -> None:
    """一维谱开关:十字线 + 上/右条带,点击位置显示两个一维谱。"""
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    assert viewer.show_1d_button.text() == "一维谱"
    assert not viewer.show_1d_button.isChecked()
    viewer.set_1d_mode(True)
    assert viewer._strips_active
    assert viewer.show_1d_button.isChecked()
    assert not viewer.strip_top.isHidden()
    assert not viewer.strip_right.isHidden()
    # 更新十字线位置 → 两个一维迹线
    viewer._update_strips(40, 120)
    _xt, yt = viewer.strip_top_curve.getData()
    np.testing.assert_allclose(np.asarray(yt), spectrum.data[40, :])
    xr, _yr = viewer.strip_right_curve.getData()
    np.testing.assert_allclose(np.asarray(xr), spectrum.data[:, 120])
    # 鼠标在数据 (120, 40) 处(view y = rows-40):条带应显示 data[40,:] 与 data[:,120]
    scene_pt = viewer.plot.getViewBox().mapViewToScene(
        QPointF(120.0, spectrum.data.shape[0] - 40)
    )
    viewer._on_mouse_moved(scene_pt)
    _xt, yt = viewer.strip_top_curve.getData()
    np.testing.assert_allclose(np.asarray(yt), spectrum.data[40, :])
    xr, _yr = viewer.strip_right_curve.getData()
    np.testing.assert_allclose(np.asarray(xr), spectrum.data[:, 120])
    assert viewer._crosshair_v.pos().x() == pytest.approx(120.0)
    assert viewer._crosshair_h.pos().y() == pytest.approx(
        spectrum.data.shape[0] - 40
    )
    # 关闭:条带与十字线隐藏
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


def test_viewer_view_to_data_y_flip(qapp: QApplication) -> None:
    """视图坐标 → 数据下标:view y=rows-r 对应数据行 r,越界返回 (-1,-1)。"""
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    rows, cols = spectrum.data.shape
    assert viewer._view_to_data(QPointF(120.0, rows - 40)) == (120, 40)
    assert viewer._view_to_data(QPointF(0.0, rows)) == (0, 0)
    assert viewer._view_to_data(QPointF(0.0, 0)) == (-1, -1)
    assert viewer._view_to_data(QPointF(cols + 5, rows - 40)) == (-1, -1)
    viewer.close()


def test_viewer_peaks_y_flip_alignment(qapp: QApplication) -> None:
    """峰标记 view y 应与 contour 一致:数据行 r → view y = rows - r。"""
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    rows = spectrum.data.shape[0]
    viewer.set_peaks(
        [
            {
                "H_shift": spectrum.x_axis.ppm_at(120),
                "N_shift": spectrum.y_axis.ppm_at(40),
            }
        ]
    )
    assert float(viewer.peak_item.data["x"][0]) == 120.0
    assert float(viewer.peak_item.data["y"][0]) == rows - 40
    viewer.close()


class _FakeClickEvent:
    """最小化鼠标事件桩:左键单击(非拖拽)。"""

    def __init__(self, scene_pos: QPointF) -> None:
        self._pos = scene_pos

    def button(self) -> Qt.MouseButton:
        return Qt.MouseButton.LeftButton

    def scenePos(self) -> QPointF:
        return self._pos

    def buttonDownScenePos(self, _button) -> QPointF:
        return self._pos


def test_viewer_plot_click_y_flip(qapp: QApplication) -> None:
    """点击数据 (120, 40) 的屏幕位置:条带显示正确行/列,且能选中该峰。"""
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    rows = spectrum.data.shape[0]
    scene_pt = viewer.plot.getViewBox().mapViewToScene(
        QPointF(120.0, rows - 40)
    )
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
