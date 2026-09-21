"""Peak detection (for QC and peak tables, not for assignment, framework §19)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.qc import noise

# importing scipy.ndimage costs about 150ms the first time (measured on the VM): peak
# detection is needed only when peaks are really picked or snapped, so a top-level import
# would add to start-up time for nothing. Hence the lazy import (0.2.199-patch29hs).
_MAXIMUM_FILTER = None


def maximum_filter(*args: Any, **kwargs: Any) -> np.ndarray:
    """Lazy-import wrapper around scipy.ndimage.maximum_filter (cached after the first call)."""
    global _MAXIMUM_FILTER
    if _MAXIMUM_FILTER is None:
        from scipy.ndimage import maximum_filter as _impl

        _MAXIMUM_FILTER = _impl
    return _MAXIMUM_FILTER(*args, **kwargs)


@dataclass
class Peak:
    position: tuple[float, ...] = ()
    height: float = 0.0
    volume: float = 0.0
    width: tuple[float, ...] = ()
    snr: float = 0.0
    sign: int = 1
    # peak localisation diagnostics (2026-09-13, optional): records the localisation
    # method actually used and the Gaussian fit QC. Detection itself never writes this
    # field -- the caller in core.peaks.localize fills it in, and old code is unaffected
    # (None = no localisation diagnostics).
    localization: dict[str, Any] | None = None


@dataclass
class PeakDetectionParams:
    sigma_multiplier: float = 3.0
    min_snr: float = 3.0
    neighborhood: int = 3
    # peak sign mode (0.2.199-patch29ap, user rule):
    #   positive  positive peaks only (default, keeps the old behaviour);
    #   negative  negative peaks only;
    #   both      pick positive and negative peaks (mixed experiments such as the inverted
    #             13Ca/13Cb of HNCACB);
    #   dominant  keep only the sign that dominates (uniform experiments; the dominant sign
    #             is decided by the candidate count, ties by the sum of absolute
    #             intensities, and a remaining tie goes to positive -- user: "never mind
    #             positive or negative, it is clearly the more numerous ones").
    sign_mode: str = "positive"
    # axis peak exclusion (0.2.199-patch29at, user): drop peaks within edge_margin points
    # of the edges of axis 0 (top and bottom) -- axis peaks are the horizontal band at the
    # very top/bottom (indirect-dimension signal that never evolved lands on the F1 edge).
    edge_margin: int = 0


def _refined_index(value: np.ndarray, idx: np.ndarray, axis: int) -> float:
    """Sub-pixel peak position: parabolic vertex correction of the local maximum and its
    two neighbours along ``axis``.

    A real peak top usually falls between pixels, and the integer-grid argmax is off by
    about 0.34px on average (measured on the VM: 71% of peaks >0.25px, worst case 0.69px),
    which makes peak markers visibly miss the top when zoomed in; after the parabolic
    correction synthetic Gaussian peaks are accurate to ~0.03-0.07px (0.2.199-patch29eo).
    """
    i = int(idx[axis])
    if not (0 < i < value.shape[axis] - 1):
        return float(i)
    sl = list(idx)
    vals = []
    for di in (-1, 0, 1):
        sl[axis] = i + di
        vals.append(float(value[tuple(sl)]))
    v0, v1, v2 = vals
    denom = v0 - 2.0 * v1 + v2
    if abs(denom) < 1e-12:
        return float(i)
    offset = 0.5 * (v0 - v2) / denom
    return float(i + float(np.clip(offset, -0.5, 0.5)))


def refine_parabolic(value: np.ndarray, idx: Any, axis: int) -> float:
    """Public parabolic sub-pixel refinement (the same implementation as ``_refined_index``).

    The peak-localisation dispatcher (``core.peaks.localize``) reuses this function so that
    ``method="parabolic"`` stays **bit-for-bit identical** to the existing peak picking; do
    not add new logic here.
    """
    return _refined_index(value, idx, axis)


def _candidates(
    real: np.ndarray, sigma: float, params: PeakDetectionParams, sign: int
) -> list[Peak]:
    """Extract candidate peaks in the ``sign`` direction (+1/-1).

    Strict local maximum (the centre must exceed the ring neighbourhood, which rejects the
    false peaks on flats or ridges that merely "equal" the window maximum, fixed in
    0.2.199-patch29aq) plus intensity > noise x sigma plus the S/N threshold; the position
    then gets the sub-pixel parabolic correction (0.2.199-patch29eo) so that a peak top
    falling between pixels still lines up.
    """
    value = sign * real
    footprint = np.ones([params.neighborhood] * real.ndim, dtype=bool)
    ring = footprint.copy()
    ring[tuple(s // 2 for s in footprint.shape)] = False
    if ring.any():
        neighbor_max = maximum_filter(value, footprint=ring, mode="constant")
        mask = (value > neighbor_max) & (value > sigma * params.sigma_multiplier)
    else:
        maxima = maximum_filter(value, footprint=footprint, mode="constant")
        mask = (value == maxima) & (value > sigma * params.sigma_multiplier)
    peaks: list[Peak] = []
    margin = max(0, int(params.edge_margin))
    for idx in np.argwhere(mask):
        if margin and (idx[0] < margin or idx[0] >= real.shape[0] - margin):
            continue  # axis peak: the horizontal band at the top/bottom edge
        val = float(real[tuple(idx)])
        snr_value = abs(val) / sigma if sigma > 0 else 0.0
        if snr_value >= params.min_snr:
            position = tuple(
                _refined_index(value, idx, a) for a in range(real.ndim)
            )
            peaks.append(
                Peak(
                    position=position,
                    height=val,
                    snr=snr_value,
                    sign=sign,
                )
            )
    return peaks


def keep_dominant(candidates: list[Peak]) -> list[Peak]:
    """dominant mode: keep only the sign with more candidate peaks (ties by the sum of the
    absolute intensities, then positive).
    Picking may detect with both and let the spectrum share decide whether to call this
    function (0.2.199-patch29fc)."""
    counts: dict[int, int] = {1: 0, -1: 0}
    totals: dict[int, float] = {1: 0.0, -1: 0.0}
    for peak in candidates:
        s = 1 if peak.height >= 0 else -1
        counts[s] += 1
        totals[s] += abs(peak.height)
    dominant = 1
    for s in (-1, 1):
        if counts[s] > counts[dominant]:
            dominant = s
        elif counts[s] == counts[dominant] and totals[s] > totals[dominant]:
            dominant = s
    return [
        peak
        for peak in candidates
        if (1 if peak.height >= 0 else -1) == dominant
    ]


def snap_to_peak_top(
    data: Any, row: int, col: int, radius: int = 6
) -> tuple[int, int]:
    """Snap a click onto the nearby peak top (the local maximum of |value|).

    Look for the largest |value| inside a radius window around (row, col); when that point
    is clearly stronger than the clicked point (there is a peak top) return the snapped
    point, otherwise return the click unchanged (the user click is the peak).
    2D data only (the click-to-add-peak case).
    """
    real = np.real(np.asarray(data))
    if real.ndim != 2 or real.size == 0:
        return int(row), int(col)
    radius = max(1, int(radius))
    row, col = int(row), int(col)
    r0 = max(0, row - radius)
    r1 = min(real.shape[0], row + radius + 1)
    c0 = max(0, col - radius)
    c1 = min(real.shape[1], col + radius + 1)
    if r1 <= r0 or c1 <= c0:
        return row, col
    window = real[r0:r1, c0:c1]
    pr, pc = np.unravel_index(int(np.argmax(np.abs(window))), window.shape)
    pr += r0
    pc += c0
    if abs(float(real[pr, pc])) > abs(float(real[row, col])):
        return int(pr), int(pc)
    return row, col


def detect(data: Any, params: PeakDetectionParams | None = None) -> list[Peak]:
    """Local maxima + intensity > noise x sigma + S/N threshold (2D/3D alike).

    sign_mode controls the peak sign: uniform experiments (HSQC/COSY and other
    single-sign cases) use dominant to keep the dominant peaks only, while mixed
    experiments (HNCACB and other cases where both signs coexist) use both.
    Peak.height keeps the true sign (the CSV Intensity is signed too); snr uses the
    absolute value.
    """
    arr = np.asarray(data)
    params = params or PeakDetectionParams()
    sigma = noise.estimate(arr).global_sigma
    real = np.real(arr)
    mode = params.sign_mode
    signs = {
        "positive": (1,),
        "negative": (-1,),
        "both": (1, -1),
        "dominant": (1, -1),
    }.get(mode, (1,))
    candidates: list[Peak] = []
    for sign in signs:
        candidates.extend(_candidates(real, sigma, params, sign))
    if mode == "dominant" and candidates:
        candidates = keep_dominant(candidates)
    candidates.sort(key=lambda peak: abs(peak.height), reverse=True)
    return candidates
