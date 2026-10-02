"""QC metric tests (synthetic spectrum)."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from core.qc import (
    artifact_detection,
    baseline_quality,
    noise,
    peak_detection,
    phase_quality,
    snr,
    spectrum_quality,
)


def _synthetic_spectrum(shape: tuple[int, int] = (128, 256), seed: int = 7) -> np.ndarray:
    """A "phased" synthetic spectrum of real-part Gaussian peaks plus noise."""
    rng = np.random.default_rng(seed)
    real = np.zeros(shape)
    for (y, x), amp in [((60, 180), 100), ((70, 120), 70), ((40, 90), 50)]:
        real[y, x] = amp
    real = gaussian_filter(real, sigma=(1.5, 1.5))
    return real + rng.normal(0, 0.8, size=shape)


def test_noise_estimate_on_pure_noise() -> None:
    rng = np.random.default_rng(0)
    data = rng.normal(0, 3.0, size=(64, 128))
    est = noise.estimate(data)
    assert abs(est.global_sigma - 3.0) < 0.5
    assert est.method == "robust_mad"


def test_peak_detection_subpixel_position() -> None:
    """Sub-pixel peak position: when the centre of the Gaussian peak falls between pixels, the
    detection position is close to the true centre (0.2.199-patch29eo parabolic correction)."""
    shape = (128, 256)
    yy, xx = np.mgrid[0:128, 0:256]
    rng = np.random.default_rng(3)
    real = np.exp(-(((yy - 50.4) ** 2) / (2 * 1.2**2) + ((xx - 120.7) ** 2) / (2 * 1.2**2)))
    real = real + rng.normal(0, 0.01, size=shape)
    peaks = peak_detection.detect(real)
    top = max(peaks, key=lambda p: p.height)
    assert abs(top.position[0] - 50.4) < 0.3
    assert abs(top.position[1] - 120.7) < 0.3


def test_peak_detection_finds_peaks() -> None:
    spec = _synthetic_spectrum()
    peaks = peak_detection.detect(spec)
    assert len(peaks) >= 3
    positions = {tuple(round(v) for v in p.position) for p in peaks}
    assert any(abs(p[0] - 60) <= 2 and abs(p[1] - 180) <= 2 for p in positions)
    assert any(abs(p[0] - 70) <= 2 and abs(p[1] - 120) <= 2 for p in positions)


def test_snr_metrics() -> None:
    spec = _synthetic_spectrum()
    peaks = peak_detection.detect(spec)
    metrics = snr.compute(spec, peaks)
    assert metrics.global_snr > 5
    assert 0 < metrics.median_peak_snr <= metrics.global_snr


def test_phase_quality_good_vs_bad() -> None:
    spec = _synthetic_spectrum()
    good = spec + 0j
    bad = good * 1j
    assert phase_quality.evaluate(good).absorption_fraction > 0.9
    assert phase_quality.evaluate(bad).absorption_fraction < 0.2


def _phase_sweep_spectrum(n: int = 2048, seed: int = 3, noise: float = 0.1) -> np.ndarray:
    """Complex Lorentzian multi-peak spectrum (the real part is absorptive at phase=0) with noise
    added to the real part.

    Lorentzian peaks (an exponentially decaying FID) are used instead of Gaussian ones: real
    NMR lineshapes have long tails, so the dispersive negative side lobes of a phase error can
    be captured to first order by the negative-area metric.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=float) / n
    fid = np.zeros(n, dtype=complex)
    for _ in range(20):
        f0 = rng.uniform(0.04, 0.9)
        amp = rng.uniform(0.8, 6.0)
        width = rng.uniform(0.0015, 0.004)
        fid += amp * np.exp(1j * 2 * np.pi * f0 * t) * np.exp(-t / width)
    spec = np.fft.fft(fid)
    return spec + np.random.default_rng(7).normal(0, noise, size=n)


