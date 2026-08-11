"""QC 数据结构骨架测试。"""

from __future__ import annotations

from core.qc.noise import NoiseEstimate
from core.qc.peak_detection import Peak, PeakDetectionParams


def test_noise_estimate_defaults() -> None:
    est = NoiseEstimate(global_sigma=12.0)
    assert est.global_sigma == 12.0
    assert est.confidence == 0.0


def test_peak_detection_params() -> None:
    params = PeakDetectionParams(sigma_multiplier=5.0)
    assert params.sigma_multiplier == 5.0
    assert params.min_snr == 3.0


def test_peak_defaults() -> None:
    peak = Peak(position=(1.0, 2.0), height=10.0)
    assert peak.snr == 0.0
    assert peak.volume == 0.0
