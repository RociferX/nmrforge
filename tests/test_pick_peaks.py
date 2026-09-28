"""Peak picking tests: synthetic detection + Poky .list output + WorkflowRun records."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from core.peaks.peak_table import export_peaks_poky, import_peaks_poky
from core.project import ProjectManager
from workflow.pick_peaks import PickPeaksError, pick_peaks


def _write_ft2(path: Path, data: np.ndarray) -> None:
    """Write a synthetic NMRPipe 2D spectrum with nmrglue (ORIG/SW/OBS/CAR header)."""
    from nmrglue.fileio import pipe

    dic = {k: "0" for k in pipe.fdata_dic}
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


def _manager_with_spectrum(
    tmp_path: Path, spec_path: Path
) -> tuple[ProjectManager, str, str]:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")
    manager.set_data_spectrum(entry.id, data.id, str(spec_path))
    return manager, entry.id, data.id


def test_pick_peaks_detects_and_writes(tmp_path: Path) -> None:
    spec = np.zeros((64, 128))
    spec[20, 40] = 500.0
    spec[25, 90] = 350.0
    spec = gaussian_filter(spec, sigma=1.5)
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)

    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)

    assert result["status"] == "success"
    assert result["peak_count"] >= 1
    peak_path = Path(result["peak_path"])
    assert peak_path.is_file()
    assert peak_path.name == f"{exp_id}-{data_id}.list"
    assert peak_path.parent == manager.data_dir(exp_id, data_id, "peaks")

    rows = import_peaks_poky(peak_path)
    assert len(rows) >= 1
    assert "H_shift" in rows[0] and "N_shift" in rows[0]
    assert float(rows[0]["Intensity"]) > 0

    runs = [r for r in manager.project.workflow_runs if r.workflow_ref == "pick_peaks"]
    assert len(runs) == 1
    assert runs[0].status == "success"
    assert runs[0].inputs["spectrum_path"] == str(ft2)
    assert runs[0].inputs["data_id"] == data_id
    assert runs[0].outputs["peak_path"] == result["peak_path"]



def test_permutation_to_logical_maps_storage_to_logical() -> None:
    """0.2.199-patch29df: storage→logical axis permutation shares its source with the viewer."""
    from workflow.pick_peaks import _permutation_to_logical

    # storage (15N, 1H, 13C) but logical (1H, 15N, 13C): F1=storage axis 1, F2=storage axis 0
    perm = _permutation_to_logical(["15N", "1H", "13C"], ["1H", "15N", "13C"])
    assert perm == [1, 0, 2]
    logical_axes = [0, 0, 0]
    for spos, lpos in enumerate(perm):
        logical_axes[lpos] = spos
    assert logical_axes == [1, 0, 2]  # F1←axis 1, F2←axis 0, F3←axis 2


def test_pick_peaks_subpixel_shift_interpolated(tmp_path: Path) -> None:
    """Sub-pixel peak positions: the ppm written to .list is interpolated
    (not an integer pixel value, 0.2.199-patch29eo)."""
    shape = (64, 128)
    yy, xx = np.mgrid[0:64, 0:128]
    rng = np.random.default_rng(4)
    real = np.exp(
        -(((yy - 20.4) ** 2) / (2 * 1.2 ** 2) + ((xx - 40.7) ** 2) / (2 * 1.2 ** 2))
    )
    real = real + rng.normal(0, 0.01, size=shape)
    ft2 = tmp_path / "sub.ft2"
    _write_ft2(ft2, real)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    rows = import_peaks_poky(Path(result["peak_path"]))
    top = max(rows, key=lambda r: float(r["Intensity"]))
    h = float(top["H_shift"])
    n = float(top["N_shift"])
    # _write_ft2 header ORIG=1000/600, SW=6000:
    #   ppm_i = 1000/600 + (size-1-i)*6000/(size*600)
    exp_h = 1000 / 600 + (127 - 40.7) * 6000 / (128 * 600)
    exp_n = 1000 / 600 + (63 - 20.4) * 6000 / (64 * 600)
    assert abs(h - exp_h) < 0.02
    assert abs(n - exp_n) < 0.03
    # confirm it is not rounded to the nearest integer pixel (interpolation happened)
    int_h = 1000 / 600 + (127 - 41) * 6000 / (128 * 600)
    assert abs(h - int_h) > 0.01


def test_pick_peaks_missing_spectrum_fails(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")

    with pytest.raises(PickPeaksError, match="谱图缺失"):
        pick_peaks(manager, entry.id, data.id)

    runs = [r for r in manager.project.workflow_runs if r.workflow_ref == "pick_peaks"]
    assert len(runs) == 1
    assert runs[0].status == "failed"


def test_pick_peaks_writes_poky_list(tmp_path: Path) -> None:
    """0.2.199-patch29ar: picking outputs a Poky .list (no CSV/reliability columns)."""
    spec = np.zeros((64, 128))
    spec[20, 40] = 500.0
    spec = gaussian_filter(spec, sigma=1.5)
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)

    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    path = Path(result["peak_path"])
    assert path.suffix == ".list"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "Assignment w1 w2 Data Height Volume"
    assert len(lines) >= 2
    assert "阈值" in result["logs"][0]
    assert "25.0σ" in result["logs"][0]  # mechanism test uses explicit 25σ (default 35σ, patch29hn)


def _write_metadata(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    name: str,
    confidence: float = 1.0,
) -> None:
    """Write data metadata (experiment_type.name+confidence), driving the peak sign mode."""
    import json

    path = manager.data_metadata_path(exp_id, data_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 0.2.199-patch29fa (fix): real metadata nests dataset.experiment_type
    # (tests used to write a top-level experiment_type, hiding the bug where
    # picking could not read the type)
    path.write_text(
        json.dumps(
            {
                "dataset": {
                    "experiment_type": {
                        "name": name,
                        "confidence": confidence,
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _write_ft3_ordered(
    path: Path, data: np.ndarray, fddimorder: list[float]
) -> None:
    """Write a 3D stream file with FDDIMORDER (same shape as test_viewer3d)."""
    from nmrglue.fileio import pipe

    nz, ny, nx = data.shape
    dic = {k: "0" for k in pipe.fdata_dic}
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
    blocks = {
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


def _spectrum_with_peaks(
    shape: tuple[int, ...], peaks: list[tuple[tuple[int, ...], float]]
) -> np.ndarray:
    """Spectrum with Gaussian kernels at the peak points (positive or negative);
    a σ≈1 noise floor makes the global noise estimate use robust MAD, so the
    threshold-to-peak-height ratio matches real spectra (0.2.199-patch29cm)."""
    rng = np.random.default_rng(20260829)
    spec = rng.normal(0, 1.0, shape)
    for pos, height in peaks:
        spec[pos] += height
    return gaussian_filter(spec, sigma=1.5)


def _read_rows(path: Path, nuclei=None) -> list[dict]:
    # 0.2.199-patch29dk: 3D .list columns follow the external N,C,H convention;
    # reading requires the nucleus name of each F axis
    return import_peaks_poky(path, nuclei=nuclei)



def test_experiment_type_name_reads_dataset_and_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29fa: dataset.experiment_type wins; the legacy top level still works."""
    import json

    from workflow.pick_peaks import _experiment_type_name

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")
    _write_metadata(manager, entry.id, data.id, "HNCACB")
    assert _experiment_type_name(manager, entry.id, data.id) == "HNCACB"

    path = manager.data_metadata_path(entry.id, data.id)
    path.write_text(
        json.dumps({"experiment_type": {"name": "HNCACB", "confidence": 1.0}}),
        encoding="utf-8",
    )
    assert _experiment_type_name(manager, entry.id, data.id) == "HNCACB"

    path.write_text(json.dumps({"dataset": {}}), encoding="utf-8")
    assert _experiment_type_name(manager, entry.id, data.id) == ""


