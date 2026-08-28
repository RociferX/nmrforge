"""谱轴核名映射测试:metadata 维度核信息 → H/N/C 轴标签(同核加下标)。"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from viewer.axis_labels import (
    axis_labels_from_nuclei,
    nuclei_from_metadata,
    nucleus_symbol,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def test_infer_nucleus_from_sf() -> None:
    """0.2.89:按观测频率 sf 推断核:600→1H、60.8→15N、150.9→13C。"""
    from viewer.axis_labels import infer_nucleus

    assert infer_nucleus(600.13) == "1H"
    assert infer_nucleus(60.82) == "15N"
    assert infer_nucleus(150.9) == "13C"
    # 0.2.110:1200 MHz(1.2 GHz)系统回归——sampleI 间接维 sf≈121.7 不再误判为 31P
    assert infer_nucleus(121.67) == "15N"
    assert infer_nucleus(301.9) == "13C"
    assert infer_nucleus(1200.58) == "1H"
    # 0.2.111:磁场列表补到 2 GHz(未来更高场谱仪)
    assert infer_nucleus(2000.0) == "1H"
    assert infer_nucleus(202.74) == "15N"  # 2 GHz 系统 15N
    assert infer_nucleus(502.9) == "13C"  # 2 GHz 系统 13C"
    assert infer_nucleus(10) == ""
    assert infer_nucleus(3000) == ""  # 3 GHz 超出列表,判定为空
    assert infer_nucleus(0) == ""


def test_nucleus_symbol() -> None:
    assert nucleus_symbol("1H") == "H"
    assert nucleus_symbol("15N") == "N"
    assert nucleus_symbol("13C") == "C"
    assert nucleus_symbol("31P") == "P"
    assert nucleus_symbol("19F") == "F"
    assert nucleus_symbol("未知核") == "未知核"


def test_axis_labels_from_nuclei() -> None:
    """下标优先级:直接维 > acqu2 > acqu3(逻辑序位置越靠后越优先)。"""
    assert axis_labels_from_nuclei(["15N", "1H"]) == ("N", "H")
    assert axis_labels_from_nuclei(["1H", "1H"]) == ("Hy", "Hx")  # 2D:F2(直接)→Hx、F1→Hy
    assert axis_labels_from_nuclei(["13C", "15N", "1H"]) == ("C", "N", "H")
    assert axis_labels_from_nuclei(["1H", "1H", "15N"]) == ("Hy", "Hx", "N")
    assert axis_labels_from_nuclei(["1H", "1H", "1H"]) == ("Hz", "Hy", "Hx")  # 3D 三同核
    assert axis_labels_from_nuclei(["15N", "15N", "1H"]) == ("Ny", "Nx", "H")  # HNN


def test_nuclei_from_metadata() -> None:
    metadata = {
        "dataset": {
            "dimensions": [
                {"logical_axis": "F2", "nucleus": "1H", "role": "direct"},
                {"logical_axis": "F1", "nucleus": "15N", "role": "indirect"},
            ]
        }
    }
    assert nuclei_from_metadata(metadata) == ["15N", "1H"]
    assert nuclei_from_metadata({}) is None
    assert nuclei_from_metadata({"dataset": {"dimensions": []}}) is None


def _write_ft2(path: Path, shape: tuple[int, int] = (64, 128)) -> None:
    import numpy as np
    from nmrglue.fileio import pipe

    data = np.zeros(shape, dtype=np.float32)
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = shape[1]
    dic["FDSPECNUM"] = shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    for prefix, size in (("FDF1", shape[0]), ("FDF2", shape[1])):
        dic[prefix + "T"] = size
        dic[prefix + "SW"] = 6000.0
        dic[prefix + "OBS"] = 600.0
        dic[prefix + "CAR"] = 4.7
        dic[prefix + "ORIG"] = 4.7 * 600.0
    pipe.write(str(path), dic, data, overwrite=True)


def _metadata_dims() -> dict:
    return {
        "dataset": {
            "dimensions": [
                {"logical_axis": "F2", "nucleus": "1H", "role": "direct"},
                {"logical_axis": "F1", "nucleus": "15N", "role": "indirect"},
            ]
        }
    }


def test_spectrum_panel_uses_nucleus_labels(
    tmp_path: Path, qapp: QApplication
) -> None:
    """打开带 metadata 的数据谱图:轴标签为核名(N-H),而非 F1/F2。"""
    from gui.spectrum_panel import SpectrumPanel

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    exp_id, data_id = entry.id, data.id
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / f"{exp_id}-{data_id}.ft2")
    meta_path = manager.data_metadata_path(exp_id, data_id)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps(_metadata_dims()), encoding="utf-8"
    )
    manager.save()
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    assert panel.load_current_spectrum() is True  # 0.2.88:显式加载
    assert panel.viewer._primary is not None
    assert panel.viewer._primary.x_axis.label == "H"
    assert panel.viewer._primary.y_axis.label == "N"
    panel.close()


def test_spectrum_panel_fallback_labels(
    tmp_path: Path, qapp: QApplication
) -> None:
    """无 metadata 时按头部核推导标签(0.2.152,不再回退 F1/F2)。"""
    from gui.spectrum_panel import SpectrumPanel

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    exp_id, data_id = entry.id, data.id
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / f"{exp_id}-{data_id}.ft2")
    manager.save()
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    assert panel.load_current_spectrum() is True  # 0.2.88:显式加载
    assert panel.viewer._primary is not None
    # 合成文件两轴 OBS 均 600 MHz → 推断为 1H,同核加下标;
    # 直接维 F2 在 x 轴 → Hx、间接维 F1 → Hy(0.2.199-补29ah)
    assert panel.viewer._primary.x_axis.label == "Hx"
    assert panel.viewer._primary.y_axis.label == "Hy"
    panel.close()


def test_projection_same_nucleus_uses_header_and_subscript(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.168:同核投影(H-H 平面)用文件头槽位构建轴参数,标签为
    Hx/Hy 下标(不再按核种类从 3D 谱取错轴)。"""
    from gui.spectrum_panel import SpectrumPanel

    manager = ProjectManager.create_project(tmp_path / "proj3", "demo")
    entry = manager.create_experiment("NOESY-HSQC-15N")
    data = manager.import_data(entry.id, "/fake/1")
    exp_id, data_id = entry.id, data.id
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    proj = spectra / f"{data_id}_H-H.ft2"
    _write_ft2(proj)
    meta_path = manager.data_metadata_path(exp_id, data_id)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps(
            {
                "dataset": {
                    "dimensions": [
                        {"logical_axis": "F3", "nucleus": "1H", "role": "direct"},
                        {"logical_axis": "F2", "nucleus": "15N", "role": "indirect"},
                        {"logical_axis": "F1", "nucleus": "1H", "role": "indirect"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    manager.save()
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    spec = panel._load_projection_ft2(proj)
    assert spec is not None
    assert spec.x_axis.label == "Hx"
    assert spec.y_axis.label == "Hy"
    assert spec.x_axis.obs_mhz == pytest.approx(600.0)
    assert spec.y_axis.obs_mhz == pytest.approx(600.0)
    panel.close()


def test_viewer_app_axis_labels_from_path(
    tmp_path: Path, qapp: QApplication
) -> None:
    """独立查看器从谱图旁 metadata 推断核名。"""
    from viewer.app import SpectrumWindow

    base = tmp_path / "exp_001" / "d_001"
    spectra = base / "spectra"
    spectra.mkdir(parents=True, exist_ok=True)
    ft2 = spectra / "exp_001-d_001.ft2"
    _write_ft2(ft2)
    (base / "metadata.json").write_text(
        json.dumps(_metadata_dims()), encoding="utf-8"
    )
    window = SpectrumWindow()
    assert window.load_spectrum(ft2) is True
    assert window.viewer._primary is not None
    assert window.viewer._primary.x_axis.label == "H"
    assert window.viewer._primary.y_axis.label == "N"
    window.close()

def test_parse_nmrpipe_label_suffix() -> None:
    """0.2.199-补29ai:同核下标标签(15Nx/1Hy/1Hz)可直接解析为核名。"""
    from viewer.spectrum import _parse_nmrpipe_label

    assert _parse_nmrpipe_label("1Hx") == "1H"
    assert _parse_nmrpipe_label("1Hy") == "1H"
    assert _parse_nmrpipe_label("1Hz") == "1H"
    assert _parse_nmrpipe_label("15Nx") == "15N"
    assert _parse_nmrpipe_label("15Ny") == "15N"
    assert _parse_nmrpipe_label("13Cz") == "13C"
    # 原有格式兼容
    assert _parse_nmrpipe_label("1H") == "1H"
    assert _parse_nmrpipe_label("N15") == "15N"
    assert _parse_nmrpipe_label("H1") == "1H"
    assert _parse_nmrpipe_label("C13") == "13C"
    assert _parse_nmrpipe_label("") == ""
    assert _parse_nmrpipe_label("未知") == ""
