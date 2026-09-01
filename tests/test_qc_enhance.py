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
    """孤立峰惩罚连续化(0.2.199-补29el 密度归一化):
    稀疏谱正常分布不误报;密集峰场中异常孤立的强峰才扣分。"""
    rng = np.random.default_rng(0)
    shape = (128, 256)
    # 稀疏谱:两个相距很远的峰,正常分布 → 不判为伪影(旧绝对阈值误报)
    sparse = np.zeros(shape)
    sparse[20, 20] = 100.0
    sparse[110, 230] = 100.0
    sparse = gaussian_filter(sparse, sigma=(1.5, 1.5))
    sparse = sparse + rng.normal(0, 0.8, size=shape)
    assert artifact_detection.detect(sparse + 0j).score == 100.0
    # 密集峰场 + 注入孤立强峰:异常孤立 → 扣分;无注入 → 100
    dense = np.zeros(shape)
    for y in range(30, 95, 8):
        for x in range(60, 181, 20):
            dense[y, x] = 80.0
    dense = gaussian_filter(dense, sigma=(1.5, 1.5))
    dense = dense + rng.normal(0, 0.8, size=shape)
    clean = artifact_detection.detect(dense + 0j).score
    bad = dense.copy()
    bad[10, 240] += 500.0
    score_bad = artifact_detection.detect(bad + 0j).score
    assert clean == 100.0
    assert score_bad < clean
    assert 0.0 <= score_bad <= 100.0


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
