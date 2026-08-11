"""QC 指标测试（合成谱）。"""

from __future__ import annotations

import numpy as np
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
    """真实部高斯峰 + 噪声的「已相位」合成谱。"""
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


def test_baseline_quality_flags_ramp() -> None:
    ramp = np.linspace(0.0, 10.0, 128)[np.newaxis, :] + 0j
    quality = baseline_quality.evaluate(ramp)
    assert quality.needs_correction is True


def test_artifact_detection_isolated_peaks() -> None:
    spec = _synthetic_spectrum() + 0j
    report = artifact_detection.detect(spec)
    assert report.score < 100


def test_spectrum_quality_decision() -> None:
    spec = _synthetic_spectrum() + 0j
    result = spectrum_quality.evaluate(spec)
    assert result.decision.value == "accept"
    assert result.score.overall > 0
    assert result.score.components.snr > 0
