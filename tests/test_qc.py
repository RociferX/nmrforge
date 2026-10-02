"""QC data structure skeleton tests."""

from __future__ import annotations

import numpy as np
import pytest

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
    """mixed experiment: both positive and negative peaks are picked, height keeps the
    true sign.
    """
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
    """uniform experiment: keep only the majority-sign peaks (mostly negative -> only
    negative peaks are reported).
    """
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
    """A flat baseline produces no false peaks (strict local maxima, fixed in
    0.2.199-patch29aq).
    """
    import numpy as np
    from scipy.ndimage import gaussian_filter

    from core.qc.peak_detection import detect

    spec = np.full((64, 128), 100.0)
    spec[20, 40] = 500.0
    spec[25, 90] = 500.0
    peaks = detect(gaussian_filter(spec, sigma=1.0))
    assert len(peaks) == 2


def test_peak_detection_threshold_5sigma_filters_noise() -> None:
    """The 5-sigma threshold rejects noise local maxima below 5 sigma (the default peak
    picking threshold, 0.2.199-patch29aq).
    """
    import numpy as np
    from scipy.ndimage import gaussian_filter

    from core.qc.peak_detection import PeakDetectionParams, detect

    rng = np.random.default_rng(1)
    spec = rng.uniform(-4.0, 4.0, (64, 128))
    spec[20, 40] = 100.0
    spec[25, 90] = 90.0
    spec = gaussian_filter(spec, sigma=1.0)
    peaks = detect(spec, PeakDetectionParams(sigma_multiplier=5.0, min_snr=5.0))
    assert len(peaks) == 2


def test_snap_to_peak_top_finds_peak() -> None:
    """A click near a peak snaps to the peak top; a click on flat ground stays as it is
    (0.2.199-patch29ar).
    """
    import numpy as np
    from scipy.ndimage import gaussian_filter

    from core.qc.peak_detection import snap_to_peak_top

    spec = np.zeros((32, 32))
    spec[10, 12] = 500.0
    spec = gaussian_filter(spec, sigma=1.0)
    assert snap_to_peak_top(spec, 9, 11) == (10, 12)
    flat = np.full((32, 32), 5.0)
    assert snap_to_peak_top(flat, 5, 5) == (5, 5)


@pytest.mark.parametrize("sign", [1, -1])
def test_peak_detection_finds_half_grid_gaussian_center(sign: int) -> None:
    "Regression coverage: test peak detection finds half grid gaussian center."
    from core.qc.peak_detection import detect

    row, col = np.mgrid[:64, :64]
    spec = sign * 100.0 * np.exp(-((row - 30.0) ** 2 + (col - 30.5) ** 2) / 2.0)
    peaks = detect(
        spec,
        PeakDetectionParams(sigma_multiplier=3.0, min_snr=3.0, sign_mode="both"),
    )

    assert len(peaks) == 1
    assert peaks[0].sign == sign
    assert peaks[0].position[0] == pytest.approx(30.0, abs=0.1)
    assert peaks[0].position[1] == pytest.approx(30.5, abs=0.1)


@pytest.mark.parametrize("sign", [1, -1])
def test_peak_detection_collapses_equal_height_2x2_peak_top(sign: int) -> None:
    "Regression coverage: test peak detection collapses equal height 2x2 peak top."
    from core.qc.peak_detection import detect

    spec = np.random.default_rng(12).uniform(-0.01, 0.01, (64, 64))
    spec[30:32, 30:32] = sign * 100.0
    peaks = detect(
        spec,
        PeakDetectionParams(sigma_multiplier=3.0, min_snr=3.0, sign_mode="both"),
    )

    assert len(peaks) == 1
    assert peaks[0].sign == sign
    assert peaks[0].position == pytest.approx((30.0, 30.0), abs=0.5)


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("shape", ["plateau", "ridge"])
def test_peak_detection_rejects_wide_flat_top_and_long_ridge(sign: int, shape: str) -> None:
    "Regression coverage: test peak detection rejects wide flat top and long ridge."
    from core.qc.peak_detection import detect

    spec = np.random.default_rng(13).uniform(-0.01, 0.01, (64, 64))
    if shape == "plateau":
        spec[28:36, 28:36] = sign * 100.0
    else:
        spec[32, 20:44] = sign * 100.0
    peaks = detect(
        spec,
        PeakDetectionParams(sigma_multiplier=3.0, min_snr=3.0, sign_mode="both"),
    )

    assert peaks == []


@pytest.mark.parametrize("offset", [1.0, -1.0])
def test_peak_detection_is_invariant_to_constant_baseline_shift(offset: float) -> None:
    "Regression coverage: test peak detection is invariant to constant baseline shift."
    from core.qc.peak_detection import detect

    spec = np.random.default_rng(14).uniform(-0.01, 0.01, (64, 64))
    spec[30, 30] = 100.0
    params = PeakDetectionParams(sigma_multiplier=35.0, min_snr=3.0)
    original = detect(spec, params)
    shifted = detect(spec + offset, params)

    assert len(original) == len(shifted) == 1
    assert shifted[0].position == pytest.approx(original[0].position, abs=1e-8)
    assert shifted[0].sign == original[0].sign
    assert shifted[0].height == pytest.approx(original[0].height, rel=1e-6, abs=1e-6)
    assert shifted[0].snr == pytest.approx(original[0].snr, rel=1e-6, abs=1e-6)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_peak_detection_rejects_nonfinite_spectrum(bad: float) -> None:
    "Regression coverage: test peak detection rejects nonfinite spectrum."
    from core.qc.peak_detection import detect

    spec = np.zeros((8, 8))
    spec[0, 0] = bad
    with pytest.raises(ValueError) as error:
        detect(spec)
    assert "non-finite" in str(error.value) or "NaN" in str(error.value)


@pytest.mark.parametrize(
    "params",
    [
        PeakDetectionParams(sign_mode="sideways"),
        PeakDetectionParams(sigma_multiplier=np.inf),
        PeakDetectionParams(min_snr=np.nan),
        PeakDetectionParams(neighborhood=2),
    ],
)
def test_peak_detection_rejects_invalid_parameters(params: PeakDetectionParams) -> None:
    from core.qc.peak_detection import detect

    with pytest.raises(ValueError):
        detect(np.zeros((8, 8)), params)
