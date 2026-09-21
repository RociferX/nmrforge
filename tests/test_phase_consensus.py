"""Dimension-wise consensus phase search test (0.2.199-patch29i, artificial projection phase
modulation idea). Model: The total phase of each peak of the 3D complex spectrum = the sum of
the signal phases of each of the three axes (the correction (p0, p1) of each axis is known). Put
multiple peaks with sufficient spacing in each trace of the target axis (fixed coordinates of
the other two axes), simulating the clean 1D trace seen by artificial projection: - p1 is fitted
by the multi-peak phase difference in the trace (the other dimensions phase is the entire trace)
Common constant, offset), each dimension is independent and does not interfere with each other;
- p0 is restored in one axis when the phase of other axes is zero; when multiple axes have
phases at the same time, what can be distinguished is the sum of p0 in each dimension (the
absolute phase of the spectrum peak is only determined by the sum, and the splitting of each
dimension is a convention)."""

from __future__ import annotations

import numpy as np

from core.optimization.phase_consensus import (
    search_axis_phase_consensus,
    search_direct_phase_real_ht,
)


def _make_3d(
    target_axis: int = 2,
    phases: dict[str, tuple[float, float]] | None = None,
    seed: int = 5,
    n_per_trace: int = 3,
) -> np.ndarray:
    """Synthesize the replica 3D spectrum: (F2, F1, F3), each trace of the target axis has multiple
    peaks and adequate intervals."""
    n2, n1, n3 = 40, 40, 48
    phases = phases or {
        "F3": (40.0, 25.0),
        "F1": (0.0, 0.0),
        "F2": (0.0, 0.0),
    }
    rng = np.random.default_rng(seed)
    spec = np.zeros((n2, n1, n3), dtype=complex)
    k2 = np.arange(n2, dtype=float)
    k1 = np.arange(n1, dtype=float)
    k3 = np.arange(n3, dtype=float)
    sig2 = np.exp(-1j * np.deg2rad(phases["F2"][0] + phases["F2"][1] * k2 / (n2 - 1)))
    sig1 = np.exp(-1j * np.deg2rad(phases["F1"][0] + phases["F1"][1] * k1 / (n1 - 1)))
    sig3 = np.exp(-1j * np.deg2rad(phases["F3"][0] + phases["F3"][1] * k3 / (n3 - 1)))

    def _add(i: int, j: int, k: int, a: float) -> np.ndarray:
        g2 = np.exp(-((k2 - i) ** 2) / (2.0 * 0.9))
        g1 = np.exp(-((k1 - j) ** 2) / (2.0 * 0.9))
        g3 = np.exp(-((k3 - k) ** 2) / (2.0 * 1.1))
        return (
            a
            * g2[:, None, None]
            * g1[None, :, None]
            * g3[None, None, :]
            * sig3[None, None, :]
            * sig2[i]
            * sig1[j]
        )

    def _trace_positions(n: int, n_pts: int) -> list[int]:
        """N_pts Take n peak positions with spacing >=6 on the axis."""
        lo, hi = 6, n_pts - 6
        if n == 1:
            return [int(rng.integers(lo, hi))]
        step = (hi - lo) // n
        out: list[int] = []
        for idx in range(n):
            base = lo + idx * step
            out.append(int(base + rng.integers(0, max(step - 6, 1))))
        return out

    if target_axis == 2:  # Trace along F3, fixed (F2,F1).
        for i in range(4, n2 - 4):
            for j in range(4, n1 - 4):
                for k in _trace_positions(n_per_trace, n3):
                    spec += _add(i, j, k, float(rng.uniform(40.0, 80.0)))
    elif target_axis == 0:  # Trace along F2, fixed (F1,F3).
        for j in range(4, n1 - 4):
            for k in range(4, n3 - 4):
                for i in _trace_positions(n_per_trace, n2):
                    spec += _add(i, j, k, float(rng.uniform(40.0, 80.0)))
    else:  # Trace along F1, fixed (F2,F3).
        for i in range(4, n2 - 4):
            for k in range(4, n3 - 4):
                for j in _trace_positions(n_per_trace, n1):
                    spec += _add(i, j, k, float(rng.uniform(40.0, 80.0)))
    spec += rng.normal(0.0, 0.1, spec.shape) + 1j * rng.normal(
        0.0, 0.1, spec.shape
    )
    return spec


def _close(actual: float, expected: float, tol: float) -> bool:
    d = (actual - expected) % 360.0
    if d > 180.0:
        d -= 360.0
    return abs(d) <= tol


def test_single_axis_phase_recovered() -> None:
    """When the phase of other axes is zero, the target axis (p0, p1) is completely restored."""
    spec = _make_3d(target_axis=2, phases={"F3": (40.0, 25.0), "F1": (0.0, 0.0), "F2": (0.0, 0.0)})
    est = search_axis_phase_consensus(spec, axis=2)
    assert est is not None, "There should be a clean peak"
    assert _close(est[0], 40.0, 8.0), est
    assert abs(est[1] - 25.0) <= 6.0, est
    assert est[2] > 55.0, est