def test_pick_peaks_uniform_type_keeps_dominant_sign_only(tmp_path: Path) -> None:
    """uniform (single-sign) experiment: keep only dominant-sign peaks; the few
    inverted peaks are treated as spurious and dropped."""
    spec = _spectrum_with_peaks(
        (64, 128),
        [
            ((20, 40), 500.0),
            ((25, 90), 350.0),
            ((40, 60), 280.0),
            ((10, 100), -300.0),
        ],
    )
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    _write_metadata(manager, exp_id, data_id, "HSQC")  # peak_sign: uniform

    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    rows = _read_rows(Path(result["peak_path"]))
    assert len(rows) == 3
    assert all(float(r["Intensity"]) > 0 for r in rows)
    assert "仅主符号峰" in result["logs"][0]


def test_pick_peaks_uniform_type_negative_dominant(tmp_path: Path) -> None:
    """When the uniform experiment's dominant sign is negative, only the dominant
    sign (negative peaks) is kept."""
    spec = _spectrum_with_peaks(
        (64, 128),
        [
            ((20, 40), -500.0),
            ((25, 90), -350.0),
            ((40, 60), -280.0),
            ((10, 100), 300.0),
        ],
    )
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    _write_metadata(manager, exp_id, data_id, "HSQC")

    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    rows = _read_rows(Path(result["peak_path"]))
    assert len(rows) == 3
    assert all(float(r["Intensity"]) < 0 for r in rows)



