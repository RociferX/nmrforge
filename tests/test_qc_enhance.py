"""QC Enhanced testing (0.2.47): artifact serialization + resolution penalty."""

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
    """Isolated peak penalty continuousization (0.2.199-patch29el density normalisation): Sparse
    spectra are normally distributed without false alarms; only abnormally isolated strong peaks
    in dense peak fields will be deducted."""
    rng = np.random.default_rng(0)
    shape = (128, 256)
    # Sparse spectrum: two peaks far apart, normal distribution -> not judged as artifacts (old
    # absolute threshold false positive).
    sparse = np.zeros(shape)
    sparse[20, 20] = 100.0
    sparse[110, 230] = 100.0
    sparse = gaussian_filter(sparse, sigma=(1.5, 1.5))
    sparse = sparse + rng.normal(0, 0.8, size=shape)
    assert artifact_detection.detect(sparse + 0j).score == 100.0
    # Dense peak field + injection of isolated strong peaks: abnormal isolation -> deduction; no
    # injection -> 100.
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
    """Min_shape Resolution penalty: When the score is lower than the minimum requirement, the
    overall score will be reduced."""
    small = _peaks_spectrum(shape=(16, 32), peaks=((8, 20), (10, 12)))
    base = spectrum_quality.evaluate(small).score.overall
    penalized = spectrum_quality.evaluate(
        small, min_shape=(128, 256)
    ).score.overall
    assert penalized < base
    # There is no penalty for meeting the standards.
    full = spectrum_quality.evaluate(small, min_shape=(16, 32)).score.overall
    assert full == base
