"""显示层逐维相位/基线/填零引擎测试(实型谱 + 希尔伯特重建)。"""

from __future__ import annotations

import numpy as np

from core.optimization.display_phase_engine import (
    analytic_axis,
    assess_baseline,
    assess_fill,
    search_axis_phase,
)


def _make_2d_absorptive(size: tuple[int, int]) -> np.ndarray:
    """构造一个干净的二维吸收峰(纯实,峰窗可被信号选择识别)。"""
    n0, n1 = size
    k0 = np.arange(n0, dtype=float)
    k1 = np.arange(n1, dtype=float)
    data = np.zeros(size, dtype=float)
    for c0, c1, amp in ((n0 * 0.35, n1 * 0.45, 400.0), (n0 * 0.62, n1 * 0.58, 320.0)):
        g0 = 1.0 / (1.0 + ((k0 - c0) / 5.0) ** 2)
        g1 = 1.0 / (1.0 + ((k1 - c1) / 5.0) ** 2)
        data += amp * g0[:, None] * g1[None, :]
    return data


def _rotate_analytic_axis(real: np.ndarray, axis: int, p0: float, p1: float) -> np.ndarray:
    """构造复型显示谱:希尔伯特重建虚部后按 (p0,p1) 旋转,保留复型。"""
    analytic = analytic_axis(real, axis)
    n = real.shape[axis]
    k = np.arange(n, dtype=float)
    ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
    shape = [1] * real.ndim
    shape[axis] = n
    return analytic * ramp.reshape(shape)


def test_search_axis_phase_recovers_axis0() -> None:
    """axis 0 的显示层相位应恢复到已知校正相位的容差内。"""
    base = _make_2d_absorptive((128, 96))
    mixed = _rotate_analytic_axis(base, 0, 90.0, 0.0)
    est = search_axis_phase(mixed, axis=0)
    assert est is not None
    assert 0.0 <= est.score <= 100.0, est
    assert abs(est.p1) <= 20.0, est


def test_search_axis_phase_recovers_axis1() -> None:
    """axis 1 的显示层相位应恢复到已知校正相位的容差内。"""
    base = _make_2d_absorptive((128, 96))
    mixed = _rotate_analytic_axis(base, 1, 90.0, 0.0)
    est = search_axis_phase(mixed, axis=1)
    assert est is not None
    assert 0.0 <= est.score <= 100.0, est
    assert abs(est.p1) <= 20.0, est


def test_search_axis_phase_near_zero_stays_zero() -> None:
    """近零相位谱的最小修正应回到 (0, 0)。"""
    base = _make_2d_absorptive((96, 80))
    est = search_axis_phase(analytic_axis(base, axis=0), axis=0)
    assert est is not None
    assert abs(est.p0) <= 5.0, est
    assert abs(est.p1) <= 5.0, est


def test_assess_baseline_detects_tilt_and_curvature() -> None:
    """线性/二次基线分别给出 order 1/2,平坦基线关闭。"""
    n0, n1 = 64, 48
    k0 = np.arange(n0, dtype=float)
    flat = np.zeros((n0, n1))
    tilt = 0.03 * k0[:, None]
    curve = 0.0006 * (k0[:, None] - n0 / 2) ** 2
    assert assess_baseline(flat, axis=0).mode == "auto"
    assert assess_baseline(tilt, axis=0).order == 1
    assert assess_baseline(curve, axis=0).order == 2


def test_assess_fill_flags_narrow_peak() -> None:
    """窄峰建议填零,宽峰不建议。"""
    n = 128
    k = np.arange(n, dtype=float)
    narrow = 1.0 / (1.0 + ((k - 64) / 1.2) ** 2)
    wide = 1.0 / (1.0 + ((k - 64) / 8.0) ** 2)
    data_narrow = narrow[:, None] + np.zeros((1, 16))
    data_wide = wide[:, None] + np.zeros((1, 16))
    assert assess_fill(data_narrow, axis=0).suggested is True
    assert assess_fill(data_wide, axis=0).suggested is False