def test_pick_peaks_spectrum_evidence_backfill(tmp_path: Path) -> None:
    """0.2.199-patch29fc: low-confidence type, but both signs are frequent and
    strong enough -> pick as mixed."""
    spec = _spectrum_with_peaks(
        (64, 128),
        [
            ((20, 40), 600.0),
            ((25, 90), 500.0),
            ((40, 60), 400.0),
            ((10, 100), 300.0),
            ((15, 50), -600.0),
            ((30, 70), -500.0),
            ((45, 20), -400.0),
        ],
    )
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    _write_metadata(manager, exp_id, data_id, "HSQC", confidence=0.3)  # low confidence

    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    rows = _read_rows(Path(result["peak_path"]))
    signs = {float(r["Intensity"]) > 0 for r in rows}
    assert signs == {True, False}  # both signs picked
    assert any("谱面回补" in log for log in result["logs"])


def test_pick_peaks_spectrum_evidence_keeps_dominant_when_mostly_one_sign(
    tmp_path: Path,
) -> None:
    """0.2.199-patch29fc: low confidence and only stray negative peaks -> stay dominant."""
    spec = _spectrum_with_peaks(
        (64, 128),
        [
            ((20, 40), 600.0),
            ((25, 90), 500.0),
            ((40, 60), 400.0),
            ((10, 100), 300.0),
            ((15, 50), -200.0),  # one stray negative peak
        ],
    )
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    _write_metadata(manager, exp_id, data_id, "HSQC", confidence=0.3)

    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    rows = _read_rows(Path(result["peak_path"]))
    assert all(float(r["Intensity"]) > 0 for r in rows)


def test_pick_peaks_spectrum_evidence_respects_confident_uniform(
    tmp_path: Path,
) -> None:
    """0.2.199-patch29fc: a high-confidence uniform template (HSQC 0.9+) keeps
    its dominant sign even when the spectrum is balanced."""
    spec = _spectrum_with_peaks(
        (64, 128),
        [
            ((20, 40), 600.0),
            ((25, 90), 500.0),
            ((40, 60), 400.0),
            ((10, 100), 300.0),
            ((15, 50), -600.0),
            ((30, 70), -500.0),
            ((45, 20), -400.0),
        ],
    )
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    _write_metadata(manager, exp_id, data_id, "HSQC", confidence=0.95)

    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    rows = _read_rows(Path(result["peak_path"]))
    assert all(float(r["Intensity"]) > 0 for r in rows)
    assert not any("谱面回补" in log for log in result["logs"])


