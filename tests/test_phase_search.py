"""直接维统计相位搜索测试。"""

from __future__ import annotations

import numpy as np

from core.optimization.phase_search import direct_ft_traces, search_phase


def _real_trace(n: int = 256, center: int = 64) -> np.ndarray:
    k = np.arange(n)
    return np.exp(-((k - center) / 8.0) ** 2).astype(complex)


def test_search_phase_zero_error() -> None:
    trace = _real_trace()
    p0, p1, score, gain = search_phase(trace[np.newaxis, :])
    assert p0 == 0.0
    assert p1 == 0.0
    assert score > 0.95


def test_search_phase_p1_consensus() -> None:
    """公共 p1 + 每迹随机 t1 相位：应恢复 p1 幅值（±180 歧义内）。"""
    n = 256
    k = np.arange(n)
    # 双峰：p1 对两个不同位置的峰产生不同相位误差（单峰时被 p0 吸收，测不出 p1）
    g1 = np.exp(-((k - 64) / 8.0) ** 2)
    g2 = np.exp(-((k - 192) / 8.0) ** 2)
    peak = (g1 + 0.7 * g2).astype(complex)
    true_p1 = 90.0
    ramp = np.exp(1j * np.deg2rad(true_p1 * k / max(n - 1, 1)))
    rng = np.random.default_rng(0)
    traces = []
    for _ in range(30):
        t1_phase = np.exp(1j * rng.uniform(0, 2 * np.pi))
        for _ in range(10):
            traces.append(peak * ramp * t1_phase)
    p0, p1, score, gain = search_phase(np.array(traces))
    assert p0 == 0.0
    assert abs(abs(p1) - 90.0) <= 30.0
    assert gain > 0.05
    assert score > 0.5


def test_direct_ft_traces() -> None:
    fid = np.zeros((4, 8), dtype=complex)
    out = direct_ft_traces(fid, zf_size=16)
    assert out.shape == (4, 16)
    out2 = direct_ft_traces(fid)
    assert out2.shape == (4, 8)
