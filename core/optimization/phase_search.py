"""Direct-dimension / final-spectrum phase search.

Direct-dimension phase search primitives (used by the backend and the unified phase route):
- ``search_direct_spectrum_phase``: aggregated (p0, p1) search on a direct-dimension FT spectrum;
- ``nus_direct_phase``: non-uniform DFT phase correction of the NUS direct dimension;
- ``search_direct_phase_on_spectrum``: symmetry-scored search on a reconstructed plane or final
  spectrum.
(Since 0.2.164 the old entry points search_phase/search_spectrum_phase/apply_phase_axis were
removed in favour of memory_phase_search; the old git history keeps them.)

Metrics: the absorption ratio over the peak window (+-5 points) Sigma|Re|/(Sigma|Re|+Sigma|Im|)
drives the fine tuning, while the signed ratio (SigmaRe+Sigmaneg)/Sigma|Re| disambiguates +-180.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import numpy as np

from ui_support.i18n import tr

logger = logging.getLogger("nmrforge.optimization.phase_search")


def direct_ft_traces(
    fid: np.ndarray,
    *,
    zf_size: int | None = None,
    sp_off: float = 0.45,
    sp_end: float = 0.95,
    sp_pow: float = 1,
) -> np.ndarray:
    """Apply SP+ZF+FT along the direct dimension (the last axis); returns complex traces shaped
    (n_traces, n)."""
    arr = np.asarray(fid)
    n = arr.shape[-1]
    t = np.linspace(0.0, 1.0, n)
    window = np.sin(np.pi * (sp_off + (sp_end - sp_off) * t)) ** sp_pow
    work = arr * window
    if zf_size is not None and zf_size > n:
        pad = [(0, 0)] * work.ndim
        pad[-1] = (0, int(zf_size) - n)
        work = np.pad(work, pad)
    spectrum = np.fft.fft(work, axis=-1)
    return spectrum.reshape(-1, spectrum.shape[-1])


def _trace_profiles(
    traces: np.ndarray, positions: np.ndarray, radius: int = 5
) -> np.ndarray:
    """Slice a +-radius window at each fixed peak position; returns (m, 2*radius+1) profiles."""
    n = traces.shape[-1]
    offset = np.arange(-radius, radius + 1)
    index = np.clip(positions[:, None] + offset[None, :], 0, n - 1)
    rows = np.arange(len(positions))[:, None]
    return traces[rows, index]


def _window_metrics(profiles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per profile: (absorption ratio, signed ratio)."""
    real = np.real(profiles)
    re_abs = np.abs(real)
    im_abs = np.abs(np.imag(profiles))
    denom = re_abs + im_abs + 1e-12
    absorption = np.sum(re_abs, axis=-1) / np.sum(denom, axis=-1)
    positive = np.clip(real, 0.0, None).sum(axis=-1)
    negative = np.clip(real, None, 0.0).sum(axis=-1)
    sign = (positive + negative) / (np.sum(re_abs, axis=-1) + 1e-12)
    return absorption, sign


# -------------------------------------- direct-dimension FT spectrum (p0, p1) frequency search