def test_pick_peaks_spectrum_evidence_rejects_contamination(
    tmp_path: Path,
) -> None:
    """0.2.199-patch29fc-fix: a minor sign dominated by a single very strong
    peak (suspected contamination) does not trigger mixed."""
    spec = _spectrum_with_peaks(
        (64, 128),
        [
            ((20, 40), 500.0),
            ((25, 90), 450.0),
            ((40, 60), 400.0),
            ((10, 100), 350.0),
            ((15, 50), -1800.0),  # one very strong negative peak (contamination)
            ((30, 70), -260.0),
            ((45, 20), -240.0),
        ],
    )
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    _write_metadata(manager, exp_id, data_id, "HSQC", confidence=0.3)

    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    rows = _read_rows(Path(result["peak_path"]))
    assert all(float(r["Intensity"]) > 0 for r in rows)
    assert not any("谱面回补" in log for log in result["logs"])



def test_gui_user_type_updates_metadata(tmp_path: Path) -> None:
    """0.2.199-patch29fd: a GUI user-selected type is written back to metadata
    authoritatively, and picking follows the new type (mixed)."""
    import json

    from workflow.import_workflow import apply_user_experiment_type

    spec = _spectrum_with_peaks(
        (64, 128),
        [
            ((20, 40), 600.0),
            ((25, 90), 500.0),
            ((15, 50), -600.0),
            ((30, 70), -500.0),
        ],
    )
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    _write_metadata(manager, exp_id, data_id, "HSQC", confidence=0.95)

    assert apply_user_experiment_type(manager, exp_id, data_id, "HNCACB") is True
    path = manager.data_metadata_path(exp_id, data_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    et = payload["dataset"]["experiment_type"]
    assert et["name"] == "HNCACB"
    assert et["confidence"] == 1.0
    assert "gui_user_selected" in et["evidence"]

    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    rows = _read_rows(Path(result["peak_path"]))
    signs = {float(r["Intensity"]) > 0 for r in rows}
    assert signs == {True, False}  # HNCACB mixed → both signs picked


def test_pick_peaks_mixed_type_picks_both_signs(tmp_path: Path) -> None:
    """mixed experiment (e.g. HNCACB 13Cα/13Cβ anti-phase): both signs are picked."""
    spec = _spectrum_with_peaks(
        (64, 128),
        [
            ((20, 40), 500.0),
            ((25, 90), 350.0),
            ((10, 100), -300.0),
            ((45, 20), -280.0),
        ],
    )
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    _write_metadata(manager, exp_id, data_id, "HNCACB")  # peak_sign: mixed

    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    rows = _read_rows(Path(result["peak_path"]))
    assert len(rows) == 4
    signs = {float(r["Intensity"]) > 0 for r in rows}
    assert signs == {True, False}
    assert "正负峰都选" in result["logs"][0]


def test_pick_peaks_ft3_shifts_follow_logical_axes(tmp_path: Path) -> None:
    """3D ORDER 2 3 1: F1/F2/F3_shift take ppm from the data axis of the
    logical dimension (0.2.199-patch29ap fix)."""
    data = np.zeros((16, 16, 16))  # (FDF3SIZE=15N, FDSPECNUM=13C, FDSIZE=1H)
    data[8, 5, 8] = 500.0  # F1=8 avoids the top/bottom edges (patch29bf excludes 5 points)
    data = gaussian_filter(data, sigma=1.0)
    ft3 = tmp_path / "out.ft3"
    _write_ft3_ordered(ft3, data, [2.0, 3.0, 1.0])
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft3)

    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    rows = _read_rows(Path(result["peak_path"]), nuclei=["15N", "1H", "13C"])
    assert len(rows) >= 1
    row = rows[0]
    # logical dims: F1=15N (FDF1), F2=1H (FDF2), F3=13C (FDF3)
    f1 = 100.0 + (16 - 1 - 8) * 2189.0 / (16 * 60.8)
    f2 = 6.0 + (16 - 1 - 8) * 3000.0 / (16 * 600.0)
    f3 = 40.0 + (16 - 1 - 5) * 11300.0 / (16 * 150.9)
    assert abs(float(row["F1_shift"]) - f1) < 0.05
    assert abs(float(row["F2_shift"]) - f2) < 0.05
    assert abs(float(row["F3_shift"]) - f3) < 0.05


