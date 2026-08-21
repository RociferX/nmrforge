"""谱图查看模块测试:ppm 轴、ft2 加载、等高线、查看器与独立窗口(offscreen)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

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
    assert ppm[0] > ppm[-1]  # ppm 随索引递减
    for index in (0, 1, 100, 255):
        assert axis.index_at(axis.ppm_at(index)) == index
    # ORIG 定义时轴末点 ppm = ORIG/OBS
    assert abs(axis.ppm_at(255) - 4.7) < 1e-9


def test_spectrum_axis_fallback_carrier() -> None:
    axis = SpectrumAxis(
        label="F1", size=64, sw_hz=3000.0, obs_mhz=150.0, carrier_ppm=118.0
    )
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
    # 小谱走 matplotlib 等高线(尺寸分流,VM 全量稳定路径,见 CHANGELOG 0.2.73)
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
    # 大谱走 contourpy 真实等值线(POKY/nmrDraw 式细线框,见 CHANGELOG 0.2.75)
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
    # 级数滑块改变后路径重建且级数更新
    old_count = viewer._level_count
    viewer.count_slider.setValue(old_count + 8)
    assert viewer._level_count == old_count + 8
    assert len(viewer.layers[0]._levels) == 2 * (old_count + 8)  # 正负对称
    viewer.reset_view()
    viewer.close()


def test_viewer_contour_defaults_and_english_labels(
    qapp: QApplication,
) -> None:
    """0.2.77:轮廓起点默认 3%、级数默认 8,常用术语英文显示。"""
    viewer = SpectrumViewer()
    assert viewer.level_slider.value() == 31
    assert viewer._level_count == 8
    assert viewer.count_slider.value() == 8
    assert viewer.level_label.text().startswith("Contour start 2.98%")
    # 立方映射:前 10% 阈值占拖动条大部分(比平方更陡)
    viewer.level_slider.setValue(50)
    assert viewer._level_fraction() < 0.15
    viewer.level_slider.setValue(31)
    assert viewer.count_label.text() == "Levels 8"
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
    assert len(viewer.peak_label_items) == 2  # 有标签峰 + 选中峰
    viewer.highlight_peak(0)
    assert float(viewer.peak_item.data["size"][0]) == 16  # 选中放大
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
    """显示约定(用户 0.2.59 反馈):1H 高 ppm 在左(x 列 0 靠左),