def test_phase_quality_continuous_metrics() -> None:
    """Continuous negative area + spectral entropy: the larger the phase error, the lower the
    score, and there is a measurable margin within 5°."""
    spec = _phase_sweep_spectrum()
    metrics = [
        phase_quality.evaluate(np.real(spec * np.exp(-1j * np.deg2rad(d))))
        for d in (0.0, 5.0, 10.0, 30.0, 90.0)
    ]
    scores = [m.score for m in metrics]
    assert scores[0] > scores[1] > scores[2] > scores[3] > scores[4]
    # Spectral entropy points the same way (0.2.38 new metric)
    assert metrics[1].entropy > metrics[0].entropy
    # 0.2.63: the peak-window negative area does not apply to the 1D synthetic spectrum (its
    # strongest peak is an FFT edge artifact); the direction is covered by the 2D middle-peak
    # test (test_phase_quality_180_inversion_penalty). Here we only check that score still falls
    # monotonically with the phase error (peak-window term + entropy together).
    assert metrics[0].score - metrics[1].score > 0.01
    assert metrics[1].score - metrics[2].score > 0.01
    assert metrics[2].score - metrics[3].score > 0.01
    assert metrics[3].score - metrics[4].score > 0.01


@pytest.mark.parametrize("offset", [10.0, 100.0, -100.0])
def test_phase_window_selection_uses_baseline_relative_peak_height(offset: float) -> None:
    spectrum = _synthetic_spectrum()
    before = phase_quality.evaluate(spectrum)
    after = phase_quality.evaluate(spectrum + offset)
    assert after.score == pytest.approx(before.score, abs=1e-8)
    assert phase_quality.profile_symmetry_axis(spectrum + offset, 0) == pytest.approx(
        phase_quality.profile_symmetry_axis(spectrum, 0),
        abs=1e-8,
    )


def test_quality_nonfinite_imaginary_component_returns_invalid_not_exception() -> None:
    spectrum = _synthetic_spectrum().astype(complex)
    spectrum[0, 0] = complex(1.0, float("nan"))
    result = spectrum_quality.evaluate(spectrum)
    assert result.decision is spectrum_quality.QcDecision.ROLLBACK
    assert result.score.overall == 0
    assert result.reasons


def test_phase_quality_180_inversion_penalty() -> None:
    """180° inversion (the whole spectrum is negated) is penalised by both the negative area and
    negative peaks; the score is far below the positive phase."""
    from scipy.ndimage import gaussian_filter

    base = np.zeros((48, 96))
    base[16, 40] = 200.0
    base = gaussian_filter(base, sigma=1.5)
    good = phase_quality.evaluate(base)
    bad = phase_quality.evaluate(-base)
    assert good.score > bad.score + 20
    assert bad.negative_area_fraction > good.negative_area_fraction
    assert bad.entropy > good.entropy


def _negative_lobe_spectrum(
    lobes: list[tuple[tuple[int, int], float]],
    *,
    shape: tuple[int, int] = (128, 256),
    noise_level: float = 1.0,
    seed: int = 5,
    main_peak: float = 80.0,
) -> np.ndarray:
    "Regression coverage:  negative lobe spectrum."
    rng = np.random.default_rng(seed)
    real = rng.normal(0.0, noise_level, size=shape)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]

    def _clear_noise(cy: int, cx: int, radius: int = 2) -> None:
        real[(yy - cy) ** 2 + (xx - cx) ** 2 <= radius * radius] = 0.0

    _clear_noise(60, 180)
    real[60, 180] = main_peak * noise_level
    for (cy, cx), multiple in lobes:
        _clear_noise(cy, cx)
        real += (
            multiple
            * noise_level
            * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2) / (2.0 * 1.6 * 1.6)))
        )
    return real


def test_phase_quality_negative_peak_counts_only_significant_lobes() -> None:
    "Regression coverage: test phase quality negative peak counts only significant lobes."
    five_sigma = _negative_lobe_spectrum([((70, 120), -5.0)])
    thirteen_sigma = _negative_lobe_spectrum([((70, 120), -13.0)])
    both = _negative_lobe_spectrum([((70, 120), -5.0), ((40, 90), -13.0)])

    sigma = float(noise.estimate(five_sigma).global_sigma)
    assert sigma == pytest.approx(1.0, abs=0.1)

    detected = peak_detection.detect(-np.real(five_sigma))
    assert any(abs(float(p.height)) == pytest.approx(5.0, abs=0.5) for p in detected)

    small_only = phase_quality.evaluate(five_sigma)
    assert small_only.negative_peak_fraction == 0.0
    assert small_only.negative_peak_sigma == phase_quality.NEGATIVE_PEAK_SIGMA == 12.0
    assert small_only.noise_sigma > 0.0

    big_only = phase_quality.evaluate(thirteen_sigma)
    assert big_only.negative_peak_fraction > 0.0

    assert phase_quality.evaluate(both).negative_peak_fraction == pytest.approx(
        big_only.negative_peak_fraction
    )
    assert big_only.negative_peak_fraction > small_only.negative_peak_fraction


