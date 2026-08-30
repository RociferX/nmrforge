"""3D 谱查看测试(契约 §10):Spectrum3D 模型 + 独立窗口/面板 3D 模式。"""

from __future__ import annotations

import logging
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

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
    """合成 3D 谱:峰位于 (1, 2, 3),三个轴标签 F1/F2/F3。"""
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
    """写 NMRPipe 3D 谱:流文件(FDPIPEFLAG=1)或非流单文件(FDPIPEFLAG=0)。"""
    from nmrglue.fileio import pipe

    axes = spectrum3d.axes
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 3
    dic["FDPIPEFLAG"] = 1 if stream else 0
    dic["FDSIZE"] = axes[2].size
    # 流文件 FDSPECNUM = F2(planes 数由 FDF3SIZE 给定);非流存储为 F1*F2
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
    """写带 FDDIMORDER 的 3D 流文件。

    data 为 nmrglue 读回形状 (FDF3SIZE, FDSPECNUM, FDSIZE) 的自然数组
    (轴序 = 存储序反转;FDDIMORDER 记录每轴对应的逻辑维号)。FDF1/FDF2/
    FDF3 分别为逻辑维 1/2/3 的参数块。
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
    blocks = {  # 逻辑维号 -> (核, 点数, SW, OBS, CAR, ORIG)
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
    """0.2.151:ORDER 2 3 1(存储 F2,F3,F1)文件正确映射到逻辑序。

    28.ft3/61.ft3 等真实 NMRPipe 3D 输出即 ORDER 2 3 1(自然数组轴序
    (F1,F3,F2));修复前 viewer 按位置配 FDF1/FDF2/FDF3,数据-轴对应
    错位(轴 1/2 参数块互换且无告警)。
    """
    # 自然数组 (F1=15N, F3=13C, F2=1H):P[z,y,x] = z*100 + y*10 + x
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
    # 逻辑序 (F1=15N, F2=1H, F3=13C):形状 (2, 6, 4),data[i,j,k]=P[i,k,j]
    assert loaded.data.shape == (nz, nx, ny)
    assert loaded.data[1, 2, 3] == P[1, 3, 2]
    assert [ax.label for ax in loaded.axes] == ["N", "H", "C"]
    assert [ax.size for ax in loaded.axes] == [nz, nx, ny]
    assert loaded.axes[0].obs_mhz == pytest.approx(60.8)  # 15N
    assert loaded.axes[1].obs_mhz == pytest.approx(600.0)  # 1H
    assert loaded.axes[2].obs_mhz == pytest.approx(150.9)  # 13C
    # 切片:固定 F3(13C) → 平面 (15N, 1H),数据与逻辑序一致
    sl = loaded.slice(2, 1)
    assert sl.data.shape == (nz, nx)
    assert sl.y_axis.label == "N" and sl.x_axis.label == "H"
    np.testing.assert_allclose(sl.data, loaded.data[:, :, 1])


def test_load_from_ft3_without_metadata_reorders_and_labels(
    tmp_path: Path,
) -> None:
    """0.2.152:无 metadata 直接打开时,按 FDDIMORDER 重排逻辑序并推导标签。"""
    nz, ny, nx = 2, 4, 6
    P = np.arange(nz * ny * nx, dtype=np.float32).reshape(nz, ny, nx)
    path = tmp_path / "order231_nomd.ft3"
    _write_ft3_ordered(path, P, [2.0, 3.0, 1.0])
    loaded = Spectrum3D.load_from_ft3(path)
    # ORDER 2 3 1 → 逻辑序 (F1=15N, F2=1H, F3=13C),标签由头部核推导
    assert loaded.data.shape == (nz, nx, ny)
    assert [ax.label for ax in loaded.axes] == ["N", "H", "C"]
    assert loaded.axes[0].obs_mhz == pytest.approx(60.8)
    assert loaded.axes[1].obs_mhz == pytest.approx(600.0)
    assert loaded.axes[2].obs_mhz == pytest.approx(150.9)

def _write_ft2(path: Path, data: np.ndarray) -> None:
    """写合成 2D 谱(供「ft3 拒绝 2D 文件」断言)。"""
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
# Spectrum3D 模型(契约 §10.1)
# ----------------------------------------------------------------------
def test_load_from_ft3_roundtrip(tmp_path: Path) -> None:
    spectrum3d = _synthetic3d()
    path = tmp_path / "test.ft3"
    _write_ft3(path, spectrum3d)
    loaded = Spectrum3D.load_from_ft3(path)
    assert loaded.data.shape == spectrum3d.data.shape
    np.testing.assert_allclose(loaded.data, spectrum3d.data)
    assert loaded.source == path
    # 0.2.152:无 metadata 时按头部核推导标签(合成文件 OBS 全 1H → 同核下标;
    # 0.2.199-补29ah:直接维 F3→Hx、F2→Hy、F1→Hz)
    assert [axis.label for axis in loaded.axes] == ["Hz", "Hy", "Hx"]
    assert loaded.max_intensity > 0


def test_load_from_ft3_reshapes_non_stream(tmp_path: Path) -> None:
    """非流单文件(nmgrue 读回二维存储)按 FDF3SIZE/FDSPECNUM/FDSIZE 重塑。"""
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
    # 固定 F3(index 3)→ 平面 F1-F2(y=F1, x=F2)
    sl = spectrum3d.slice(2, 3)
    assert sl.data.shape == (4, 6)
    assert sl.y_axis.label == "F1" and sl.x_axis.label == "F2"
    assert sl.data[1, 2] > 0  # 峰位于 (1, 2, 3)
    np.testing.assert_allclose(sl.data, spectrum3d.data[:, :, 3])
    # 固定 F1 → 平面 F2-F3
    sl = spectrum3d.slice(0, 1)
    assert sl.data.shape == (6, 8)
    assert sl.y_axis.label == "F2" and sl.x_axis.label == "F3"
    np.testing.assert_allclose(sl.data, spectrum3d.data[1, :, :])
    # 固定 F2 → 平面 F1-F3
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
    """0.2.89:nmrPipe projZ 式投影:低于阈值置零后沿轴求和。"""
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
    # MIP 沿 F3 → 平面 F1-F2
    proj = spectrum3d.project(2, "max")
    assert proj.data.shape == (4, 6)
    assert proj.y_axis.label == "F1" and proj.x_axis.label == "F2"
    np.testing.assert_allclose(proj.data, np.max(spectrum3d.data, axis=2))
    # 求和沿 F3
    proj = spectrum3d.project(2, "sum")
    np.testing.assert_allclose(proj.data, np.sum(spectrum3d.data, axis=2))
    # 沿 F1 → 平面 F2-F3
    proj = spectrum3d.project(0, "max")
    assert proj.data.shape == (6, 8)
    np.testing.assert_allclose(proj.data, np.max(spectrum3d.data, axis=0))


def test_index_at_by_ppm() -> None:
    spectrum3d = _synthetic3d()
    for index in (0, 1, 3):
        assert spectrum3d.index_at(0, spectrum3d.axes[0].ppm_at(index)) == index
    assert spectrum3d.index_at(2, spectrum3d.axes[2].ppm_at(5)) == 5


# ----------------------------------------------------------------------
# 3D 控制面板
# ----------------------------------------------------------------------
def test_spectrum3d_panel_widget(qapp: QApplication) -> None:
    from viewer.spectrum3d_panel import Spectrum3DPanel

    panel = Spectrum3DPanel()
    panel.set_spectrum3d(_synthetic3d())
    assert panel.slice_axis_label() == "F3"  # 默认 F1-F2 平面,固定 F3
    # 0.2.133:仅 slice 模式(默认),无 mode_combo
    assert panel._mode == "slice"
    assert not hasattr(panel, "mode_combo")
    spectrum = panel.current_spectrum()
    assert spectrum is not None and spectrum.data.shape == (4, 6)
    assert panel.slice_slider.isEnabled() is True
    # 0.2.199-补29da:切片携带固定轴/位置,供峰按平面过滤
    assert spectrum.slice_axis == 2
    assert spectrum.slice_ppm is not None
    assert spectrum.slice_step_ppm > 0
    panel.close()


def test_viewer_slice_filters_peaks_to_plane(qapp: QApplication) -> None:
    """0.2.199-补29da:3D 切片只显示固定轴坐标落在当前平面的峰。"""
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
    assert xy[0][0] == xy[0][0] and xy[2][0] == xy[2][0]  # 本平面可见
    assert xy[1][0] != xy[1][0]  # 其它平面峰隐藏(NaN)
    assert viewer._nearest_peak(int(xy[0][0]), int(xy[0][1])) == 0
    viewer.close()
    panel.close()


def test_viewer_slice_shows_peaks_without_axis_coord(
    qapp: QApplication,
) -> None:
    """0.2.199-补29db:缺固定轴坐标的峰(如 2D 峰表)在 3D 切片仍显示。"""
    from viewer.spectrum3d_panel import Spectrum3DPanel
    from viewer.spectrum_viewer import SpectrumViewer

    panel = Spectrum3DPanel()
    panel.set_spectrum3d(_synthetic3d())
    spectrum = panel.current_spectrum()
    viewer = SpectrumViewer()
    viewer.add_spectrum(spectrum)
    x_ppm = float(spectrum.x_axis.ppm_at(2))
    y_ppm = float(spectrum.y_axis.ppm_at(3))
    # 2D 峰表(无 F3_shift):无法按平面过滤,应显示而非全隐藏
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
    """0.2.199-补29dc:3D 峰表点峰跳到该峰对应切面。"""
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
    # 跳转后该峰落在当前切片平面,viewer 可见并闪烁
    assert panel.viewer._visible_peak_rows is None or 0 in panel.viewer._visible_peak_rows
    assert panel.viewer._flash_item is not None
    panel.close()


def test_peak_table_click_skips_out_of_range_peak(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.199-补29de:固定轴坐标越界/为 0 的峰不跳转、不闪、日志提示。"""
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
            "F3_shift": 0.0,  # 固定轴(F3)坐标无效
        }
    ]
    panel._populate_peak_table()
    panel.peak_table.selectRow(0)
    panel._on_peak_row_selected()
    assert panel._spectrum3d_panel.slice_slider.value() == before
    assert any("无法定位切面" in line or "不在当前谱轴范围" in line for line in logs)
    panel.close()


