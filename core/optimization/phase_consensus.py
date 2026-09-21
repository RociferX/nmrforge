"""Per-dimension consensus phase search (0.2.199-patch29i, the manual projection-tuning idea).

User scheme: manual 3D phase tuning looks at three projection planes -- the 1D spectrum of the
direct dimension is the set of complex traces of every (F1,F2) combination along the direct axis
(and likewise for the two indirect dimensions); phase these 1D spectra one by one and then take the
statistically best phase. Each dimension is independent and does not disturb the others: the
phase of
the other dimensions only contributes a per-peak constant offset that cancels symmetrically across
traces/peaks.

Requirement: the upstream must keep complex data (the full NMRPipe chain keeps PS without -di, or
holds complex data itself) so every 1D trace carries an imaginary part for the absorption/dispersion
discrimination and the per-dimension phase fit.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from core.optimization.phase_search import (
    _row_absorption,
    _row_p1_fit,
)
from ui_support.i18n import tr


def _row_p0_raw(
    arr: np.ndarray,
    positions: np.ndarray,
    heights: np.ndarray,
    p1_signal: float,
) -> float:
    """Raw p0 of a single trace once p1 (the signal ramp) is fixed (no +-180 disambiguation yet).

    Every peak phase is the common p0 plus a trace constant (t1 and the phase of the other
    dimensions); the negated weighted circular mean is the PS correction p0. The +-180 flips are
    resolved uniformly by the cross-trace consensus, so a single trace is never flipped early.
    """
    n = arr.shape[-1]
    vals = arr[positions]
    ramp = np.exp(-1j * np.deg2rad(p1_signal * positions / max(n - 1, 1)))
    unit = np.exp(1j * np.angle(vals * ramp))
    weights = heights + 1e-12
    vec = np.sum(weights * unit) / max(float(np.sum(weights)), 1e-12)
    return float((-np.rad2deg(np.angle(vec))) % 360.0)


def _lock_trace_peaks(
    row: np.ndarray,
    *,
    margin: int = 8,
    max_peaks: int = 8,
    snr: float = 10.0,
    global_frac: float = 0.005,
    global_max: float = 0.0,
    separation: int = 0,
    coherent: bool = True,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Lock the local maxima of a trace into peaks (custom, tuned for short traces).

    A 99-percentile threshold on a short trace (48 points, say) would cut everything but the
    strongest peak; here the gate is the larger of local MAD noise x snr and global maximum x
    global_frac, so a multi-peak trace locks all its peaks while a pure-noise trace (peak ~3-4x MAD
    < 8x MAD) returns None.

    0.2.199-patch29m: exclude the ringing side lobes of strong peaks -- the phase of a real peak is
    smooth inside a +-1 window (Lorentzian w~2 points: phase change ~+-27 deg) whereas ringing side
    lobes alternate by ~180 deg point to point (on real data this systematically pulled the
    per-trace
    p1 concentration fit towards the +-90 boundary). With coherent=True, candidates whose phase
    change across the +-1 window is too large are dropped; with separation>0, greedy selection keeps
    the largest peak and, within +-separation, only the tallest.
    """
    mag = np.abs(row)
    n = mag.size
    margin = min(margin, max(2, n // 6))  # adaptive for short axes (16 points -> 2)
    lo, hi = margin, n - margin
    if hi <= lo + 2:
        return None
    local = np.zeros(n, dtype=bool)
    local[lo:hi] = (mag[lo:hi] >= mag[lo - 1:hi - 1]) & (
        mag[lo:hi] > mag[lo + 1:hi + 1]
    )
    med = float(np.median(mag))
    mad = float(np.median(np.abs(mag - med))) * 1.4826 + 1e-12
    thr = max(snr * mad, global_frac * global_max)
    idx = np.where(local & (mag > thr))[0]
    if not idx.size:
        return None
    keep = []
    for p in idx:
        if coherent and 1 <= p <= n - 2:
            # phase change either side of the peak: smooth for a real peak, a big jump for ringing
            d1 = float(np.angle(row[p + 1] / (row[p] + 1e-30)))
            d2 = float(np.angle(row[p] / (row[p - 1] + 1e-30)))
            if abs(d1) + abs(d2) > 2.0:  # ~115 deg
                continue
        keep.append(int(p))
    if not keep:
        return None
    if separation <= 0:
        separation = max(3, n // 48)  # adaptive 3..n/48 (tighter on short axes)
    order = np.argsort(mag[keep])[::-1]
    selected: list[int] = []
    for i in order:
        p = keep[i]
        if any(abs(p - q) <= separation for q in selected):
            continue
        selected.append(p)
        if len(selected) >= max_peaks:
            break
    if not selected:
        return None
    sel = np.asarray(selected, dtype=np.intp)
    return sel, mag[sel]


def search_axis_phase_consensus(
    complex_arr: np.ndarray,
    axis: int,
    *,
    max_rows: int = 256,
    min_peaks: int = 2,
    margin: int = 8,
    max_peaks: int = 8,
    sign_mode: str = "uniform",  # noqa: ARG001 - unused since 0.2.199-patch29p
    # (the direct dimension is never flipped by 180)
    progress: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> tuple[float, float, float] | None:
    """Phase every 1D complex trace along the chosen axis individually, then take the statistical
    consensus.

    Per trace:
      - lock clean peaks (``_row_peak_positions``: row noise/global thresholds plus the leading and
        trailing margin);
      - p1: fit the multi-peak phase concentration (``_row_p1_fit``, decoupled from the trace
        constant);
      - p0: negate the circular mean of the peak phases under that p1 (``_row_p0_raw``, no early
        +-180 flip).
    Cross-trace consensus:
      - p1 = the median of the per-trace p1 values (the signal ramp; the PS correction negates it);
      - p0 = the circular mean of the raw per-trace p0 values after folding them into +-180 for
        alignment (two iterations); finally the global +-180 disambiguation uses the absorption of
        every peak window under the consensus phase (sign_mode=mixed skips the positive-peak
        preference and scores negative absorption peaks as well);
      - score = 100 x the median peak-window absorption of the traces under the consensus phase
        (0..100, matching the existing gate; a correct phase is usually >60).
    Returns (p0, p1, score); None when there are not enough clean peaks.
    """
    if cancel is not None and cancel():
        raise RuntimeError(
            tr(
            "cancelled by the user: dimension-by-dimension consensus phase "
            "search",
        )
        )
    moved = np.moveaxis(np.asarray(complex_arr, dtype=np.complex128), axis, -1)
    flat = moved.reshape(-1, moved.shape[-1])
    n = flat.shape[-1]
    if flat.ndim != 2 or n < 8:
        return None
    if flat.shape[0] > max_rows:
        index = np.linspace(0, flat.shape[0] - 1, max_rows).astype(int)
        flat = flat[index]
    global_max = float(np.max(np.abs(flat))) if flat.size else 0.0
    infos: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for row in flat:
        peaks = _lock_trace_peaks(
            row,
            margin=margin,
            max_peaks=max_peaks,
            global_max=global_max,
        )
        if peaks is None:
            continue
        pos, heights = peaks
        if pos.size >= 1:
            infos.append((row, pos, heights))
    if not infos:
        return None
    if progress is not None:
        progress(tr(
            "Dimension-wise consensus phase: axis {p0} locked {p1} clean "
            "trace",
            p0=axis,
            p1=len(infos),
        ))
    p1_rows: list[float] = []
    for arr, pos, heights in infos:
        fit = _row_p1_fit(arr, pos, heights)
        if fit is not None:
            p1_rows.append(fit[0])
    p1_signal = float(np.median(p1_rows)) if p1_rows else 0.0
    p1 = -p1_signal
    raw_p0 = np.array(
        [_row_p0_raw(arr, pos, heights, p1_signal) for arr, pos, heights in infos],
        dtype=float,
    )

    # canonical p0 angle: fold onto [0,180) by doubling (robust for an exact 180 bimodal case and
    # for any +-180 offset)
    folded = raw_p0 % 180.0
    p0 = (
        0.5
        * (
            np.rad2deg(
                np.arctan2(
                    np.mean(np.sin(2.0 * np.deg2rad(folded))),
                    np.mean(np.cos(2.0 * np.deg2rad(folded))),
                )
            )
        )
    ) % 180.0

    def _median_abs_and_sign(phase: float) -> tuple[float, float]:
        abs_vals: list[float] = []
        sign_vals: list[float] = []
        for arr, pos, heights in infos:
            a, sgn = _row_absorption(arr, pos, heights, phase, p1, radius=1)
            abs_vals.append(a)
            sign_vals.append(sgn)
        return float(np.median(abs_vals)), float(np.median(sign_vals))

    if sign_mode != "mixed":
        a0, s0 = _median_abs_and_sign(p0)
        a180, s180 = _median_abs_and_sign((p0 + 180.0) % 360.0)
        # the +-180 disambiguation uses the positive-peak sign (absorption is insensitive to the
        # sign
        # and cannot decide a flip)
        if s180 > s0:
            p0 = (p0 + 180.0) % 360.0
            a0 = a180
    else:
        a0, _s0 = _median_abs_and_sign(p0)
    score = 100.0 * a0
    if cancel is not None and cancel():
        raise RuntimeError(
            tr(
            "cancelled by the user: dimension-by-dimension consensus phase "
            "search",
        )
        )
    return p0, p1, score


def _hilbert(x: np.ndarray) -> np.ndarray:
    """One-dimensional Hilbert transform to supply the imaginary part, matching the display layer
    (scipy.signal.hilbert).

    0.2.199-patch29o fix: the direct-dimension search supplies the imaginary part with the standard
    analytic signal (positive frequencies doubled), matching the viewer ``_display_phase`` and what
    nmrDraw shows the eye -- the user tuned sampleK direct p0 to ~150 by hand, scipy gives a median
    per-trace p0 of 146.7 in agreement, whereas the imaginary part of the nmrPipe HT function itself
    (-H_scipy, state.md 0.2.101) would return the conjugate phase here (17.6/197.6, off by ~48 deg).
    The search output is exactly the p0 the script PS should carry.
    """
    n = x.size
    X = np.fft.fft(np.asarray(x, dtype=float))
    h = np.zeros(n)
    if n % 2 == 0:
        h[0] = h[n // 2] = 1.0
        h[1:n // 2] = 2.0
    else:
        h[0] = 1.0
        h[1:(n + 1) // 2] = 2.0
    return np.fft.ifft(X * h)


def _direct_projected_traces(
    real_spectrum: np.ndarray,
    axis: int,
) -> np.ndarray | None:
    """Direct-dimension projection traces (matching the XZ/YZ planes of proj3D.tcl -sum).

    For a 3D+ spectrum, sum as complex along each of the other axes (for a purely real spectrum a
    complex sum is a real sum) to get the projection planes, then take one trace along ``axis`` per
    point of the other dimension from each plane and concatenate them -- matching the two proj3D
    outputs that contain the direct dimension (summed over an indirect one); for a 2D spectrum the
    rows are used directly.

    Note: experiments such as HNN legitimately contain two indirect nuclei with the same name
    (15N/15N) and therefore repeated header labels, which proj3D.tcl cannot disambiguate when it
    picks an axis by label (sampleK reported "bad axis name Y"); this implementation picks the plane
    by the role and size of the direct axis instead.
    """
    arr = np.asarray(real_spectrum, dtype=float)
    axis = axis if axis >= 0 else arr.ndim - 1
    if arr.ndim < 2 or arr.shape[axis] < 8:
        return None
    if arr.ndim == 2:
        moved = np.moveaxis(arr, axis, -1)
        return moved.reshape(-1, moved.shape[-1])
    other = [a for a in range(arr.ndim) if a != axis]
    traces: list[np.ndarray] = []
    for keep in other:
        proj = arr.sum(axis=keep)
        proj_axis = axis if axis < keep else axis - 1
        moved = np.moveaxis(proj, proj_axis, -1)
        traces.append(moved.reshape(-1, moved.shape[-1]))
    return np.concatenate(traces, axis=0)


def search_direct_phase_real_ht(
    real_spectrum: np.ndarray,
    axis: int = -1,
    *,
    max_traces: int = 512,
    margin: int = 8,
    max_peaks: int = 8,
    sign_mode: str = "uniform",
    progress: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> tuple[float, float, float] | None:
    """Direct-dimension phase optimisation: take the direct-dimension projection traces from a
    purely
    real final spectrum, supply the imaginary part of each by HT, phase each trace and take the
    statistical optimum (user scheme 0.2.199-patch29l/patch29p).

    The 1D spectra are the direct-dimension projection traces (3D: one per F2 point summed along F1
    plus one per F1 point summed along F2, i.e. indirect-1 points + indirect-2 points; 2D: one per
    indirect point), matching the XZ/YZ planes of NMRPipe proj3D.tcl -sum (summed along the third
    axis); each real trace gets its imaginary part from a Hilbert transform (the display-layer scipy
    convention, Im = +H_scipy); per trace p1 is fitted from the phase concentration (multi-peak,
    decoupled from the cluster centre and the trace constant) and p0 then follows under that p1;
    across traces: p1 = median, p0 = circular mean folded onto the half circle.

    The +-180 flip is not forced: the sign only decides whether peaks point up and affects neither
    peak picking nor the lineshape (the user confirmed), and the projection traces mix 13C CA+ and
    CB-, which makes the sign statistics unreliable (a uniform flip once took 100/30 to 180 deg away
    from the hand-tuned value). The result is always the value folded into 0-180 deg, as in manual
    tuning (102/100/101 ~ 150, 30 ~ 2). score = 100 x the median peak absorption under the consensus
    phase.
    """
    if cancel is not None and cancel():
        raise RuntimeError(
            tr(
            "cancelled by the user: direct-dimension HT trace-by-trace phase "
            "search",
        )
        )
    flat = _direct_projected_traces(real_spectrum, axis)
    if flat is None:
        return None
    if flat.shape[0] > max_traces:
        index = np.linspace(0, flat.shape[0] - 1, max_traces).astype(int)
        flat = flat[index]
    global_max = float(np.max(np.abs(flat))) if flat.size else 0.0
    ht_rows: list[np.ndarray] = []
    for row in flat:
        cplx = _hilbert(row)
        peaks = _lock_trace_peaks(
            np.abs(cplx), margin=margin, max_peaks=max_peaks,
            global_max=global_max,
        )
        if peaks is not None and peaks[0].size >= 1:
            ht_rows.append(cplx)
    if not ht_rows:
        return None
    if len(ht_rows) > 16:
        # 0.2.199-patch29o: exclude the strongest 2% of traces (the phase of oversized or strongly
        # overlapping peaks differs from ordinary peaks; measured 153.7 after exclusion in agreement
        # with the user hand-tuned 150, with 28 unaffected)
        row_max = np.max(np.abs(np.asarray(ht_rows)), axis=-1)
        cutoff = float(np.percentile(row_max, 98))
        keep = [i for i, m in enumerate(row_max) if m <= cutoff]
        if keep:
            ht_rows = [ht_rows[i] for i in keep]
    if progress is not None:
        progress(tr(
            "direct dimension HT item by item phase: locked {p0} projection "
            "trace",
            p0=len(ht_rows),
        ))
    # inlined per-trace consensus (avoiding the gmax mismatch a second peak-locking pass inside
    # search_axis_phase_consensus would cause): fit p1 per trace from the concentration, then p0 per
    # trace under that global p1, take the folded circular mean and disambiguate +-180 by the
    # positive-peak sign; score = 100 x the median absorption under the consensus phase.
    infos: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for cplx in ht_rows:
        peaks = _lock_trace_peaks(
            np.abs(cplx), margin=margin, max_peaks=max_peaks,
            global_max=global_max,
        )
        if peaks is not None and peaks[0].size >= 1:
            infos.append((cplx, peaks[0], peaks[1]))
    if not infos:
        return None
    p1_rows: list[float] = []
    for cplx, pos, heights in infos:
        fit = _row_p1_fit(cplx, pos, heights)
        if fit is not None:
            p1_rows.append(fit[0])
    p1_signal = float(np.median(p1_rows)) if p1_rows else 0.0
    p1 = -p1_signal
    raw_p0 = np.array(
        [_row_p0_raw(c, p, h, p1_signal) for c, p, h in infos],
        dtype=float,
    )
    folded = raw_p0 % 180.0
    p0 = (
        0.5
        * np.rad2deg(
            np.arctan2(
                np.mean(np.sin(2.0 * np.deg2rad(folded))),
                np.mean(np.cos(2.0 * np.deg2rad(folded))),
            )
        )
    ) % 180.0

    # 0.2.199-patch29p: the direct dimension does not force a positive peak -- +-180 only flips the
    # peak sign and affects neither peak picking nor the lineshape; the folded value is the final p0
    # (0-180 deg) and the score uses its absorption.
    abs_vals: list[float] = []
    for cplx, pos, heights in infos:
        a, _sgn = _row_absorption(cplx, pos, heights, p0, p1, radius=1)
        abs_vals.append(a)
    score = 100.0 * float(np.median(abs_vals))
    if cancel is not None and cancel():
        raise RuntimeError(
            tr(
            "cancelled by the user: direct-dimension HT trace-by-trace phase "
            "search",
        )
        )
    return p0, p1, score

__all__ = ["search_axis_phase_consensus", "search_direct_phase_real_ht"]
