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
from core.peaks.gaussian_fit import REASON_FLAT_REGION, REASON_NON_FINITE
from core.peaks.peak_table import import_peaks_poky
from core.project import ProjectManager
from workflow.pick_peaks import pick_peaks

_N15 = {"obs": 60.8, "sw": 2000.0, "car": 118.0}
_H1 = {"obs": 600.0, "sw": 6000.0, "car": 4.7}
# Non-integer true centre (user requirement: must not fall on the integer grid)
_TRUE_CENTER = (30.37, 60.62)
_TRUE_SIGMA = (2.2, 2.6)
# The synthetic peak is wider than the config default line width (~0.48 points/0.78
# points), so the ROI is given explicitly and generously
_ROI_F1_PPM = 2.0
_ROI_F2_PPM = 0.4


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
        axes.append(
            spec["car"] + (size / 2.0 - idx) * spec["sw"] / (size * spec["obs"])
        )
    return axes


def _argmax(arr: np.ndarray) -> tuple[int, int]:
    row, col = np.unravel_index(int(np.argmax(arr)), arr.shape)
    return int(row), int(col)


def _err(a: tuple[float, float], b: tuple[float, float]) -> float:
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


# --------------------------------------------------------------- Test 1
def test_gaussian_recovers_subpoint_center_2d() -> None:
    """Test 1: isolated Gaussian at a non-integer centre -- the fitted centre beats the
    integer grid and is more accurate."""
    shape = (64, 128)
    arr = _synth(shape, _TRUE_CENTER, _TRUE_SIGMA)
    axes = _ppm_axes(shape)
    index = _argmax(arr)
    grid_err = _err(index, _TRUE_CENTER)
    assert grid_err > 0.25  # the integer grid really is off (else the test is meaningless)

    parabolic = lz.localize_peak(arr, index, method="parabolic")
    # Give the ROI in ppm: the synthetic peak is wider than the config line width, so a
    # generous ROI keeps the fit well conditioned
    gaussian = lz.localize_peak(
        arr,
        index,
        method="gaussian",
        ppm_axes=axes,
        roi_f1_ppm=_ROI_F1_PPM,
        roi_f2_ppm=_ROI_F2_PPM,
    )

    assert gaussian.success and gaussian.actual_method == "gaussian"
    fit_err = _err(gaussian.position, _TRUE_CENTER)
    assert fit_err < 0.02                       # sub-grid: far better than the integer grid
    assert fit_err < grid_err / 10              # clearly better than the integer-grid maximum
    # Both methods land in the same order of magnitude (with no noise the parabola is
    # nearly exact for a Gaussian too)
    assert fit_err <= max(0.02, _err(parabolic.position, _TRUE_CENTER) * 3)

    record = gaussian.to_dict(axes)
    step0 = _N15["sw"] / (shape[0] * _N15["obs"])
    step1 = _H1["sw"] / (shape[1] * _H1["obs"])
    assert record["amplitude"] == pytest.approx(100.0, rel=0.01)
    assert record["baseline"] == pytest.approx(3.0, rel=0.05)
    assert record["fit_rmse"] < 0.1
    assert not record["boundary_hit"]
    # FWHM = 2*sqrt(2 ln 2) * sigma(ppm)
    assert record["fwhm_f1"] == pytest.approx(
        _TRUE_SIGMA[0] * lz.FWHM_FACTOR * step0, rel=0.03
    )
    assert record["fwhm_f2"] == pytest.approx(
        _TRUE_SIGMA[1] * lz.FWHM_FACTOR * step1, rel=0.03
    )
    # Centre ppm matches the true ppm (same axis mapping convention)
    assert record["center_f1"] == pytest.approx(
        lz.ppm_from_point(axes[0], _TRUE_CENTER[0]), abs=0.02 * step0
    )
    assert record["center_f2"] == pytest.approx(
        lz.ppm_from_point(axes[1], _TRUE_CENTER[1]), abs=0.02 * step1
    )