def test_viewer_slice_shows_out_of_range_peaks(qapp: QApplication) -> None:
    """0.2.199-补29de:固定轴坐标越界的峰仍尽力显示,不全隐藏。"""
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
    """0.2.199-补29dd:懒加载(只读 2D 切片)与全量读取切片一致。"""
    nz, ny, nx = 2, 4, 6
    P = np.zeros((nz, ny, nx), dtype=np.float32)
    for z in range(nz):
        for y in range(ny):
            for x in range(nx):
                P[z, y, x] = z * 100 + y * 10 + x
    path = tmp_path / "o231.ft3"
    _write_ft3_ordered(path, P, [2.0, 3.0, 1.0])  # ORDER 2 3 1(真实常见)
    full = Spectrum3D.load_from_ft3(path)
    lazy = Spectrum3D.load_from_ft3(path, lazy=True)
    assert getattr(lazy, "_lazy", False) is True
    assert lazy.axes[0].size == full.axes[0].size
    for axis in range(3):
        for index in range(lazy.axes[axis].size):
            a = lazy.slice(axis, index).data
            b = full.slice(axis, index).data
            assert np.allclose(a, b), (axis, index)
    # 懒对象不读全量:data 是流式懒对象而非普通 ndarray
    assert type(lazy.data).__name__ != "ndarray"
    # 噪声/最大强度懒估计可用
    assert lazy.estimate_noise() >= 0.0
    assert lazy.max_intensity > 0.0