def _row_peak_positions(
    spectrum: np.ndarray,
    *,
    max_peaks: int = 8,
    margin: int = 0,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Lock the top-K local peaks of a 1D direct-dimension spectrum; returns (indices, heights).

    A peak must exceed 5x the corner noise (a pure-noise trace returns None). With margin>0 the
    first and last margin points are excluded (in real data the ends of a direct-dimension FT are
    often DC/Nyquist artefacts, several times stronger than real peaks).
    """
    arr = np.asarray(spectrum, dtype=np.complex128)
    n = arr.shape[-1]
    if n < 8:
        return None
    mag = np.abs(arr)
    corner = slice(0, min(16, n))
    noise = float(np.std(mag[corner])) if n else 0.0
    if float(np.max(mag)) <= max(noise * 5.0, 1e-9):
        return None
    threshold = max(float(np.percentile(mag, 99.0)), noise * 5.0)
    interior = np.zeros(n, dtype=bool)
    interior[1:-1] = (mag[1:-1] >= mag[:-2]) & (mag[1:-1] >= mag[2:])
    if margin > 0:
        interior[:margin] = False
        interior[-margin:] = False
    candidates = np.where(interior & (mag > threshold))[0]
    if not candidates.size:
        return None
    order = np.argsort(mag[candidates])[::-1][:max_peaks]
    positions = candidates[order]
    return positions, mag[positions]


def _row_absorption(
    arr: np.ndarray,
    positions: np.ndarray,
    heights: np.ndarray,
    p0: float,
    p1: float,
    *,
    radius: int = 1,
) -> tuple[float, float]:
    """Peak-height-weighted (absorption, signed ratio) over a fixed +-radius peak window, matching
    the frequency-domain rotation of NMRPipe PS.

    The radius defaults to 1: the edges of the SP window give the peak tails a non-linear phase, so
    a +-5 window absorption actually drops at the correct phase (0.46 vs 0.51 measured), whereas a
    +-1 window separates the cases correctly (0.65 vs 0.47).
    """
    n = arr.shape[-1]
    k = np.arange(n, dtype=float)
    ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
    rot = arr * ramp
    rows2d = np.repeat(rot.reshape(1, -1), positions.size, axis=0)
    profiles = _trace_profiles(rows2d, positions, radius=radius)
    absorption, sign = _window_metrics(profiles)
    weights = heights + 1e-12
    return (
        float(np.average(absorption, weights=weights)),
        float(np.average(sign, weights=weights)),
    )


def _row_p1_fit(
    arr: np.ndarray,
    positions: np.ndarray,
    heights: np.ndarray,
    *,
    coarse_step: float = 10.0,
) -> tuple[float, float] | None:
    """p1 fit from multi-peak traces: after rotation the peak phases are maximally concentrated.

    The peak phase of a trace is the common p0 + that increment t1 (a constant) + p1*k/(n-1); after
    rotating by a candidate p1, a correct p1 makes every peak phase equal (p0+t1) and the modulus
    of the mean unit vector |vec| maximal (phase concentration). p1 is naturally decoupled from
    p0/t1, so the flatness of the absorption metric along p1 (saturation at small angles) does not
    arise here. Returns (p1, concentration).
    """
    if positions.size < 2:
        return None
    n = arr.shape[-1]
    vals = arr[positions]
    weights = heights + 1e-12
    denom = float(np.sum(weights))

    def _concentration(p1: float) -> float:
        ramp = np.exp(-1j * np.deg2rad(p1 * positions / max(n - 1, 1)))
        unit = np.exp(1j * np.angle(vals * ramp))
        vec = np.sum(weights * unit) / denom
        return float(np.abs(vec))

    best_p1, best_conc = 0.0, -1.0
    for p1 in np.arange(-90.0, 91.0, coarse_step):
        conc = _concentration(float(p1))
        if conc > best_conc:
            best_conc, best_p1 = conc, float(p1)
    # 0.2.199-patch29m: with ringing or narrow peak clusters the concentration surface decreases
    # monotonically and the coarse optimum often lands on the +-90 boundary (real data used to pick
    # -90 systematically); when the improvement over p1=0 is below 0.05 the boundary value is judged
    # a false solution and p1=0 is returned. An interior optimum (the true p1) is unaffected.
    if abs(best_p1) >= 85.0 and _concentration(0.0) + 0.05 >= best_conc:
        return 0.0, float(_concentration(0.0))
    for span, step in ((30.0, 5.0), (10.0, 2.5)):
        for offset in np.arange(-span, span + 1e-9, step):
            conc = _concentration(best_p1 + offset)
            if conc > best_conc:
                best_conc, best_p1 = conc, best_p1 + offset
    # 0.2.199-patch29i: refinement can drift outside the coarse range (artefacts on short axes,
    # +-220 measured), so clamp it back
    best_p1 = float(np.clip(best_p1, -90.0, 90.0))
    return best_p1, best_conc


def _row_p0_at_p1(
    arr: np.ndarray,
    positions: np.ndarray,
    heights: np.ndarray,
    p1_signal: float,
) -> tuple[float, float]:
    """p0 correction of a single trace once p1 (the signal ramp) is fixed.

    After the signal ramp is removed by rotation, every peak phase equals a constant (the common
    p0 + t1); the negated weighted circular mean is the PS correction p0, and the +-180
    disambiguation takes the positive-peak solution. Returns (p0, score), where score is the
    weighted peak-window absorption under the (p0, -p1_signal) correction.
    """
    n = arr.shape[-1]
    vals = arr[positions]
    ramp = np.exp(-1j * np.deg2rad(p1_signal * positions / max(n - 1, 1)))
    unit = np.exp(1j * np.angle(vals * ramp))
    weights = heights + 1e-12
    vec = np.sum(weights * unit) / max(float(np.sum(weights)), 1e-12)
    p0 = float((-np.rad2deg(np.angle(vec))) % 360.0)
    p1_corr = -p1_signal
    _a0, sign0 = _row_absorption(arr, positions, heights, p0, p1_corr)
    _a1, sign1 = _row_absorption(
        arr, positions, heights, (p0 + 180.0) % 360.0, p1_corr
    )
    if sign1 > sign0:
        p0 = (p0 + 180.0) % 360.0
    score, _sign = _row_absorption(arr, positions, heights, p0, p1_corr)
    return p0, score


def dominant_absorption_ratio(
    spectrum: np.ndarray, p0: float, p1: float, radius: int = 3
) -> float:
    """Absorption ratio |Re|/(|Re|+|Im|) of the dominant (largest) peak after applying (p0, p1).

    Used to pick the better of the old and the new 1D phase result (0.2.199-patch29gk)."""
    arr = np.asarray(spectrum, dtype=np.complex128)
    n = arr.shape[-1]
    k = np.arange(n, dtype=float)
    rot = arr * np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
    mag = np.abs(arr)
    imax = int(np.argmax(mag))
    lo, hi = max(0, imax - radius), min(n, imax + radius + 1)
    re = np.sum(np.real(rot[lo:hi]))
    im = np.sum(np.imag(rot[lo:hi]))
    return float(np.abs(re) / (np.abs(re) + np.abs(im) + 1e-12))


def orient_dominant_positive(
    spectrum: np.ndarray, p0: float, p1: float, radius: int = 3
) -> float:
    """Adjust (p0, p1) so the dominant peak points up (positive absorption): flip p0 by 180 when the
    real part of the dominant peak window is negative.

    0.2.199-patch29gk (user: peaks must be upright absorption, not inverted)."""
    arr = np.asarray(spectrum, dtype=np.complex128)
    n = arr.shape[-1]
    k = np.arange(n, dtype=float)
    rot = arr * np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
    imax = int(np.argmax(np.abs(arr)))
    lo, hi = max(0, imax - radius), min(n, imax + radius + 1)
    re = float(np.sum(np.real(rot[lo:hi])))
    if re < 0.0:
        return (p0 + 180.0) % 360.0
    return p0 % 360.0


def search_direct_spectrum_phase(
    traces: np.ndarray,
    *,
    max_rows: int = 128,
    p0_source: str = "first",
) -> tuple[float, float, float, float] | None:
    """Aggregated (p0, p1) search on a direct-dimension FT spectrum (pure numpy, no backend rerun).

    Input: a (..., n) complex spectrum whose last dimension is the direct one; each row is one
    indirect increment (one row per file for sliced fids, one row per increment for a single-file
    fid). Aggregation strategy:
    1) p1 consensus: fit the phase concentration of the multi-peak traces and take the median (t1 is
       only a per-peak constant offset and does not affect the p1 ramp; single-peak traces cannot
       determine p1 and are left out);
    2) p0 anchor: p0_source="first" takes the first trace that has peaks (increment 0, t1=0; the
       direct dimension of sliced NUS data has a clean phase), "strongest" takes the trace with the
       tallest peaks (a pseudo-uniform spectrum: the strongest row corresponds to a real indirect
       frequency and the constant phase offset of the zero-fill side lobes is d~0).
    Returns (p0, p1, score, gain); None when there is no signal or too few points.
    """
    arr = np.asarray(traces, dtype=np.complex128)
    if arr.ndim == 0 or arr.shape[-1] < 8:
        return None
    rows = arr.reshape(-1, arr.shape[-1])
    if rows.shape[0] > max_rows:
        index = np.linspace(0, rows.shape[0] - 1, max_rows).astype(int)
        rows = rows[index]
    infos: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for index in range(rows.shape[0]):
        peaks = _row_peak_positions(rows[index])
        if peaks is not None:
            infos.append((rows[index], peaks[0], peaks[1]))
    if not infos:
        return None
    p1_rows: list[float] = []
    for _arr, pos, heights in infos:
        fit = _row_p1_fit(_arr, pos, heights)
        if fit is not None:
            p1_rows.append(fit[0])
    # the p1 fit yields the signal ramp; the PS correction negates it (the same convention as p0)
    p1_signal = float(np.median(p1_rows)) if p1_rows else 0.0
    p1 = -p1_signal
    # p0 anchor: first = the first trace with peaks (increment 0, t1=0); strongest = tallest trace
    if p0_source == "strongest":
        anchor = max(
            range(len(infos)), key=lambda i: float(np.max(infos[i][2]))
        )
    else:
        anchor = 0
    _arr, pos, heights = infos[anchor]
    p0, score = _row_p0_at_p1(_arr, pos, heights, p1_signal)
    baseline, _sign = _row_absorption(_arr, pos, heights, 0.0, 0.0)
    gain = score - baseline
    return p0, p1, score, gain


def nus_direct_phase(
    fids: np.ndarray,
    points: list[tuple[int, ...]],
    n_f1: int,
    n_f2: int = 1,
    *,
    oversample: int = 8,
    refine_step: float = 0.02,
) -> tuple[float, float, float, float, int] | None:
    """NUS direct-dimension (p0, p1) correction: the phase of the strongest direct peak at its real
    F1/F2 frequency in every increment.

    Principle: the phase of direct peak k* in slice i is phi(k*) + w1*dt1*p1_i + w2*dt2*p2_i; a
    non-uniform DFT over the increments at the true F1/F2 frequency cancels the t1 modulation
    exactly (d=0), leaving the peak phase phi(k*) = phi0 + p1*k*/(n-1). p1 comes from fitting the
    multi-peak phase concentration of every slice and taking the median (the 0.2.88 mechanism).
    Pure numpy; SMILE and the backend are not run.

    Returns (p0_corr, p1_corr, score, gain, kstar); None when there is no signal or too few points.
    score/gain use the peak-window absorption (radius 1) of the strongest slice under
    (p0_corr, p1_corr) minus the zero-phase baseline (for gating). Both p0 and p1 are PS
    corrections (the negated signal phase).
    """
    arr = np.asarray(fids, dtype=np.complex128)
    if arr.ndim != 2 or arr.shape[0] != len(points) or arr.shape[-1] < 8:
        return None
    if n_f1 <= 0:
        return None
    spectra = direct_ft_traces(arr, sp_off=0.45, sp_end=0.95, sp_pow=1)
    # strongest direct peak: the interior local maximum with the largest median peak height across
    # slices (the first/last points are excluded as DC/Nyquist artefacts -- in real data they can be
    # several times stronger than real peaks; the 0.2.92 VM sampleI run showed a k=0 artefact of
    # 4.9e8 against a real peak of 8e7)
    margin = max(2, arr.shape[-1] // 64)
    med = np.median(np.abs(spectra), axis=0)
    interior = np.zeros(med.size, dtype=bool)
    interior[margin:-margin] = True
    local_max = np.zeros(med.size, dtype=bool)
    local_max[1:-1] = (med[1:-1] >= med[:-2]) & (med[1:-1] >= med[2:])
    cand = np.where(interior & local_max)[0]
    if not cand.size:
        cand = np.where(interior)[0]
    kstar = int(cand[int(np.argmax(med[cand]))])
    v = spectra[:, kstar]
    # increment index (modulo arithmetic tolerates 1-based vs complex-point unit differences)
    if n_f2 > 1:
        p1 = np.array(
            [int(pt[1]) % n_f1 if len(pt) >= 2 else 0 for pt in points],
            dtype=float,
        )
        p2 = np.array(
            [int(pt[0]) % n_f2 if len(pt) >= 1 else 0 for pt in points],
            dtype=float,
        )
    else:
        p1 = np.array([int(pt[0]) % n_f1 for pt in points], dtype=float)
        p2 = np.zeros(len(points), dtype=float)
    # coarse non-uniform DFT grid (memory cap: coarse cells x number of slices)
    max_cells = 1_000_000
    os = oversample
    while (n_f1 * os) * (max(n_f2, 1) * os) > max_cells and os > 2:
        os //= 2
    f1_coarse = np.arange(n_f1 * os) / os
    if n_f2 > 1:
        f2_coarse = np.arange(n_f2 * os) / os
        F1, F2 = np.meshgrid(f1_coarse, f2_coarse, indexing="ij")
        arg = (
            2.0 * np.pi
            * (
                F1[..., None] * p1[None, None, :] / n_f1
                + F2[..., None] * p2[None, None, :] / n_f2
            )
        )
        V = np.sum(v[None, None, :] * np.exp(-1j * arg), axis=-1)
        idx = np.unravel_index(int(np.argmax(np.abs(V))), V.shape)
        f1pk, f2pk = float(f1_coarse[idx[0]]), float(f2_coarse[idx[1]])
    else:
        arg = 2.0 * np.pi * f1_coarse[:, None] * p1[None, :] / n_f1
        V = np.sum(v[None, :] * np.exp(-1j * arg), axis=-1)
        idx = int(np.argmax(np.abs(V)))
        f1pk, f2pk = float(f1_coarse[idx]), 0.0
    # local refinement (a fine grid around the peak)
    span = 1.0
    if n_f2 > 1:
        f1s = np.arange(max(0.0, f1pk - span), min(n_f1, f1pk + span) + 1e-9, refine_step)
        f2s = np.arange(max(0.0, f2pk - span), min(n_f2, f2pk + span) + 1e-9, refine_step)
        F1f, F2f = np.meshgrid(f1s, f2s, indexing="ij")
        argf = (
            2.0 * np.pi
            * (
                F1f[..., None] * p1[None, None, :] / n_f1
                + F2f[..., None] * p2[None, None, :] / n_f2
            )
        )
        Vf = np.sum(v[None, None, :] * np.exp(-1j * argf), axis=-1)
        idf = np.unravel_index(int(np.argmax(np.abs(Vf))), Vf.shape)
        f1pk, f2pk = float(f1s[idf[0]]), float(f2s[idf[1]])
        phase = float(np.rad2deg(np.angle(Vf[idf])))
    else:
        f1s = np.arange(max(0.0, f1pk - span), min(n_f1, f1pk + span) + 1e-9, refine_step)
        argf = 2.0 * np.pi * f1s[:, None] * p1[None, :] / n_f1
        Vf = np.sum(v[None, :] * np.exp(-1j * argf), axis=-1)
        idf = int(np.argmax(np.abs(Vf)))
        f1pk = float(f1s[idf])
        phase = float(np.rad2deg(np.angle(Vf[idf])))
    # p1: fit the multi-peak phase concentration of every slice and take the median (signal ramp)
    p1_rows: list[float] = []
    for index in range(spectra.shape[0]):
        peaks = _row_peak_positions(spectra[index], margin=margin)
        if peaks is not None and peaks[0].size >= 2:
            fit = _row_p1_fit(spectra[index], peaks[0], peaks[1])
            if fit is not None:
                p1_rows.append(fit[0])
    p1_sig = float(np.median(p1_rows)) if p1_rows else 0.0
    # p0_corr = -(phi(k*) - p1_sig*k*/(n-1)); p1_corr = -p1_sig
    p0_corr = (-(phase - p1_sig * kstar / max(spectra.shape[-1] - 1, 1))) % 360.0
    p1_corr = -p1_sig
    # +-180 disambiguation: the NU-DFT peak phase is phi(k*) (the t1 modulation cancels exactly at
    # the true F1 frequency), so p0_corr = -phi(k*) is the only solution, matching the "take the
    # positive-peak solution" semantics of the existing methods. The absorption sign of a single
    # slice must not be used to decide the sign -- that slice t1 phase can be 180 deg (an inverted
    # peak) and 0.2.91 flipped p0 by 180 deg because of it. Only a numerical guard is kept here
    # (normally always a positive real).
    if float(np.real(Vf[idf] * np.exp(1j * np.deg2rad(p0_corr)))) < 0.0:
        p0_corr = (p0_corr + 180.0) % 360.0
    # coherent SNR for gating: |V_peak|/(sqrt(N)*mean|v|) -- about sqrt(N) for signal, 1 for noise
    v_mean = float(np.mean(np.abs(v))) + 1e-12
    score = float(np.abs(Vf[idf])) / (np.sqrt(len(points)) * v_mean)
    return p0_corr, p1_corr, score, score - 1.0, kstar


def _net_window_metric(profile: np.ndarray) -> float:
    """Net absorption (positive area + negative area) / total absolute area; as in the existing
    uniform optimisation score."""
    positive = float(np.clip(profile, 0.0, None).sum())
    negative = float(np.clip(profile, None, 0.0).sum())
    total = float(np.abs(profile).sum())
    return (positive + negative) / total if total else 0.0


def _symmetry_sign_metric(
    profile: np.ndarray, sign_mode: str = "uniform"
) -> float:
    """Peak-window symmetry plus a shape-aware sign penalty (0..1), imitating nmrDraw phase tuning.

    sym = left-right symmetry: a real absorption peak is even (left = right, with negative side
    lobes either side of a positive peak) -> 1; a dispersive peak is odd -> 0.

    Sign penalty (0.2.199-patch20, shape-aware, user scheme):
    - sign_mode="mixed" (a spectrum with both signs, e.g. HNCACB 13C): returns pure sym -- global
      symmetry is best and negative absorption peaks are not penalised;
    - sign_mode="uniform" (a single-sign spectrum): a negative window is penalised mildly as
      sym*0.2 -- a symmetric negative absorption (an inverted peak with symmetric negative side
      lobes) only drops to 0.2 (a small penalty), while a one-sided dispersive negative peak
      already has sym~0 and is suppressed naturally; anomalous sign-flipped peaks are
      down-weighted and no longer dominate the score, and the +-180 disambiguation is preserved.
    """
    f = np.asarray(profile, dtype=float)
    n = f.size
    if n < 2:
        return 0.0
    half = n // 2
    left = f[:half]
    right = f[n - half:][::-1]
    denom = 2.0 * (left**2 + right**2) + 1e-12
    sym = float(np.mean((left + right) ** 2 / denom))
    if n % 2 == 1:
        c = f[half]
        sym = float(
            np.mean(
                np.concatenate(
                    [np.asarray((left + right) ** 2 / denom), [c**2 / (c**2 + 1e-12)]]
                )
            )
        )
    if sign_mode == "mixed":
        return sym
    return sym if float(np.sum(f)) >= 0.0 else sym * 0.2


def _signal_peak_windows(
    real: np.ndarray,
    *,
    axis: int = -1,
    snr: float = 10.0,
    global_frac: float = 0.05,
    margin: int = 8,
    max_peaks: int = 8,
) -> list[tuple[int, int]]:
    """Pick the signal-row peaks (user scheme: a protein spectrum has only a few tall peaks per row,
    so noise and artefact regions are excluded first).

    For every trace (along axis, not the direct-dimension combination) find local maxima whose
    height reaches max(snr x row noise (MAD), global_frac x global maximum peak height), allowing at
    most max_peaks peaks per row (dense artefact rows are excluded). Returns [(row, peak_pos), ...];
    [] when there is no clean row.
    """
    n = real.shape[axis]
    moved = np.moveaxis(real, axis, -1)
    traces = np.abs(moved).reshape(-1, n)
    gmax = float(np.max(traces))
    windows: list[tuple[int, int]] = []
    for row in range(traces.shape[0]):
        mag = traces[row]
        mad = float(np.median(np.abs(mag - np.median(mag)))) * 1.4826 + 1e-12
        lo, hi = margin, n - margin
        if hi <= lo + 2:
            continue
        local = np.zeros(n, dtype=bool)
        local[lo:hi] = (mag[lo:hi] >= mag[lo - 1 : hi - 1]) & (
            mag[lo:hi] > mag[lo + 1 : hi + 1]
        )
        peaks = np.where(local & (mag > max(snr * mad, global_frac * gmax)))[0]
        if 0 < len(peaks) <= max_peaks:
            for p in peaks:
                windows.append((row, int(p)))
    return windows


def search_direct_phase_on_spectrum(
    spectrum: np.ndarray,
    *,
    axis: int = -1,
    coarse_p0_step: float = 30.0,
    metric: str = "symmetry",
    radius: int = 12,
    min_windows: int = 5,
    prefer_p1_zero: bool = True,
    sign_mode: str = "uniform",
    progress: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> tuple[float, float, float] | None:
    """Search the direct-dimension phase score (p0, p1) on a spectrum.

    axis selects the axis holding the direct dimension (the last one by default: the direct F2 of a
    final F1xF2 spectrum; for the complex reconstructed plane recon.ft1 the layout is (F2, F1) and
    the direct dimension is axis 0 -- 0.2.96).

    0.2.95 (the nmrDraw phase-tuning idea, metric="symmetry" by default): first select the signal
    row peaks to exclude noise/artefact regions (a few tall peaks per row), then score the locked
    peak windows for frequency-domain rotational symmetry and take the smallest correction on the
    near-optimal plateau. sign_mode (0.2.199-patch20): mixed = a spectrum with both signs, where
    global symmetry is best; uniform = a single-sign spectrum with the shape-aware mild penalty on
    negative windows (anomalous sign-flipped peaks are down-weighted). metric="net" keeps the old
    net-absorption metric (+-90 plateau). Returns (p0, p1, score); None when there is no clean
    signal peak.
    """
    arr = np.asarray(spectrum)
    axis = axis if axis >= 0 else arr.ndim - 1
    if arr.ndim < 2 or arr.shape[axis] < 8:
        return None
    n = arr.shape[axis]
    real = np.real(arr) if np.iscomplexobj(arr) else arr
    if metric == "symmetry":
        windows = _signal_peak_windows(real, axis=axis)
        if len(windows) < min_windows:
            return None
        # 0.2.199-patch6: too many windows (a 3D plane can reach thousands) slows down every scoring
        # round; the score is a mean, so a uniform sub-sample to <=200 windows barely changes it and
        # the search drops from tens of seconds to seconds
        if len(windows) > 200:
            index = np.linspace(0, len(windows) - 1, 200).astype(int)
            windows = [windows[i] for i in index]

        def _sym_window(profile: np.ndarray) -> float:
            return _symmetry_sign_metric(profile, sign_mode=sign_mode)

        window_metric = _sym_window
    else:
        traces = np.moveaxis(real, axis, -1).reshape(-1, n)
        peak_mag = np.max(np.abs(traces), axis=-1)
        corner = tuple(slice(0, min(16, s)) for s in real.shape)
        noise = float(np.std(real[corner])) if real.size else 0.0
        threshold = max(float(np.percentile(real, 99.5)), noise * 5.0)
        idx = np.where(peak_mag > threshold)[0]
        if idx.size == 0:
            return None
        pos = np.argmax(np.abs(traces[idx]), axis=-1)
        windows = list(zip(idx.tolist(), pos.tolist()))
        # 0.2.199-patch6: as in the symmetry branch, a uniform sub-sample caps the scoring cost
        if len(windows) > 200:
            index = np.linspace(0, len(windows) - 1, 200).astype(int)
            windows = [windows[i] for i in index]
        window_metric = _net_window_metric
    comp = np.asarray(arr, dtype=np.complex128)
    # 0.2.199-patch6: scoring only depends on the signal window rows -- take the rows first and
    # rotate afterwards (element-wise operations commute, so the result matches rotating the whole
    # plane) and the cost drops from "all points x ~2000 evaluations" to "window rows x direct
    # points x ~2000"; this also allows cancellation and stage progress
    rows_flat = np.moveaxis(comp, axis, -1).reshape(-1, n)
    sel_idx = np.asarray([i for i, _peak in windows], dtype=np.intp)
    selected = rows_flat[sel_idx]
    window_slices = [
        (j, max(0, peak - radius), min(n, peak + radius + 1))
        for j, (_i, peak) in enumerate(windows)
    ]
    k = np.arange(n, dtype=float)
    _n_p0_coarse = int(np.ceil(360.0 / max(coarse_p0_step, 1.0)))
    total_scores = _n_p0_coarse * 7 + 2 * 25
    if prefer_p1_zero:
        total_scores += 1
    if metric == "symmetry":
        total_scores += 73 * 25
    _evaluated = 0

    def _check_cancel() -> None:
        if cancel is not None and cancel():
            raise RuntimeError(tr("cancelled by the user: direct-dimension phase search"))

    def _score(p0: float, p1: float) -> float:
        nonlocal _evaluated
        ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
        rot = selected * ramp
        rot_real = np.real(rot)
        vals = []
        for j, lo, hi in window_slices:
            vals.append(window_metric(rot_real[j, lo:hi]))
        _evaluated += 1
        _check_cancel()
        if progress is not None and _evaluated % 100 == 0:
            progress(
                tr(
                    "direct-dimension phase search: evaluated {p0}/{p1} grid "
                    "points",
                    p0=_evaluated,
                    p1=total_scores,
                )
            )
        if metric == "symmetry":
            return 100.0 * float(np.mean(vals))
        return 50.0 * (float(np.median(vals)) + 1.0)

    t0 = time.time()
    if progress is not None:
        progress(tr("direct-dimension phase search in progress, please wait"))
    # coarse p0 x p1 grid (serial; parallelising it hung real runs, reverted 2026-08-19)
    best = None
    for p0 in np.arange(0.0, 360.0, coarse_p0_step):
        for p1 in (-90, -60, -30, 0, 30, 60, 90):
            s = _score(float(p0), float(p1))
            if best is None or s > best[0]:
                best = (s, float(p0), float(p1))
    s0, p0, p1 = best
    for _ in range(2):
        for dp0 in (-15, -5, 0, 5, 15):
            for dp1 in (-15, -5, 0, 5, 15):
                ss = _score((p0 + dp0) % 360.0, p1 + dp1)
                if ss > s0:
                    best = (ss, (p0 + dp0) % 360.0, p1 + dp1)
                    s0, p0, p1 = best
    if prefer_p1_zero and abs(_score(p0, 0.0) - s0) < 1.0:
        p1 = 0.0
        s0 = _score(p0, 0.0)
    if metric == "symmetry":
        # take the "smallest correction" on the near-optimal plateau over the full circle (the human
        # habit: do not pile corrections onto a spectrum already close to a good phase).
        # 0.2.199-patch19: the plateau is wide under a pure symmetry metric (+-180 period), so the
        # tolerance was tightened from 5 to 1 -- (0,0) is no longer pulled back when it scores more
        # than 1 below the optimum (e.g. sampleK: 41.7 vs 45.3) and the true optimum (e.g.
        # p0~80 deg, p1~55 deg) wins.
        near = []
        for dp0 in np.arange(-180.0, 181.0, 5.0):
            for dp1 in np.arange(-60.0, 61.0, 5.0):
                pc = (p0 + dp0) % 360.0
                qc = p1 + dp1
                sc = _score(pc, qc)
                if sc >= s0 - 1.0:
                    near.append((sc, pc, qc))
        if near:
            near.sort(
                key=lambda t: (
                    min(t[1], 360.0 - t[1]),
                    abs(t[2]),
                    -t[0],
                )
            )
            p0, p1 = near[0][1], near[0][2]
            s0 = _score(p0, p1)
    logger.info(
        tr("direct dimension phase search: shape=%s serial completion, takes %.1fs"),
        tuple(comp.shape),
        time.time() - t0,
    )
    if progress is not None:
        progress(
            tr("direct-dimension phase search done in {p0:.1f} s", p0=time.time() - t0)
        )
    return p0, p1, s0
