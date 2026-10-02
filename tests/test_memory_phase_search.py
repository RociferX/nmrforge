"""In-memory phase-search engine tests (complex data + legacy criteria, no backend)."""

from __future__ import annotations

import numpy as np

from workflow.memory_phase_search import (
    joint_recheck_memory,
    rotate_real,
    score_axis_memory,
    search_axis_memory,
)


def _complex_axis_2d(
    size: tuple[int, int],
    axis: int,
    p0: float = 0.0,
    p1: float = 0.0,
    *,
    width: float = 1.5,
) -> np.ndarray:
    """Build a 2D spectrum that is complex along one axis only (matching the real
    "per-axis complex preview"): the evaluated axis is an analytic complex Lorentzian with a
    known phase applied, the other axis a real pure-absorption Lorentzian (equivalent to the
    other dimensions after -di, avoiding phase contamination).
    """
    n0, n1 = size
    k0 = np.arange(n0, dtype=float)
    k1 = np.arange(n1, dtype=float)
    data = np.zeros(size, dtype=np.complex128)
    for c0, c1, amp in (
        (n0 * 0.35, n1 * 0.45, 400.0),
        (n0 * 0.62, n1 * 0.58, 320.0),
    ):
        z0 = 1.0 / (1.0 + 1j * (k0 - c0) / width)
        z1 = 1.0 / (1.0 + 1j * (k1 - c1) / width)
        real0 = 1.0 / (1.0 + ((k0 - c0) / width) ** 2)
        real1 = 1.0 / (1.0 + ((k1 - c1) / width) ** 2)
        if axis == 0:
            data += amp * np.outer(z0, real1)
        else:
            data += amp * np.outer(real0, z1)
    return _apply_ramp(data, axis, p0, p1)


def _apply_ramp(arr: np.ndarray, axis: int, p0: float, p1: float) -> np.ndarray:
    n = arr.shape[axis]
    k = np.arange(n, dtype=float)
    ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
    shape = [1] * arr.ndim
    shape[axis] = n
    return arr * ramp.reshape(shape)


def test_rotate_real_recovers_absorptive() -> None:
    """Rotating back and taking the real part restores the pure-absorption real spectrum
    (same semantics as the legacy PS -di)."""
    base = _complex_axis_2d((128, 96), axis=0)
    mixed = _apply_ramp(base, 0, 90.0, 0.0)
    recovered = rotate_real(mixed, 0, -90.0, 0.0)
    assert float(np.max(np.abs(recovered - np.real(base)))) < 1e-6


def test_search_axis_memory_recovers_known_p0_axis0() -> None:
    """A constant -40° phase should be recovered as a correction of about +40° (coarse 30° grid).

    0.2.199-patch29dn: on a flat score surface the coarse-grid optimum is kept (no more
    platform circular median — the median drifts on flat regions, e.g. the data/8 F1 coarse
    grid value 90° was dragged to 80°); the recovery precision is therefore the coarse grid
    step (30°) and the tolerance is relaxed to ±15°.
    """
    mixed = _complex_axis_2d((128, 96), axis=0, p0=-40.0)
    est = search_axis_memory(mixed, axis=0)
    assert est is not None
    assert abs((est.phase[0] - 40.0 + 180.0) % 360.0 - 180.0) <= 15.0, est
    assert abs(est.phase[1]) <= 5.0, est


def test_search_axis_memory_recovers_known_p0_axis1() -> None:
    """0.2.199-patch29dn: flat surface keeps the coarse-grid optimum; accuracy is the coarse
    grid step (±15°)."""
    mixed = _complex_axis_2d((96, 128), axis=1, p0=-50.0)
    est = search_axis_memory(mixed, axis=1)
    assert est is not None
    assert abs((est.phase[0] - 50.0 + 180.0) % 360.0 - 180.0) <= 15.0, est


def test_search_axis_memory_near_zero_stays_zero() -> None:
    base = _complex_axis_2d((96, 80), axis=0)
    est = search_axis_memory(base, axis=0)
    assert est is not None
    assert abs(est.phase[0]) <= 5.0, est
    assert abs(est.phase[1]) <= 5.0, est


def test_score_axis_memory_matches_formula() -> None:
    base = _complex_axis_2d((64, 48), axis=0)
    real0 = np.real(base)
    indices, positions = [], []
    for i in range(real0.shape[0]):
        if float(np.max(np.abs(real0[i, :]))) > 0:
            indices.append(i)
            positions.append(int(np.argmax(np.abs(real0[i, :]))))
    s = score_axis_memory(base, 0, 0.0, 0.0, indices, positions)
    assert 0.0 <= s <= 100.0
    assert s > 95.0  # a pure-absorption spectrum should score near full marks


def test_window_nets_flattens_baseline_before_sign_split() -> None:
    """A global baseline offset (positive or negative) no longer pollutes the net absorption:
    after flattening, an absorption peak has net ≈ +1 and a dispersive peak ≈ 0; an offset
    spectrum scores the same as a zero-baseline spectrum (0.2.175 user scheme restored)."""
    from workflow.memory_phase_search import _window_nets

    base = _complex_axis_2d((64, 48), axis=0)
    real0 = np.real(base)
    indices = [i for i in range(real0.shape[0]) if float(np.max(np.abs(real0[i, :]))) > 0]
    positions = [int(np.argmax(np.abs(real0[i, :]))) for i in indices]

    nets_flat = _window_nets(real0, 0, indices, positions)
    # Baseline lifted as a whole (peak height ~400, offset 30 is significant): after flattening
    # the net absorption should match the zero-baseline result; without flattening the positive
    # offset inflates net.
    shifted = real0 + 30.0
    nets_shifted = _window_nets(shifted, 0, indices, positions)
    assert len(nets_flat) == len(nets_shifted) >= 2
    med_flat = float(np.median(nets_flat))
    med_shifted = float(np.median(nets_shifted))
    assert med_flat > 0.5, med_flat
    assert abs(med_shifted - med_flat) < 0.15, (med_flat, med_shifted)


