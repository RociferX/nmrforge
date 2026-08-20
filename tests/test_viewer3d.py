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
    # Task E:切片模式出谱
    panel.mode_combo.setCurrentIndex(0)
    spectrum = panel.current_spectrum()
    assert spectrum is not None and spectrum.data.shape == (4, 6)
    # 投影模式(加载文件):未注入时提示,注入后返回投影谱
    panel.mode_combo.setCurrentIndex(1)
    assert panel.slice_slider.isEnabled() is False
    assert panel.current_spectrum() is None
    axes = panel._spectrum3d.axes
    panel.set_projections(
        {2: Spectrum(np.zeros((4, 6)), [axes[0], axes[1]])}
    )
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
    # Task E:默认投影无文件时提示,切到切片后出谱
    window._spectrum3d_panel.mode_combo.setCurrentIndex(0)
    assert window.viewer.layer_list.count() == 1
    # 切换平面 F2-F3
    window._spectrum3d_panel.plane_combo.setCurrentIndex(2)
    assert window.viewer.layer_list.count() == 1
    # 投影模式(加载文件):注入后显示,滑块禁用
    axes = window._spectrum3d_panel._spectrum3d.axes
    window._spectrum3d_panel.set_projections(
        {0: Spectrum(np.zeros((6, 8)), [axes[1], axes[2]])}
    )
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
    # 0.2.89:3D 默认 nmrPipe 式阈值求和投影(Proj)
    assert panel._spectrum3d_panel._mode == "proj"
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


def test_load_from_ft3_reorders_axes_to_logical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """0.2.122:存储轴序 (15N,1H,13C) 重排到逻辑序 (13C,15N,1H)。"""
    dic, data = _misordered_dic_and_data()
    monkeypatch.setattr("nmrglue.pipe.read", lambda path: (dic, data))
    with caplog.at_level(logging.WARNING, logger="nmrforge.viewer.spectrum"):
        spec = Spectrum3D.load_from_ft3(
            tmp_path / "61.ft3", labels=("C", "N", "H"),
            nuclei=["13C", "15N", "1H"],
        )
    assert [a.label for a in spec.axes] == ["C", "N", "H"]
    assert spec.data.shape == (30, 20, 40)  # (13C, 15N, 1H)
    assert round(spec.axes[0].obs_mhz, 1) == 150.9
    assert round(spec.axes[1].obs_mhz, 1) == 81.1
    assert round(spec.axes[2].obs_mhz, 1) == 600.1
    assert "轴序重排" in caplog.text


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


def test_load_from_ft2_reorders_axes_to_logical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """0.2.122:2D 同样按存储头重排到逻辑序(存储 1H/15N → 逻辑 15N/1H)。"""
    data = np.zeros((40, 20), dtype=np.float32)
    dic = {
        "FDDIMCOUNT": 2,
        "FDF1T": 40, "FDF1SW": 6000.0, "FDF1OBS": 600.0, "FDF1CAR": 4.7,
        "FDF1ORIG": 4.7 * 600.0, "FDF1LABEL": "H1",
        "FDF2T": 20, "FDF2SW": 1703.0, "FDF2OBS": 60.8, "FDF2CAR": 117.0,
        "FDF2ORIG": 117.0 * 60.8, "FDF2LABEL": "N15",
    }
    monkeypatch.setattr("nmrglue.pipe.read", lambda path: (dic, data))
    with caplog.at_level(logging.WARNING, logger="nmrforge.viewer.spectrum"):
        spec = Spectrum.load_from_ft2(
            tmp_path / "x.ft2", labels=("N", "H"), nuclei=["15N", "1H"]
        )
    assert spec.data.shape == (20, 40)
    assert spec.y_axis.label == "N"
    assert spec.x_axis.label == "H"
    assert round(spec.y_axis.obs_mhz, 1) == 60.8
    assert "轴序重排" in caplog.text

def test_3d_panel_modes_no_mip_sum() -> None:
    '''Task E:3D 面板删除 MIP/Sum,只保留切片与投影(加载文件)。'''
    from viewer.spectrum3d_panel import _MODES, Spectrum3DPanel

    modes = [m for _, m in _MODES]
    assert "max" not in modes
    assert "sum" not in modes
    assert "slice" in modes
    assert "proj" in modes
    panel = Spectrum3DPanel()
    assert panel.mode_combo.count() == 2


def test_3d_panel_projection_from_files(qapp: QApplication) -> None:
    '''Task E:proj 模式返回 set_projections 注入的投影文件谱;缺失提示不崩溃。'''
    from viewer.spectrum3d_panel import Spectrum3DPanel

    axes = [_axis3("C", 8, 40.0), _axis3("N", 8, 118.0), _axis3("H", 8, 4.7)]
    spec3d = Spectrum3D(np.zeros((8, 8, 8), dtype=float), axes)
    panel = Spectrum3DPanel()
    panel.set_spectrum3d(spec3d)
    # 未注入投影:proj 模式返回 None 并提示,不崩溃
    panel.mode_combo.setCurrentIndex(1)
    assert panel.current_spectrum() is None
    assert "投影未生成" in panel.position_label.text()
    # 注入投影文件谱:返回对应平面的 Spectrum
    proj_axes = [_axis3("N", 8, 118.0), _axis3("H", 8, 4.7)]
    proj = {2: Spectrum(np.zeros((8, 8), dtype=float), proj_axes)}
    panel.set_projections(proj)
    out = panel.current_spectrum()
    assert out is not None
    assert out.data.shape == (8, 8)
    # 切片模式仍可用
    panel.mode_combo.setCurrentIndex(0)
    panel.slice_slider.setEnabled(True)
    assert panel.current_spectrum() is not None


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
    # 15N-1H 平面:固定轴 = 13C(F1, 下标 0);x=15N(N), y=1H(H)
    s0 = proj[0]
    assert s0.x_axis.label == 'N', f'x label {s0.x_axis.label}'
    assert s0.y_axis.label == 'H', f'y label {s0.y_axis.label}'
    assert s0.data.shape == (8, 16)
    # 13C-1H 平面:固定轴 = 15N(F2, 下标 1);x=13C(C), y=1H(H)
    s1 = proj[1]
    assert s1.x_axis.label == 'C', f'x label {s1.x_axis.label}'
    assert s1.y_axis.label == 'H', f'y label {s1.y_axis.label}'
    assert s1.data.shape == (16, 8)
    # 13C-15N 平面:固定轴 = 1H(F3, 下标 2);x=13C(C), y=15N(N)
    s2 = proj[2]
    assert s2.x_axis.label == 'C'
    assert s2.y_axis.label == 'N'
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