# --------------------------------------------------------------- Test 2
def test_gaussian_center_is_grid_density_independent() -> None:
    """Test 2: the same physical peak yields the same ppm centre and width on 1x/4x grids
    (zero filling)."""
    results: dict[int, tuple[dict, float]] = {}
    for factor in (1, 4):
        shape = (64 * factor, 128 * factor)
        center = (_TRUE_CENTER[0] * factor, _TRUE_CENTER[1] * factor)
        sigma = (_TRUE_SIGMA[0] * factor, _TRUE_SIGMA[1] * factor)
        arr = _synth(shape, center, sigma)
        axes = _ppm_axes(shape)
        loc = lz.localize_peak(
            arr,
            _argmax(arr),
            method="gaussian",
            ppm_axes=axes,
            roi_f1_ppm=_ROI_F1_PPM,
            roi_f2_ppm=_ROI_F2_PPM,
        )
        assert loc.success, loc.reason
        record = loc.to_dict(axes)
        results[factor] = (record, _N15["sw"] / (shape[0] * _N15["obs"]))

    coarse, coarse_step = results[1]
    fine, _ = results[4]
    # Centre ppm: the two grids differ by << one coarse-grid point spacing (otherwise the
    # centre would "drift with zero filling")
    assert abs(coarse["center_f1"] - fine["center_f1"]) < 0.1 * coarse_step
    assert abs(coarse["center_f2"] - fine["center_f2"]) < 0.1 * coarse_step
    # The width agrees as well (a physical quantity, independent of point spacing)
    assert coarse["fwhm_f1"] == pytest.approx(fine["fwhm_f1"], rel=0.05)
    assert coarse["fwhm_f2"] == pytest.approx(fine["fwhm_f2"], rel=0.05)
    # But sigma in point units really does differ (zero filling only changes point spacing)
    assert fine["sigma_points_f1"] == pytest.approx(
        coarse["sigma_points_f1"] * 4, rel=0.05
    )


# --------------------------------------------------------------- Test 3
def test_gaussian_and_parabolic_agree_on_strong_isolated_peak() -> None:
    """Test 3: on a high-S/N isolated peak the parabola and Gaussian results are very
    close."""
    shape = (64, 128)
    arr = _synth(shape, _TRUE_CENTER, _TRUE_SIGMA, noise=0.2)
    axes = _ppm_axes(shape)
    index = _argmax(arr)
    parabolic = lz.localize_peak(arr, index, method="parabolic")
    gaussian = lz.localize_peak(
        arr,
        index,
        method="gaussian",
        ppm_axes=axes,
        roi_f1_ppm=_ROI_F1_PPM,
        roi_f2_ppm=_ROI_F2_PPM,
    )
    assert gaussian.success
    assert _err(gaussian.position, parabolic.position) < 0.05
    assert _err(parabolic.position, _TRUE_CENTER) < 0.05
    assert _err(gaussian.position, _TRUE_CENTER) < 0.05


# --------------------------------------------------------------- Test 4
def test_gaussian_failure_paths_are_recorded_and_fall_back() -> None:
    """Test 4: failure does not crash: fit_success=False, parabola fallback, reason logged."""
    axes = _ppm_axes((32, 32))
    cases: list[tuple[str, np.ndarray, float, float, str]] = []
    flat = np.full((32, 32), 7.0)
    cases.append(("flat", flat, 0.5, 0.5, REASON_FLAT_REGION))
    nan_arr = _synth((32, 32), (16.2, 16.3), (2.0, 2.0))
    nan_arr[16, 16] = np.nan
    cases.append(("nan", nan_arr, 0.5, 0.5, REASON_NON_FINITE))
    # ROI radius too small (1 ppm step < 3 points) -> roi_too_small
    cases.append(
        ("tiny_roi", _synth((32, 32), (16.2, 16.3), (2.0, 2.0)), 0.2, 0.2, "roi_too_small")
    )
    for name, arr, roi1, roi2, expected in cases:
        loc = lz.localize_peak(
            arr,
            _argmax(arr) if name != "flat" else (16, 16),
            method="gaussian",
            ppm_axes=axes,
            roi_f1_ppm=roi1,
            roi_f2_ppm=roi2,
        )
        assert loc.success is False, name
        assert loc.fallback is True, name
        assert loc.actual_method == "parabolic", name
        assert loc.requested_method == "gaussian", name
        assert expected in loc.reason, f"{name}: {loc.reason}"
        # The fallback position must be finite: with NaN in the spectrum the parabola
        # returns NaN, so the Gaussian exit has to block it
        assert all(np.isfinite(v) for v in loc.position), name
        reference = lz.localize_peak(
            arr, _argmax(arr) if name != "flat" else (16, 16), method="parabolic"
        )
        if all(np.isfinite(v) for v in reference.position):
            assert loc.position == pytest.approx(reference.position)
        record = loc.to_dict(axes)
        assert record["gaussian_fit_success"] is False
        assert record["fallback"] is True
        assert record["fit_success"] is False
        assert record["fallback_reason"]
        assert record["fit_failure_reason"]
        assert record["requested_method"] == "gaussian"


