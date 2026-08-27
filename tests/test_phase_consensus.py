"""逐维共识相位搜索测试(0.2.199-补29i,人工投影调相思路)。

模型:3D 复型谱每个峰总相位 = 三轴各自信号相位之和(各轴校正 (p0,p1)
已知)。目标轴每条迹线(固定其它两轴坐标)内放多个**间距足够**的峰,
模拟人工投影看到的干净 1D 迹线:
- p1 由迹线内多峰相位差拟合(其它维相位是整条迹线公共常数,抵消),
  每维独立、互不干扰;
- p0 在其它轴相位为零时单轴恢复;多轴同时有相位时可辨的是各维
  p0 之和(谱峰绝对相位只由总和决定,各维拆分是约定)。
"""

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
    """合成复型 3D 谱:(F2, F1, F3),目标轴每条迹线多峰且间隔足够。"""
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
        """n_pts 轴上取 n 个间距 >=6 的峰位。"""
        lo, hi = 6, n_pts - 6
        if n == 1:
            return [int(rng.integers(lo, hi))]
        step = (hi - lo) // n
        out: list[int] = []
        for idx in range(n):
            base = lo + idx * step
            out.append(int(base + rng.integers(0, max(step - 6, 1))))
        return out

    if target_axis == 2:  # 迹线沿 F3,固定 (F2,F1)
        for i in range(4, n2 - 4):
            for j in range(4, n1 - 4):
                for k in _trace_positions(n_per_trace, n3):
                    spec += _add(i, j, k, float(rng.uniform(40.0, 80.0)))
    elif target_axis == 0:  # 迹线沿 F2,固定 (F1,F3)
        for j in range(4, n1 - 4):
            for k in range(4, n3 - 4):
                for i in _trace_positions(n_per_trace, n2):
                    spec += _add(i, j, k, float(rng.uniform(40.0, 80.0)))
    else:  # 迹线沿 F1,固定 (F2,F3)
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
    """其它轴相位为零时,目标轴 (p0,p1) 完整恢复。"""
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
    """各轴同时有相位:p1 由迹线内拟合,不受其它轴相位常数干扰。"""
    all_phases = {"F3": (40.0, 25.0), "F1": (355.0, 10.0), "F2": (80.0, -15.0)}
    for axis, expected_p1 in ((2, 25.0), (0, -15.0), (1, 10.0)):
        spec = _make_3d(target_axis=axis, phases=all_phases)
        est = search_axis_phase_consensus(spec, axis)
        assert est is not None, f"axis {axis} 应有峰"
        assert abs(est[1] - expected_p1) <= 6.0, (axis, est)


def test_p0_sum_identifiable_when_all_phased() -> None:
    """多轴同时有相位:各轴 p0 之和(谱峰绝对相位)可辨。"""
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
    """sign_mode=mixed:一半迹线取反(其它维 ±180 偏移)仍能共识。"""
    spec = _make_3d(target_axis=2, phases={"F3": (40.0, 25.0), "F1": (0.0, 0.0), "F2": (0.0, 0.0)})
    for i in range(0, spec.shape[0], 2):
        spec[i] = -spec[i]
    est = search_axis_phase_consensus(spec, axis=2, sign_mode="mixed")
    assert est is not None
    assert _close(est[0], 40.0, 12.0), est







    assert est[2] > 50.0, est


def test_direct_phase_real_ht_2d_rows() -> None:
    """2D 谱:各行即直接维迹线(无投影),同样恢复校正。"""
    rng = np.random.default_rng(11)
    n1, n_dir = 24, 120
    spec = np.zeros((n1, n_dir), dtype=complex)
    t = np.arange(n_dir, dtype=float)
    for i in range(4, n1 - 4):
        for _ in range(3):
            f0 = int(rng.integers(12, n_dir // 2 - 12))
            amp = float(rng.uniform(20.0, 60.0))
            t2 = float(rng.uniform(40.0, 80.0))
            fid = amp * np.exp(-t / t2) * np.exp(
                1j * (2 * np.pi * f0 * t / n_dir + np.deg2rad(-70.0))
            )
            spec[i] += np.fft.fft(fid)
    real = np.real(spec) + rng.normal(0.0, 0.05, spec.shape)
    est = search_direct_phase_real_ht(real, axis=-1)
    assert est is not None
    assert _close(est[0], 70.0, 25.0), est
    assert abs(est[1]) <= 15.0, est



def _exact_direct_spectrum(
    shape: tuple[int, ...],
    psi0: float,
    *,
    seed: int,
    occ: float = 1.0,
    t2: tuple[float, float] = (6.0, 15.0),
) -> np.ndarray:
    """精确重建的纯实直接维谱:时域 Ŝ 只保留正半(t<n/2),X=IFFT(Ŝ),
    则 z_std(_hilbert 正频半)数学上精确还原 X,峰相位 = psi0(常数),
    搜索应返回 (-psi0, 0)。取实部丢弃虚部;迹线稀疏可调。
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
            amp * np.exp(-t / t2v) * np.exp(
                1j * (2 * np.pi * f0 * t / n_dir + np.deg2rad(psi0))
            ),
            0.0,
        )
        idx = np.unravel_index(flat_idx, shape[:-1])
        spec[idx] += np.fft.ifft(shat)
    return np.real(spec) + rng.normal(0.0, 0.05, spec.shape)


def test_direct_phase_real_ht_projected_traces() -> None:
    """3D 投影迹线 + HT(正频半/scipy 约定)恢复直接维 p0。"""
    real = _exact_direct_spectrum(
        (20, 16, 160), 40.0, seed=7, occ=0.15, t2=(4.0, 10.0)
    )
    est = search_direct_phase_real_ht(real, axis=-1)
    assert est is not None, "投影迹线应有干净峰"
    # 0.2.199-补29p:直接维返回 0-180° 折叠值(不强制正峰)
    assert _close(est[0], (-40.0) % 180.0, 20.0), est
    assert abs(est[1]) <= 15.0, est
    assert est[2] > 50.0, est


def test_direct_phase_real_ht_2d_rows() -> None:
    """2D 谱:各行即直接维迹线(无投影),同样恢复校正。"""
    real = _exact_direct_spectrum((24, 120), -70.0, seed=11)
    est = search_direct_phase_real_ht(real, axis=-1)
    assert est is not None
    assert _close(est[0], 70.0, 20.0), est
    assert abs(est[1]) <= 15.0, est
    assert est[2] > 50.0, est