def test_phase_quality_inverted_spectrum_keeps_a_high_negative_ratio() -> None:
    "Regression coverage: test phase quality inverted spectrum keeps a high negative ratio."
    positive = _negative_lobe_spectrum([((70, 120), 13.0), ((40, 90), 13.0)])
    inverted = -positive
    assert phase_quality.evaluate(inverted).negative_peak_fraction > 0.9


def test_spectrum_quality_no_longer_reports_a_negative_peak_reason() -> None:
    "Regression coverage: test spectrum quality no longer reports a negative peak reason."

    spec = _strong_spectrum([((60, 180), -100), ((70, 120), -70), ((40, 90), 45)])
    result = spectrum_quality.evaluate(spec, sign_mode="uniform")
    assert not any("负峰比例偏高" in r for r in result.reasons)

    assert phase_quality.evaluate(spec).negative_peak_fraction > 0.15


def test_spectrum_quality_weak_noise_peaks_do_not_raise_the_negative_ratio() -> None:
    "Regression coverage: test spectrum quality weak noise peaks do not raise the negative ratio."
    weak = _synthetic_spectrum()
    phase = phase_quality.evaluate(weak)
    assert phase.negative_peak_fraction == 0.0
    result = spectrum_quality.evaluate(weak, sign_mode="uniform")
    assert not any("负峰比例偏高" in r for r in result.reasons)


def test_baseline_quality_flags_ramp() -> None:
    ramp = np.linspace(0.0, 10.0, 128)[np.newaxis, :] + 0j
    quality = baseline_quality.evaluate(ramp)
    assert quality.needs_correction is True


def test_baseline_quality_worst_axis_flags_other_dimension() -> None:
    """0.2.170: spectrum quality takes the baseline worst over all axes -- a ramp is detected on
    non-last stored axes too."""
    ramp = np.zeros((96, 64), dtype=np.complex128)
    ramp[:, :] = np.linspace(0.0, 10.0, 96)[:, None]  # ramp along axis 0 (F1)
    quality = baseline_quality.evaluate(ramp, axis=0)
    assert quality.needs_correction is True
    worst_idx, worst = baseline_quality.worst_axis(ramp)
    assert worst_idx == 0
    assert worst.needs_correction is True


def test_artifact_detection_isolated_peaks() -> None:
    """Injecting an isolated strong peak into a dense peak field lowers the artifact score
    (0.2.199-patch29el density normalisation)."""
    rng = np.random.default_rng(0)
    shape = (128, 256)
    dense = np.zeros(shape)
    for y in range(30, 95, 8):
        for x in range(60, 181, 20):
            dense[y, x] = 80.0
    dense = gaussian_filter(dense, sigma=(1.5, 1.5))
    dense = dense + rng.normal(0, 0.8, size=shape)
    clean = artifact_detection.detect(dense + 0j)
    assert clean.score == 100.0
    bad = dense.copy()
    bad[10, 240] += 500.0  # isolated strong spurious peak outside the dense field
    report = artifact_detection.detect(bad + 0j)
    assert report.score < clean.score
    assert report.isolated_peak_clusters >= 1


def test_spectrum_quality_decision() -> None:
    spec = _synthetic_spectrum() + 0j
    result = spectrum_quality.evaluate(spec)
    assert result.decision.value == "accept"
    assert result.score.overall > 0
    assert result.score.components.snr > 0


# ---------------------------------------------- QC sign convention and invalid input (2026-09-20)
def _all_negative(shape: tuple[int, int] = (128, 256), seed: int = 7) -> np.ndarray:
    """Negate the whole "phased" synthetic spectrum -- the reproduction recipe reported
    downstream."""
    return -_synthetic_spectrum(shape=shape, seed=seed)


