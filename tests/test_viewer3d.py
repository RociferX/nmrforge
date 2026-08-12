"""3D 谱查看测试(契约 §10):Spectrum3D 模型 + 独立窗口/面板 3D 模式。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from gui.spectrum_panel import SpectrumPanel
from viewer.app import SpectrumWindow
from viewer.spectrum import Spectrum3D, SpectrumAxis
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
    assert [axis.label for axis in loaded.axes] == ["F1", "F2", "F3"]
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
    spectrum = panel.current_spectrum()
    assert spectrum is not None and spectrum.data.shape == (4, 6)
    # 投影模式后滑块禁用
    panel.mode_combo.setCurrentIndex(1)  # MIP 投影
    assert panel.slice_slider.isEnabled() is False
    spectrum = panel.current_spectrum()
    assert spectrum is not None and spectrum.data.shape == (4, 6)
    panel.close()


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
    assert window.viewer.layer_list.count() == 1
    # 切换平面 F2-F3
    window._spectrum3d_panel.plane_combo.setCurrentIndex(2)
    assert window.viewer.layer_list.count() == 1
    # 切换 MIP 投影
    window._spectrum3d_panel.mode_combo.setCurrentIndex(1)
    assert window._spectrum3d_panel.slice_slider.isEnabled() is False
    assert window.viewer.layer_list.count() == 1
    # 回到切片并移动滑块
    window._spectrum3d_panel.mode_combo.setCurrentIndex(0)
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
    ft3 = spectra / f"{entry.id}-{data.id}.ft3"
    _write_ft3(ft3, _synthetic3d())
    manager.save()

    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data.id)
    assert panel._current_spectrum == ft3
    assert panel.viewer.layer_list.count() == 1
    assert not panel._spectrum3d_panel.isHidden()
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
    assert "F1_shift" in headers
    assert "F2_shift" in headers
    assert "F3_shift" in headers
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
