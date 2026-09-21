"""Phase quality: absorption ratio / continuous negative area / spectral entropy / negative peak
fraction / peak symmetry.

Scoring follows established literature:
- negative-area minimisation (de Brouwer et al., JMR 201 (2009) 230-238): apply a robust baseline
  correction first and then measure the negative area -- a phase error spreads absorption energy
  into dispersive side lobes and the negative area rises to first order with the error, which makes
  it the most discriminative continuous metric in the +-5 deg neighbourhood;
- spectral-entropy minimisation (Ernst 1966; the efficient Chen 2002 version): a correct phase gives
  the most concentrated spectrum, with the lowest positive-part entropy, pointing the same way as
  the negative area;
- the absorption ratio (real/imaginary balance) and the negative-peak count (the 180 deg inverted
  fallback) as complements.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.qc import peak_detection


@dataclass
class PhaseQuality:
    absorption_fraction: float = 0.0
    negative_peak_fraction: float = 0.0
    negative_area_fraction: float = 0.0
    entropy: float = 0.0
    symmetry: float = 0.0
    score: float = 0.0


def negative_area_fraction(
    real: Any, radius: int = 8, *, symmetry_gated: bool = True
) -> float:
    """Negative-area fraction over the peak windows: detect peaks, then measure the negative share
    inside the peak window (weighted by peak height).

    Since 0.2.63 this is measured over the peak windows rather than the whole spectrum (calibrated
    on real VM data): the whole-spectrum negative area is diluted by noise and baseline and hardly
    phase-sensitive (sampleF scored only 0.02 points across +-5 deg), whereas the dispersive
    negative side lobes of a phase error sit inside the peak window, whose negative fraction is
    sharp and sensitive (sampleF: ~2 points of 100 on the coarse grid).
    Returns [0, 1]; 0 = no negative region inside the peak window, 1 = all negative.
    """
    arr = np.asarray(np.real(real), dtype=float)
    # positive + negative peaks (a 180 deg inverted spectrum has no positive peak, so both must be
    # detected separately)
    _pos = peak_detection.detect(arr)
    _neg = peak_detection.detect(-arr)
    _total = len(_pos) + len(_neg)
    # 0.2.171: an inverted spectrum (negative peaks in the majority) is not gated, just penalised
    neg_majority = _total > 0 and len(_neg) > 0.6 * _total
    peaks = list(_pos) + list(_neg)
    if not peaks:
        return 0.0  # no peaks: no negative-area information, so no penalty
    # only strong peaks: noise peaks have no systematic negative lobes and would dilute the
    # phase-sensitive signal (of 501 peaks measured the real signal peaks are a minority, and
    # averaging over all of them leaves the negative area at ~0, which loses the phase contrast)
    heights = np.asarray([float(p.height) for p in peaks])
    threshold = max(float(np.percentile(np.abs(arr), 99.5)), 0.0)
    strong = [p for p, h in zip(peaks, heights) if h >= threshold]
    # skip edge peaks (an incomplete window makes the statistic meaningless; FFT boundary artefacts
    # often sit at both ends of the array)
    strong = [
        p
        for p in strong
        if all(
            radius <= int(round(float(v))) < s - radius
            for v, s in zip(np.atleast_1d(p.position), arr.shape)
        )
    ]
    if not strong:
        return 0.0  # every strong peak is at an edge / in an incomplete window: no penalty
    # keep only the five tallest strong peaks (0.2.63, calibrated on VM sampleF): aggregating all
    # strong peaks pulls in many weak peaks and artefacts and disagrees with how the main peaks look
    # (the sequential search then converges to a local optimum); the main (tallest) peak is the most
    # reliable phase indicator and top-N matches the 1D peak shapes the user sees
    strong.sort(key=lambda p: p.height, reverse=True)
    strong = strong[:5]
    total_abs = 0.0
    total_neg = 0.0
    for peak in strong:
        pos = np.round(np.asarray(peak.position)).astype(int)
        slices = tuple(
            slice(max(0, i - radius), min(s, i + radius + 1))
            for i, s in zip(pos, arr.shape)
        )
        win = arr[slices]
        if symmetry_gated and not neg_majority:
            # 0.2.171: absorption-type (symmetric peak window) negative content -- real negative
            # peaks (Cb in a mixed spectrum) and noise negative lobes -- is not a phase error; only
            # asymmetric dispersive negative lobes are penalised
            flipped = np.flip(win)
            denom = float(np.sum(win * win) * np.sum(flipped * flipped))
            corr = (
                float(np.sum(win * flipped) / np.sqrt(denom))
                if denom > 1e-12
                else 0.0
            )
            if corr >= 0.0:
                continue
        total_abs += float(np.sum(np.abs(win)))
        total_neg += float(-np.sum(np.minimum(win, 0.0)))
    return float(total_neg / (total_abs + 1e-12))


def negative_area_axis(real: Any, axis: int, radius: int = 8) -> float:
    """Negative area of the peak window along a 1D profile of the chosen axis (the per-axis phase
    tuning of the older NMRFlow project).

    The dispersive negative lobes of a phase error spread along the axis being tuned; take the 1D
    profile at the peak positions along that axis and measure the negative share (positive and
    negative peaks detected separately, strong peaks only, weighted by height). A 2D window would
    include peak shapes whose direction is already corrected (an absorbed F2) and dilute the phase
    signal of the target axis -- on VM sampleF the 2D aggregate ranked differently from the 1D
    main-peak profile, which matches the 1D peak shapes the user sees.
    """
    arr = np.asarray(np.real(real), dtype=float)
    peaks = list(peak_detection.detect(arr)) + list(
        peak_detection.detect(-arr)
    )
    if not peaks:
        return 0.0
    heights = np.asarray([float(p.height) for p in peaks])
    threshold = max(float(np.percentile(np.abs(arr), 99.5)), 0.0)
    strong = [p for p, h in zip(peaks, heights) if h >= threshold]
    strong = [
        p
        for p in strong
        if 0 <= axis < arr.ndim
        and radius
        <= int(round(float(np.atleast_1d(p.position)[axis])))
        < arr.shape[axis] - radius
    ]
    if not strong:
        return 0.0
    # keep only the five tallest strong peaks (as in negative_area_fraction: main peaks first)
    strong.sort(key=lambda p: p.height, reverse=True)
    strong = strong[:5]
    total_abs = 0.0
    total_neg = 0.0
    for peak in strong:
        pos = np.round(np.asarray(peak.position)).astype(int)
        lo = max(0, int(pos[axis]) - radius)
        hi = min(arr.shape[axis], int(pos[axis]) + radius + 1)
        sl = tuple(
            slice(lo, hi) if i == axis else slice(int(pos[i]), int(pos[i]) + 1)
            for i in range(arr.ndim)
        )
        win = arr[sl]
        total_abs += float(np.sum(np.abs(win)))
        total_neg += float(-np.sum(np.minimum(win, 0.0)))
    return float(total_neg / (total_abs + 1e-12))


def profile_symmetry_axis(real: Any, axis: int, radius: int = 6) -> float:
    """Mirror symmetry of a 1D peak-window profile (absorption ~+1, dispersion ~-1), median over the
    five tallest peaks.

    The older NMRFlow project used the correlation between a 1D profile and its mirror image to
    resolve the +-90 deg phase ambiguity: an absorption peak is even (mirror correlation ~+1) and a
    dispersive peak is odd (~-1). Note that flipping the whole spectrum (the old 0.2.38 scheme)
    mistakes a 90 deg dispersive 2D spectrum for a symmetric one; only the 1D peak-window profile
    judges the parity correctly.
    """
    arr = np.asarray(np.real(real), dtype=float)
    peaks = list(peak_detection.detect(arr)) + list(
        peak_detection.detect(-arr)
    )
    if not peaks:
        return 0.0
    heights = np.asarray([float(p.height) for p in peaks])
    threshold = max(float(np.percentile(np.abs(arr), 99.5)), 0.0)
    strong = [p for p, h in zip(peaks, heights) if h >= threshold]
    strong = [
        p
        for p in strong
        if 0 <= axis < arr.ndim
        and radius
        <= int(round(float(np.atleast_1d(p.position)[axis])))
        < arr.shape[axis] - radius
    ]
    strong.sort(key=lambda p: p.height, reverse=True)
    strong = strong[:5]
    if not strong:
        return 0.0
    corrs: list[float] = []
    for peak in strong:
        pos = np.round(np.asarray(peak.position)).astype(int)
        lo = max(0, int(pos[axis]) - radius)
        hi = min(arr.shape[axis], int(pos[axis]) + radius + 1)
        sl = tuple(
            slice(lo, hi) if i == axis else slice(int(pos[i]), int(pos[i]) + 1)
            for i in range(arr.ndim)
        )
        profile = arr[sl].astype(float).ravel()
        if profile.size < 5 or float(np.std(profile)) == 0.0:
            continue
        corr = float(np.corrcoef(profile, profile[::-1])[0, 1])
        if corr == corr:
            corrs.append(corr)
    return float(np.median(corrs)) if corrs else 0.0


def _peak_window_nets(real: Any, radius: int = 8) -> list[float]:
    """Signed net-absorption list of the strong peak windows (same formula as the optimiser
    score_axis_memory, 0.2.172).

    Detect positive/negative peaks, take the five tallest strong peaks (avoiding the edges) and
    measure the net absorption (positive+negative)/|total| inside a +-radius peak window. An
    absorption peak (of either sign) nets about +-1 and a dispersive peak about 0.
    """
    arr = np.asarray(np.real(real), dtype=float)
    peaks = list(peak_detection.detect(arr)) + list(
        peak_detection.detect(-arr)
    )
    if not peaks:
        return []
    heights = np.asarray([float(p.height) for p in peaks])
    # threshold fallback (0.2.172): FFT edge artefacts often hold the 99.5 percentile and are then
    # removed by the edge filter, leaving no valid strong peak -- stepping the threshold down
    # (95/75) lets real signal peaks through, matching the fallback in the optimiser
    # _lock_discrete_traces
    strong: list[Any] = []
    for pct in (99.5, 95.0, 75.0):
        threshold = max(float(np.percentile(np.abs(arr), pct)), 0.0)
        strong = [p for p, h in zip(peaks, heights) if h >= threshold]
        strong = [
            p
            for p in strong
            if all(
                radius <= int(round(float(v))) < s - radius
                for v, s in zip(np.atleast_1d(p.position), arr.shape)
            )
        ]
        if strong:
            break
    strong.sort(key=lambda p: p.height, reverse=True)
    nets: list[float] = []
    for peak in strong[:5]:
        pos = np.round(np.asarray(peak.position)).astype(int)
        # take a 1D peak profile along each axis (as in the optimiser trace windows, +-radius). A
        # phase error spreads along the axis being tuned while the other axes stay absorbed, so the
        # profile with the smallest |net absorption| (the worst direction) represents the phase
        # state of that peak; taking the strongest profile instead would miss the dispersion of the
        # tuned axis (an uncorrected 45 deg candidate in a 2D spectrum still absorbs along F1 and
        # used to be scored too high)
        best: tuple[float, float] | None = None
        for axis in range(arr.ndim):
            n_axis = arr.shape[axis]
            center = int(pos[axis])
            lo = max(0, center - radius)
            hi = min(n_axis, center + radius + 1)
            sl = tuple(
                slice(lo, hi) if a == axis else slice(i, i + 1)
                for a, (i, s) in enumerate(zip(pos, arr.shape))
            )
            profile = arr[sl].ravel()
            total = float(np.abs(profile).sum())
            if total <= 1e-12:
                continue
            # flatten the baseline (the same scheme as the optimiser _window_nets): the mean of the
            # median levels either side of the peak window is the baseline level, and the profile is
            # split into positive and negative only after subtracting it, so a global baseline shift
            # no longer pollutes the net absorption. For 1D spectra peak picking and baseline
            # estimation are unreliable, so the zero level is kept (0.2.175 comment); only 2D+ is
            # flattened, with the baseline regions just outside the peak window to avoid overlap
            prof = profile
            if arr.ndim >= 2:
                left_base = arr[
                    tuple(
                        slice(
                            max(0, center - radius - 12),
                            max(0, center - radius - 1),
                        )
                        if a == axis
                        else slice(i, i + 1)
                        for a, (i, s) in enumerate(zip(pos, arr.shape))
                    )
                ].ravel()
                right_base = arr[
                    tuple(
                        slice(
                            min(n_axis, center + radius + 2),
                            min(n_axis, center + radius + 14),
                        )
                        if a == axis
                        else slice(i, i + 1)
                        for a, (i, s) in enumerate(zip(pos, arr.shape))
                    )
                ].ravel()
                if left_base.size >= 4 and right_base.size >= 4:
                    baseline = 0.5 * (
                        float(np.median(left_base)) + float(np.median(right_base))
                    )
                else:
                    baseline = float(np.median(profile))
                peak_h = float(np.max(np.abs(profile)))
                if peak_h > 1e-12 and abs(baseline) / peak_h >= 0.01:
                    prof = profile - baseline
            net = float(
                (
                    np.clip(prof, 0.0, None).sum()
                    + np.clip(prof, None, 0.0).sum()
                )
                / float(np.abs(prof).sum() + 1e-12)
            )
            if best is None or abs(net) < best[1]:
                best = (net, abs(net))
        if best is not None:
            nets.append(best[0])
    return nets


def spectral_entropy(real: Any) -> float:
    """Positive-part spectral entropy (Ernst minimum entropy, normalised to [0, 1]).

    After subtracting the baseline the positive part is the energy distribution: a uniform
    distribution has entropy 1 and a single concentrated point has 0. A phase error spreads the
    energy out and the entropy rises; for a real-valued final spectrum it points the same way as
    the negative area.
    """
    arr = np.asarray(np.real(real), dtype=float)
    pos = np.maximum(arr - float(np.median(arr)), 0.0)
    total = float(np.sum(pos))
    if total <= 1e-12 or pos.size < 2:
        return 1.0
    p = pos.ravel() / total
    p = p[p > 0]
    n = p.size
    if n < 2:
        return 0.0
    return float(-np.sum(p * np.log(p)) / np.log(n))


def evaluate(data: Any, *, sign_mode: str = "uniform") -> PhaseQuality:
    """Evaluate phase quality (absorption + continuous negative area + entropy + negative peaks +
    symmetry).

    score = 100 x (0.25 x absorption + 0.40 x (1 - negative area) + 0.20 x (1 - entropy)
    + 0.15 x (1 - negative fraction)). A real-valued final spectrum (D005) has an absorption of 1,
    so the discrimination rests on the negative area and the entropy; the negative-peak count is the
    180 deg inverted fallback. Mirror symmetry no longer scores: a 90 deg dispersive spectrum is in
    fact highly symmetric, and the old formula mistook it for good phase quality (the field is kept
    for callers to read).
    """
    arr = np.asarray(data)
    real = np.real(arr)
    imag = np.imag(arr)
    abs_real = float(np.mean(np.abs(real))) + 1e-12
    abs_imag = float(np.mean(np.abs(imag))) + 1e-12
    absorption = abs_real / (abs_real + abs_imag)

    positive = peak_detection.detect(arr)
    negative = peak_detection.detect(-real)
    total = len(positive) + len(negative)
    neg_fraction = len(negative) / total if total else 0.0
    na = negative_area_fraction(real)
    ent = spectral_entropy(real)
    # 0.2.172: the main phase score shares its source with the optimiser -- the signed net
    # absorption of the peak windows (the same formula as memory_phase_search.score_axis_memory);
    # this fixes "the spectrum looks correctly phased but scores low"
    nets = _peak_window_nets(real)

    symmetry = 0.5
    if real.size > 1 and float(np.std(real)) > 1e-12:
        mirrored = np.flip(real)
        corr = float(np.corrcoef(real.ravel(), mirrored.ravel())[0, 1])
        if not np.isnan(corr):
            symmetry = max(0.0, min(1.0, (corr + 1.0) / 2.0))

    # 0.2.172: the same source as the optimiser (signed net absorption of the peak windows, 0-100)
    # dominates, with 10% spectral entropy as a fine adjustment so that weak peak windows and 1D
    # spectra also track the phase error monotonically; with no valid peak window the score is a
    # neutral 50
    # (matching the optimiser score_axis_memory, which returns 50 without traces)
    if nets:
        if sign_mode == "mixed":
            # 0.2.199-patch29u: a mixed spectrum is no longer penalised 0.7 for the five tallest
            # peaks sharing a sign -- a correctly phased CBCA(CO)NH/HNN whose five tallest peaks
            # happen to share a sign (say all CA) would be penalised wrongly and "look correctly
            # phased but score low"; the median |nets| already reflects the lineshape quality, and
            # the +-180 sign ambiguity is unresolvable for a mixed spectrum anyway (the user
            # confirmed the sign does not affect peak picking).
            net_score = 50.0 * (float(np.median(np.abs(nets))) + 1.0)
        else:
            # uniform keeps the signed median, so a 180 deg inverted spectrum (nets~-1) is penalised
            net_score = 50.0 * (float(np.median(nets)) + 1.0)
        phase_score = 0.9 * net_score + 0.1 * (100.0 * (1.0 - ent))
        # real-part energy share (absorption) factor: after correction the real part holds the most
        # energy -- this breaks the tie where 0 deg and +-45 deg give the same net absorption (the
        # real-spectrum-times-e^{i phi} case, whose ordering is unstable under VM numpy 2.4 floats);
        # a normal absorption spectrum has absorption~1 and loses nothing
        phase_score *= 0.5 + 0.5 * absorption
    else:
        phase_score = 50.0
    score = float(np.clip(phase_score, 0.0, 100.0))
    return PhaseQuality(
        absorption_fraction=float(absorption),
        negative_peak_fraction=float(neg_fraction),
        negative_area_fraction=float(na),
        entropy=float(ent),
        symmetry=float(symmetry),
        score=score,
    )
