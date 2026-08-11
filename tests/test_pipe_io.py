"""NMRPipe 复型拆包与重构平面相位搜索测试。"""

from __future__ import annotations

import numpy as np

from core.data.pipe_io import _unpack_interleaved
from workflow.recon_phase_search import search_recon_phase


def test_unpack_interleaved() -> None:
    real = np.zeros((4, 3))
    real[0] = 1.0
    real[1] = 2.0
    real[2] = 3.0
    real[3] = 4.0
    cx = _unpack_interleaved(real)
    assert cx.shape == (2, 3)
    assert np.allclose(cx[0], 1.0 + 2.0j)
    assert np.allclose(cx[1], 3.0 + 4.0j)


def test_search_recon_phase_recovers_rotation() -> None:
    """复型平面沿轴 1 引入 30° 相位误差，搜索应恢复并给出正增益。"""
    rng = np.random.default_rng(0)
    base = np.zeros((4, 16, 8), dtype=complex)
    for p in range(4):
        base[p, 6, 2] = 500.0
        base[p, 7, 3] = 350.0
    from core.optimization.phase_search import apply_phase_axis

    rotated = apply_phase_axis(base, 1, 30.0, 0.0) + rng.normal(
        0, 0.1, size=base.shape
    )
    results = search_recon_phase(rotated)
    axis1 = results["axis1"]
    assert axis1["gain"] > 0.05
    assert abs(((axis1["p0"] + 30.0) + 180.0) % 360.0 - 180.0) <= 10.0