def test_pick_peaks_flat_plateau_not_picked(tmp_path: Path) -> None:
    """A flat baseline yields no peaks (strict local maximum + 6σ picking,
    0.2.199-patch29aq/ar fix)."""
    spec = np.full((64, 128), 100.0)
    spec[20, 40] = 500.0
    spec[25, 90] = 500.0
    spec = gaussian_filter(spec, sigma=1.0)
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)

    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    rows = _read_rows(Path(result["peak_path"]))
    assert len(rows) == 2


def test_pick_peaks_sigma_multiplier_param(tmp_path: Path) -> None:
    """sigma_multiplier sets the threshold: a higher value picks fewer peaks
    (0.2.199-patch29ar)."""
    rng = np.random.default_rng(3)
    spec = rng.normal(0, 1.0, (64, 128))
    spec[20, 40] += 30.0
    spec[25, 90] += 12.0
    spec[45, 60] += 6.0
    spec = gaussian_filter(spec, sigma=1.0)
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)

    low = pick_peaks(manager, exp_id, data_id, sigma_multiplier=4.0)
    high = pick_peaks(manager, exp_id, data_id, sigma_multiplier=8.0)
    assert high["peak_count"] <= low["peak_count"]
    assert "4.0σ" in low["logs"][0]
    assert "8.0σ" in high["logs"][0]



def test_pick_peaks_excludes_axial_edges(tmp_path: Path) -> None:
    # 0.2.199-patch29at: axial peaks (bars) at the top/bottom edges are not
    # picked, in-spectrum peaks are kept
    # (with a σ≈1 noise floor the intent is unchanged at the 25σ default,
    # 0.2.199-patch29gc)
    rng = np.random.default_rng(20260829)
    spec = rng.normal(0, 1.0, (64, 128))
    spec[0, 60] += 800.0  # top axial peak (bar)
    spec[63, 60] += 700.0  # bottom axial peak (bar)
    spec[20, 40] += 500.0
    spec[40, 90] += 450.0
    spec = gaussian_filter(spec, sigma=1.0)
    ft2 = tmp_path / 'out.ft2'
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)

    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    rows = _read_rows(Path(result['peak_path']))
    assert len(rows) == 2  # two in-spectrum peaks, axial peaks excluded


def test_infer_nucleus_obs_covers_common_spectrometers() -> None:
    """0.2.199-patch29dh: OBS inference covers all field strengths and 15N/13C
    (the old implementation inverted and only knew 600 MHz)."""
    from workflow.pick_peaks import _infer_nucleus_obs as infer

    assert infer(500.13) == "1H"
    assert infer(700.13) == "1H"
    assert infer(800.3) == "1H"
    assert infer(1200.57) == "1H"
    assert infer(50.68) == "15N"  # 500 MHz 15N
    assert infer(81.1) == "15N"  # 800 MHz 15N
    assert infer(125.76) == "13C"  # 500 MHz 13C
    assert infer(201.2) == "13C"  # 800 MHz 13C
    assert infer(0.0) == ""
    assert infer(-1.0) == ""


def test_parse_nmrpipe_label_hn_alias() -> None:
    """0.2.199-patch29dh: the real NMRPipe LABEL 'HN' parses as 1H (measured on
    30.ft3/d_011.ft3)."""
    from workflow.pick_peaks import _parse_nmrpipe_label as parse

    assert parse("HN") == "1H"
    assert parse("15N") == "15N"
    assert parse("13C") == "13C"
    assert parse("N15") == "15N"
    assert parse("H1") == "1H"
    assert parse("C13") == "13C"
    assert parse("15Nx") == "15N"
    assert parse("") == ""


def _write_metadata_dims(
    manager: ProjectManager, exp_id: str, data_id: str, dims: list[dict]
) -> None:
    """Write metadata with dataset.dimensions (drives the picking metadata nuclei)."""
    import json

    path = manager.data_metadata_path(exp_id, data_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"experiment_type": {"name": "HNCA"},
                    "dataset": {"dimensions": dims}}),
        encoding="utf-8",
    )


