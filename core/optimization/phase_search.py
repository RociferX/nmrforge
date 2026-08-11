"""直接维/最终谱相位搜索。

两类搜索：
1. ``search_phase``：原始 FID 直接维 FT 迹线上的 p1 共识搜索
   （t1 相位纠缠，p0 不可靠；写进脚本 PS 用）。
2. ``search_spectrum_phase``：最终谱上按维（F1→F2→F3）内存内相位搜索
   （旧项目方法域：终谱无 t1 纠缠，p0/p1 都可估；全部 numpy，不重跑重构，
   满足 NUS/非 NUS「只重构一次」）。

指标：峰窗口（±5 点）吸收度比例 Σ|Re|/(Σ|Re|+Σ|Im|) 用于细调，
正负符号 (ΣRe+Σneg)/Σ|Re| 用于 ±180 消歧。
"""

from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------- p1 共识


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
        # 峰高加权均值：强迹线主导，避免大量弱迹线把中位数稀释到噪声地板
        return (
            float(np.average(best_abs, weights=peak_weights)),
            float(np.average(best_sign, weights=peak_weights)),
        )

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


# ------------------------------------------------------ 最终谱按维相位搜索


def apply_phase_axis(
    data: np.ndarray, axis: int, p0: float, p1: float
) -> np.ndarray:
    """沿指定轴应用相位（与 NMRPipe PS 等价，频域逐点复乘）。"""
    n = data.shape[axis]
    k = np.arange(n, dtype=float)
    angle = np.deg2rad(p0 + p1 * k / max(n - 1, 1))
    shape = [1] * data.ndim
    shape[axis] = n
    return np.asarray(data) * np.exp(1j * angle).reshape(shape)


def _trace_profiles(
    traces: np.ndarray, positions: np.ndarray, radius: int = 5
) -> np.ndarray:
    """在固定峰位切 ±radius 窗口，返回 (m, 2*radius+1) 剖面。"""
    n = traces.shape[-1]
    offset = np.arange(-radius, radius + 1)
    index = np.clip(positions[:, None] + offset[None, :], 0, n - 1)
    rows = np.arange(len(positions))[:, None]
    return traces[rows, index]


def _window_metrics(profiles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """逐剖面 (吸收度比例, 正负符号)。"""
    real = np.real(profiles)
    re_abs = np.abs(real)
    im_abs = np.abs(np.imag(profiles))
    denom = re_abs + im_abs + 1e-12
    absorption = np.sum(re_abs, axis=-1) / np.sum(denom, axis=-1)
    positive = np.clip(real, 0.0, None).sum(axis=-1)
    negative = np.clip(real, None, 0.0).sum(axis=-1)
    sign = (positive + negative) / (np.sum(re_abs, axis=-1) + 1e-12)
    return absorption, sign


def _search_axis(
    data: np.ndarray,
    axis: int,
    *,
    max_traces: int = 2000,
    coarse_step: float = 30.0,
) -> tuple[float, float, float] | None:
    """沿单个轴搜索 (p0, p1)，返回 (p0, p1, score)。"""
    n = data.shape[axis]
    if n < 8:
        return None
    moved = np.moveaxis(data, axis, -1)
    traces = moved.reshape(-1, n)
    real = np.real(data)
    corner = tuple(slice(0, min(16, s)) for s in data.shape)
    noise = float(np.std(real[corner])) if real.size else 0.0
    threshold = max(float(np.percentile(real, 99.5)), noise * 5.0)
    peak_mag = np.max(np.abs(traces), axis=-1)
    signal = peak_mag > threshold
    if not np.any(signal):
        return None
    sig_traces = traces[signal]
    if len(sig_traces) > max_traces:
        index = np.linspace(0, len(sig_traces) - 1, max_traces).astype(int)
        sig_traces = sig_traces[index]
    positions = np.argmax(np.abs(sig_traces), axis=-1)
    peak_weights = np.max(np.abs(sig_traces), axis=-1) + 1e-12
    k = np.arange(n, dtype=float)

    def _ramp(p0: float, p1: float) -> np.ndarray:
        return np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))

    def _evaluate(p0: float, p1: float) -> tuple[float, float]:
        rotated = sig_traces * _ramp(p0, p1)
        profiles = _trace_profiles(rotated, positions)
        absorption, sign = _window_metrics(profiles)
        return float(np.median(absorption)), float(np.median(sign))

    best_p0 = 0.0
    best_score = -1.0
    for p0 in np.arange(0.0, 360.0, coarse_step):
        a, _ = _evaluate(float(p0), 0.0)
        if a > best_score:
            best_score, best_p0 = a, float(p0)
    for span, step in ((30.0, 10.0), (10.0, 2.5)):
        for offset in np.arange(-span, span + 1e-9, step):
            p0 = (best_p0 + offset) % 360.0
            a, _ = _evaluate(p0, 0.0)
            if a > best_score:
                best_score, best_p0 = a, p0
    # ±180 消歧：负峰被符号指标排除
    _, sign_here = _evaluate(best_p0, 0.0)
    _, sign_opposite = _evaluate((best_p0 + 180.0) % 360.0, 0.0)
    if sign_opposite > sign_here:
        best_p0 = (best_p0 + 180.0) % 360.0
    # p1 精修
    best_p1 = 0.0
    best_score = _evaluate(best_p0, 0.0)[0]
    for p1 in np.arange(-90.0, 91.0, 30.0):
        a, _ = _evaluate(best_p0, float(p1))
        if a > best_score:
            best_score, best_p1 = a, float(p1)
    for span, step in ((30.0, 10.0), (10.0, 5.0)):
        for offset in np.arange(-span, span + 1e-9, step):
            p1 = best_p1 + offset
            a, _ = _evaluate(best_p0, p1)
            if a > best_score:
                best_score, best_p1 = a, p1
    return float(best_p0), float(best_p1), float(best_score)


def search_spectrum_phase(
    data: np.ndarray,
    *,
    max_traces: int = 2000,
) -> tuple[np.ndarray, dict[str, dict[str, float]]]:
    """最终谱上按维（F1→F2→F3）内存内相位搜索。

    返回 (校正后谱, {"F1": {"p0","p1","score"}, ...})。
    全程 numpy，不重跑重构——满足 NUS/非 NUS 只重构一次。
    """
    data = np.asarray(data, dtype=np.complex128)
    work = data.copy()
    phases: dict[str, dict[str, float]] = {}
    for axis in range(data.ndim):
        found = _search_axis(work, axis, max_traces=max_traces)
        if found is None:
            continue
        p0, p1, score = found
        work = apply_phase_axis(work, axis, p0, p1)
        phases[f"F{axis + 1}"] = {"p0": p0, "p1": p1, "score": score}
    return work, phases