# --------------------------------------------------------------- Test 5
def test_gaussian_is_rejected_for_non_2d_spectra() -> None:
    """Test 5: 1D/3D is rejected outright (message matches the user requirement) rather
    than silently running the wrong algorithm."""
    for ndim in (1, 3):
        data = np.zeros((8,) * ndim)
        with pytest.raises(lz.LocalizationError) as exc:
            lz.localize_peak(data, (4,) * ndim, method="gaussian")
        assert "only for 2D spectra" in str(exc.value)
    # The dimensionality helper itself must agree as well
    assert lz.localization_supported("gaussian", 2) is True
    assert lz.localization_supported("gaussian", 1) is False
    assert lz.localization_supported("gaussian", 3) is False
    assert lz.localization_supported("parabolic", 3) is True


# --------------------------------------------------------------- Test 6
def test_parabolic_default_matches_legacy_implementation() -> None:
    """Test 6: default method = parabola, matching the existing ``_refined_index`` bit for
    bit."""
    from core.qc.peak_detection import _refined_index

    shape = (64, 128)
    arr = _synth(shape, _TRUE_CENTER, _TRUE_SIGMA, noise=0.3)
    index = _argmax(arr)
    default = lz.localize_peak(arr, index)
    assert default.requested_method == "parabolic"
    assert default.actual_method == "parabolic"
    assert lz.DEFAULT_LOCALIZATION_METHOD == "parabolic"
    value = np.asarray(arr, dtype=float)
    expected = tuple(
        _refined_index(value, list(index), axis) for axis in range(value.ndim)
    )
    assert default.position == pytest.approx(expected)
    # A Gaussian request also uses the same candidate: the parabola position is the
    # Gaussian seed (§4)
    gaussian = lz.localize_peak(
        arr,
        index,
        method="gaussian",
        ppm_axes=_ppm_axes(shape),
    )
    assert gaussian.gaussian is not None
    assert gaussian.gaussian.seed == pytest.approx(default.position)


# ---------------------------------------------------- Peak-picking integration (2D)
def _write_ft2(path: Path, data: np.ndarray) -> None:
    """Write a synthetic NMRPipe 2D spectrum with N15/H1 labels and CAR (same ppm
    convention as peak picking)."""
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
    dic["FDF1LABEL"] = "N15"
    dic["FDF2LABEL"] = "H1"
    for prefix, spec in (("FDF1", _N15), ("FDF2", _H1)):
        dic[prefix + "SW"] = str(spec["sw"])
        dic[prefix + "OBS"] = str(spec["obs"])
        dic[prefix + "CAR"] = str(spec["car"])
        dic[prefix + "ORIG"] = "0"
    path.parent.mkdir(parents=True, exist_ok=True)
    pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)


def _manager_with_spectrum(
    tmp_path: Path, spec_path: Path
) -> tuple[ProjectManager, str, str]:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")
    manager.set_data_spectrum(entry.id, data.id, str(spec_path))
    return manager, entry.id, data.id


def _two_peak_spectrum() -> np.ndarray:
    arr = _synth((64, 128), _TRUE_CENTER, _TRUE_SIGMA, noise=0.5)
    arr = arr + _synth((64, 128), (45.28, 90.71), (2.4, 2.1), amp=60.0, noise=0.0)
    return arr + np.random.default_rng(7).normal(0.0, 0.5, (64, 128))