def test_pick_peaks_ft3_header_order_wins_over_metadata(
    tmp_path: Path,
) -> None:
    """0.2.199-patch29dh: when the file header (FDDIMORDER+LABEL) conflicts with
    metadata, the header wins.

    Reproduces a real HNCA (Bruker acquisition-order metadata
    F1=13C/F2=15N/F3=1H=CNH vs the NMRPipe .ft3 header
    F1=15N/F2=1H/F3=13C=NHC): the peak table must write values as NHC.
    """
    data = np.zeros((16, 16, 16))
    data[8, 5, 8] = 500.0  # logical F1(N)=8, F2(H)=8, F3(C)=5
    data = gaussian_filter(data, sigma=1.0)
    ft3 = tmp_path / "out.ft3"
    _write_ft3_ordered(ft3, data, [2.0, 3.0, 1.0])
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft3)
    _write_metadata_dims(
        manager,
        exp_id,
        data_id,
        [
            {"logical_axis": "F1", "sf": 150.9, "nucleus": "13C"},
            {"logical_axis": "F2", "sf": 60.8, "nucleus": "15N"},
            {"logical_axis": "F3", "sf": 600.13, "nucleus": "1H"},
        ],
    )

    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    row = _read_rows(Path(result["peak_path"]), nuclei=["15N", "1H", "13C"])[0]
    n_ppm = 100.0 + (16 - 1 - 8) * 2189.0 / (16 * 60.8)
    h_ppm = 6.0 + (16 - 1 - 8) * 3000.0 / (16 * 600.0)
    c_ppm = 40.0 + (16 - 1 - 5) * 11300.0 / (16 * 150.9)
    assert abs(float(row["F1_shift"]) - n_ppm) < 0.05
    assert abs(float(row["F2_shift"]) - h_ppm) < 0.05
    assert abs(float(row["F3_shift"]) - c_ppm) < 0.05


def test_pick_peaks_2d_reversed_storage_maps_by_nucleus(
    tmp_path: Path,
) -> None:
    """0.2.199-patch29dh: with 2D storage order (1H,15N), N/H columns match by nucleus."""
    from nmrglue.fileio import pipe

    spec = np.zeros((64, 32))
    spec[20, 10] = 500.0  # storage axis0=1H idx20, axis1=15N idx10
    spec = gaussian_filter(spec, sigma=1.2)
    ft2 = tmp_path / "hn.ft2"
    dic = {k: "0" for k in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = spec.shape[1]
    dic["FDSPECNUM"] = spec.shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    blocks = {
        1: ("1H", 64, 3000.0, 600.0, 4.7, 6.0 * 600.0),
        2: ("15N", 32, 2189.0, 60.8, 118.0, 100.0 * 60.8),
    }
    for dim, (lab, size, sw, obs, car, orig) in blocks.items():
        prefix = f"FDF{dim}"
        dic[prefix + "T"] = size
        dic[prefix + "SW"] = sw
        dic[prefix + "OBS"] = obs
        dic[prefix + "CAR"] = car
        dic[prefix + "ORIG"] = orig
        dic[prefix + "LABEL"] = lab
    pipe.write(str(ft2), dic, spec.astype(np.float32), overwrite=True)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)

    # axis-mapping test: explicit 15σ (default is 25σ; this weak-peak case does not rely on it)
    result = pick_peaks(
        manager, exp_id, data_id, sigma_multiplier=15.0
    )
    row = _read_rows(Path(result["peak_path"]))[0]
    n_ppm = 100.0 + (32 - 1 - 10) * 2189.0 / (32 * 60.8)
    h_ppm = 6.0 + (64 - 1 - 20) * 3000.0 / (64 * 600.0)
    assert abs(float(row["N_shift"]) - n_ppm) < 0.05
    assert abs(float(row["H_shift"]) - h_ppm) < 0.05

