"""Spectrum axis core name mapping test: metadata dimension core information -> H/N/C axis label
(same core plus index)."""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtcompat.QtWidgets import QApplication

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
    """0.2.89: Infer the core according to the observation frequency sf: 600 -> 1H, 60.8 -> 15N,
    150.9 -> 13C."""
    from viewer.axis_labels import infer_nucleus

    assert infer_nucleus(600.13) == "1H"
    assert infer_nucleus(60.82) == "15N"
    assert infer_nucleus(150.9) == "13C"
    # 0.2.110:1200 MHz(1.2 GHz) system regression -- sampleI indirect dimension sf≈121.7 is no
    # longer misjudged as 31P.
    assert infer_nucleus(121.67) == "15N"
    assert infer_nucleus(301.9) == "13C"
    assert infer_nucleus(1200.58) == "1H"
    # 0.2.111: The magnetic field list has been supplemented to 2 GHz (higher field spectrometer in
    # the future).
    assert infer_nucleus(2000.0) == "1H"
    assert infer_nucleus(202.74) == "15N"  # 2 GHz System 15N.
    assert infer_nucleus(502.9) == "13C"  # 2 GHz System 13C".
    assert infer_nucleus(10) == ""
    assert infer_nucleus(3000) == ""  # 3 GHz If it exceeds the list, it will be judged as empty.
    assert infer_nucleus(0) == ""


def test_nucleus_symbol() -> None:
    assert nucleus_symbol("1H") == "H"
    assert nucleus_symbol("15N") == "N"
    assert nucleus_symbol("13C") == "C"
    assert nucleus_symbol("31P") == "P"
    assert nucleus_symbol("19F") == "F"
    assert nucleus_symbol("unknown core") == "unknown core"


def test_axis_labels_from_nuclei() -> None:
    """Index priority: direct dimension > acqu2 > acqu3 (the further back in the logical order, the
    higher the priority)."""
    assert axis_labels_from_nuclei(["15N", "1H"]) == ("N", "H")
    assert axis_labels_from_nuclei(["1H", "1H"]) == ("Hy", "Hx")  # 2D:F2(Direct) -> Hx, F1 -> Hy.
    assert axis_labels_from_nuclei(["13C", "15N", "1H"]) == ("C", "N", "H")
    assert axis_labels_from_nuclei(["1H", "1H", "15N"]) == ("Hy", "Hx", "N")
    # Triple homonuclear.
    assert axis_labels_from_nuclei(["1H", "1H", "1H"]) == ("Hz", "Hy", "Hx")
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


def _write_ft2_nh(path: Path) -> None:
    """Real N-H head clamp (0.2.199-patch29dh): FDF1=15N, FDF2=1H, with LABEL."""
    import numpy as np
    from nmrglue.fileio import pipe

    data = np.zeros((64, 128), dtype=np.float32)
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = 128
    dic["FDSPECNUM"] = 64
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDF1T"] = 64
    dic["FDF1SW"] = 1703.0
    dic["FDF1OBS"] = 60.8
    dic["FDF1CAR"] = 117.0
    dic["FDF1ORIG"] = 117.0 * 60.8
    dic["FDF1LABEL"] = "N15"
    dic["FDF2T"] = 128
    dic["FDF2SW"] = 6000.0
    dic["FDF2OBS"] = 600.0
    dic["FDF2CAR"] = 4.7
    dic["FDF2ORIG"] = 4.7 * 600.0
    dic["FDF2LABEL"] = "H1"
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
    """Open spectrum: the axis label is the head core name (N-H), not F1/F2; metadata does not
    participate in the axis order/label (0.2.199-patch29dh, user: the software has an axis
    rearrangement process)."""
    from gui.spectrum_panel import SpectrumPanel

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    exp_id, data_id = entry.id, data.id
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2_nh(spectra / f"{exp_id}-{data_id}.ft2")
    meta_path = manager.data_metadata_path(exp_id, data_id)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps(_metadata_dims()), encoding="utf-8"
    )
    manager.save()
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    assert panel.load_current_spectrum() is True  # 0.2.88:Explicit loading.
    assert panel.viewer._primary is not None
    assert panel.viewer._primary.x_axis.label == "H"
    assert panel.viewer._primary.y_axis.label == "N"
    panel.close()


def test_spectrum_panel_fallback_labels(
    tmp_path: Path, qapp: QApplication
) -> None:
    """When there is no metadata, the label is deduced according to the head core (0.2.152, and no
    longer falls back to F1/F2)."""
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
    assert panel.load_current_spectrum() is True  # 0.2.88:Explicit loading.
    assert panel.viewer._primary is not None
    # The two axes of the synthesized file OBS are both 600 MHz -> inferred to be 1H, with the same
    # core plus index; direct dimension F2 is on the x axis -> Hx, indirect dimension F1 ->
    # Hy(0.2.199-patch29ah).
    assert panel.viewer._primary.x_axis.label == "Hx"
    assert panel.viewer._primary.y_axis.label == "Hy"
    panel.close()