def test_load_from_ft3_lazy_non_stream_falls_back(tmp_path: Path) -> None:
    """0.2.199-补29dd:非流/头部不完整文件懒加载回退全量。"""
    path = tmp_path / "ns.ft3"
    _write_ft3(path, _synthetic3d(), stream=False)
    loaded = Spectrum3D.load_from_ft3(path, lazy=True)
    assert getattr(loaded, "_lazy", False) is False
    assert loaded.slice(2, 1).data.shape == (4, 6)


# ----------------------------------------------------------------------
# 独立查看器 / 谱图面板
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
    # 0.2.133:仅 slice 模式(默认),打开即出谱
    assert window._spectrum3d_panel._mode == "slice"
    assert window.viewer.layer_list.count() == 1
    # 切换平面 F2-F3
    window._spectrum3d_panel.plane_combo.setCurrentIndex(2)
    assert window.viewer.layer_list.count() == 1
    # 移动滑块刷新切片
    assert window._spectrum3d_panel.slice_slider.isEnabled() is True
    window._spectrum3d_panel.slice_slider.setValue(5)
    window._spectrum3d_panel.refresh()
    assert window.viewer.layer_list.count() == 1
    # 加载 2D 后退出 3D 模式
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
    # Task E:三个投影文件(proj3D 产物,前缀 d_001)供面板加载
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
    # 0.2.133:3D 默认切片模式(投影文件由列表直接点开)
    assert panel._spectrum3d_panel._mode == "slice"
    # 峰表 3D 列联动
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
    # 0.2.199-补29df/补29dg:3D 列名改按核名显示(N_shift 风格),不再 F1/F2/F3
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
    """3D 峰表(F1/F2/F3_shift)按当前切片平面轴标签映射;2D H/N 回退不变。"""
    spectrum3d = _synthetic3d()
    viewer = SpectrumViewer()
    sl = spectrum3d.slice(2, 3)  # 平面 F1-F2
    sl.dim_indices = (0, 1)  # 与 spectrum3d_panel.current_spectrum 一致
    viewer.add_spectrum(sl)
    x_ppm, y_ppm = viewer._peak_xy(
        {
            "F1_shift": sl.y_axis.ppm_at(1),
            "F2_shift": sl.x_axis.ppm_at(2),
        }
    )
    assert sl.x_axis.index_at(x_ppm) == 2
    assert sl.y_axis.index_at(y_ppm) == 1
    # 2D 峰表 H/N 回退
    x_ppm, y_ppm = viewer._peak_xy(
        {
            "H_shift": sl.x_axis.ppm_at(2),
            "N_shift": sl.y_axis.ppm_at(1),
        }
    )
    assert sl.x_axis.index_at(x_ppm) == 2
    assert sl.y_axis.index_at(y_ppm) == 1
    # F2-F3 平面
    viewer2 = SpectrumViewer()
    sl2 = spectrum3d.slice(0, 1)
    sl2.dim_indices = (1, 2)  # 与 spectrum3d_panel.current_spectrum 一致
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
    """存储轴序 (15N, 1H, 13C)(对应逻辑 F2/F3/F1)的 ft3 头部与数据。"""
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
    """0.2.199-补29dh:头部无 FDDIMORDER 时保持存储序,标签按头部核生成;
    metadata 不再兜底重排(用户:软件有轴重排过程,metadata 采集序不可作兜底)。"""
    dic, data = _misordered_dic_and_data()
    monkeypatch.setattr("nmrglue.pipe.read", lambda path: (dic, data))
    with caplog.at_level(logging.INFO, logger="nmrforge.viewer.spectrum"):
        spec = Spectrum3D.load_from_ft3(
            tmp_path / "61.ft3", labels=("C", "N", "H"),
            nuclei=["13C", "15N", "1H"],
        )
    # 存储序 (FDF1=15N, FDF2=1H, FDF3=13C):不重排,标签由头部核生成 N,H,C
    assert [a.label for a in spec.axes] == ["N", "H", "C"]
    assert spec.data.shape == (20, 40, 30)
    assert round(spec.axes[0].obs_mhz, 1) == 81.1
    assert round(spec.axes[1].obs_mhz, 1) == 600.1
    assert round(spec.axes[2].obs_mhz, 1) == 150.9


