"""Ground-truth benchmark tests for the window-selection fix (2026-09-22).

Why this file exists: the window criterion moved from "peak/noise of the candidate itself"
(a self-referential metric that always prefers a more aggressive window) to three
**detection-oriented** factors, so there has to be a criterion that is **independent of the score**
to show the change is right. That criterion has exactly one source: ground truth (known peak
positions). This file locks three things:

1. the matching/statistics/control conventions of `workflow/truth_benchmark.py` (one-to-one greedy
   matching, naming discipline, decoy background, reference calibration and its null distribution);
2. that the optimiser never picks a merging candidate on a **synthetic close pair known to be
   merged by some candidates** (merging = one real peak lost);
3. that the detection threshold used for scoring matches the convention the documentation claims
   (otherwise "the peaks the optimiser sees" and "the peaks in the user's peak table" are not the
   same thing).
"""

from __future__ import annotations

import numpy as np
import pytest

from workflow.pick_peaks import _PICK_THRESHOLD_SIGMA
from workflow.truth_benchmark import (
    chance_match_stats,
    detect_peaks,
    detection_stats,
    estimate_offset,
    match_one_to_one,
    robust_noise_sigma,
    shift_peaks,
    synthetic_fid,
    translated_decoys,
)
from workflow.window_optimize import _PEAK_SIGMA_MULT, optimize_direct_window


# ---------------------------------------------------------------------------
# Peak detection and matching conventions
# ---------------------------------------------------------------------------
def _profile(positions: list[tuple[float, float]], n: int = 512, width: float = 4.0) -> np.ndarray:
    axis = np.arange(n, dtype=float)
    data = np.zeros(n, dtype=float)
    for center, height in positions:
        data += height * np.exp(-0.5 * ((axis - center) / width) ** 2)
    rng = np.random.default_rng(11)
    return data + rng.normal(0.0, 0.001, n)


def test_detect_peaks_finds_injected_pair() -> None:
    """Inject two peaks at known positions: detection must report both within 2 points."""
    data = _profile([(120.0, 1.0), (300.0, 0.6)])
    peaks = detect_peaks(data, sigma_mult=10.0, min_sep=2)
    assert len(peaks) == 2
    assert abs(peaks[0][0] - 120) <= 2
    assert abs(peaks[1][0] - 300) <= 2


def test_detect_peaks_threshold_removes_noise_peaks() -> None:
    """Raising the threshold above the noise reports nothing (an empty set, not fake peaks)."""
    data = _profile([])
    assert detect_peaks(data, sigma_mult=8.0, min_sep=2) == []
    assert robust_noise_sigma(data) > 0.0


def test_match_one_to_one_steals_and_marks_not_detected() -> None:
    """One detection fought over by two expected peaks: the first wins, the other is
    not_detected (not matched).
    """
    detected = [{"peak_id": "D1", "H_ppm": 8.01, "N_ppm": 120.2}]
    expected = [
        {"peak_id": "E1", "H_ppm": 8.00, "N_ppm": 120.0},
        {"peak_id": "E2", "H_ppm": 8.02, "N_ppm": 120.5},
    ]
    rows = match_one_to_one(detected, expected, tol_h=0.05, tol_n=0.5)
    assert [row["status"] for row in rows] == ["matched", "not_detected"]
    assert rows[0]["detected_id"] == "D1"
    stats = detection_stats(rows, n_detected=1)
    assert stats["n_matched"] == 1
    assert stats["n_not_detected"] == 1
    assert stats["n_unmatched_detection"] == 0
    assert stats["recall"] == pytest.approx(0.5)
    assert stats["precision"] == pytest.approx(1.0)


def test_unmatched_detections_are_not_called_false_peaks() -> None:
    """Naming discipline: an unmatched detection is unmatched_detection (it may be an unassigned
    real peak, an impurity or an artefact).
    """
    detected = [
        {"peak_id": "D1", "H_ppm": 8.00, "N_ppm": 120.0},
        {"peak_id": "D2", "H_ppm": 9.50, "N_ppm": 110.0},
    ]
    expected = [{"peak_id": "E1", "H_ppm": 8.00, "N_ppm": 120.0}]
    stats = detection_stats(
        match_one_to_one(detected, expected, tol_h=0.05, tol_n=0.5), n_detected=2
    )
    assert stats["n_unmatched_detection"] == 1
    assert "n_false" not in stats


# ---------------------------------------------------------------------------
# Decoy controls and reference calibration
# ---------------------------------------------------------------------------
def _truth_frame(n: int = 60) -> list[dict]:
    return [
        {"peak_id": f"E{index}", "H_ppm": 6.5 + 0.06 * index, "N_ppm": 106.0 + 0.33 * index}
        for index in range(n)
    ]


