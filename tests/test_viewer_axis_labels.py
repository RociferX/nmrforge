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


def test_nucleus_symbol() -> None:
    assert nucleus_symbol("1H") == "H"
    assert nucleus_symbol("15N") == "N"
    assert nucleus_symbol("13C") == "C"
    assert nucleus_symbol("31P") == "P"
    assert nucleus_symbol("19F") == "F"
    assert nucleus_symbol("未知核") == "未知核"


def test_axis_labels_from_nuclei() -> None:
    assert axis_labels_from_nuclei(["15N", "1H"]) == ("N", "H")
    assert axis_labels_from_nuclei(["1H", "1H"]) == ("Hx", "Hy")
    assert axis_labels_from_nuclei(["13C", "15N", "1H"]) == ("C", "N", "H")
    assert axis_labels_from_nuclei(["1H", "1H", "15N"]) == ("Hx", "Hy", "N")


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
    assert panel.viewer._primary is not None
    assert panel.viewer._primary.x_axis.label == "H"
    assert panel.viewer._primary.y_axis.label == "N"
    panel.close()


def test_spectrum_panel_fallback_labels(
    tmp_path: Path, qapp: QApplication
) -> None:
    """无 metadata 时回退 F1/F2。"""
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
    assert panel.viewer._primary is not None
    assert panel.viewer._primary.x_axis.label == "F2"
    assert panel.viewer._primary.y_axis.label == "F1"
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