def test_load_from_ft3_warns_ppm_range_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """0.2.122:核/ppm 范围不符时自检告警(无 metadata 时保持存储序)。"""
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
    """0.2.199-补29dh:2D 头部无 FDDIMORDER 时保持存储序,标签按头部核生成;
    metadata 不再兜底重排(用户决策)。"""
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
    # 存储序 (FDF1=1H, FDF2=15N):不重排;load_from_ft2 不做显示定向,
    # 原始轴序 x=F2(15N)、y=F1(1H);显示层由 orient_x_priority 转置
    assert spec.data.shape == (40, 20)
    assert spec.y_axis.label == "H"
    assert spec.x_axis.label == "N"
    assert round(spec.y_axis.obs_mhz, 1) == 600.0

def test_3d_panel_slice_only() -> None:
    '''0.2.133:3D 面板只保留切片(slice),无 MIP/Sum/投影模式。'''
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
    """0.2.133: 投影按 {data_id}_{核A}-{核B}.ft2 新命名加载,核由文件名解析。"""
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

    # 文件名 = {data_id}_{核A}-{核B}.ft2(核A=X 轴/列,核B=Y 轴/行)
    _write_ft2(spectra / f'{data.id}_15N-1H.ft2', np.zeros((8, 16)))
    _write_ft2(spectra / f'{data.id}_13C-1H.ft2', np.zeros((16, 8)))
    _write_ft2(spectra / f'{data.id}_13C-15N.ft2', np.zeros((8, 8)))

    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data.id)
    proj = panel._load_3d_projections()
    assert len(proj) == 3, f'expected 3, got {len(proj)}: {list(proj.keys())}'
    # 0.2.133:ppm 小的核放横坐标(载波/参考表排序,必要时转置)
    # 15N-1H 平面:固定轴 = 13C(F1, 下标 0);x=1H(H) < 15N(N)
    s0 = proj[0]
    assert s0.x_axis.label == 'H', f'x label {s0.x_axis.label}'
    assert s0.y_axis.label == 'N', f'y label {s0.y_axis.label}'
    assert s0.data.shape == (16, 8)
    # 13C-1H 平面:固定轴 = 15N(F2, 下标 1);x=1H(H) < 13C(C)
    s1 = proj[1]
    assert s1.x_axis.label == 'H', f'x label {s1.x_axis.label}'
    assert s1.y_axis.label == 'C', f'y label {s1.y_axis.label}'
    assert s1.data.shape == (8, 16)
    # 13C-15N 平面:固定轴 = 1H(F3, 下标 2);0.2.153 起横坐标优先级
    # H > N > C → x=15N(N) > 13C(C)
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
    """0.2.153:3D 切片横坐标按 H > N > C 定向(必要时转置)。"""
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
    # F1-F2 平面(固定 F3):(N, H) → H 已在横坐标,不转置
    sl = loaded.slice(2, 1)
    assert sl.y_axis.label == "N" and sl.x_axis.label == "H"
    np.testing.assert_allclose(sl.data, loaded.data[:, :, 1])
    # F1-F3 平面(固定 F2):(N, C) → N > C,转置后 N 在横坐标
    sl = loaded.slice(1, 2)
    assert sl.y_axis.label == "C" and sl.x_axis.label == "N"
    np.testing.assert_allclose(sl.data, loaded.data[:, 2, :].T)
    # F2-F3 平面(固定 F1):(H, C) → H > C,转置后 H 在横坐标
    sl = loaded.slice(0, 1)
    assert sl.y_axis.label == "C" and sl.x_axis.label == "H"
    np.testing.assert_allclose(sl.data, loaded.data[1, :, :].T)


