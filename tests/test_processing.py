"""处理原语测试（numpy 合成数据）。"""

from __future__ import annotations

import numpy as np

from core.processing import (
    apodization,
    baseline,
    calibration,
    ft,
    phase,
    sign_correction,
    transpose,
    zero_fill,
)


def test_apodization_sine_window() -> None:
    data = np.ones((4, 16), dtype=complex)
    params = apodization.ApodizationParams(
        window="sine_bell", axis="F2", params={"off": 0.0, "end": 1.0, "pow": 1}
    )
    out = apodization.apply(data, params)
    expected = np.sin(np.pi * np.linspace(0.0, 1.0, 16))
    assert out.shape == data.shape
    assert np.allclose(out[0], expected)


def test_zero_fill_auto() -> None:
    data = np.zeros((4, 16), dtype=complex)
    out = zero_fill.apply(data, zero_fill.ZeroFillParams(size="auto", axis="F2"))
    assert out.shape == (4, 32)


def test_ft_peak_bin() -> None:
    n = 256
    t = np.arange(n)
    signal = np.sin(2 * np.pi * 10 * t / n)
    data = signal[np.newaxis, :].astype(complex)
    out = ft.apply(data, ft.FtParams(axis="F2"))
    assert int(np.argmax(np.abs(out[0]))) == 10


def test_phase_90_degrees() -> None:
    data = np.ones((1, 8), dtype=complex)
    out = phase.apply(data, phase.PhaseParams(p0=90.0, axis="F2"))
    assert np.allclose(out.real, 0.0, atol=1e-12)
    assert np.allclose(out.imag, 1.0, atol=1e-12)


def test_baseline_detect_and_correct() -> None:
    data = np.linspace(0.0, 10.0, 64)[np.newaxis, :] + 0j
    metrics = baseline.detect(data)
    assert metrics["slope"] > 0
    corrected = baseline.apply(data, baseline.BaselineParams(order=1, axis="F2"))
    assert np.allclose(corrected.real, 0.0, atol=1e-8)


def test_ppm_axis_decreasing() -> None:
    size, sf, sw, o1p = 256, 599.8937495, 10000.0, 4.703
    ppm = calibration.ppm_axis(size, sf, sw, 0.0, o1p)
    assert ppm[0] > ppm[-1]
    assert np.isclose(ppm[size // 2], o1p)


def test_transpose_2d() -> None:
    data = np.zeros((8, 16), dtype=complex)
    out = transpose.apply(data, transpose.TransposeParams(order=("F2", "F1")))
    assert out.shape == (16, 8)


def test_sign_correction() -> None:
    data = (np.arange(12).reshape(3, 4) + 1j).astype(complex)
    flipped = sign_correction.apply(data, sign_correction.SignCorrectionParams(sign_flip=True))
    assert np.allclose(flipped, -data)
    conj = sign_correction.apply(data, sign_correction.SignCorrectionParams(conjugate=True))
    assert np.allclose(conj, np.conj(data))