def _mixed_spectrum(shape: tuple[int, int] = (128, 256), seed: int = 11) -> np.ndarray:
    """A synthetic spectrum with both signs (a mixed experiment such as HNCACB): two negative
    peaks plus one positive peak."""
    rng = np.random.default_rng(seed)
    real = np.zeros(shape)
    for (y, x), amp in [((60, 180), -100.0), ((70, 120), -70.0), ((40, 90), 45.0)]:
        real[y, x] = amp
    real = gaussian_filter(real, sigma=(1.5, 1.5))
    return real + rng.normal(0, 0.8, size=shape)


def test_spectrum_quality_mixed_snr_ignores_overall_sign() -> None:
    """mixed: a negative peak is real signal, so inverting the whole spectrum must not change the
    S/N component (S/N = signal vs noise)."""
    spec = _synthetic_spectrum()
    positive = spectrum_quality.evaluate(spec, sign_mode="mixed")
    negative = spectrum_quality.evaluate(-spec, sign_mode="mixed")
    assert positive.score.components.snr == pytest.approx(negative.score.components.snr)
    assert positive.score.components.snr > 0


def test_spectrum_quality_uniform_takes_positive_peaks_and_reports_a_flip() -> None:
    """uniform: a single-sign spectrum takes positive peaks by project convention (the peaks
    point up after the +/-180 disambiguation) -- an inverted spectrum loses S/N and
    **explicitly reports** that negative peaks dominate, so downstream cannot read the anomaly
    as "quality is fine".
    """
    spec = _synthetic_spectrum()
    normal = spectrum_quality.evaluate(spec, sign_mode="uniform")
    flipped = spectrum_quality.evaluate(-spec, sign_mode="uniform")
    assert flipped.score.components.snr < normal.score.components.snr
    assert flipped.decision is spectrum_quality.QcDecision.WARNING
    assert any("负峰信号占优" in reason for reason in flipped.reasons)
    assert not any("负峰信号占优" in reason for reason in normal.reasons)


def test_spectrum_quality_mixed_is_sign_symmetric() -> None:
    """The mixed convention (negative peaks are legitimate): the verdict and the overall score
    agree before and after inverting the whole spectrum."""
    for spec in (_synthetic_spectrum(), _all_negative(), _mixed_spectrum()):
        positive = spectrum_quality.evaluate(spec, sign_mode="mixed")
        negative = spectrum_quality.evaluate(-spec, sign_mode="mixed")
        assert positive.decision is negative.decision
        # The phase component is not strictly symmetric at the ~1e-4 level (peak windows /
        # normalisation details); the S/N component is asserted bit-for-bit in the test above,
        # so the overall score gets a 0.01 margin here.
        assert positive.score.overall == pytest.approx(negative.score.overall, abs=0.01)


def test_spectrum_quality_uses_sign_aware_peaks_for_snr() -> None:
    """The S/N component must pick peaks by sign_mode (the old behaviour used the default
    "positive peaks only")."""
    spec = _mixed_spectrum()  # negative peaks dominate
    sigma = noise.estimate(spec).global_sigma
    with_sign = snr.compute(
        spec,
        peak_detection.detect(spec, peak_detection.PeakDetectionParams(sign_mode="both")),
        sigma,
    ).global_snr
    positive_only = snr.compute(
        spec,
        peak_detection.detect(spec, peak_detection.PeakDetectionParams(sign_mode="positive")),
        sigma,
    ).global_snr
    assert with_sign > positive_only * 1.4  # including the negative peaks is a factor apart
    result = spectrum_quality.evaluate(spec, sign_mode="mixed")
    assert result.score.components.snr == pytest.approx(
        min(100.0, with_sign / 40.0 * 100.0), abs=1e-6
    )
    # The uniform convention takes positive peaks (project convention): this negative-dominant
    # spectrum gets a low S/N plus an inverted-sign note
    uniform = spectrum_quality.evaluate(spec, sign_mode="uniform")
    assert uniform.score.components.snr == pytest.approx(
        min(100.0, positive_only / 40.0 * 100.0), abs=1e-6
    )
    assert any("负峰信号占优" in reason for reason in uniform.reasons)