def test_indirect_axis_phase_recovered() -> None:
    spec = _make_3d(target_axis=0, phases={"F3": (0.0, 0.0), "F1": (0.0, 0.0), "F2": (80.0, -15.0)})
    est2 = search_axis_phase_consensus(spec, axis=0)
    assert est2 is not None
    assert _close(est2[0], 80.0, 8.0), est2
    assert abs(est2[1] - (-15.0)) <= 6.0, est2


def test_p1_independent_of_other_axis_phases() -> None:
    """Each axis has phase at the same time: p1 is fitted within the trace and is not interfered by
    the phase constants of other axes."""
    all_phases = {"F3": (40.0, 25.0), "F1": (355.0, 10.0), "F2": (80.0, -15.0)}
    for axis, expected_p1 in ((2, 25.0), (0, -15.0), (1, 10.0)):
        spec = _make_3d(target_axis=axis, phases=all_phases)
        est = search_axis_phase_consensus(spec, axis)
        assert est is not None, f"axis {axis} There should be a peak"
        assert abs(est[1] - expected_p1) <= 6.0, (axis, est)


def test_p0_sum_identifiable_when_all_phased() -> None:
    """Multiple axes have phases at the same time: the sum of p0 of each axis (the absolute phase
    of the spectrum peak) can be distinguished."""
    spec = _make_3d(
        target_axis=2,
        phases={"F3": (40.0, 25.0), "F1": (355.0, 10.0), "F2": (80.0, -15.0)},
    )
    est = search_axis_phase_consensus(spec, axis=2)
    assert est is not None
    total = (40.0 + 355.0 + 80.0) % 360.0  # 115
    assert _close(est[0], total, 25.0), est


def test_noise_only_returns_none() -> None:
    rng = np.random.default_rng(3)
    spec = rng.normal(0.0, 1.0, (16, 16, 32)) + 1j * rng.normal(
        0.0, 1.0, (16, 16, 32)
    )
    assert search_axis_phase_consensus(spec, axis=2) is None


def test_mixed_sign_mode_keeps_negative_peaks() -> None:
    """Sign_mode=mixed: Half of the traces can be inverted (other dimensions +/-180 offset) and
    still achieve consensus."""
    spec = _make_3d(target_axis=2, phases={"F3": (40.0, 25.0), "F1": (0.0, 0.0), "F2": (0.0, 0.0)})
    for i in range(0, spec.shape[0], 2):
        spec[i] = -spec[i]
    est = search_axis_phase_consensus(spec, axis=2, sign_mode="mixed")
    assert est is not None
    assert _close(est[0], 40.0, 12.0), est







    assert est[2] > 50.0, est


def _exact_direct_spectrum(
    shape: tuple[int, ...],
    psi0: float,
    *,
    seed: int,
    occ: float = 1.0,
    t2: tuple[float, float] = (6.0, 15.0),
) -> np.ndarray:
    """Accurately reconstructed pure real direct dimension spectrum: time domain Ŝ only retains the
    positive half (t<n/2), X=IFFT(Ŝ), then z_std(_hilbert positive frequency half)
    mathematically accurately restores."""
    rng = np.random.default_rng(seed)
    n_dir = shape[-1]
    half = n_dir // 2
    t = np.arange(n_dir, dtype=float)
    mask = t < half
    spec = np.zeros(shape, dtype=complex)
    n_other = int(np.prod(shape[:-1]))
    for flat_idx in range(n_other):
        if rng.random() < (1.0 - occ):
            continue
        f0 = int(rng.integers(12, half - 12))
        amp = float(rng.uniform(80.0, 200.0))
        t2v = float(rng.uniform(*t2))
        shat = np.where(
            mask,
            amp * np.exp(-t / t2v) * np.exp(
                1j * (2 * np.pi * f0 * t / n_dir + np.deg2rad(psi0))
            ),
            0.0,
        )
        idx = np.unravel_index(flat_idx, shape[:-1])
        spec[idx] += np.fft.ifft(shat)
    return np.real(spec) + rng.normal(0.0, 0.05, spec.shape)


def test_direct_phase_real_ht_projected_traces() -> None:
    """3D projection trace + HT(Positive frequency half/scipy convention) restores direct dimension
    p0."""
    real = _exact_direct_spectrum(
        (20, 16, 160), 40.0, seed=7, occ=0.15, t2=(4.0, 10.0)
    )
    est = search_direct_phase_real_ht(real, axis=-1)
    assert est is not None, "The projected trace should have clean peaks"
    # 0.2.199-patch29p: direct dimension returns 0-180° folded value (positive peak is not forced).
    assert _close(est[0], (-40.0) % 180.0, 20.0), est
    assert abs(est[1]) <= 15.0, est
    assert est[2] > 50.0, est


def test_direct_phase_real_ht_2d_rows() -> None:
    """2D spectrum: Each row is a direct dimension trace (without projection), and the correction
    is also restored."""
    real = _exact_direct_spectrum((24, 120), -70.0, seed=11)
    est = search_direct_phase_real_ht(real, axis=-1)
    assert est is not None
    assert _close(est[0], 70.0, 20.0), est
    assert abs(est[1]) <= 15.0, est
    assert est[2] > 50.0, est
