"""QC Data structure skeleton test."""

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


def test_peak_detection_both_signs() -> None:
    """Mixed experiment: select both positive and negative peaks, height retains the true sign."""
    import numpy as np
    from scipy.ndimage import gaussian_filter

    from core.qc.peak_detection import detect

    spec = np.zeros((32, 32))
    spec[5, 5] = 400.0
    spec[20, 20] = -400.0
    spec = gaussian_filter(spec, sigma=1.0)
    peaks = detect(spec, PeakDetectionParams(sign_mode="both"))
    assert len(peaks) == 2
    assert {1 if p.height > 0 else -1 for p in peaks} == {1, -1}


def test_peak_detection_dominant_keeps_majority_sign() -> None:
    """Uniform experiment: only retain the majority symbol peaks (if there are more negative peaks,
    only negative peaks will be output)."""
    import numpy as np
    from scipy.ndimage import gaussian_filter

    from core.qc.peak_detection import detect

    spec = np.zeros((32, 32))
    spec[5, 5] = -400.0
    spec[20, 20] = -300.0
    spec[10, 10] = 350.0
    spec = gaussian_filter(spec, sigma=1.0)
    peaks = detect(spec, PeakDetectionParams(sign_mode="dominant"))
    assert len(peaks) == 2
    assert all(p.height < 0 for p in peaks)



def test_peak_detection_ignores_flat_plateau() -> None:
    """A flat baseline does not produce spurious peaks (strict local maximum, 0.2.199-patch29aq
    modification)."""
    import numpy as np
    from scipy.ndimage import gaussian_filter

    from core.qc.peak_detection import detect

    spec = np.full((64, 128), 100.0)
    spec[20, 40] = 500.0
    spec[25, 90] = 500.0
    peaks = detect(gaussian_filter(spec, sigma=1.0))
    assert len(peaks) == 2


def test_peak_detection_threshold_5sigma_filters_noise() -> None:
    """The 5σ threshold eliminates local maxima of noise <5σ (default threshold for peak selection,
    0.2.199-patch29aq)."""
    import numpy as np
    from scipy.ndimage import gaussian_filter

    from core.qc.peak_detection import PeakDetectionParams, detect

    rng = np.random.default_rng(1)
    spec = rng.uniform(-4.0, 4.0, (64, 128))
    spec[20, 40] = 100.0
    spec[25, 90] = 90.0
    spec = gaussian_filter(spec, sigma=1.0)
    peaks = detect(
        spec, PeakDetectionParams(sigma_multiplier=5.0, min_snr=5.0)
    )
    assert len(peaks) == 2



def test_snap_to_peak_top_finds_peak() -> None:
    """Click near the peak to snap to the top; click flat to keep it as is (0.2.199-patch29ar)."""
    import numpy as np
    from scipy.ndimage import gaussian_filter

    from core.qc.peak_detection import snap_to_peak_top

    spec = np.zeros((32, 32))
    spec[10, 12] = 500.0
    spec = gaussian_filter(spec, sigma=1.0)
    assert snap_to_peak_top(spec, 9, 11) == (10, 12)
    flat = np.full((32, 32), 5.0)
    assert snap_to_peak_top(flat, 5, 5) == (5, 5)