def test_spectrum_quality_uniform_flags_a_fully_inverted_spectrum() -> None:
    """An inverted spectrum is still an anomaly in a uniform experiment: the phase component
    catches it (this is not the S/N term penalising it twice)."""
    spec = _synthetic_spectrum()
    positive = spectrum_quality.evaluate(spec, sign_mode="uniform")
    negative = spectrum_quality.evaluate(-spec, sign_mode="uniform")
    assert negative.score.components.phase < positive.score.components.phase
    assert negative.score.overall < positive.score.overall
    assert negative.decision is spectrum_quality.QcDecision.WARNING


@pytest.mark.parametrize(
    ("label", "data"),
    [
        ("空数组", np.array([])),
        ("全零谱", np.zeros((32, 64))),
        ("常数谱", np.full((32, 64), 5.0)),
        ("含 NaN", np.full((32, 64), np.nan)),
    ],
)
def test_spectrum_quality_invalid_input_is_reported_not_raised(
    label: str, data: np.ndarray
) -> None:
    """Degenerate input: no exception, verdict rollback, overall 0 and a clear reason (see the
    evaluate contract)."""
    result = spectrum_quality.evaluate(data, sign_mode="mixed")
    assert result.decision.value == "rollback", label
    assert result.score.overall == 0.0, label
    assert result.reasons and result.reasons[0], label


def test_spectrum_quality_invalid_input_reasons_are_specific() -> None:
    """The reason strings must distinguish "empty" / "no signal" / "not finite", otherwise
    downstream cannot triage them."""
    empty = spectrum_quality.evaluate(np.array([])).reasons[0]
    zeros = spectrum_quality.evaluate(np.zeros((16, 16))).reasons[0]
    nan = spectrum_quality.evaluate(np.full((16, 16), np.nan)).reasons[0]
    assert "空数组" in empty
    assert "常数谱" in zeros
    assert "NaN" in nan
    assert len({empty, zeros, nan}) == 3


def _strong_spectrum(
    peaks: list[tuple[tuple[int, int], float]],
    *,
    noise_level: float = 0.02,
    seed: int = 7,
    target: float = 100.0,
) -> np.ndarray:
    """A synthetic spectrum with peak heights of ~100 sigma (the order of a real spectrum; sigma
    is set by the noise alone)."""
    shape = (128, 256)
    rng = np.random.default_rng(seed)
    real = np.zeros(shape)
    for (y, x), amp in peaks:
        real[y, x] = amp
    real = gaussian_filter(real, sigma=(1.5, 1.5))
    real = real / (float(np.max(np.abs(real))) or 1.0) * target
    return real + rng.normal(0, noise_level, size=shape)


def test_spectrum_quality_auto_judges_the_sign_convention() -> None:
    """The standalone entry point (auto) judges "single-sign positive / single-sign negative /
    both signs coexist" by itself.

    A spectrum the user processed themselves may be single-sign **all negative** -- that is not
    a defect, it is simply scored in the negative-peak convention; the pipeline convention
    (uniform = positive peaks) still treats such a spectrum as an inverted anomaly. Each
    convention covers its own entry point.
    """
    positive = _strong_spectrum([((60, 180), 100), ((70, 120), 70), ((40, 90), 50)])
    negative = -positive
    mixed = _strong_spectrum([((60, 180), -100), ((70, 120), -70), ((40, 90), 45)])

    r_pos = spectrum_quality.evaluate(positive, sign_mode="auto")
    r_neg = spectrum_quality.evaluate(negative, sign_mode="auto")
    r_mix = spectrum_quality.evaluate(mixed, sign_mode="auto")
    assert any("单符号正峰" in reason for reason in r_pos.reasons)
    assert any("单符号负峰" in reason for reason in r_neg.reasons)
    assert any("正负共存" in reason for reason in r_mix.reasons)

    # A negative spectrum scores the same as its positive mirror image (it is flipped and scored
    # with the peaks up) and is accepted
    assert r_neg.score.overall == pytest.approx(r_pos.score.overall, abs=0.2)
    assert r_neg.decision is spectrum_quality.QcDecision.ACCEPT
    # The pipeline convention has not changed: a single-sign spectrum whose negative peaks
    # dominate is still reported as an anomaly
    strict = spectrum_quality.evaluate(negative, sign_mode="uniform")
    assert strict.decision is spectrum_quality.QcDecision.WARNING
    assert any("负峰信号占优" in reason for reason in strict.reasons)