15N 高 ppm 在下(y 行 0 靠下)。"""
    import pyqtgraph as pg

    viewer = SpectrumViewer()
    spectrum = _synthetic_spectrum()
    viewer.add_spectrum(spectrum)
    vb = viewer.plot.getViewBox()
    nx, ny = spectrum.data.shape[1], spectrum.data.shape[0]
    p0 = vb.mapViewToScene(pg.QtCore.QPointF(0, 0))
    p1 = vb.mapViewToScene(pg.QtCore.QPointF(nx - 1, 0))
    assert p0.x() < p1.x()  # 列 0(高 ppm)在左
    # view y 即数据行:行 0(高 ppm)= view y=0 在底部,行 ny-1(低 ppm)在顶部
    q0 = vb.mapViewToScene(pg.QtCore.QPointF(0, 0))
    q1 = vb.mapViewToScene(pg.QtCore.QPointF(0, ny - 1))
    assert q0.y() > q1.y()  # 行 0(高 ppm)显示在底部,行 ny-1(低 ppm)在顶部
    viewer.close()


def test_viewer_aspect_ratio(qapp: QApplication) -> None:
    viewer = SpectrumViewer()
    viewer.add_spectrum(_synthetic_spectrum())
    viewer.set_aspect_ratio(2.0)
    assert viewer.plot.getViewBox().state['aspectLocked'] == 2.0
    viewer.set_aspect_ratio(None)
    assert viewer.plot.getViewBox().state['aspectLocked'] is False
    viewer.close()


def test_viewer_aspect_slider(qapp: QApplication) -> None:
    viewer = SpectrumViewer()
    viewer.add_spectrum(_synthetic_spectrum())
    assert viewer.aspect_slider.value() == 100
    assert viewer.plot.getViewBox().state['aspectLocked'] == 1.0
    viewer.aspect_slider.setValue(200)
    assert viewer.plot.getViewBox().state['aspectLocked'] == 2.0
    viewer.aspect_slider.setValue(0)
    assert viewer.plot.getViewBox().state['aspectLocked'] is False
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
    """0.2.133:滚轮/scaleBy 缩小不能越过完整范围,平移不能移出谱图。"""
    from PyQt6.QtCore import QPointF

    viewer = SpectrumViewer()
    viewer.add_spectrum(_synthetic_spectrum((64, 128)))
    vb = viewer.plot.getViewBox()
    full = vb.viewRange()
    full_x_span = full[0][1] - full[0][0]
    # 缩小(滚轮路径 scaleBy)20 次:跨度与位置都必须留在完整范围内
    vb.setRange(xRange=(10, 20), yRange=(10, 20), padding=0)
    for _ in range(20):
        vb.scaleBy((0.9, 0.9), center=QPointF(16.0, 16.0))
    vr = vb.viewRange()
    assert vr[0][1] - vr[0][0] <= full_x_span + 1e-6
    assert vr[0][0] >= full[0][0] - 1e-6
    assert vr[0][1] <= full[0][1] + 1e-6
    # 平移出界:视图被拉回,不能把谱图移出视野
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
    assert not hasattr(vb, 'menu') or vb.menu is None
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
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

    viewer = SpectrumViewer()
    viewer.add_spectrum(_synthetic_spectrum())
    viewer.set_1d_mode(True)
    assert viewer._strips_active
    scene = viewer.plot.scene()
    # ???? scene wrapper(PyQt ???? plot.scene() ???????)

    viewer._mouse_left_pressed = True
    calls: list[str] = []
    viewer._move_crosshair = lambda x, y: calls.append(f"move {x} {y}") or None
    viewer._update_strips = lambda y, x: calls.append(f"strips {y} {x}") or None

    # ?????? 1:1,????? (40, 60) ????????
    drag_pos = viewer.plot.getViewBox().mapViewToScene(QPointF(40.0, 60.0))
    drag = QMouseEvent(
        QEvent.Type.MouseMove,
        drag_pos, drag_pos, drag_pos,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    viewer.eventFilter(scene, drag)

    assert any(c.startswith("move") for c in calls), f"crosshair not following: {calls}"
    assert any(c.startswith("strips") for c in calls)
    viewer.close()


def test_viewer_drag_1d_updates_readout(qapp: QApplication) -> None:
    """in 1D data mode, hold-drag updates readout label live."""
    from types import SimpleNamespace

    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

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
        drag_pos, drag_pos, drag_pos,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    viewer.eventFilter(scene, drag)
    text = viewer.crosshair_label.text()
    assert "1H" in text and "ppm" in text
    assert text != "Move mouse to read ppm"
    viewer.close()

def test_scene_mouse_event_kind_maps_graphics_types(qapp: QApplication) -> None:
    """0.2.148:pyqtgraph 场景着重事件类型 GraphicsSceneMouse* 必须认为按住/移动。"""
    from PyQt6.QtCore import QEvent
    from viewer.spectrum_viewer import SpectrumViewer

    viewer = SpectrumViewer()
    assert QEvent.Type.GraphicsSceneMouseMove != QEvent.Type.MouseMove
    assert viewer._mouse_event_kind(QEvent.Type.GraphicsSceneMousePress) == "press"
    assert viewer._mouse_event_kind(QEvent.Type.GraphicsSceneMouseMove) == "move"
    assert viewer._mouse_event_kind(QEvent.Type.GraphicsSceneMouseRelease) == "release"
    viewer.close()

def test_value_spinboxes_roundtrip(qapp: QApplication) -> None:
    """0.2.148:数值可输入与滑块双向同步;级数自动取整。"""
    viewer = SpectrumViewer()
    # 输入 -> 滑块
    viewer.count_label.setValue(12)
    assert viewer.count_slider.value() == 12
    assert viewer._level_count == 12
    viewer.aspect_label.setValue(2.0)
    assert viewer.aspect_slider.value() == 200
    viewer.level_label.setValue(12.5)  # 百分比反推立方映射约 50
    assert 45 <= viewer.level_slider.value() <= 55
    # 滑块 -> 输入
    viewer.count_slider.setValue(20)
    assert viewer.count_label.value() == 20
    viewer.aspect_slider.setValue(150)
    assert viewer.aspect_label.value() == 1.5
    viewer.level_slider.setValue(50)
    percent = viewer.level_label.value()
    assert 12.0 <= percent <= 13.0  # (0.5)^3 = 12.5%
    # 标题后直接显示数值
    assert viewer.level_label.text().startswith("Contour start ")
    assert viewer.level_label.text().endswith("%")
    assert viewer.count_label.text().startswith("Levels ")
    viewer.close()


def test_phase_panel_spinboxes_roundtrip(qapp: QApplication) -> None:
    """0.2.148:P0/P1 可输入,与滑块同步。"""
    from viewer.spectrum_viewer import SpectrumViewer

    viewer = SpectrumViewer()
    panel = viewer.phase_panel
    panel.p0_label.setValue(30.0)
    assert panel.p0_slider.value() == 30
    panel.p1_label.setValue(-45.0)
    assert panel.p1_slider.value() == -45
    panel.p0_slider.setValue(75)
    assert panel.p0_label.value() == 75.0
    assert panel.p0_label.text() == "P0: 75°"
    viewer.close()


def test_2d_readout_refreshes_on_mouse_move(qapp: QApplication) -> None:
    """0.2.148:普通 2D(含 3D 切片)鼠标移动实时刷新 ppm 读数。"""
    from PyQt6.QtCore import QPointF

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
    """0.2.149:谱图数据范围框根据实际数据大小,不随视图变化。"""
    viewer = SpectrumViewer()
    assert not viewer._data_bounds_item.isVisible()
    viewer.add_spectrum(_synthetic_spectrum())
    item = viewer._data_bounds_item
    assert item.isVisible()
    rect = item.rect()
    assert rect.left() == -0.5 and rect.top() == -0.5
    assert rect.width() == float(_synthetic_spectrum().x_axis.size)
    assert rect.height() == float(_synthetic_spectrum().y_axis.size)
    # 缩放后矩形不变(框的是数据范围,不是视图)
    vb = viewer.plot.getViewBox()
    vb.setRange(xRange=(10.0, 60.0), yRange=(5.0, 90.0), padding=0)
    assert item.rect() == rect
    # 不经 ViewBox 管理:不参与自动缩放计算
    assert item not in vb.addedItems
    viewer.clear()
    assert not item.isVisible()
    viewer.close()


def test_slice_point_and_ppm_editable(qapp: QApplication) -> None:
    """0.2.149:切片 point / ppm 可输入,与滑块三向同步。"""
    import numpy as np

    from viewer.spectrum3d_panel import Spectrum3DPanel
    from viewer.spectrum import Spectrum3D, SpectrumAxis

    axes = [
        SpectrumAxis(label="H", size=24, sw_hz=3000.0, obs_mhz=500.0,
                     carrier_ppm=4.7, orig_hz=4.7 * 500.0),
        SpectrumAxis(label="N", size=16, sw_hz=1200.0, obs_mhz=50.0,
                     carrier_ppm=118.0, orig_hz=118.0 * 50.0),
        SpectrumAxis(label="C", size=12, sw_hz=4000.0, obs_mhz=125.0,
                     carrier_ppm=55.0, orig_hz=55.0 * 125.0),
    ]
    spec = Spectrum3D(data=np.zeros((24, 16, 12)), axes=axes)
    panel = Spectrum3DPanel()
    fired: list[int] = []
    panel.slice_changed.connect(lambda: fired.append(1))
    panel.set_spectrum3d(spec)
    axis = axes[2]  # 默认 F1-F2 平面,固定 F3
    mid = 12 // 2
    assert panel.slice_slider.value() == mid
    assert panel.point_spin.value() == mid
    assert abs(panel.ppm_spin.value() - axis.ppm_at(mid)) < 1e-2
    # 输入 point
    panel.point_spin.setValue(3)
    assert panel.slice_slider.value() == 3
    assert abs(panel.ppm_spin.value() - axis.ppm_at(3)) < 1e-2
    # 输入 ppm
    panel.ppm_spin.setValue(axis.ppm_at(7))
    assert panel.slice_slider.value() == 7
    assert panel.point_spin.value() == 7
    # 滑块拖动反向同步
    panel.slice_slider.setValue(9)
    assert panel.point_spin.value() == 9
    assert abs(panel.ppm_spin.value() - axis.ppm_at(9)) < 1e-2
    assert fired  # set_spectrum3d/输入均触发重绘
    panel.clear()
    assert not panel.point_spin.isEnabled()
    assert not panel.ppm_spin.isEnabled()
    panel.close()

