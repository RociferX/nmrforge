"""Per-dimension consensus phase-search tests (0.2.199-patch29i, manual projection tuning).

Model: in a 3D complex spectrum each peak's total phase is the sum of the three axes'
signal phases (each axis's correction (p0, p1) is known). Every trace of the target axis
(the other two coordinates fixed) holds several peaks with **sufficient spacing**,
mimicking the clean 1D traces seen in a manual projection:
- p1 is fitted from the phase differences between peaks in a trace (the other dimensions'
  phases are a common constant along the whole trace and cancel), so each dimension is
  independent and does not disturb the others;
- p0 is recovered per axis when the other axes have zero phase; when several axes carry
  phase at once, what is identifiable is the sum of the per-axis p0 values (a peak's
  absolute phase depends only on the total; splitting it across dimensions is a
  convention).
"""

from __future__ import annotations

import numpy as np
import pytest

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
    """Synthetic complex 3D spectrum (F2, F1, F3): each target-axis trace has spaced peaks."""
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
        """Pick n peak positions with spacing >= 6 along the n_pts axis."""
        lo, hi = 6, n_pts - 6
        if n == 1:
            return [int(rng.integers(lo, hi))]
        step = (hi - lo) // n
        out: list[int] = []
        for idx in range(n):
            base = lo + idx * step
            out.append(int(base + rng.integers(0, max(step - 6, 1))))
        return out

    if target_axis == 2:  # traces along F3, (F2, F1) fixed
        for i in range(4, n2 - 4):
            for j in range(4, n1 - 4):
                for k in _trace_positions(n_per_trace, n3):
                    spec += _add(i, j, k, float(rng.uniform(40.0, 80.0)))
    elif target_axis == 0:  # traces along F2, (F1, F3) fixed
        for j in range(4, n1 - 4):
            for k in range(4, n3 - 4):
                for i in _trace_positions(n_per_trace, n2):
                    spec += _add(i, j, k, float(rng.uniform(40.0, 80.0)))
    else:  # traces along F1, (F2, F3) fixed
        for i in range(4, n2 - 4):
            for k in range(4, n3 - 4):
                for j in _trace_positions(n_per_trace, n1):
                    spec += _add(i, j, k, float(rng.uniform(40.0, 80.0)))
    spec += rng.normal(0.0, 0.1, spec.shape) + 1j * rng.normal(0.0, 0.1, spec.shape)
    return spec


def _close(actual: float, expected: float, tol: float) -> bool:
    d = (actual - expected) % 360.0
    if d > 180.0:
        d -= 360.0
    return abs(d) <= tol


def test_single_axis_phase_recovered() -> None:
    """With zero phase on the other axes, the target axis (p0, p1) is fully recovered."""
    spec = _make_3d(target_axis=2, phases={"F3": (40.0, 25.0), "F1": (0.0, 0.0), "F2": (0.0, 0.0)})
    est = search_axis_phase_consensus(spec, axis=2)
    assert est is not None, "应有干净峰"
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
    """With phase on several axes, p1 is fitted within a trace and is unaffected by the
    other axes' constant phase."""
    all_phases = {"F3": (40.0, 25.0), "F1": (355.0, 10.0), "F2": (80.0, -15.0)}
    for axis, expected_p1 in ((2, 25.0), (0, -15.0), (1, 10.0)):
        spec = _make_3d(target_axis=axis, phases=all_phases)
        est = search_axis_phase_consensus(spec, axis)
        assert est is not None, f"axis {axis} 应有峰"
        assert abs(est[1] - expected_p1) <= 6.0, (axis, est)


def test_p0_sum_identifiable_when_all_phased() -> None:
    """With phase on several axes, the sum of the per-axis p0 values (the peak's absolute
    phase) is what can be identified."""
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
    spec = rng.normal(0.0, 1.0, (16, 16, 32)) + 1j * rng.normal(0.0, 1.0, (16, 16, 32))
    assert search_axis_phase_consensus(spec, axis=2) is None


def test_mixed_sign_mode_keeps_negative_peaks() -> None:
    """sign_mode=mixed: half the traces inverted (±180 offset on other dims) still agree."""
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
    """Exactly reconstructed pure-real direct-dimension spectrum: the time-domain Ŝ keeps only
    the positive half (t<n/2) and X=IFFT(Ŝ), so z_std (the _hilbert positive-frequency half)
    restores X exactly, the peak phase is the constant psi0, and the search should return
    (-psi0, 0). The real part is kept and the imaginary part dropped; trace sparsity is
    tunable.
    """
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
            amp * np.exp(-t / t2v) * np.exp(1j * (2 * np.pi * f0 * t / n_dir + np.deg2rad(psi0))),
            0.0,
        )
        idx = np.unravel_index(flat_idx, shape[:-1])
        spec[idx] += np.fft.ifft(shat)
    return np.real(spec) + rng.normal(0.0, 0.05, spec.shape)


def test_direct_phase_real_ht_projected_traces() -> None:
    """3D projection traces + HT (positive half, scipy convention) recover the direct p0."""
    real = _exact_direct_spectrum((20, 16, 160), 40.0, seed=7, occ=0.15, t2=(4.0, 10.0))
    est = search_direct_phase_real_ht(real, axis=-1)
    assert est is not None, "投影迹线应有干净峰"
    assert _close(est[0], (-40.0) % 360.0, 20.0), est
    assert abs(est[1]) <= 15.0, est
    assert est[2] > 50.0, est


def test_direct_phase_real_ht_2d_rows() -> None:
    """2D spectrum: each row is a direct-dimension trace (no projection) and recovers the same."""
    real = _exact_direct_spectrum((24, 120), -70.0, seed=11)
    est = search_direct_phase_real_ht(real, axis=-1)
    assert est is not None
    assert _close(est[0], 70.0, 20.0), est
    assert abs(est[1]) <= 15.0, est
    assert est[2] > 50.0, est


@pytest.mark.parametrize("shape", [(24, 160), (20, 16, 160)])
@pytest.mark.parametrize("correction", [-6.0, -40.0, 120.0, 174.0])
def test_direct_ht_uniform_returns_full_circle_phase(shape, correction) -> None:
    if len(shape) == 3:
        # Use clean projected peaks to isolate 180-degree ambiguity from random overlap fitting.
        row = _exact_direct_spectrum((1, shape[-1]), -correction, seed=41)
        real = np.broadcast_to(row, shape)
    else:
        real = _exact_direct_spectrum(shape, -correction, seed=41, t2=(5.0, 10.0))
    est = search_direct_phase_real_ht(real)
    assert est is not None
    assert _close(est[0], correction % 360.0, 8.0), est
    assert abs(est[1]) < 10.0


@pytest.mark.parametrize("negative_fraction", [0.25, 0.5, 0.75, 1.0])
def test_direct_ht_mixed_does_not_flip_small_negative_correction(negative_fraction) -> None:
    real = _exact_direct_spectrum((24, 160), 6.0, seed=43, t2=(5.0, 10.0))
    n_negative = int(real.shape[0] * negative_fraction)
    real[:n_negative] *= -1.0
    est = search_direct_phase_real_ht(real, sign_mode="mixed")
    assert est is not None
    assert _close(est[0], 354.0, 8.0), est
    assert abs(est[1]) < 10.0