def _write_ft2_nh(path: Path, data: np.ndarray) -> None:
    """Real N-H 2D spectrum fixture (used by the 0.2.199-patch29dl reference tests)."""
    from nmrglue.fileio import pipe

    dic = {k: "0" for k in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = data.shape[1]
    dic["FDSPECNUM"] = data.shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    blocks = {
        1: ("15N", data.shape[0], 2189.0, 60.8, 118.0, 100.0 * 60.8),
        2: ("1H", data.shape[1], 3000.0, 600.0, 4.7, 6.0 * 600.0),
    }
    for dim, (lab, size, sw, obs, car, orig) in blocks.items():
        prefix = f"FDF{dim}"
        dic[prefix + "T"] = size
        dic[prefix + "SW"] = sw
        dic[prefix + "OBS"] = obs
        dic[prefix + "CAR"] = car
        dic[prefix + "ORIG"] = orig
        dic[prefix + "LABEL"] = lab
    pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)


def _nh_ppm(n_idx: int, h_idx: int) -> tuple[float, float]:
    """(N,H) ppm of the synthetic N-H spectrum (matches the _write_ft2_nh header)."""
    n_ppm = 100.0 + (64 - 1 - n_idx) * 2189.0 / (64 * 60.8)
    h_ppm = 6.0 + (128 - 1 - h_idx) * 3000.0 / (128 * 600.0)
    return n_ppm, h_ppm


def test_pick_peaks_reference_constraint_2d(tmp_path: Path) -> None:
    """0.2.199-patch29dl: reference-table constraint -- a 2D run keeps only
    peaks matching the reference (N,H)."""
    rng = np.random.default_rng(20260831)
    spec = rng.normal(0, 0.3, (64, 128))
    spec[20, 40] += 1500.0
    spec[25, 90] += 1200.0
    spec[40, 60] += 1000.0
    spec = gaussian_filter(spec, sigma=1.0)
    ft2 = tmp_path / "out.ft2"
    _write_ft2_nh(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    ref_path = tmp_path / "ref.list"
    export_peaks_poky(
        ref_path,
        [
            {"N_shift": _nh_ppm(20, 40)[0], "H_shift": _nh_ppm(20, 40)[1],
             "Intensity": 1, "label": ""},
            {"N_shift": _nh_ppm(25, 90)[0], "H_shift": _nh_ppm(25, 90)[1],
             "Intensity": 1, "label": ""},
        ],
        ndim=2,
    )
    ref_peaks = import_peaks_poky(ref_path)
    result = pick_peaks(
        manager, exp_id, data_id,
        ref_peaks=ref_peaks, ref_nuclei=["15N", "1H"],
    )
    rows = _read_rows(Path(result["peak_path"]))
    assert len(rows) == 2
    assert "参考峰表约束" in "".join(result["logs"])


def test_pick_peaks_reference_constraint_3d_with_2d_ref(
    tmp_path: Path,
) -> None:
    """0.2.199-patch29dl: 3D picking is constrained by the 2D reference (N,H) --
    the third dimension is free, so one reference peak may keep several peaks
    (e.g. CA/CB of HNCA)."""
    rng = np.random.default_rng(20260831)
    data = rng.normal(0, 0.3, (16, 16, 16))
    # logical F1(N)=8, F2(H)=8, F3(C)=5 and F3(C)=10; another (N,H)=(10,10)
    data[8, 5, 8] += 1500.0
    data[8, 10, 8] += 1200.0
    data[10, 5, 10] += 1000.0
    data = gaussian_filter(data, sigma=1.0)
    ft3 = tmp_path / "out.ft3"
    _write_ft3_ordered(ft3, data, [2.0, 3.0, 1.0])
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft3)
    n_ppm = 100.0 + (16 - 1 - 8) * 2189.0 / (16 * 60.8)
    h_ppm = 6.0 + (16 - 1 - 8) * 3000.0 / (16 * 600.0)
    ref_path = tmp_path / "ref.list"
    export_peaks_poky(
        ref_path,
        [{"N_shift": n_ppm, "H_shift": h_ppm, "Intensity": 1, "label": ""}],
        ndim=2,
    )
    ref_peaks = import_peaks_poky(ref_path)
    result = pick_peaks(
        manager, exp_id, data_id,
        ref_peaks=ref_peaks, ref_nuclei=["15N", "1H"],
        tolerance_ppm={"15N": 2.0, "1H": 0.5, "13C": 20.0},
    )
    rows = _read_rows(Path(result["peak_path"]), nuclei=["15N", "1H", "13C"])
    assert len(rows) == 2  # both C values for (N=8,H=8) kept, the other (N,H) dropped
    assert "参考峰表约束" in "".join(result["logs"])


