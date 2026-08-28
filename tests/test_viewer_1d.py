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


def _write_fid2d(path: Path, data: np.ndarray) -> None:
    """写二维时域 FID(FDDIMCOUNT=2:行=间接维增量,列=直接维时点)。"""
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
    assert not loaded.ppm_valid  # 时间域 FID 不用 ppm 轴
    np.testing.assert_allclose(loaded.x_values(), np.arange(64))
    assert loaded.source == path


def test_spectrum1d_load_from_fid_2d_returns_timedomain_spectrum(
    tmp_path: Path,
) -> None:
    """0.2.78:二维 FID 按 nmrDraw 式显示整块时域图(行=各 FID,列=时点)。"""
    rng = np.random.default_rng(1)
    data = rng.normal(size=(16, 32))
    data[0, 3] += 10000.0  # 个别尖峰:不影响分位数基准
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
    # 等高线默认基准用高分位数,避免被尖峰淹没
    assert 0 < loaded.robust_max < loaded.max_intensity


def test_viewer_fid_2d_window_load(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.78:二维 FID 打开后按 2D 时域图显示,而非第一条 FID 的 1D 迹线。"""
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
    """0.2.87:显示用相位(仅显示不改数据):0°还原,180°反号,90°变化。"""
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
    """0.2.87:相位面板对实数 1D 谱即可用,仅显示调相,不改数据。"""
    from viewer.spectrum import SpectrumAxis

    n = 64
    time = np.arange(n)
    real = np.exp(-time / 20.0) * np.cos(2 * np.pi * time / 8.0)
    spectrum1d = Spectrum1D(real, SpectrumAxis("1H", n, 6000.0, 600.0, 4.7))
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum1d, name="1d")
    assert viewer.phase_panel.p0_slider.isEnabled()
    viewer._on_phase_changed(True)  # 建立显示基线(P0=P1=0)
    _x0, y0 = viewer._plot_1d.getData()
    viewer.phase_panel.set_values(90, 0)
    _x1, y1 = viewer._plot_1d.getData()
    assert not np.allclose(np.asarray(y0), np.asarray(y1))
    # 仅显示调相:原始数据不变
    np.testing.assert_allclose(viewer._primary_1d.data, real)
    viewer.phase_panel.set_values(0, 0)
    _x2, y2 = viewer._plot_1d.getData()
    np.testing.assert_allclose(np.asarray(y2), np.asarray(y0))
    viewer.close()


def test_viewer_1d_strips_toggle_and_update(qapp: QApplication) -> None:
    """一维谱开关:十字线 + 上/右条带,点击位置显示两个一维谱。"""
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
    # 更新十字线位置 → 两个一维迹线
    viewer._update_strips(40, 120)
    _xt, yt = viewer.strip_top_curve.getData()
    np.testing.assert_allclose(np.asarray(yt), spectrum.data[40, :])
    xr, _yr = viewer.strip_right_curve.getData()
    np.testing.assert_allclose(np.asarray(xr), spectrum.data[:, 120])
    # 0.2.133:1D 模式十字虚线需按住左键才跟随;先模拟按下再移动
    viewer._mouse_left_pressed = True
    scene_pt = viewer.plot.getViewBox().mapViewToScene(QPointF(120.0, 40))
    viewer._on_mouse_moved(scene_pt)
    # 松开左键后十字线不再跟随新位置
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


def test_viewer_view_to_data_maps_view_y_to_row(qapp: QApplication) -> None:
    """视图坐标即数据下标:view y=行号,越界返回 (-1,-1)。"""
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
    """峰标记 view y 即数据行(与 contour 一致)。"""
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
    """最小化鼠标事件桩:左键单击(非拖拽)。"""

    def __init__(self, scene_pos: QPointF) -> None:
        self._pos = scene_pos

    def button(self) -> Qt.MouseButton:
        return Qt.MouseButton.LeftButton

    def scenePos(self) -> QPointF:
        return self._pos

    def buttonDownScenePos(self, _button) -> QPointF:
        return self._pos


def test_viewer_plot_click_strips_and_select(qapp: QApplication) -> None:
    """点击数据 (120, 40) 的屏幕位置:条带显示正确行/列,且能选中该峰。"""
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
    """右侧 1D 条带方向与二维谱 Y 轴一致,且主图缩放时 y 范围联动。"""
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    viewer.set_1d_mode(True)
    main_inv = viewer.plot.getViewBox().state["yInverted"]
    right_inv = viewer.strip_right.getViewBox().state["yInverted"]
    assert right_inv == main_inv
    # 主图 Y 缩放 → 右条带 yRange 跟随(与二维谱坐标轴移动/缩放同步)
    viewer.plot.getViewBox().setRange(yRange=(10.0, 40.0), padding=0)
    np.testing.assert_allclose(
        viewer.strip_right.getViewBox().viewRange()[1],
        viewer.plot.getViewBox().viewRange()[1],
    )
    # 右条带数据行 0 与主图同为底部方向(与二维谱 Y 轴一致)
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
    """clear() 统一主图 2D 显示方向(行 0=高 ppm 在底部)。"""
    spectrum = _synthetic_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    viewer.plot.getViewBox().invertY(True)  # 模拟 1D 视图后状态
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
    """模拟 pyqtgraph MouseClickEvent:无 buttonDownScenePos(0.2.199-补29aw)。"""

    def buttonDownScenePos(self, _button):
        raise AttributeError("MouseClickEvent has no attribute 'buttonDownScenePos'")


def test_viewer_plot_click_without_button_down_pos(qapp: QApplication) -> None:
    # 0.2.199-补29aw:真实 MouseClickEvent 无 buttonDownScenePos 不再报错
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
