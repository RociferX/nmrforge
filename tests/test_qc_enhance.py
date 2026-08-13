"""QC 增强测试(0.2.47):artifact 连续化 + 分辨率惩罚。"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter

from core.qc import artifact_detection, spectrum_quality


def _peaks_spectrum(shape=(128, 256), peaks=((60, 180), (70, 120))) -> np.ndarray:
    real = np.zeros(shape)
    for (y, x) in peaks:
        real[y, x] = 100.0
    return gaussian_filter(real, sigma=(1.5, 1.5)) + 0j


def test_artifact_penalty_continuous_with_distance() -> None:
    """孤立峰惩罚随距离连续化:峰越远(越孤立)分数越低。"""
    near = _peaks_spectrum(peaks=((60, 180), (62, 182)))  # 相邻峰 → 非孤立
    far = _peaks_spectrum(peaks=((20, 20), (110, 230)))  # 相距远 → 孤立
    score_near = artifact_detection.detect(near).score
    score_far = artifact_detection.detect(far).score
    assert score_near > score_far
    assert score_far < 100.0
    assert 0.0 <= score_near <= 100.0


def test_spectrum_quality_resolution_penalty() -> None:
    """min_shape 分辨率惩罚:小谱低于最低要求时综合分下降。"""
    small = _peaks_spectrum(shape=(16, 32), peaks=((8, 20), (10, 12)))
    base = spectrum_quality.evaluate(small).score.overall
    penalized = spectrum_quality.evaluate(
        small, min_shape=(128, 256)
    ).score.overall
    assert penalized < base
    # 达标谱不受惩罚
    full = spectrum_quality.evaluate(small, min_shape=(16, 32)).score.overall
    assert full == base