def test_pick_peaks_reference_whole_shift(tmp_path: Path) -> None:
    """0.2.199-patch29fw: whole-reference shift (3 ppm in 15N). With enough
    reference peaks (>=5) the reference is aligned first and then matched; with
    few peaks (e.g. a single one) the shift is unreliable, so matching is direct.
    This case uses 5 reference peaks (shifted +3 ppm) to verify that the aligned
    path keeps the current peaks."""
    rng = np.random.default_rng(20260831)
    spec = rng.normal(0, 0.3, (64, 128))
    for row, col in ((20, 40), (25, 90), (30, 60), (40, 100), (50, 70)):
        spec[row, col] += 1200.0
    spec = gaussian_filter(spec, sigma=1.0)
    ft2 = tmp_path / "out.ft2"
    _write_ft2_nh(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    ref_path = tmp_path / "ref.list"
    refs = []
    for row, col in ((20, 40), (25, 90), (30, 60), (40, 100), (50, 70)):
        n_ppm, h_ppm = _nh_ppm(row, col)
        refs.append({"N_shift": n_ppm + 3.0, "H_shift": h_ppm,
                     "Intensity": 1, "label": ""})
    export_peaks_poky(ref_path, refs, ndim=2)
    ref_peaks = import_peaks_poky(ref_path)
    result = pick_peaks(
        manager, exp_id, data_id,
        ref_peaks=ref_peaks, ref_nuclei=["15N", "1H"],
        tolerance_ppm={"15N": 1.0, "1H": 0.2},
    )
    rows = _read_rows(Path(result["peak_path"]))
    # all 5 real peaks survive the +3 ppm alignment (the 15N search range covers 3 ppm)
    assert len(rows) == 5
    logs = "".join(result["logs"])
    assert "整体偏移" in logs



def test_safe_figure_token() -> None:
    """0.2.199-patch29fx: a reference display name (exp/data with '/') becomes a
    single-segment safe file name."""
    from workflow.pick_peaks import _safe_figure_token

    assert _safe_figure_token("exp_009/d_002") == "exp_009_d_002"
    assert "/" not in _safe_figure_token("exp_009/d_002 (HSQC)")

def test_pick_peaks_run_records_data_id_per_data(tmp_path: Path) -> None:
    """patch29hi: each pick_peaks WorkflowRun records its own data_id, avoiding
    reports bleeding across data sets."""
    spec = np.zeros((64, 128))
    spec[20, 40] = 500.0
    spec[25, 90] = 350.0
    spec = gaussian_filter(spec, sigma=1.5)
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    d1 = manager.import_data(entry.id, "/sampleD")
    d2 = manager.import_data(entry.id, "/sampleE")
    manager.set_data_spectrum(entry.id, d1.id, str(ft2))
    manager.set_data_spectrum(entry.id, d2.id, str(ft2))

    pick_peaks(manager, entry.id, d1.id)
    pick_peaks(manager, entry.id, d2.id)

    by_data = {
        (r.inputs or {}).get("data_id", ""): r
        for r in manager.project.workflow_runs
        if r.workflow_ref == "pick_peaks"
    }
    assert by_data[d1.id] is not None
    assert by_data[d2.id] is not None
    assert by_data[d1.id].inputs["data_id"] == d1.id
    assert by_data[d2.id].inputs["data_id"] == d2.id
