"""直接维统计相位搜索。

从直接维 FT 后的一维迹线中统计最佳 (p0, p1)：
对每个候选相位逐迹应用，按迹能量加权平均吸收度
（Σ|Re| / (Σ|Re| + Σ|Im|)），正确相位时实部为吸收型 → 比例最高。

流程与 NMRPipe step1 一致：SP → ZF → FT 后取全部一维迹线
（等价于从 .ft1 中间文件中抽出所有一维 FID，但内存内完成、一次到位）。
"""

from __future__ import annotations

import numpy as np


def _apply_phase(traces: np.ndarray, p0: float, p1: float) -> np.ndarray:
    n = traces.shape[-1]
    k = np.arange(n, dtype=float)
    angle = np.deg2rad(p0 + p1 * k / max(n - 1, 1))
    return traces * np.exp(1j * angle)


def trace_absorption_score(
    traces: np.ndarray, p0: float, p1: float
) -> np.ndarray:
    """逐迹吸收度：Σ|Re| / (Σ|Re| + Σ|Im|)。"""
    rotated = _apply_phase(traces, p0, p1)
    re = np.abs(np.real(rotated))
    im = np.abs(np.imag(rotated))
    denom = re + im + 1e-12
    return np.sum(re, axis=-1) / np.sum(denom, axis=-1)


def search_phase(
    traces: np.ndarray,
    p0_values: np.ndarray | None = None,
    p1_values: np.ndarray | None = None,
    max_traces: int = 2000,
) -> tuple[float, float, float]:
    """统计最佳相位。返回 (p0, p1, score)。

    traces: (n_traces, n_points) 复数频域迹线（直接维 FT 后）。
    """
    traces = np.asarray(traces, dtype=np.complex128)
    if traces.ndim == 1:
        traces = traces[np.newaxis, :]
    n_traces = traces.shape[0]
    if n_traces > max_traces:
        index = np.linspace(0, n_traces - 1, max_traces).astype(int)
        traces = traces[index]
    if p0_values is None:
        p0_values = np.arange(-90.0, 91.0, 15.0)
    if p1_values is None:
        p1_values = np.arange(-40.0, 41.0, 20.0)
    energies = np.sum(np.abs(traces), axis=-1) + 1e-12
    best = (0.0, 0.0, -1.0)
    for p0 in p0_values:
        for p1 in p1_values:
            per_trace = trace_absorption_score(traces, p0, p1)
            score = float(np.average(per_trace, weights=energies))
            if score > best[2]:
                best = (float(p0), float(p1), score)
    return best


def direct_ft_traces(
    fid: np.ndarray,
    *,
    zf_size: int | None = None,
    sp_off: float = 0.45,
    sp_end: float = 0.95,
    sp_pow: float = 1,
) -> np.ndarray:
    """沿直接维（最后一个轴）做 SP+ZF+FT，返回 (n_traces, n) 复数迹线。"""
    arr = np.asarray(fid)
    n = arr.shape[-1]
    t = np.linspace(0.0, 1.0, n)
    window = np.sin(np.pi * (sp_off + (sp_end - sp_off) * t)) ** sp_pow
    work = arr * window
    if zf_size is not None and zf_size > n:
        pad = [(0, 0)] * work.ndim
        pad[-1] = (0, int(zf_size) - n)
        work = np.pad(work, pad)
    spectrum = np.fft.fft(work, axis=-1)
    return spectrum.reshape(-1, spectrum.shape[-1])
