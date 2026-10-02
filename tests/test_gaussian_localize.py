"""2D Gaussian peak localization regression.

Covers the six tests of user requirement §15 (non-integer centre, zero filling / grid
density, agreement with the parabola, failure path, non-2D rejection, backward
compatibility) plus peak-picking integration (peak table sidecar / run-parameter
provenance).

Convention: the ppm axis of the synthetic spectrum shares its origin with peak picking
(``CAR + (size/2 - i)*SW/(size*OBS)``); data axis 0 = F1 (indirect, 15N),
axis 1 = F2 (direct, 1H).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.peaks import localize as lz
from core.peaks.peak_table import import_peaks_poky
from core.project import ProjectManager
from ui_support import i18n
from workflow.pick_peaks import PickPeaksError, pick_peaks

_N15 = {"obs": 60.8, "sw": 2000.0, "car": 118.0}
_H1 = {"obs": 600.0, "sw": 6000.0, "car": 4.7}


@pytest.fixture(autouse=True)
def _english_messages():
    """Regression coverage:  english messages."""
    saved = i18n._language
    i18n.set_language("en")
    yield
    i18n._language = saved
    i18n.reset_cache()


_TRUE_CENTER = (30.37, 60.62)
_TRUE_SIGMA = (2.2, 2.6)


def _synth(
    shape: tuple[int, int],
    center: tuple[float, float],
    sigma: tuple[float, float],
    *,
    amp: float = 100.0,
    base: float = 3.0,
    noise: float = 0.0,
    seed: int = 1,
) -> np.ndarray:
    row, col = np.mgrid[0 : shape[0], 0 : shape[1]]
    arr = base + amp * np.exp(
        -((row - center[0]) ** 2) / (2 * sigma[0] ** 2)
        - ((col - center[1]) ** 2) / (2 * sigma[1] ** 2)
    )
    if noise:
        arr = arr + np.random.default_rng(seed).normal(0.0, noise, shape)
    return arr


def _ppm_axes(shape: tuple[int, int]) -> list[np.ndarray]:
    axes: list[np.ndarray] = []
    for spec, size in ((_N15, shape[0]), (_H1, shape[1])):
        idx = np.arange(size, dtype=float)
        axes.append(spec["car"] + (size / 2.0 - idx) * spec["sw"] / (size * spec["obs"]))
    return axes


def _argmax(arr: np.ndarray) -> tuple[int, int]:
    row, col = np.unravel_index(int(np.argmax(arr)), arr.shape)
    return int(row), int(col)


def _err(a: tuple[float, float], b: tuple[float, float]) -> float:
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def test_parabolic_recovers_subpoint_center_2d() -> None:
    """Regression coverage: test parabolic recovers subpoint center 2d."""
    shape = (64, 128)
    arr = _synth(shape, _TRUE_CENTER, _TRUE_SIGMA)
    index = _argmax(arr)
    grid_err = _err(index, _TRUE_CENTER)
    assert grid_err > 0.25
    refined = lz.localize_peak(arr, index)
    assert refined.actual_method == "parabolic"
    assert refined.requested_method == "parabolic"
    assert refined.success is True
    assert refined.fallback is False
    fine_err = _err(refined.position, _TRUE_CENTER)
    assert fine_err < grid_err
    assert fine_err < 0.1


def test_parabolic_matches_legacy_implementation() -> None:
    """Regression coverage: test parabolic matches legacy implementation."""
    from core.qc.peak_detection import refine_parabolic

    shape = (64, 128)
    arr = _synth(shape, _TRUE_CENTER, _TRUE_SIGMA, noise=0.3)
    index = _argmax(arr)
    refined = lz.localize_peak(arr, index)
    value = np.asarray(arr, dtype=float)
    expected = tuple(refine_parabolic(value, list(index), axis) for axis in range(value.ndim))
    assert refined.position == pytest.approx(expected)
    assert lz.DEFAULT_LOCALIZATION_METHOD == "parabolic"
    assert lz.LOCALIZATION_METHODS == ("parabolic",)


def test_localization_records_method_and_boundary() -> None:
    """Regression coverage: test localization records method and boundary."""
    arr = _synth((16, 16), (7.4, 8.6), (1.5, 1.5))
    index = _argmax(arr)
    record = lz.localize_peak(arr, index).to_dict()
    assert record["localization_method"] == "parabolic"
    assert record["requested_method"] == "parabolic"
    assert record["actual_method"] == "parabolic"
    assert record["fallback"] is False
    assert record["fallback_reason"] == ""
    assert record["boundary_hit"] is False

    edge = lz.localize_peak(arr, (0, 0))
    assert edge.boundary_hit is True


def test_gaussian_method_is_rejected_everywhere() -> None:
    """Regression coverage: test gaussian method is rejected everywhere."""
    for name in ("gaussian", "Gaussian", "gauss", "gaussian_fit", "高斯", "高斯拟合"):
        with pytest.raises(lz.LocalizationError) as exc:
            lz.normalize_localization_method(name)
        assert "removed" in str(exc.value)
    assert lz.localization_supported("gaussian", 2) is False
    assert lz.localization_supported("parabolic", 3) is True
    with pytest.raises(lz.LocalizationError):
        lz.normalize_localization_method("lorentzian")


def test_localization_aliases_and_defaults() -> None:
    """Regression coverage: test localization aliases and defaults."""
    assert lz.normalize_localization_method("parabolic") == "parabolic"
    assert lz.normalize_localization_method("Parabola") == "parabolic"
    assert lz.normalize_localization_method("抛物线") == "parabolic"
    assert lz.normalize_localization_method(None) == "parabolic"
    assert lz.localization_label("parabolic")
    assert lz.localization_label("nope") == "nope"
    assert lz.load_localization_defaults() == {"method": "parabolic"}
    custom = lz.load_localization_defaults({"peaks": {"localization": {"method": "parabolic"}}})
    assert custom == {"method": "parabolic"}

    broken = lz.load_localization_defaults(
        {
            "peaks": {
                "localization": {
                    "method": "gaussian",
                    "gaussian_roi_f1_ppm": -3,
                }
            }
        }
    )
    assert broken == {"method": "parabolic"}


# ---------------------------------------------------- Peak-picking integration (2D)
def _write_ft2(path: Path, data: np.ndarray) -> None:
    """Write a synthetic NMRPipe 2D spectrum with N15/H1 labels and CAR (same ppm
    convention as peak picking).
    """
    from nmrglue.fileio import pipe

    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = data.shape[1]
    dic["FDSPECNUM"] = data.shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDDIMORDER"] = [2, 1]
    dic["FDDIMORDER1"] = 2
    dic["FDDIMORDER2"] = 1
    dic["FDF1LABEL"] = "N15"
    dic["FDF2LABEL"] = "H1"
    for prefix, spec in (("FDF1", _N15), ("FDF2", _H1)):
        dic[prefix + "SW"] = str(spec["sw"])
        dic[prefix + "OBS"] = str(spec["obs"])
        dic[prefix + "CAR"] = str(spec["car"])
        dic[prefix + "ORIG"] = str(
            spec["car"] * spec["obs"]
            - spec["sw"] / 2
            + spec["sw"] / data.shape[0 if prefix == "FDF1" else 1]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)


def _manager_with_spectrum(tmp_path: Path, spec_path: Path) -> tuple[ProjectManager, str, str]:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/data/1")
    manager.set_data_spectrum(entry.id, data.id, str(spec_path))
    return manager, entry.id, data.id


def _two_peak_spectrum() -> np.ndarray:
    arr = _synth((64, 128), _TRUE_CENTER, _TRUE_SIGMA, noise=0.5)
    arr = arr + _synth((64, 128), (45.28, 90.71), (2.4, 2.1), amp=60.0, noise=0.0)
    return arr + np.random.default_rng(7).normal(0.0, 0.5, (64, 128))


def test_pick_peaks_writes_sidecar_and_user_report(tmp_path: Path) -> None:
    """Regression coverage: test pick peaks writes sidecar and user report."""
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, _two_peak_spectrum())
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)

    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    assert result["status"] == "success"
    summary = result["localization"]
    assert summary["peak_localization_method"] == "parabolic"
    assert summary["n_peaks"] == result["peak_count"]
    assert summary["fallback_count"] == 0
    assert summary["records_path"].endswith(".localization.json")
    assert Path(summary["records_path"]).is_file()

    records = lz.read_localization_records(result["peak_path"])
    assert len(records) == result["peak_count"]
    for record in records:
        assert record["requested_method"] == "parabolic"
        assert record["actual_method"] == "parabolic"
        assert record["fallback"] is False

    runs = [r for r in manager.project.workflow_runs if r.workflow_ref == "pick_peaks"]
    assert runs[-1].params["localization"]["peak_localization_method"] == "parabolic"
    assert "gaussian" not in str(runs[-1].params["localization"])

    report = "\n".join(result["logs"])
    assert "== peak-picking report ==" in report
    assert "◆ settings" in report
    assert "◆ filtering" in report
    assert "◆ output" in report
    assert result["peak_path"] in report
    assert "three-point parabolic vertex" in report
    assert "'detection':" not in str(result)
    assert str(result).startswith("== peak-picking report ==")

    assert result["detection"]["sigma_multiplier"] == pytest.approx(25.0)
    assert isinstance(result["debug_logs"], list)


def test_pick_peaks_default_and_explicit_parabolic_identical(
    tmp_path: Path,
) -> None:
    """Backward compatibility: no method given (default) and explicit parabolic produce
    byte-identical peak tables.
    """
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, _two_peak_spectrum())
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    default = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    text_default = Path(default["peak_path"]).read_text(encoding="utf-8")
    explicit = pick_peaks(
        manager,
        exp_id,
        data_id,
        sigma_multiplier=25.0,
        localization_method="parabolic",
    )
    assert explicit["status"] == "success"
    assert Path(explicit["peak_path"]).read_text(encoding="utf-8") == text_default


def test_pick_peaks_gaussian_request_fails_loudly(tmp_path: Path) -> None:
    """Regression coverage: test pick peaks gaussian request fails loudly."""
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, _two_peak_spectrum())
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    with pytest.raises(PickPeaksError) as exc:
        pick_peaks(
            manager,
            exp_id,
            data_id,
            sigma_multiplier=25.0,
            localization_method="gaussian",
        )
    assert "removed" in str(exc.value)
    runs = [r for r in manager.project.workflow_runs if r.workflow_ref == "pick_peaks"]
    assert runs and runs[-1].status == "failed"


def test_subpoint_peak_position_is_better_than_grid(tmp_path: Path) -> None:
    """Regression coverage: test subpoint peak position is better than grid."""
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, _two_peak_spectrum())
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    result = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    rows = import_peaks_poky(result["peak_path"])
    assert rows
    step0 = _N15["sw"] / (64 * _N15["obs"])
    step1 = _H1["sw"] / (128 * _H1["obs"])
    truth = (
        _N15["car"] + (32 - _TRUE_CENTER[0]) * step0,
        _H1["car"] + (64 - _TRUE_CENTER[1]) * step1,
    )
    closest = min(
        rows,
        key=lambda row: (
            abs(float(row["N_shift"]) - truth[0]) + abs(float(row["H_shift"]) - truth[1])
        ),
    )
    assert abs(float(closest["N_shift"]) - truth[0]) < 0.5 * step0
    assert abs(float(closest["H_shift"]) - truth[1]) < 0.5 * step1