def test_load_from_ft3_prefers_header_order_over_metadata(
    tmp_path: Path,
) -> None:
    """0.2.199-补29dh:文件头完整时优先按头部重排,metadata 冲突不覆盖。

    复现真实 HNCA:metadata(Bruker 采集序)F1=13C/F2=15N/F3=1H=CNH,
    而 .ft3 头部 ORDER 2 3 1 给出 NHC——viewer 必须显示 N,H,C,与
    峰表(pick_peaks 同源)一致。
    """
    nz, ny, nx = 2, 4, 6
    P = np.arange(nz * ny * nx, dtype=np.float32).reshape(nz, ny, nx)
    path = tmp_path / "conflict.ft3"
    _write_ft3_ordered(path, P, [2.0, 3.0, 1.0])
    loaded = Spectrum3D.load_from_ft3(
        path, labels=("C", "N", "H"), nuclei=["13C", "15N", "1H"]
    )
    # 头部 ORDER 2 3 1 → 逻辑 (F1=15N, F2=1H, F3=13C),metadata CNH 不生效
    assert [ax.label for ax in loaded.axes] == ["N", "H", "C"]
    assert loaded.axes[0].obs_mhz == pytest.approx(60.8)
    assert loaded.axes[1].obs_mhz == pytest.approx(600.0)
    assert loaded.axes[2].obs_mhz == pytest.approx(150.9)
    assert loaded.data.shape == (nz, nx, ny)

def test_control_panel_spans_full_row(qapp: QApplication) -> None:
    """0.2.199-补29di:add_control_panel 跨满控制区整行,不挤单列留空/撑宽。"""
    from PyQt6.QtWidgets import QWidget

    from viewer.spectrum_viewer import SpectrumViewer

    viewer = SpectrumViewer()
    panel = QWidget()
    viewer.add_control_panel(panel)
    index = viewer.controls_layout.indexOf(panel)
    assert index >= 0
    _row, _col, _row_span, col_span = viewer.controls_layout.getItemPosition(
        index
    )
    # 控制区为 3 列网格;面板必须跨满整行(原实现只占第 0 列 → 右侧留空)
    assert viewer.controls_layout.columnCount() >= 3
    assert col_span == viewer.controls_layout.columnCount()
    viewer.close()

