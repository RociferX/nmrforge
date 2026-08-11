"""直接维统计相位搜索测试。"""

from __future__ import annotations

import numpy as np

from core.optimization.phase_search import direct_ft_traces, search_phase


def _real_trace(n: int = 256, center: int = 64) -> np.ndarray:
    k = np.arange(n)
    return np.exp(-((k - center) / 8.0) ** 2).astype(complex)


def test_search_phase_recovers_rotation() -> None:
    trace = _real_trace() * np.exp(1j * np.deg2rad(30))
    p0, p1, score = search_phase(trace[np.newaxis, :])
    assert p0 == -30.0
    assert p1 == 0.0
    assert score > 0.95


def test_search_phase_zero_error() -> None:
    trace = _real_trace()
    p0, p1, score = search_phase(trace[np.newaxis, :])
    assert p0 == 0.0
    assert score > 0.95


def test_direct_ft_traces() -> None:
    fid = np.zeros((4, 8), dtype=complex)
    out = direct_ft_traces(fid, zf_size=16)
    assert out.shape == (4, 16)
    out2 = direct_ft_traces(fid)
    assert out2.shape == (4, 8)
