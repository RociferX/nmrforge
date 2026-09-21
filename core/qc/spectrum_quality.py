"""Aggregate spectrum quality: combine the QC components into a QualityScore and compare
before and after processing to decide ACCEPT/ROLLBACK (framework §47).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np

from core.optimization.scoring import QualityScore, ScoreComponents
from core.qc import artifact_detection, baseline_quality, noise, peak_detection, phase_quality, snr
from ui_support.i18n import tr


class QcDecision(StrEnum):
    ACCEPT = "accept"
    WARNING = "warning"
    ROLLBACK = "rollback"


@dataclass
class QualityResult:
    score: QualityScore = field(default_factory=QualityScore)
    decision: QcDecision = QcDecision.ACCEPT
    reasons: list[str] = field(default_factory=list)


#: ``sign_mode`` -> peak-sign convention (2026-09-20, user's convention): the S/N component
#: of the combined score must use **the same sign convention** as the phase component. The
#: project has only two meaningful conventions --
#:
#: - single-sign spectra (``uniform``, HSQC/CBCACONH and similar): **positive**. When the
#:   processing chain resolves the +/-180 phase ambiguity of a single-sign spectrum it
#:   prefers positive absorption (``core/optimization/phase_consensus``: the
#:   ``sign_mode != "mixed"`` branch), so the peaks of the final spectrum point up; the
#:   peak-picking step uses the same "majority sign" convention
#:   (``workflow/pick_peaks._sign_mode_for`` -> ``keep_dominant`` after a ``both``
#:   detection). Hence ``positive`` here: a negative-peak-dominated single-sign spectrum is
#:   an **anomaly** (inverted data / phase not disambiguated), and both S/N and phase
#:   should report it instead of QC adaptively taking negative peaks for normal signal
#:   (which would produce a misleading "accept").
#: - spectra where both signs coexist (``mixed``, HNCACB and similar): ``both``, positive
#:   and negative peaks both enter the S/N -- the old convention used the default "positive
#:   peaks only", so genuine negative peaks became noise and an entirely inverted spectrum
#:   was penalised.
#:
#: ``dominant``/``both`` are still passed through exactly as the caller gave them (for
#: callers that explicitly ask for the adaptive behaviour). ``auto`` (the standalone
#: evaluation entry point) decides "both signs coexist" from the minority-sign intensity
#: share -- the same value as the peak-picking step
#: (``workflow/pick_peaks._MIXED_INTEN_SHARE``: minority sign >= major sign x 0.15).
_MIXED_INTEN_SHARE = 0.15
#: Threshold factor used to judge signal: noise practically never reaches 6 sigma (about
#: 1e-4 points are expected among 100,000), so the intensity accumulated above 6 sigma is
#: the signal mass; the positive and the negative side are both measured with it.
_SIGN_MASS_FACTOR = 6.0


def _sign_masses(real: Any, sigma: float) -> tuple[float, float]:
    """(positive-peak mass, negative-peak mass): intensity beyond ``+/-factor*sigma``."""
    hi = _SIGN_MASS_FACTOR * float(sigma)
    pos = float(real[real > hi].sum()) + 0.0
    neg = float(-real[real < -hi].sum()) + 0.0  # +0.0: keeps -0.0 from printing as "-0"
    return pos, neg


_SIGN_TO_DETECTION: dict[str, str] = {
    "uniform": "positive",
    "mixed": "both",
    "dominant": "dominant",
    "both": "both",
}


def _invalid(reason: str) -> QualityResult:
    """Input that cannot be evaluated: judge ``rollback`` and state why (never raises; the
    ``evaluate`` contract)."""
    return QualityResult(
        score=QualityScore(overall=0.0, components=ScoreComponents()),
        decision=QcDecision.ROLLBACK,
        reasons=[str(reason)],
    )


def _clip100(value: float) -> float:
    return float(max(0.0, min(100.0, value)))


def _resolution_penalty(shape: tuple[int, ...], min_shape: tuple[int, ...]) -> float:
    """Digital-resolution penalty (0.2.47): any dimension whose actual point count is below
    the required minimum loses score proportionally.

    Zero filling does not change the true frequency resolution (that is set by AQ), but too
    coarse a digital point spacing loses the measurable peak position/linewidth; this stops
    the optimiser from choosing zero-fill modes that halve the resolution. Returns 0..20.
    """
    penalty = 0.0
    for actual, required in zip(shape, min_shape):
        if required <= 0:
            continue
        ratio = actual / required
        if ratio < 1.0:
            penalty = max(penalty, 20.0 * (1.0 - ratio))
    return penalty


def evaluate(
    data: Any,
    *,
    min_shape: tuple[int, ...] | None = None,
    sign_mode: str = "uniform",
) -> QualityResult:
    """Evaluate overall spectrum quality (weighted SNR/phase/baseline/artefact).

    sign_mode: "uniform" (single-sign spectrum, the project convention takes positive
    peaks) / "mixed" (positive and negative peaks coexist) / "auto" (standalone
    evaluation: decide by itself between single-sign positive, single-sign negative and
    both signs; a negative verdict is scored after flipping the whole array, as "peaks
    up") -- the phase score shares its source with phase optimisation (0.2.172), and with
    mixed data negative peaks are normal and no longer raise a false alarm. **The same
    convention is used for the S/N** (2026-09-20): with ``mixed`` both signs enter the S/N
    (the old convention looked for positive peaks only, so genuine negative peaks counted
    as noise); ``uniform`` always takes positive peaks, because that is the project
    convention (the final spectrum of a single-sign experiment has its peaks up, see
    ``_SIGN_TO_DETECTION``), and a negative-peak-dominated spectrum is stated explicitly
    in ``reasons`` instead of being taken for normal signal.

    When min_shape is not None a resolution penalty is applied per dimension (for example
    the SI lower bound after zero filling).

    Parameters
    ----------
    data : Any
        2D+ spectrum data (complex or real array; the magnitude enters the scoring).
    min_shape : tuple[int, ...], optional
        Smallest allowed shape; anything below it is judged unacceptable.
    sign_mode : str, default "uniform"
        Peak sign convention (``uniform`` single sign / ``mixed`` both signs / ``auto``
        decide from the spectrum), used for the consistency check.

    Returns
    -------
    QualityResult
        The score (0-100) plus the individual metrics: peak shape/baseline/stripe/artefact
        scores, whether it passed and a list of explanations.

    Raises
    ------
    - never raises: unusable data is expressed as a low score plus explanations (the
      caller decides from the score whether to block). An empty array / NaN or Inf / a
      constant spectrum (all zeros) / zero noise -> ``decision=rollback``,
      ``score.overall=0``, with the concrete reason in ``reasons`` (since 2026-09-20;
      before that an empty array raised numpy's ``ValueError`` outright).

    Side effects
    ------------
    Pure computation: writes no files and does not modify the spectrum.

    Examples
    --------
        quality = evaluate(spectrum.data)
        if not quality.passed:
            ...  # quality.messages holds the concrete reasons
    """
    arr = np.asarray(data)
    # Input validity (2026-09-20): an empty array / NaN or Inf / a constant spectrum is
    # reported explicitly as "cannot evaluate" instead of letting numpy raise ValueError
    # inside a reduction, and instead of giving a zero-information spectrum a "warning".
    if arr.size == 0:
        return _invalid(tr("empty array: nothing to evaluate"))
    real = np.real(arr)
    if not bool(np.all(np.isfinite(real))):
        return _invalid(
            tr(
            "the spectrum contains NaN/Inf: fix the data first; the quality score takes no part in "
            "the decision",
        )
        )
    if float(np.ptp(real)) == 0.0:
        return _invalid(
            tr(
            "constant/all-zero spectrum: there is no signal (not something a quality score can "
            "judge)",
        )
        )
    sigma = noise.estimate(arr).global_sigma
    if not float(sigma) > 0.0:
        return _invalid(tr("noise estimate is 0: cannot compute the S/N"))
    mode_key = str(sign_mode).strip().lower() or "uniform"
    detection_mode = _SIGN_TO_DETECTION.get(mode_key, "positive")
    sign_note: str | None = None
    sign_anomaly: str | None = None
    judged_mixed = False
    auto_flip = False
    if mode_key in {"auto", "single_sign"}:
        # Standalone evaluation entry points (the standalone "spectrum quality" menu item,
        # the low-level API, the VM validation scripts): the caller does not know the
        # experiment type and the spectrum may have been processed by the user -- so judge
        # the sign convention here: single-sign positive / single-sign negative / both
        # signs. The criterion is the intensity mass above 6 sigma (peak counts drown in
        # noise), and the coexistence threshold shares its source with the peak-picking
        # step (_MIXED_INTEN_SHARE).
        both = peak_detection.detect(
            arr, peak_detection.PeakDetectionParams(sign_mode="both")
        )
        pos_peaks = [peak for peak in both if peak.height > 0]
        neg_peaks = [peak for peak in both if peak.height < 0]
        pos_mass, neg_mass = _sign_masses(real, sigma)
        major = max(pos_mass, neg_mass)
        if major <= 0.0:
            peaks = both
            sign_note = (
                tr(
                    "sign judgement: no signal above {p0:g} sigma (evaluating the detected peaks "
                    "as they "
                    "are)",
                    p0=_SIGN_MASS_FACTOR,
                )
            )
        elif min(pos_mass, neg_mass) / major >= _MIXED_INTEN_SHARE:
            peaks = both
            judged_mixed = True
            sign_note = (
                tr(
                    "sign judgement: both signs coexist (positive mass {p0:.4g} / negative mass "
                    "{p1:.4g})",
                    p0=pos_mass,
                    p1=neg_mass,
                )
            )
        elif pos_mass > neg_mass:
            peaks = pos_peaks
            sign_note = (
                tr(
                    "sign judgement: single-sign positive (positive mass {p0:.4g} / negative mass "
                    "{p1:.4g})",
                    p0=pos_mass,
                    p1=neg_mass,
                )
            )
        else:
            peaks = neg_peaks
            auto_flip = True
            sign_note = (
                tr(
                    "sign judgement: single-sign negative (positive mass {p0:.4g} / negative mass "
                    "{p1:.4g}); flipping the whole array and scoring as 'peaks "
                    "up'",
                    p0=pos_mass,
                    p1=neg_mass,
                )
            )
    elif detection_mode == "positive":
        # Single-sign spectrum (uniform): the project convention is that the final
        # spectrum has its peaks up -- one "both" detection collects the positive peaks for
        # the S/N, and the negative peaks only serve to judge "this data is inverted / the
        # phase was never disambiguated".
        both = peak_detection.detect(
            arr, peak_detection.PeakDetectionParams(sign_mode="both")
        )
        peaks = [peak for peak in both if peak.height > 0]
        pos_sum = sum(float(peak.height) for peak in peaks)
        neg_sum = sum(-float(peak.height) for peak in both if peak.height < 0)
        if neg_sum > pos_sum:
            sign_anomaly = (
                tr(
                    "single-sign spectrum: negative peaks dominate (negative total {p0:.4g} > "
                    "positive {p1:.4g}); the data is inverted overall or the +/-180 phase was not "
                    "disambiguated",
                    p0=neg_sum,
                    p1=pos_sum,
                )
            )
    else:
        peaks = peak_detection.detect(
            arr, peak_detection.PeakDetectionParams(sign_mode=detection_mode)
        )
    if auto_flip:
        # The phase/baseline/artefact components are all defined with the peaks up: a
        # single-sign negative spectrum processed by the user is not a defect, so once it
        # has been judged, flip the whole array and score it -- it then scores the same as
        # its positive mirror image.
        arr = -np.asarray(arr)
        real = np.real(arr)
        peaks = peak_detection.detect(
            arr, peak_detection.PeakDetectionParams(sign_mode="positive")
        )
    effective_mode = (
        ("mixed" if judged_mixed else "uniform")
        if mode_key in {"auto", "single_sign"}
        else sign_mode
    )
    # No peaks means no S/N (an empty slice makes numpy emit a RuntimeWarning, and the S/N
    # is 0 anyway)
    snr_value = float(snr.compute(arr, peaks, sigma).global_snr) if peaks else 0.0
    phase_metrics = phase_quality.evaluate(arr, sign_mode=effective_mode)
    _worst_axis, baseline_metrics = baseline_quality.worst_axis(arr)
    artifact_report = artifact_detection.detect(arr)

    components = ScoreComponents(
        snr=_clip100(snr_value / 40.0 * 100.0),
        phase=phase_metrics.score,
        baseline=baseline_metrics.score,
        artifact=artifact_report.score,
    )
    score = QualityScore(
        components=components,
        # 0.2.199-patch29hq (user): quality weights SNR 35 / phase 25 / baseline 20 / artefact 20
        weights={"snr": 0.35, "phase": 0.25, "baseline": 0.20, "artifact": 0.20},
    )
    overall = score.compute()
    resolution_penalty = (
        _resolution_penalty(arr.shape, min_shape) if min_shape is not None else 0.0
    )
    if resolution_penalty > 0:
        # write the penalty back into score.overall so callers read the overall score including it
        overall = float(max(0.0, overall - resolution_penalty))
        score.overall = overall

    reasons: list[str] = []
    if resolution_penalty > 0:
        reasons.append(
            tr(
                "insufficient digital resolution (shape={p0}, below the minimum {p1}; penalty "
                "{p2:.1f} "
                "points)",
                p0=tuple(arr.shape),
                p1=tuple(min_shape),
                p2=resolution_penalty,
            )
        )
    if sign_note:
        reasons.append(sign_note)
    if sign_anomaly:
        reasons.append(sign_anomaly)
    if snr_value < 10:
        reasons.append(tr("Global SNR is low ({p0:.1f})", p0=snr_value))
    # 0.2.199-patch29z: in a spectrum with both signs (mixed, such as CBCA(CO)NH/HNN) the
    # negative fraction is naturally about half, so "too many negative peaks" is not reported
    if (
        sign_mode != "mixed"
        and phase_metrics.negative_peak_fraction > 0.15
    ):
        reasons.append(
            tr(
            "the fraction of negative peaks is high "
            "({p0:.2f})",
            p0=phase_metrics.negative_peak_fraction,
        )
        )
    if baseline_metrics.needs_correction:
        reasons.append(
            tr(
            "baseline tilt/offset detected (worst storage axis): baseline correction "
            "recommended",
        )
        )
    if artifact_report.isolated_peak_clusters > 0:
        reasons.append(
            tr(
            "detected {p0} cluster(s) of isolated "
            "peaks",
            p0=artifact_report.isolated_peak_clusters,
        )
        )

    if overall >= 60.0:
        decision = QcDecision.ACCEPT
    elif overall >= 40.0:
        decision = QcDecision.WARNING
    else:
        decision = QcDecision.ROLLBACK
    if decision is not QcDecision.ACCEPT and not reasons:
        reasons.append(
            tr(
            "overall quality {p0:.1f} did not reach the automatic accept "
            "threshold",
            p0=overall,
        )
        )
    return QualityResult(score=score, decision=decision, reasons=reasons)
