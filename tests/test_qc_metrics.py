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

def _phase_sweep_spectrum(
    n: int = 2048, seed: int = 3, noise: float = 0.1
) -> np.ndarray:
    """复型 Lorentzian 多峰谱(相位=0 时实部为吸收),加实部噪声。

    用 Lorentzian(指数衰减 FID)而非高斯峰:真实 NMR 谱形长尾,
    相位误差的色散负边瓣才能被负面积指标一阶捕捉。
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
    """连续负面积 + 谱熵:相位误差越大评分越低,且 5° 内有可测余量。"""
    spec = _phase_sweep_spectrum()
    metrics = [
        phase_quality.evaluate(np.real(spec * np.exp(-1j * np.deg2rad(d))))
        for d in (0.0, 5.0, 10.0, 30.0, 90.0)
    ]
    scores = [m.score for m in metrics]
    assert scores[0] > scores[1] > scores[2] > scores[3] > scores[4]
    # 谱熵方向一致(0.2.38 新指标)
    assert metrics[1].entropy > metrics[0].entropy
    # 0.2.63:峰窗负面积对 1D 合成谱不适用(最强峰是 FFT 边缘伪影),
    # 方向由 2D 中间峰测试(test_phase_quality_180_inversion_penalty)覆盖;
    # 这里校验 score 仍随相位误差单调下降(峰窗分量 + 熵共同作用)
    assert metrics[0].score - metrics[1].score > 0.01
    assert metrics[1].score - metrics[2].score > 0.01
    assert metrics[2].score - metrics[3].score > 0.01
    assert metrics[3].score - metrics[4].score > 0.01


def test_phase_quality_180_inversion_penalty() -> None:
    """180° 反相(整谱取负)被负面积/负峰同时惩罚,评分远低于正相。"""
    from scipy.ndimage import gaussian_filter

    base = np.zeros((48, 96))
    base[16, 40] = 200.0
    base = gaussian_filter(base, sigma=1.5)
    good = phase_quality.evaluate(base)
    bad = phase_quality.evaluate(-base)
    assert good.score > bad.score + 20
    assert bad.negative_area_fraction > good.negative_area_fraction
    assert bad.entropy > good.entropy


def test_baseline_quality_flags_ramp() -> None:
    ramp = np.linspace(0.0, 10.0, 128)[np.newaxis, :] + 0j
    quality = baseline_quality.evaluate(ramp)
    assert quality.needs_correction is True


def test_baseline_quality_worst_axis_flags_other_dimension() -> None:
    """0.2.170:谱图质量基线按全轴取最差——斜坡在非最后存储轴也检出。"""
    ramp = np.zeros((96, 64), dtype=np.complex128)
    ramp[:, :] = np.linspace(0.0, 10.0, 96)[:, None]  # 斜坡沿轴 0(F1)
    quality = baseline_quality.evaluate(ramp, axis=0)
    assert quality.needs_correction is True
    worst_idx, worst = baseline_quality.worst_axis(ramp)
    assert worst_idx == 0
    assert worst.needs_correction is True


def test_artifact_detection_isolated_peaks() -> None:
    """密集峰场中注入孤立强峰 → 伪影分下降(0.2.199-补29el 密度归一化)。"""
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
    bad[10, 240] += 500.0  # 密集场外孤立强伪峰
    report = artifact_detection.detect(bad + 0j)
    assert report.score < clean.score
    assert report.isolated_peak_clusters >= 1


def test_spectrum_quality_decision() -> None:
    spec = _synthetic_spectrum() + 0j
    result = spectrum_quality.evaluate(spec)
    assert result.decision.value == "accept"
    assert result.score.overall > 0
    assert result.score.components.snr > 0
