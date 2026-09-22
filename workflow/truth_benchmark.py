"""Ground-truth benchmark: checking detection and localisation against *known* peak positions
instead of comparing a processing result with itself (2026-09-22).

Why this layer exists: if window/parameter selection is scored only on *its own* spectrum (for
example "peak height / noise after apodisation"), the metric moves together with the candidate --
it can say which candidate fits that score best, but not which candidate is closer to the truth.
For the same reason, "a peak that shows up in every independent reconstruction of the same data"
only proves **repeatability**, not **truth**: a systematic artefact is reconstructed consistently
and would be counted as a real peak. There are only two ways to get truth:

1. **synthetic data**: positions/intensities/line widths are injected, so the truth is exact
   (recipe: ``synthetic_fid`` in this module);
2. **real data**: the published chemical shifts of the same sample and condition (BMRB and the
   like) give the expected peak positions (``load_expected_csv`` reads such an expected table).

Conventions (changing them means changing the tests too):

* match distance ``d = hypot(dH / tol_H, dN / tol_N)``; only ``d <= 1`` counts as a match;
* **one-to-one greedy nearest matching** (sorted by d, stable): a detected peak can be used by one
  expected peak only, and an expected peak that was stolen is reported as ``not_detected``;
* **naming discipline**: an unmatched detected peak is ``unmatched_detection`` (**not** a false
  peak -- it may be an unassigned real peak, a side-chain NH2, an impurity or an artefact); an
  unmatched expected peak is ``not_detected`` (**not** "it does not exist" -- it may be too weak,
  broadened, overlapped or shifted);
* **the referencing is aligned once and then frozen**: deposited shifts and the spectrum differ by
  a constant, estimated once by a grid scan (``estimate_offset``) and never re-aligned afterwards.
  To test "could this still match by chance *after* re-alignment", the decoys must be **per-peak
  independent shifts** (``per_peak=True``) -- a rigid translation is absorbed by the alignment;
* **chance matching needs a background control**: shift the expected table (rigid translation,
  fixed seed, fixed count) and compute "what fraction matches by pure chance" with the same
  matcher -- ``chance_recall``. Evidence must report it next to the measured value, otherwise
  "recovered the real peaks" and "the tolerance is wide enough to hit anything" cannot be told
  apart.

This module only matches, aggregates and synthesises. It never changes processing parameters.
Uses: (1) locking window/localisation behaviour in tests; (2) producing numbers on real data from
the evidence scripts.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

#: Default tolerances (ppm): the synthetic benchmark uses tight ones (1H 0.05 / 15N 0.15),
#: real data uses whatever its own plan specifies
DEFAULT_TOL_H = 0.05
DEFAULT_TOL_N = 0.15
#: 15N weight when aggregating position errors (same convention as the CSP literature)
DEFAULT_N_WEIGHT = 0.2
#: Required columns of an expected/detected peak table
PEAK_COLUMNS = ("peak_id", "H_ppm", "N_ppm")


# ---------------------------------------------------------------------------
# Noise and peak detection (window scoring and truth matching share one criterion)
# ---------------------------------------------------------------------------
def robust_noise_sigma(values: np.ndarray) -> float:
    """Robust noise sigma (MAD x 1.4826); peaks occupy few points, so they do not bias it."""
    data = np.asarray(values, dtype=float).ravel()
    if data.size == 0:
        return 0.0
    med = float(np.median(data))
    mad = float(np.median(np.abs(data - med)))
    return max(1.4826 * mad, float(np.finfo(float).eps))


def detect_peaks(
    amplitude: np.ndarray,
    *,
    sigma_mult: float = 5.0,
    min_sep: int = 2,
    noise_sigma: float | None = None,
) -> list[tuple[int, float]]:
    """1D peak detection on a spectrum: local maximum + noise threshold + minimum separation
    (greedy, strongest first).

    Returns ``[(position, height)]`` sorted by position. The "merging" criterion of the window
    scoring and the detection of the truth benchmark both call this one function -- the two
    conventions must agree, otherwise "the optimiser did not merge peaks" and "the benchmark says
    it merged peaks" would contradict each other.
    """
    data = np.asarray(amplitude, dtype=float).ravel()
    n = data.size
    if n < 3:
        return []
    sigma = robust_noise_sigma(data) if noise_sigma is None else float(noise_sigma)
    threshold = sigma * float(sigma_mult)
    candidates: list[tuple[float, int]] = []
    for index in range(1, n - 1):
        value = data[index]
        if value <= threshold:
            continue
        if value >= data[index - 1] and value >= data[index + 1]:
            candidates.append((float(value), index))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    taken: list[int] = []
    for _height, index in candidates:
        if any(abs(index - other) < int(min_sep) for other in taken):
            continue
        taken.append(index)
    taken.sort()
    return [(index, float(data[index])) for index in taken]


# ---------------------------------------------------------------------------
# Synthetic ground-truth recipe
# ---------------------------------------------------------------------------
def synthetic_fid(
    peaks: Sequence[dict[str, Any]],
    *,
    n_points: int = 1024,
    n_traces: int = 16,
    noise: float = 0.02,
    seed: int = 20260922,
) -> tuple[np.ndarray, list[dict[str, float]]]:
    """Build a synthetic FID from a known peak table (complex, exponential decay, white noise).

    Each entry of ``peaks``: ``{"offset": frequency (spectral point position, may be fractional),
    "amp": amplitude, "lw": line width (points)}``. The time-domain samples follow
    ``exp(2j*pi*offset*k/n) * exp(-pi*lw*k/n)``, so after the FFT the peak sits near ``offset``
    with a half-height width of about ``lw`` points -- the truth is known **to the point**.

    Returns ``(fid, truth)``; each entry of ``truth`` is ``{"peak_id", "offset", "amp"}`` (sorted).
    """
    rng = np.random.default_rng(seed)
    n = int(n_points)
    k = np.arange(n, dtype=float)
    signal = np.zeros(n, dtype=complex)
    for peak in peaks:
        offset = float(peak["offset"])
        amp = float(peak.get("amp", 1.0))
        lw = float(peak.get("lw", 4.0))
        signal += amp * np.exp(2j * np.pi * offset * k / n) * np.exp(-np.pi * lw * k / n)
    fid = np.tile(signal, (int(n_traces), 1))
    fid = fid * rng.uniform(1.0, 1.0, (fid.shape[0], 1))
    fid += rng.normal(0.0, noise, fid.shape) + 1j * rng.normal(0.0, noise, fid.shape)
    truth = [
        {
            "peak_id": f"T{index + 1}",
            "offset": float(peak["offset"]),
            "amp": float(peak.get("amp", 1.0)),
        }
        for index, peak in enumerate(sorted(peaks, key=lambda item: float(item["offset"])))
    ]
    return fid, truth


# ---------------------------------------------------------------------------
# Truth <-> detection matching
# ---------------------------------------------------------------------------
def _check_peaks(frame: Iterable[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    rows = [dict(row) for row in frame]
    for row in rows:
        for column in PEAK_COLUMNS:
            if column not in row:
                raise ValueError(f"{label} misses column {column!r}: {row}")
    return rows


def scaled_distance(
    detected: dict[str, Any],
    expected: dict[str, Any],
    *,
    tol_h: float = DEFAULT_TOL_H,
    tol_n: float = DEFAULT_TOL_N,
) -> float:
    """Normalised match distance ``hypot(dH/tol_H, dN/tol_N)`` (1D data can ignore tol_N)."""
    dh = float(detected["H_ppm"]) - float(expected["H_ppm"])
    dn = float(detected["N_ppm"]) - float(expected["N_ppm"])
    return float(math.hypot(dh / float(tol_h), dn / float(tol_n)))


def match_one_to_one(
    detected: Iterable[dict[str, Any]],
    expected: Iterable[dict[str, Any]],
    *,
    tol_h: float = DEFAULT_TOL_H,
    tol_n: float = DEFAULT_TOL_N,
) -> list[dict[str, Any]]:
    """One-to-one greedy nearest matching per expected peak; returns **one row per expected peak**
    (including the unmatched ones).
    """
    det = _check_peaks(detected, "detected")
    exp = _check_peaks(expected, "expected")
    taken: set[int] = set()
    rows: list[dict[str, Any]] = []
    for reference in exp:
        distances = np.array(
            [scaled_distance(item, reference, tol_h=tol_h, tol_n=tol_n) for item in det],
            dtype=float,
        )
        order = np.argsort(distances, kind="stable") if distances.size else []
        best: int | None = None
        for index in order:
            if float(distances[index]) <= 1.0 and int(index) not in taken:
                best = int(index)
                break
        if best is None:
            nearest = int(np.argmin(distances)) if distances.size else None
            rows.append(
                {
                    "expected_id": str(reference["peak_id"]),
                    "detected_id": "",
                    "status": "not_detected",
                    "dH": math.nan,
                    "dN": math.nan,
                    "scaled_distance": math.nan,
                    # "not detected" does not mean "nothing nearby": record the closest
                    # detection and how far it is, in tolerance units (2026-09-22 - the
                    # question readers keep asking when a black box sits on a strong peak).
                    "nearest_id": str(det[nearest]["peak_id"]) if nearest is not None else "",
                    "nearest_distance": (
                        float(distances[nearest]) if nearest is not None else math.nan
                    ),
                }
            )
            continue
        taken.add(best)
        hit = det[best]
        rows.append(
            {
                "expected_id": str(reference["peak_id"]),
                "detected_id": str(hit["peak_id"]),
                "status": "matched",
                "dH": float(hit["H_ppm"]) - float(reference["H_ppm"]),
                "dN": float(hit["N_ppm"]) - float(reference["N_ppm"]),
                "scaled_distance": float(distances[best]),
                "nearest_id": str(hit["peak_id"]),
                "nearest_distance": float(distances[best]),
            }
        )
    return rows


def detection_stats(
    matches: Sequence[dict[str, Any]],
    *,
    n_detected: int,
    n_weight: float = DEFAULT_N_WEIGHT,
) -> dict[str, Any]:
    """Detection/localisation metrics derived from the output of ``match_one_to_one``."""
    matched = [row for row in matches if row["status"] == "matched"]
    n_expected = len(matches)
    dh = np.array([row["dH"] for row in matched], dtype=float) if matched else np.array([])
    dn = np.array([row["dN"] for row in matched], dtype=float) if matched else np.array([])
    e_pos = np.hypot(dh, n_weight * dn) if matched else np.array([])
    return {
        "n_expected": int(n_expected),
        "n_detected": int(n_detected),
        "n_matched": int(len(matched)),
        "n_unmatched_detection": int(max(n_detected - len(matched), 0)),
        "n_not_detected": int(n_expected - len(matched)),
        "recall": float(len(matched) / n_expected) if n_expected else math.nan,
        "precision": float(len(matched) / n_detected) if n_detected else math.nan,
        "median_abs_dH": float(np.median(np.abs(dh))) if dh.size else math.nan,
        "p90_abs_dH": float(np.quantile(np.abs(dh), 0.9)) if dh.size else math.nan,
        "median_abs_dN": float(np.median(np.abs(dn))) if dn.size else math.nan,
        "p90_abs_dN": float(np.quantile(np.abs(dn), 0.9)) if dn.size else math.nan,
        "median_E_pos": float(np.median(e_pos)) if e_pos.size else math.nan,
        "rmse_E_pos": float(np.sqrt(np.mean(e_pos**2))) if e_pos.size else math.nan,
    }


# ---------------------------------------------------------------------------
# Chance-matching background (control)
# ---------------------------------------------------------------------------
def _draw_shift(
    rng: np.random.Generator,
    shift_radii: float,
    tol_h: float,
    tol_n: float,
) -> tuple[float, float]:
    """Draw one shift vector that is at least ``shift_radii`` match radii away (normalised)."""
    while True:
        angle = rng.uniform(0.0, 2.0 * math.pi)
        radius = float(shift_radii) * rng.uniform(1.0, 2.0)
        shift_h = radius * tol_h * math.cos(angle)
        shift_n = radius * tol_n * math.sin(angle)
        if math.hypot(shift_h / tol_h, shift_n / tol_n) >= float(shift_radii):
            return shift_h, shift_n


def translated_decoys(
    expected: Sequence[dict[str, Any]],
    *,
    n_decoys: int = 200,
    seed: int = 20260922,
    shift_radii: float = 5.0,
    tol_h: float = DEFAULT_TOL_H,
    tol_n: float = DEFAULT_TOL_N,
    span_h: tuple[float, float] | None = None,
    span_n: tuple[float, float] | None = None,
    per_peak: bool = False,
) -> list[list[dict[str, Any]]]:
    """Shift the expected table to build decoy control tables (same seed and count -> fully
    reproducible).

    The shift is normalised by the **matching radius**:
    ``sqrt((dH/tol_H)^2 + (dN/tol_N)^2) >= shift_radii``, so shifted positions are guaranteed to
    be far from the original truth; if ``span_*`` is given the shifted points are folded back into
    the spectral range, otherwise they are unconstrained.

    ``per_peak=False`` (default) is a **rigid translation**: every peak shares one vector. It is
    only a control for matching in a *frozen* reference frame -- as soon as the referencing may be
    re-estimated, a rigid translation is absorbed by the alignment and the control is meaningless.
    To ask "could this still match by chance *after* re-alignment", use ``per_peak=True``: each
    peak draws its own shift, so the marginal distribution is unchanged but the peak-to-peak
    correspondence is destroyed.
    """
    rng = np.random.default_rng(int(seed))
    base = _check_peaks(expected, "expected")
    out: list[list[dict[str, Any]]] = []
    for index in range(int(n_decoys)):
        rigid_h, rigid_n = _draw_shift(rng, shift_radii, tol_h, tol_n)
        rows = []
        for item in base:
            if per_peak:
                shift_h, shift_n = _draw_shift(rng, shift_radii, tol_h, tol_n)
            else:
                shift_h, shift_n = rigid_h, rigid_n
            row = dict(item)
            row["peak_id"] = f"{item['peak_id']}#d{index}"
            row["H_ppm"] = float(item["H_ppm"]) + shift_h
            row["N_ppm"] = float(item["N_ppm"]) + shift_n
            if span_h is not None and not (span_h[0] <= row["H_ppm"] <= span_h[1]):
                continue
            if span_n is not None and not (span_n[0] <= row["N_ppm"] <= span_n[1]):
                continue
            rows.append(row)
        out.append(rows)
    return out


def chance_match_stats(
    detected: Sequence[dict[str, Any]],
    expected: Sequence[dict[str, Any]],
    *,
    n_decoys: int = 200,
    seed: int = 20260922,
    shift_radii: float = 5.0,
    tol_h: float = DEFAULT_TOL_H,
    tol_n: float = DEFAULT_TOL_N,
    per_peak: bool = False,
) -> dict[str, float]:
    """Chance-matching background: recall statistics over the decoy tables (report next to the
    measured recall).

    ``per_peak=True`` pairs with a matcher that re-aligned the referencing (see
    :func:`translated_decoys`).
    """
    rates: list[float] = []
    for decoy in translated_decoys(
        expected,
        n_decoys=n_decoys,
        seed=seed,
        shift_radii=shift_radii,
        tol_h=tol_h,
        tol_n=tol_n,
        per_peak=per_peak,
    ):
        if not decoy:
            continue
        decoy_ids = {"H_ppm", "N_ppm", "peak_id"}
        rows = [{key: value for key, value in row.items() if key in decoy_ids} for row in decoy]
        matches = match_one_to_one(detected, rows, tol_h=tol_h, tol_n=tol_n)
        rates.append(float(np.mean([row["status"] == "matched" for row in matches])))
    if not rates:
        return {"n_decoys": 0, "mean": math.nan, "p95": math.nan, "max": math.nan}
    values = np.array(rates, dtype=float)
    return {
        "n_decoys": int(values.size),
        "mean": float(values.mean()),
        "p95": float(np.quantile(values, 0.95)),
        "max": float(values.max()),
    }


# ---------------------------------------------------------------------------
# Global reference shift (real data: deposited shifts and the spectrum differ by a constant)
# ---------------------------------------------------------------------------
def _matched_within(
    diff_h: np.ndarray,
    diff_n: np.ndarray,
    d_h: float,
    d_n: float,
    tol_h: float,
    tol_n: float,
) -> int:
    """Fast counter for the grid scan: expected peaks that have **at least one** detection in
    tolerance.

    ``diff_h[j, i] = detected[i].H - expected[j].H`` (precomputed), so a shifted grid point costs
    one subtraction and one comparison. It is not the same convention as the reported
    ``match_one_to_one`` (no one-to-one), it is only the scan objective; every reported number uses
    the one-to-one matcher.
    """
    mask = (np.abs(diff_h - d_h) <= tol_h) & (np.abs(diff_n - d_n) <= tol_n)
    return int(np.count_nonzero(mask.any(axis=1)))


def _median_inlier_distance(
    diff_h: np.ndarray,
    diff_n: np.ndarray,
    d_h: float,
    d_n: float,
    tol_h: float,
    tol_n: float,
) -> float:
    """Median (normalised) distance from a matched expected peak to its nearest detection at that
    shift.
    """
    distance = np.hypot((diff_h - d_h) / tol_h, (diff_n - d_n) / tol_n)
    per_expected = distance.min(axis=1)
    inside = per_expected <= 1.0
    if not bool(inside.any()):
        return float("inf")
    return float(np.median(per_expected[inside]))


def _scan(
    diff_h: np.ndarray,
    diff_n: np.ndarray,
    center_h: float,
    center_n: float,
    half_h: float,
    half_n: float,
    step_h: float,
    step_n: float,
    tol_h: float,
    tol_n: float,
) -> dict[str, Any]:
    """Scan one grid over ``center +- half``: most matches, ties broken by smallest median
    distance.

    Maximising the count alone lands on the wrong shift when the tolerance is wide and the peaks
    are dense (measured: 0.14 ppm off in 15N); breaking ties by the median distance is the standard
    fix (same convention as the external reference calibration).
    """
    shifts_h = np.arange(-half_h, half_h + step_h * 0.5, step_h) + center_h
    shifts_n = np.arange(-half_n, half_n + step_n * 0.5, step_n) + center_n
    best_count = -1
    candidates: list[tuple[float, float]] = []
    counts: list[float] = []
    for shift_h in shifts_h:
        for shift_n in shifts_n:
            count = _matched_within(
                diff_h, diff_n, float(shift_h), float(shift_n), tol_h, tol_n
            )
            counts.append(float(count))
            if count > best_count:
                best_count = count
                candidates = [(float(shift_h), float(shift_n))]
            elif count == best_count:
                candidates.append((float(shift_h), float(shift_n)))
    array = np.array(counts, dtype=float)
    if best_count < 0 or not candidates:
        return {
            "dH": float(center_h), "dN": float(center_n), "count": -1,
            "distance": float("inf"), "counts": array,
        }

    def _key(item: tuple[float, float]) -> float:
        return _median_inlier_distance(diff_h, diff_n, item[0], item[1], tol_h, tol_n)

    best_h, best_n = min(candidates, key=_key)
    return {
        "dH": float(best_h), "dN": float(best_n), "count": int(best_count),
        "distance": float(_key((best_h, best_n))), "counts": array,
    }


def estimate_offset(
    detected: Sequence[dict[str, Any]],
    expected: Sequence[dict[str, Any]],
    *,
    tol_h: float = DEFAULT_TOL_H,
    tol_n: float = DEFAULT_TOL_N,
    span_h: float = 0.30,
    span_n: float = 2.0,
    coarse_h: float = 0.01,
    coarse_n: float = 0.10,
) -> dict[str, Any]:
    """Estimate one global reference shift (detected - expected) with a grid scan whose tolerance
    tightens stage by stage.

    Why one tolerance is not enough: deposited shifts and the spectrum differ by a constant
    (different lab / different script), so matching the deposited coordinates directly misses
    everything; but **the looser the tolerance, the flatter the objective** -- when the peak
    spacing is comparable to the tolerance, a shift half a spacing away matches just as many peaks
    (measured: 0.14 ppm off in 15N, and it can even push the reference the wrong way). So: the
    first stage scans the full span at a **moderate tolerance** (``min(tol, 0.02/0.10)``), then two
    more stages tighten to ``0.4x/0.2x -> 0.2x/0.1x``, each searching the neighbourhood of the
    previous stage; the objective is "expected peaks with at least one detection in tolerance",
    ties broken by smallest **median distance**. A tightening stage that yields nothing means the
    assumption does not hold, so that stage is dropped and the previous result is kept; if even the
    first stage is empty the caller's tolerance is used as a fallback.

    This never scores candidates and never selects parameters; it aligns the reference once. The
    ``dH``/``dN`` returned to the caller is what should be **added to the expected peaks**. Returns:

    * ``matched_at_best`` / ``matched_at_zero``: matches at the caller's tolerance after
      calibration vs without any shift;
    * ``null_median`` / ``null_p95``: distribution of the match count over the **full-span grid**
      of the first stage -- the level a random shift would reach (a grid instead of random draws:
      cheaper and reproducible);
    * ``refine_stages``: tolerance, match count and landing point of every stage (evidence that the
      calibration converged instead of getting lucky).
    """
    det = _check_peaks(detected, "detected")
    exp = _check_peaks(expected, "expected")
    if not det or not exp:
        return {
            "dH": 0.0, "dN": 0.0, "matched_at_best": 0.0, "matched_at_zero": 0.0,
            "median_distance_at_best": float("inf"), "null_median": 0.0, "null_p95": 0.0,
            "n_expected": float(len(exp)), "grid_points": 0.0, "refine_stages": [],
        }
    det_h = np.array([float(row["H_ppm"]) for row in det], dtype=float)
    det_n = np.array([float(row["N_ppm"]) for row in det], dtype=float)
    exp_h = np.array([float(row["H_ppm"]) for row in exp], dtype=float)
    exp_n = np.array([float(row["N_ppm"]) for row in exp], dtype=float)
    diff_h = det_h[None, :] - exp_h[:, None]
    diff_n = det_n[None, :] - exp_n[:, None]

    #: First-stage tolerance: never looser than 0.02/0.10 (see the docstring)
    start_tol = (min(float(tol_h), 0.02), min(float(tol_n), 0.10))
    stages: list[dict[str, float]] = []

    def _record(found: dict[str, Any], tol: tuple[float, float]) -> None:
        stages.append(
            {
                "tol_h": float(tol[0]),
                "tol_n": float(tol[1]),
                "matched": float(found["count"]),
                "dH": float(found["dH"]),
                "dN": float(found["dN"]),
            }
        )

    found = _scan(
        diff_h, diff_n, 0.0, 0.0, span_h, span_n, coarse_h, coarse_n, *start_tol
    )
    null_counts = found["counts"]
    _record(found, start_tol)
    current_tol = start_tol
    if found["count"] <= 0:
        fallback = (float(tol_h), float(tol_n))
        found = _scan(
            diff_h, diff_n, 0.0, 0.0, span_h, span_n, coarse_h, coarse_n, *fallback
        )
        _record(found, fallback)
        current_tol = fallback
    center_h, center_n = float(found["dH"]), float(found["dN"])
    half_h, half_n = float(coarse_h), float(coarse_n)

    for factor_h, factor_n in ((0.4, 0.2), (0.2, 0.1)):
        next_tol = (current_tol[0] * factor_h, current_tol[1] * factor_n)
        step_h, step_n = half_h / 5.0, half_n / 5.0
        found = _scan(
            diff_h, diff_n, center_h, center_n, half_h, half_n,
            step_h, step_n, *next_tol,
        )
        _record(found, next_tol)
        if found["count"] <= 0:
            break
        center_h, center_n = float(found["dH"]), float(found["dN"])
        half_h, half_n = step_h, step_n
        current_tol = next_tol

    counts = null_counts if null_counts is not None else np.zeros(1)
    return {
        "dH": float(center_h),
        "dN": float(center_n),
        "matched_at_best": float(
            _matched_within(diff_h, diff_n, center_h, center_n, tol_h, tol_n)
        ),
        "matched_at_zero": float(_matched_within(diff_h, diff_n, 0.0, 0.0, tol_h, tol_n)),
        "median_distance_at_best": float(
            _median_inlier_distance(diff_h, diff_n, center_h, center_n, tol_h, tol_n)
        ),
        "null_median": float(np.median(counts)),
        "null_p95": float(np.quantile(counts, 0.95)),
        "n_expected": float(len(exp)),
        "grid_points": float(counts.size),
        "refine_stages": stages,
    }


def shift_peaks(
    peaks: Sequence[dict[str, Any]],
    d_h: float,
    d_n: float,
    *,
    prefix: str = "",
) -> list[dict[str, Any]]:
    """Shift a whole peak table (add the estimated offset to the expected peaks, or its negative to
    the detections).
    """
    out: list[dict[str, Any]] = []
    for item in _check_peaks(peaks, "peaks"):
        row = dict(item)
        row["H_ppm"] = float(item["H_ppm"]) + float(d_h)
        row["N_ppm"] = float(item["N_ppm"]) + float(d_n)
        if prefix:
            row["peak_id"] = f"{prefix}{row['peak_id']}"
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Reading/writing expected peak tables (real data: expected positions from published shifts)
# ---------------------------------------------------------------------------
def load_expected_csv(
    path: Path | str,
    *,
    h_column: str = "H_ppm",
    n_column: str = "N_ppm",
) -> list[dict[str, Any]]:
    """Read an expected peak table CSV (at least ``peak_id, H_ppm, N_ppm``; N may be empty for 1D
    data).
    """
    rows: list[dict[str, Any]] = []
    # utf-8-sig: deposited peak tables usually carry a BOM; without it the first column name
    # reads as "\ufeffpeak_id" and peak_id silently degrades to "E1".
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            if not row:
                continue
            peak_id = str(row.get("peak_id") or row.get("id") or f"E{index + 1}")
            h_value = row.get(h_column) or row.get("H_ppm") or ""
            n_value = row.get(n_column) or row.get("N_ppm") or ""
            if not str(h_value).strip():
                continue
            rows.append(
                {
                    "peak_id": peak_id,
                    "H_ppm": float(h_value),
                    "N_ppm": float(n_value) if str(n_value).strip() else 0.0,
                }
            )
    return rows


def write_matches_csv(path: Path | str, matches: Sequence[dict[str, Any]]) -> None:
    """Write the matching detail to CSV (used by the evidence scripts)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "expected_id", "detected_id", "status", "dH", "dN", "scaled_distance",
                "nearest_id", "nearest_distance",
            ],
        )
        writer.writeheader()
        for row in matches:
            writer.writerow(row)