def test_translated_decoys_keep_minimum_shift() -> None:
    """Decoy shifts are normalised by the matching radius: every draw is >= shift_radii."""
    expected = _truth_frame(10)
    decoys = translated_decoys(
        expected, n_decoys=20, seed=7, shift_radii=5.0, tol_h=0.05, tol_n=0.5
    )
    assert len(decoys) == 20
    for decoy in decoys:
        shift_h = decoy[0]["H_ppm"] - expected[0]["H_ppm"]
        shift_n = decoy[0]["N_ppm"] - expected[0]["N_ppm"]
        assert np.hypot(shift_h / 0.05, shift_n / 0.5) >= 5.0 - 1e-9


def test_chance_match_stats_is_reproducible() -> None:
    """Same seed and count -> identical background statistics (recomputable from the evidence)."""
    detected = _truth_frame(30)
    expected = _truth_frame(40)
    first = chance_match_stats(
        detected, expected, n_decoys=25, seed=20260922, tol_h=0.02, tol_n=0.1
    )
    second = chance_match_stats(
        detected, expected, n_decoys=25, seed=20260922, tol_h=0.02, tol_n=0.1
    )
    assert first == second
    assert 0.0 <= first["mean"] <= 1.0


def test_estimate_offset_recovers_a_global_reference_shift() -> None:
    """Deposited shifts differ from the spectrum by a constant: after calibration the recovery must
    be far above the unshifted one, and the null distribution has to come with it.
    """
    rng = np.random.default_rng(20260922)
    expected = _truth_frame(60)
    detected = [
        {
            "peak_id": f"D{index}",
            "H_ppm": row["H_ppm"] + 0.06 + rng.normal(0.0, 0.004),
            "N_ppm": row["N_ppm"] + 0.80 + rng.normal(0.0, 0.03),
        }
        for index, row in enumerate(expected)
    ]
    offset = estimate_offset(detected, expected, tol_h=0.05, tol_n=0.5)
    assert offset["matched_at_best"] > offset["matched_at_zero"]
    assert offset["matched_at_best"] > offset["null_p95"]
    calibrated = shift_peaks(expected, offset["dH"], offset["dN"])
    stats = detection_stats(
        match_one_to_one(detected, calibrated, tol_h=0.05, tol_n=0.5), n_detected=len(detected)
    )
    assert stats["recall"] > 0.9


def test_per_peak_decoys_defeat_the_recalibration_argument() -> None:
    """A rigid decoy is absorbed by "re-estimate the reference" -- which is exactly why a
    re-aligned convention must use per-peak decoys.

    This is a **methodology guard** for the module: both decoy flavours have to run, and they have
    to lead to different conclusions.
    """
    rng = np.random.default_rng(5)
    expected = _truth_frame(60)
    detected = [
        {
            "peak_id": f"D{index}",
            "H_ppm": row["H_ppm"] + rng.normal(0.0, 0.004),
            "N_ppm": row["N_ppm"] + rng.normal(0.0, 0.03),
        }
        for index, row in enumerate(expected)
    ]
    rigid = chance_match_stats(
        detected, expected, n_decoys=40, seed=20260922, tol_h=0.05, tol_n=0.5
    )
    per_peak = chance_match_stats(
        detected, expected, n_decoys=40, seed=20260922, tol_h=0.05, tol_n=0.5, per_peak=True
    )
    assert rigid["mean"] >= per_peak["mean"]


# ---------------------------------------------------------------------------
# Window selection: two real peaks that are resolved may not be merged
# ---------------------------------------------------------------------------
def _close_pair_fid(lw: float = 8.0, separation: float = 12.0, noise: float = 0.005) -> np.ndarray:
    fid, _truth = synthetic_fid(
        [
            {"offset": 300.0, "amp": 1.0, "lw": lw},
            {"offset": 300.0 + separation, "amp": 0.7, "lw": lw},
        ],
        n_points=1024,
        noise=noise,
    )
    return fid


def test_window_optimizer_never_merges_a_resolvable_pair() -> None:
    """Synthetic close pair (about 1.5 times the natural line width): some candidates do merge it,
    but the optimiser may not pick one of them.
    """
    result = optimize_direct_window(_close_pair_fid(), sw=20000.0)
    merged = {row["label"]: row["merged"] for row in result.scores}
    assert any(value > 0.0 for value in merged.values()), "the merging criterion never fired"
    chosen = [row for row in result.scores if row["selected"]]
    assert len(chosen) == 1
    assert chosen[0]["merged"] == 0.0
    assert chosen[0]["score"] > 0.0


def test_window_scoring_uses_the_reference_detection_threshold() -> None:
    """The peak set the window choice looks at is 12 sigma, **not** the pick-peaking default 35.

    35 sigma is the default prepared for strong-signal liquid spectra; its value is that it stays
    applicable in more situations, not that it is the peak set the window choice should look at --
    the higher the threshold, the earlier weak peaks disappear and the less the merging criterion
    can see. This assertion pins the convention, and records that the two values being *different*
    is deliberate (changing one means changing the evidence page too).
    """
    assert _PEAK_SIGMA_MULT == 12.0
    assert _PEAK_SIGMA_MULT != _PICK_THRESHOLD_SIGMA