def test_pick_peaks_gaussian_writes_sidecar_and_provenance(
    tmp_path: Path,
) -> None:
    """Peak picking: Gaussian localization writes the peak table sidecar plus run
    parameters; the failure/fallback counts are available."""
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, _two_peak_spectrum())
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)

    baseline = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    parabolic_rows = import_peaks_poky(baseline["peak_path"])
    assert baseline["localization"]["peak_localization_method"] == "parabolic"
    assert baseline["localization"]["gaussian_fit_success_count"] == 0

    result = pick_peaks(
        manager,
        exp_id,
        data_id,
        sigma_multiplier=25.0,
        localization_method="gaussian",
    )
    assert result["status"] == "success"
    summary = result["localization"]
    assert summary["peak_localization_method"] == "gaussian"
    assert summary["n_peaks"] == result["peak_count"]
    assert summary["gaussian_fit_success_count"] + summary["gaussian_fallback_count"] == (
        result["peak_count"]
    )
    assert summary["records_path"].endswith(".localization.json")
    assert Path(summary["records_path"]).is_file()
    assert any("峰定位" in line and "高斯" in line for line in result["logs"])

    records = lz.read_localization_records(result["peak_path"])
    assert len(records) == result["peak_count"]
    for record in records:
        assert record["requested_method"] == "gaussian"
        assert record["actual_method"] in ("gaussian", "parabolic")
        assert record["fit_success"] is (record["actual_method"] == "gaussian")
        if record["actual_method"] == "gaussian":
            assert record["fwhm_f1"] > 0 and record["fwhm_f2"] > 0
            assert record["amplitude"] > 0

    # Run parameter provenance (WorkflowRun.params)
    runs = [
        r for r in manager.project.workflow_runs if r.workflow_ref == "pick_peaks"
    ]
    assert runs[-1].params["localization"]["peak_localization_method"] == "gaussian"
    assert runs[-1].params["localization"]["gaussian_roi_f1_ppm"] > 0

    # Both methods on the same batch of peaks: the positions differ only slightly, but the
    # Gaussian lands closer to the truth (sub-grid)
    gaussian_rows = import_peaks_poky(result["peak_path"])
    assert len(gaussian_rows) == len(parabolic_rows)
    step0 = _N15["sw"] / (64 * _N15["obs"])
    step1 = _H1["sw"] / (128 * _H1["obs"])
    diffs = [
        max(
            abs(float(a["N_shift"]) - float(b["N_shift"])) / step0,
            abs(float(a["H_shift"]) - float(b["H_shift"])) / step1,
        )
        for a, b in zip(parabolic_rows, gaussian_rows)
    ]
    assert max(diffs) < 1.0  # same candidate, never jumps to another peak
    truth = (_N15["car"] + (32 - _TRUE_CENTER[0]) * step0,
             _H1["car"] + (64 - _TRUE_CENTER[1]) * step1)
    best = max(
        zip(parabolic_rows, gaussian_rows),
        key=lambda pair: abs(float(pair[0]["H_shift"]) - truth[1]),
    )
    assert abs(float(best[1]["N_shift"]) - truth[0]) <= abs(
        float(best[0]["N_shift"]) - truth[0]
    ) + 1e-9


def test_pick_peaks_default_and_explicit_parabolic_identical(
    tmp_path: Path,
) -> None:
    """Backward compatibility: no method given (default) and explicit parabolic produce
    byte-identical peak tables."""
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
    text_explicit = Path(explicit["peak_path"]).read_text(encoding="utf-8")
    assert text_default == text_explicit
    assert default["peak_count"] == explicit["peak_count"]


def test_gaussian_fit_recovers_known_peak_with_analytic_jacobian() -> None:
    """Fitting with the analytic Jacobian still recovers the known centre/width accurately
    (2026-09-14 speed-up change)."""
    shape = (64, 128)
    arr = _synth(shape, _TRUE_CENTER, _TRUE_SIGMA, amp=100.0, base=3.0)
    result = lz.localize_peak_gaussian_2d(
        arr,
        [int(round(_TRUE_CENTER[0])), int(round(_TRUE_CENTER[1]))],
        sign=1,
        ppm_axes=_ppm_axes(shape),
        roi_f1_ppm=_ROI_F1_PPM,
        roi_f2_ppm=_ROI_F2_PPM,
    )
    assert result.success and result.actual_method == "gaussian"
    fit = result.gaussian
    assert fit is not None
    assert fit.center[0] == pytest.approx(_TRUE_CENTER[0], abs=0.05)
    assert fit.center[1] == pytest.approx(_TRUE_CENTER[1], abs=0.05)
    assert fit.sigma[0] == pytest.approx(_TRUE_SIGMA[0], rel=0.05)
    assert fit.sigma[1] == pytest.approx(_TRUE_SIGMA[1], rel=0.05)
    assert fit.amplitude == pytest.approx(100.0, rel=0.05)
    assert fit.baseline == pytest.approx(3.0, abs=0.5)


