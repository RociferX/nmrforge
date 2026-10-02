"""QC enhancement tests (0.2.47): continuous artifact penalty + resolution penalty."""

from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter

from core.qc import artifact_detection, spectrum_quality


def _peaks_spectrum(shape=(128, 256), peaks=((60, 180), (70, 120))) -> np.ndarray:
    real = np.zeros(shape)
    for y, x in peaks:
        real[y, x] = 100.0
    return gaussian_filter(real, sigma=(1.5, 1.5)) + 0j


def test_artifact_penalty_continuous_with_distance() -> None:
    """Continuous isolated-peak penalty (density normalization, 0.2.199-patch29el): a
    normal sparse-spectrum distribution raises no false alarm; only a strong peak that is
    is abnormally isolated in a dense peak field loses score.
    """
    rng = np.random.default_rng(0)
    shape = (128, 256)
    # sparse spectrum: two peaks far apart, a normal distribution -> not flagged as an
    # artifact (the old absolute threshold gave a false positive)
    sparse = np.zeros(shape)
    sparse[20, 20] = 100.0
    sparse[110, 230] = 100.0
    sparse = gaussian_filter(sparse, sigma=(1.5, 1.5))
    sparse = sparse + rng.normal(0, 0.8, size=shape)
    assert artifact_detection.detect(sparse + 0j).score == 100.0
    # dense peak field + an injected isolated strong peak: abnormally isolated ->
    # penalty; no injection -> 100
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
    """min_shape resolution penalty: a small spectrum below the minimum requirement loses
    overall score.
    """
    small = _peaks_spectrum(shape=(16, 32), peaks=((8, 20), (10, 12)))
    base = spectrum_quality.evaluate(small).score.overall
    penalized = spectrum_quality.evaluate(small, min_shape=(128, 256)).score.overall
    assert penalized < base
    # a spectrum that meets the bar is not penalized
    full = spectrum_quality.evaluate(small, min_shape=(16, 32)).score.overall
    assert full == base