def test_projection_same_nucleus_uses_header_and_subscript(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.168: The homonuclear projection (H-H plane) uses the file header slot to construct the
    axis parameter, and the label is Hx/Hy index (no longer taking the wrong axis from the 3D
    spectrum according to the nuclear type)."""
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
    """Independent viewer: axis labels come from the spectrum header core name, metadata does not
    participate (0.2.199-patch29dh, user: the software has an axis rearrangement process)."""
    from viewer.app import SpectrumWindow

    base = tmp_path / "exp_001" / "d_001"
    spectra = base / "spectra"
    spectra.mkdir(parents=True, exist_ok=True)
    ft2 = spectra / "exp_001-d_001.ft2"
    _write_ft2_nh(ft2)
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
    """0.2.199-patch29ai: The same core index tag (15Nx/1Hy/1Hz) can be directly parsed into the
    core name."""
    from viewer.spectrum import _parse_nmrpipe_label

    assert _parse_nmrpipe_label("1Hx") == "1H"
    assert _parse_nmrpipe_label("1Hy") == "1H"
    assert _parse_nmrpipe_label("1Hz") == "1H"
    assert _parse_nmrpipe_label("15Nx") == "15N"
    assert _parse_nmrpipe_label("15Ny") == "15N"
    assert _parse_nmrpipe_label("13Cz") == "13C"
    # Compatible with original formats.
    assert _parse_nmrpipe_label("1H") == "1H"
    assert _parse_nmrpipe_label("N15") == "15N"
    assert _parse_nmrpipe_label("H1") == "1H"
    assert _parse_nmrpipe_label("C13") == "13C"
    assert _parse_nmrpipe_label("") == ""
    assert _parse_nmrpipe_label("unknown") == ""

def test_projection_nn_uses_subscript_labels(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.199-patch29aj:HNN NN The projection plane (15Ny-15Nx.ft2)x=Ny, y=Nx, fixed_axis
    correctly fixes 1H(F3), and the dimension is mapped to the remaining two axes."""
    from gui.spectrum_panel import SpectrumPanel

    manager = ProjectManager.create_project(tmp_path / "proj4", "demo")
    entry = manager.create_experiment("HNN")
    data = manager.import_data(entry.id, "/fake/1")
    exp_id, data_id = entry.id, data.id
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    proj = spectra / f"{data_id}_15Ny-15Nx.ft2"
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
                        {"logical_axis": "F1", "nucleus": "15N", "role": "indirect"},
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
    assert spec.x_axis.label == "Ny"  # Order N(F1).
    assert spec.y_axis.label == "Nx"  # HSQC Of N(F2).
    assert spec.dim_indices == (0, 1)  # fixed_axis=F3(1H)
    panel.close()


def test_projection_nn_axis_params_from_3d(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.199-patch29aj:NN The projection axis parameter is positioned from the 3D spectrum
    according to the logical index (x=15Ny -> F1, y=15Nx -> F2), no longer relying on unreliable
    projection file headers."""
    import numpy as np

    from gui.spectrum_panel import SpectrumPanel
    from viewer.spectrum import Spectrum3D, SpectrumAxis

    manager = ProjectManager.create_project(tmp_path / "proj5", "demo")
    entry = manager.create_experiment("HNN")
    data = manager.import_data(entry.id, "/fake/1")
    exp_id, data_id = entry.id, data.id
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    proj = spectra / f"{data_id}_15Ny-15Nx.ft2"
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
                        {"logical_axis": "F1", "nucleus": "15N", "role": "indirect"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    manager.save()
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    axes3 = [
        SpectrumAxis("Ny", 8, 2000.0, 60.0, 118.0, 118.0 * 60.0),
        SpectrumAxis("Nx", 8, 2000.0, 90.0, 118.0, 118.0 * 90.0),
        SpectrumAxis("H", 16, 6000.0, 600.0, 4.7, 4.7 * 600.0),
    ]
    panel._spectrum3d_panel._spectrum3d = Spectrum3D(
        np.zeros((8, 8, 16), dtype=np.float32), axes3
    )
    spec = panel._load_projection_ft2(proj)
    assert spec is not None
    assert spec.x_axis.obs_mhz == pytest.approx(60.0)  # F1(15Ny)
    assert spec.y_axis.obs_mhz == pytest.approx(90.0)  # F2(15Nx)
    assert spec.x_axis.label == "Ny"
    assert spec.y_axis.label == "Nx"
    panel.close()