def test_lock_discrete_traces_excludes_clump() -> None:
    """Discrete sharp-peak traces are selected while the central mixed blob (wide plateau) is
    excluded (user feedback: mixed peak clusters skew the phase, so only discrete peaks are
    tuned)."""
    from workflow.memory_phase_search import _lock_discrete_traces

    n0, n1 = 128, 128
    k0 = np.arange(n0, dtype=float)
    k1 = np.arange(n1, dtype=float)
    arr = np.zeros((n0, n1), dtype=np.complex128)
    w = 1.2
    z0 = 1.0 / (1.0 + 1j * (k0 - 30) / w)
    arr += 400.0 * np.outer(z0, 1.0 / (1.0 + ((k1 - 40) / 2.0) ** 2))
    # Central blob: spatially concentrated into one lump at k1>=64 (superimposed amplitude 200,
    # width 8, spacing 8) so it does not pollute the discrete peak column at k1≈40
    for c0 in range(60, 97, 8):
        for c1 in range(64, 97, 8):
            zz0 = 1.0 / (1.0 + ((k0 - c0) / 8.0) ** 2)
            zz1 = 1.0 / (1.0 + ((k1 - c1) / 8.0) ** 2)
            arr += 200.0 * np.outer(zz0, zz1)
    idx, pos = _lock_discrete_traces(arr, 0)
    assert idx, "应有迹线入选"
    # Discrete peaks sit at k0=30, the blob at k0>=60. Most selected traces should peak nearby
    near = sum(1 for p0 in pos if abs(p0 - 30) <= 5)
    assert near / len(pos) >= 0.7, (near, len(pos))
    assert len(idx) < n1, "不应选中全部迹线"


def test_joint_recheck_tie_keeps_fixed() -> None:
    """When the joint review has a flat p1 (±5° ties), it must not be significantly better than
    the sequential fixed route (the caller gates on PHASE_SCORE_FLAT_MARGIN instead of always
    falling back)."""
    from workflow.memory_phase_search import PHASE_SCORE_FLAT_MARGIN

    size = (96, 80)
    arr0 = _complex_axis_2d(size, axis=0, p0=-30.0)
    arr1 = _complex_axis_2d(size, axis=1, p0=-20.0)
    est0 = search_axis_memory(arr0, axis=0)
    est1 = search_axis_memory(arr1, axis=1)
    assert est0 is not None and est1 is not None
    fixed = {"F1": est0.phase, "F2": est1.phase}
    arrays = {"F1": arr0, "F2": arr1}
    index = {"F1": 0, "F2": 1}
    traces = {"F1": est0.traces, "F2": est1.traces}
    best, best_score, fixed_score, zero_score = joint_recheck_memory(arrays, index, traces, fixed)
    # Flat p1: the joint optimum is not significantly better than sequential (or the gate updates)
    assert best_score - fixed_score < PHASE_SCORE_FLAT_MARGIN + 1e-9
    assert fixed_score >= zero_score - 1e-9


def test_joint_recheck_row_scoring_matches_full_array() -> None:
    """patch29fi: joint row-wise scoring is equivalent to the legacy array score_axis_memory."""
    import itertools

    size = (96, 80)
    arr0 = _complex_axis_2d(size, axis=0, p0=-30.0)
    arr1 = _complex_axis_2d(size, axis=1, p0=-20.0)
    est0 = search_axis_memory(arr0, axis=0)
    est1 = search_axis_memory(arr1, axis=1)
    assert est0 is not None and est1 is not None
    fixed = {"F1": est0.phase, "F2": est1.phase}
    arrays = {"F1": arr0, "F2": arr1}
    index = {"F1": 0, "F2": 1}
    traces = {"F1": est0.traces, "F2": est1.traces}
    axes = list(fixed)
    combos: list[dict] = []
    for combo in itertools.product((-5.0, 0.0, 5.0), repeat=2):
        ph = dict(fixed)
        for axis, off in zip(axes, combo):
            p0, p1 = fixed[axis]
            ph[axis] = (p0, p1 + off)
        combos.append(ph)
    combos.append({axis: (0.0, 0.0) for axis in axes})

    def ref_score(ph: dict) -> float:
        vals: list[float] = []
        for axis in axes:
            idx, pos = traces[axis]
            vals.append(
                score_axis_memory(
                    arrays[axis],
                    index[axis],
                    ph[axis][0],
                    ph[axis][1],
                    idx,
                    pos,
                )
            )
        return float(np.mean(vals))

    best_ref = max(combos, key=ref_score)
    best, best_score, fixed_score, zero_score = joint_recheck_memory(arrays, index, traces, fixed)
    assert best == best_ref, (best, best_ref)
    assert abs(best_score - ref_score(best_ref)) < 1e-6
    assert abs(fixed_score - ref_score(fixed)) < 1e-6
    assert abs(zero_score - ref_score({a: (0.0, 0.0) for a in axes})) < 1e-6
