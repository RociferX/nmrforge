"""直接维统计相位搜索。

从直接维 FT 后的一维迹线中统计最佳一阶相位 p1（p0 与 t1 演化相位在
原始迹线上混合，无法分离，固定为 0；最终谱相位由后处理负责）。

方法：
- 对每个候选 p1，逐迹在 p0 上取最优峰区吸收度（Σ|Re|/(Σ|Re|+Σ|Im|)），
  取中位吸收度最高者为公共 p1；
- ±180° 歧义用实部符号比消歧（正确符号 → 实部以正峰为主）；
- 输入等价于从 .1FT 中间文件抽出所有一维 FID，但内存内完成、一次到位。
"""

from __future__ import annotations

import numpy as np


def search_phase(
    traces: np.ndarray,
    p0_values: np.ndarray | None = None,
    p1_values: np.ndarray | None = None,
    max_traces: int = 2000,
) -> tuple[float, float, float, float]:
    """p1 共识搜索。返回 (p0=0, p1, score, p1_gain)。"""
    traces = np.asarray(traces, dtype=np.complex128)
    if traces.ndim == 1:
        traces = traces[np.newaxis, :]
    n_traces = traces.shape[0]
    if n_traces > max_traces:
        index = np.linspace(0, n_traces - 1, max_traces).astype(int)
        traces = traces[index]
    if p1_values is None:
        p1_values = np.arange(-180.0, 181.0, 30.0)
    if p0_values is None:
        p0_values = np.arange(-180.0, 181.0, 20.0)
    n = traces.shape[-1]
    k = np.arange(n, dtype=float)
    magnitude = np.abs(traces)
    floor = 0.3 * np.max(magnitude, axis=-1, keepdims=True)
    mask = magnitude >= floor

    def _evaluate(p1: float) -> tuple[float, float]:
        ramp = np.exp(1j * np.deg2rad(p1 * k / max(n - 1, 1)))
        base = traces * ramp
        abs_scores: list[np.ndarray] = []
        sign_scores: list[np.ndarray] = []
        for p0 in p0_values:
            rot = base * np.exp(1j * np.deg2rad(p0))
            re = np.real(rot) * mask
            im = np.imag(rot) * mask
            re_abs = np.abs(re)
            abs_scores.append(
                np.sum(re_abs, axis=-1)
                / (np.sum(re_abs + np.abs(im), axis=-1) + 1e-12)
            )
            sign_scores.append(
                np.sum(re, axis=-1) / (np.sum(re_abs, axis=-1) + 1e-12)
            )
        abs_arr = np.array(abs_scores)
        sign_arr = np.array(sign_scores)
        best_idx = np.argmax(abs_arr, axis=0)
        rows = np.arange(len(best_idx))
        best_abs = abs_arr[best_idx, rows]
        best_sign = sign_arr[best_idx, rows]
        return float(np.median(best_abs)), float(np.median(best_sign))

    baseline_abs, _baseline_sign = _evaluate(0.0)
    candidates = []
    for p1 in p1_values:
        a, s = _evaluate(float(p1))
        candidates.append((a, s, float(p1)))
    candidates.sort(key=lambda c: (-c[0], -c[1]))
    best_abs, best_sign, best_p1 = candidates[0]
    for a, s, p1 in candidates[1:]:
        if abs(a - best_abs) < 0.01 and s > best_sign:
            best_abs, best_sign, best_p1 = a, s, p1
    return 0.0, best_p1, best_abs, best_abs - baseline_abs


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
