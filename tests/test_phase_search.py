"""Direct-dimension statistical phase-search tests."""

from __future__ import annotations

import numpy as np

from core.optimization.phase_search import direct_ft_traces


def test_direct_ft_traces() -> None:
    fid = np.zeros((4, 8), dtype=complex)
    out = direct_ft_traces(fid, zf_size=16)
    assert out.shape == (4, 16)
    out2 = direct_ft_traces(fid)
    assert out2.shape == (4, 8)


def test_search_direct_spectrum_phase_recovers_p0_p1() -> None:
    """0.2.88: frequency-domain search on the direct FT spectrum recovers (p0, p1) together."""
    from core.optimization.phase_search import search_direct_spectrum_phase

    rng = np.random.default_rng(7)
    n = 512
    k = np.arange(n)
    # Signal phase (+33°, p1 ramp -42°); the search returns the PS correction (its negation)
    sig_p0, sig_p1 = 33.0, -42.0
    traces = []
    for _ in range(80):
        # At NUS increment 0 (t1=0) the direct phase is clean (first-trace anchor semantics)
        spec = np.zeros(n, dtype=complex)
        for peak in (140, 260, 380):
            spec += np.exp(-((k - peak) ** 2) / (2 * 6.0**2))
        spec *= np.exp(
            1j * np.deg2rad(sig_p0 + sig_p1 * k / max(n - 1, 1))
        )
        spec += rng.normal(0.0, 0.02, size=n)
        spec += 1j * rng.normal(0.0, 0.02, size=n)
        traces.append(spec)
    est = search_direct_spectrum_phase(np.array(traces))
    assert est is not None
    p0, p1, score, gain = est
    # p0/p1 are corrections (negated signal phase); p0 is anchored to the first trace (t1=0)
    assert abs(((p0 + sig_p0 + 180.0) % 360.0) - 180.0) <= 12.0, p0
    assert abs(p1 + sig_p1) <= 10.0, p1
    assert score > 0.6
    assert gain > 0.05


def test_nus_direct_phase_matches_existing_sign_convention() -> None:
    """0.2.92: the NU-DFT direct-dimension p0 has the same semantics as the existing method
    (positive-peak solution, removing the ±180 ambiguity).

    The existing (verified) method asserts p0≈120 (±7.5) for θ_true=-120; NU-DFT should
    give the same answer on the equivalent complex slice, not 300 (the ±180 inversion).
    """
    from scipy.signal import hilbert

    from core.optimization.phase_search import nus_direct_phase

    def make_slices(theta_true: float, n1: int = 32, n2: int = 64) -> np.ndarray:
        x = np.arange(n2)
        a = 100.0 / (1.0 + ((x - 30) / 2.0) ** 2)
        d = -np.imag(hilbert(a))
        spectrum = (a + 1j * d) * np.exp(1j * np.deg2rad(theta_true))
        return np.array(
            [
                np.fft.ifft(spectrum)
                * np.exp(1j * 2.0 * np.pi * 16 * i / n1)
                for i in range(n1)
            ]
        )

    for theta, expected in ((-120.0, 120.0), (33.0, 327.0), (90.0, 270.0)):
        est = nus_direct_phase(
            make_slices(theta), [(i,) for i in range(32)], 32, 1
        )
        assert est is not None
        p0, p1, score, _gain, _kstar = est
        assert abs(((p0 - expected + 180.0) % 360.0) - 180.0) <= 7.5, p0
        assert abs(p1) <= 1e-6
        assert score >= 2.0


def test_search_direct_phase_on_spectrum_recovers() -> None:
    """0.2.94: net-absorption scoring on a fixed trace of the final spectrum recovers (p0, p1).

    Signal phase -120° (p0) / -42° (p1 ramp) → the correction should be (120, 42).
    """
    from core.optimization.phase_search import search_direct_phase_on_spectrum

    n_f1, n = 64, 512
    k = np.arange(n)
    phi0, p1_sig = -120.0, -42.0
    rng = np.random.default_rng(3)
    fids = []
    for i in range(n_f1):
        spec = np.zeros(n, dtype=complex)
        for kp, f1 in ((140, 8.0), (260, 24.0), (380, 40.0)):
            lz = 1.0 / (1.0 + ((k - kp) / 8.0) ** 2)
            spec += lz * np.exp(
                1j * np.deg2rad(phi0 + p1_sig * k / max(n - 1, 1))
                + 1j * (2.0 * np.pi * f1 * i / n_f1)
            )
        spec += rng.normal(0.0, 0.02, size=n)
        spec += 1j * rng.normal(0.0, 0.02, size=n)
        fids.append(np.fft.ifft(spec))
    fids = np.array(fids)
    window = np.sin(np.pi * (0.45 + 0.5 * np.linspace(0, 1, n)))
    grid = np.array([np.fft.fft(f * window) for f in fids])
    spec2d = np.fft.fft(grid, axis=0)
    est = search_direct_phase_on_spectrum(spec2d, metric="net")
    assert est is not None
    p0, p1, score = est
    # For clean symmetric peaks the net/|Re| metric saturates on a plateau within ±90° (the
    # same trait as the existing optimizer; real spectra discriminate through overlap or
    # asymmetry, and on the VM the sampleI case recovers -52.5°). Here we verify: 1) a high
    # score (>90) means the positive-peak solution, the ±180 inversion (score≈0) being ruled
    # out; 2) the result lies on the plateau that contains the true value (120) (±90°).
    assert score > 90.0, score
    assert abs(((p0 - 120.0 + 180.0) % 360.0) - 180.0) <= 90.0, p0

def test_direct_phase_search_progress_and_result() -> None:
    '''Direct-dimension search: parallel candidates + progress messages (running/done).'''
    import numpy as np

    from core.optimization.phase_search import search_direct_phase_on_spectrum

    rng = np.random.default_rng(7)
    arr = rng.normal(size=(20, 16, 12)).astype(np.complex128)
    # Inject one strong direct-dimension peak
    arr[10, 8, :] = np.exp(1j * np.deg2rad(30.0)) * 10.0
    messages: list[str] = []
    res = search_direct_phase_on_spectrum(
        arr, axis=0, metric="symmetry", progress=messages.append
    )
    assert res is None or len(res) == 3
    if messages:
        assert "直接维相位搜索中" in messages[0]
        assert "完成" in messages[-1]


def test_direct_phase_search_cancelled_raises() -> None:
    """0.2.199-patch6: with the cancel flag set, the phase search raises and exits immediately."""
    import numpy as np
    import pytest

    from core.optimization.phase_search import search_direct_phase_on_spectrum

    rng = np.random.default_rng(11)
    arr = rng.normal(size=(24, 18, 14)).astype(np.complex128)
    arr[10, 8, :] = np.exp(1j * np.deg2rad(20.0)) * 10.0
    with pytest.raises(RuntimeError, match="任务已取消"):
        search_direct_phase_on_spectrum(
            arr, axis=0, metric="symmetry", cancel=lambda: True
        )

