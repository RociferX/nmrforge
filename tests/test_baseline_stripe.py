"""Baseline streak artifact regression test (0.2.132) + robust correction (0.2.190). Recurrence:
Trace-by-trace polynomial fitting is biased by strong peaks -> adjacent trace fitting
coefficient jumps -> vertical stripes; 0.2.190 and baseline.apply use robust fitting with
iterative peak masking, strong peaks are no longer biased, there are no streaks after correction
and the true baseline is safely corrected."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from core.data.bruker_reader import read_dataset
from core.processing import baseline as baseline_proc
from core.qc import baseline_quality as bq
from core.qc.baseline_quality import stripe_penalty
from workflow.baseline_optimize import optimize_baseline


def _write_ft2(path: Path, data: np.ndarray) -> None:
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


def _strong_peak_spectrum() -> np.ndarray:
    """Drift + noise + multiple scattered strong peaks: Trace-by-trace polynomial fitting is biased
    by strong peaks -> obvious vertical lines."""
    rng = np.random.default_rng(11)
    n1, n2 = 64, 128
    spec = np.zeros((n1, n2))
    spec += np.linspace(-30.0, 30.0, n2)[np.newaxis, :]
    spec += np.linspace(-5.0, 5.0, n1)[:, np.newaxis]
    for i, row in enumerate((6, 7, 22, 23, 40, 55, 56)):
        spec[row, 50 + 8 * i] += 3500.0
    spec += rng.normal(0.0, 3.0, spec.shape)
    return spec


def test_t1_ridge_not_flagged_as_stripe() -> None:
    """T1 noise band/first increment offset (the first few traces DC offset) are no longer
    misjudged as stripes (0.2.199-patch29ek: median sideband + p95 jump statistics). The old
    max/mean combination almost classified all real 2D spectra as 0.50 stripes."""
    rng = np.random.default_rng(21)
    n1, n2 = 256, 512
    spec = rng.normal(0.0, 1.0, size=(n1, n2))
    spec[:8] += np.linspace(20.0, 80.0, 8)[:, None]
    for i, row in enumerate((50, 120, 200)):
        spec[row, 100 + 120 * i] += 200.0
    m = bq.evaluate(spec, axis=1)
    assert m.stripe <= 0.1
    assert m.score >= 80.0
    assert not m.needs_correction


def test_edge_peak_not_flagged_as_stripe() -> None:
    """Strong peaks in the axis edge band no longer raise the end mean and misjudge it as stripes
    (the median sideband is robust)."""
    rng = np.random.default_rng(22)
    n1, n2 = 128, 512
    spec = rng.normal(0.0, 1.0, size=(n1, n2))
    for row in range(60, 66):
        spec[row, 500] += 150.0  # Peaks fall within the last 8% sidebands.
    m = bq.evaluate(spec, axis=1)
    assert m.stripe <= 0.1
    assert m.score >= 80.0


def test_single_trace_stripe_still_caught() -> None:
    """The true trace-by-trace baseline offset (whole trace DC offset) is still significantly
    penalized -- p95 only misses a small amount of artifact traces and will not miss the real
    stripes."""
    rng = np.random.default_rng(0)
    spec = rng.normal(0.0, 1.0, size=(24, 40))
    spec[1, :] += 50.0
    assert float(stripe_penalty(spec)) >= 0.25


def test_robust_correction_removes_slope_without_stripes() -> None:
    """Robust correction: After strong peak + drift spectrum correction, the slope returns to zero
    and no stripes are introduced (0.2.190). The old plain polyfit will be biased by sparse
    strong peaks -> adjacent trace fitting coefficient jumps -> vertical stripes (0.2.132
    regression); robust fitting iteratively shields the peak area and estimates the polynomial
    based only on baseline points."""
    spec = _strong_peak_spectrum()
    base = float(stripe_penalty(spec))
    corrected = baseline_proc.apply(
        spec.copy(),
        baseline_proc.BaselineParams(method="polynomial", axis="F2", order=2),
    )
    after = float(stripe_penalty(corrected))
    # Robust correction does not introduce streaks (old plain polyfit correction after > 0.2).
    assert after <= base + 0.05
    assert after < 0.1
    # The slope is corrected (the mean difference between the two ends is significantly reduced),
    # and the baseline score is improved.
    before = bq.evaluate(spec, axis=1)
    after_q = bq.evaluate(corrected, axis=1)
    assert abs(after_q.slope) < abs(before.slope) * 0.2
    assert after_q.score > before.score + 1.0

    # Control: When there is no strong peak, the same correction is performed without introducing
    # stripes.
    rng = np.random.default_rng(11)
    clean = np.zeros(spec.shape)
    clean += np.linspace(-30.0, 30.0, spec.shape[1])[np.newaxis, :]
    clean += np.linspace(-5.0, 5.0, spec.shape[0])[:, np.newaxis]
    clean += rng.normal(0.0, 3.0, clean.shape)
    corrected_clean = baseline_proc.apply(
        clean.copy(),
        baseline_proc.BaselineParams(method="polynomial", axis="F2", order=2),
    )
    assert float(stripe_penalty(corrected_clean)) < 0.2


def test_has_stripe_artifact_relative_veto() -> None:
    """0.2.199-patch9: Relative rejection only blocks candidates that are significantly worse than
    the original spectrum."""
    from workflow.baseline_optimize import _has_stripe_artifact, _stripe_ratio

    rng = np.random.default_rng(0)

    def striped(offset: float) -> np.ndarray:
        arr = rng.normal(0.0, 1.0, size=(24, 40))
        arr[1, :] += offset
        return arr

    clean = rng.normal(0.0, 1.0, size=(24, 40))
    orig = striped(50.0)   # Strong stripes in the original spectrum.
    improved = striped(20.0)  # Improved but still above threshold.
    worse = striped(200.0)  # Significantly worse.
    clean_r = _stripe_ratio(clean, 1)
    orig_r = _stripe_ratio(orig, 1)
    improved_r = _stripe_ratio(improved, 1)
    assert orig_r > 8.0 and improved_r > 8.0
    # Relative: an improved candidate is not blocked, a worse candidate is blocked.
    assert not _has_stripe_artifact(improved, 1, baseline_ratio=orig_r)
    assert _has_stripe_artifact(worse, 1, baseline_ratio=orig_r)
    # Clean original spectrum + candidate introduces stripes -> block (relatively rejected also
    # blocks).
    assert _has_stripe_artifact(worse, 1, baseline_ratio=clean_r)
    # Default absolute threshold behaviour remains.
    assert _has_stripe_artifact(worse, 1)


def test_optimize_baseline_corrects_peak_spectrum_safely(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Strong peak + drift spectrum: Robust correction candidate passes (no stripes are generated),
    and the axis is written back to correction. 0.2.190: After robust fitting shields the peak
    area, it is no longer biased by strong peaks, and hard stripe rejection no longer turns all
    corrections off -- the true baseline is safely corrected."""
    spec = _strong_peak_spectrum()
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, spec)
    experiment = read_dataset(bruker_dir / "hsqc_2d")

    result = optimize_baseline(experiment, ft2)
    # F2 detects drift and safely corrects it (old plain polyfit remains off).
    assert result.baseline["F2"]["enabled"] is True
    # Applying the selected configuration does not introduce streaks, and the baseline score is
    # improved.
    chosen = result.baseline["F2"]
    applied = spec.copy()
    if chosen["enabled"]:
        applied = baseline_proc.apply(
            applied,
            baseline_proc.BaselineParams(
                method="polynomial",
                axis="F2",
                order=max(int(chosen.get("order", 0) or 0), 1),
            ),
        )
    assert stripe_penalty(applied) <= stripe_penalty(spec) + 0.2
    assert (
        bq.evaluate(applied, axis=1).score
        > bq.evaluate(spec, axis=1).score + 1.0
    )


def test_optimize_baseline_axis_mapping(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """The scoring axis is consistent with the correction axis (the error axis is no longer
    corrected when dimensions direct dimension comes first)."""
    spec = np.zeros((32, 64))
    spec += np.linspace(-50.0, 50.0, 64)[np.newaxis, :]  # Along F2(numpy axis 1).
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, spec)
    experiment = read_dataset(bruker_dir / "hsqc_2d")

    result = optimize_baseline(experiment, ft2)
    # F2 detects the drift and corrects it; F1 is constant along the row, no gain remains off.
    assert result.baseline["F2"]["enabled"] is True
    assert result.baseline["F1"]["enabled"] is False