def test_gaussian_roi_points_are_capped_and_recorded() -> None:
    """On a fine (zero-filled) grid the fit window half-width is capped and recorded."""
    shape = (256, 512)
    center = (150.4, 300.3)
    arr = _synth(shape, center, (6.0, 7.0), amp=80.0, base=2.0)
    result = lz.localize_peak_gaussian_2d(
        arr,
        [int(round(center[0])), int(round(center[1]))],
        sign=1,
        ppm_axes=_ppm_axes(shape),
        roi_f1_ppm=_ROI_F1_PPM,
        roi_f2_ppm=_ROI_F2_PPM,
        roi_max_points=8,
    )
    meta = result.fit_meta
    assert meta["roi_capped"] is True
    assert "F1" in meta["roi_capped_axes"] or "F2" in meta["roi_capped_axes"]
    assert max(meta["roi_half_points"]) <= 8
    assert max(meta["roi_half_points_uncapped"]) > 8
    assert meta["max_nfev"] == lz.DEFAULT_GAUSSIAN_MAX_NFEV
    # No truncation when the cap is relaxed
    loose = lz.localize_peak_gaussian_2d(
        arr,
        [int(round(center[0])), int(round(center[1]))],
        sign=1,
        ppm_axes=_ppm_axes(shape),
        roi_f1_ppm=_ROI_F1_PPM,
        roi_f2_ppm=_ROI_F2_PPM,
        roi_max_points=10_000,
    )
    assert loose.fit_meta["roi_capped"] is False


def test_localization_defaults_expose_fit_budget_keys() -> None:
    """The fit budget is configurable: gaussian_roi_max_points / gaussian_max_nfev."""
    defaults = lz.load_localization_defaults()
    assert defaults["gaussian_roi_max_points"] == lz.DEFAULT_GAUSSIAN_ROI_MAX_POINTS
    assert defaults["gaussian_max_nfev"] == lz.DEFAULT_GAUSSIAN_MAX_NFEV
    custom = lz.load_localization_defaults(
        {
            "peaks": {
                "localization": {
                    "gaussian_roi_max_points": 24,
                    "gaussian_max_nfev": 60,
                }
            }
        }
    )
    assert custom["gaussian_roi_max_points"] == 24
    assert custom["gaussian_max_nfev"] == 60
    # Invalid values fall back to the defaults without raising
    broken = lz.load_localization_defaults(
        {
            "peaks": {
                "localization": {
                    "gaussian_roi_max_points": -3,
                    "gaussian_max_nfev": "abc",
                }
            }
        }
    )
    assert broken["gaussian_roi_max_points"] == lz.DEFAULT_GAUSSIAN_ROI_MAX_POINTS
    assert broken["gaussian_max_nfev"] == lz.DEFAULT_GAUSSIAN_MAX_NFEV


# ------------------------------------------------ Phase 12: weak-peak localization edges
@pytest.mark.parametrize("amp", [2.0, 1.0])
def test_weak_peak_never_silently_fabricates_a_fit(amp: float) -> None:
    """Weak peak (height of the same order as the noise σ=0.5): either succeed with a
    positive width, or fall back and state the reason.

    At height 1.0 the detected maximum may land on a noise point and the fit must fail;
    then the parabola has to be the fallback and ``fallback_reason`` must be recorded --
    never a plausible-looking narrow Gaussian.
    """
    shape = (64, 128)
    arr = _synth(
        shape, _TRUE_CENTER, _TRUE_SIGMA, amp=amp, base=3.0, noise=0.5, seed=11
    )
    axes = _ppm_axes(shape)
    index = _argmax(arr)

    result = lz.localize_peak(
        arr,
        index,
        method="gaussian",
        ppm_axes=axes,
        roi_f1_ppm=_ROI_F1_PPM,
        roi_f2_ppm=_ROI_F2_PPM,
    )

    assert np.isfinite(result.position).all()
    assert result.actual_method in ("gaussian", "parabolic")
    record = result.to_dict(axes)
    assert record["gaussian_fit_success"] is (result.actual_method == "gaussian")
    if result.actual_method == "gaussian":
        assert record["fwhm_f1"] > 0 and record["fwhm_f2"] > 0
        assert np.isfinite(record["fit_rmse"])
        assert not result.fallback
    else:
        assert result.fallback is True
        assert result.reason, "回退必须写明原因(不静默)"
        assert record["fallback"] is True
        assert record["fallback_reason"] == result.reason
