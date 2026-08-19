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

import logging
import time
from collections.abc import Callable

import numpy as np

logger = logging.getLogger("nmrforge.optimization.phase_search")

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
    candidates: list[int] | None = None,
) -> tuple[float, float, float] | None:
    """沿单个轴搜索 (p0, p1)，返回 (p0, p1, score)。

    candidates 非 None 时追加本次搜索评估的候选数
    (无信号/尺寸不足记 0)，供上层统计「内存内候选评分」数量。
    """
    n = data.shape[axis]
    if n < 8:
        if candidates is not None:
            candidates.append(0)
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
        if candidates is not None:
            candidates.append(0)
        return None
    sig_traces = traces[signal]
    if len(sig_traces) > max_traces:
        # 只保留峰高最强的 top-K 迹线：相位信息只在强迹线上可靠，
        # 均匀抽样会把大量弱迹线带进来稀释指标
        order = np.argsort(peak_mag[signal])[::-1][:max_traces]
        sig_traces = sig_traces[order]
    positions = np.argmax(np.abs(sig_traces), axis=-1)
    peak_weights = np.max(np.abs(sig_traces), axis=-1) + 1e-12
    k = np.arange(n, dtype=float)

    def _ramp(p0: float, p1: float) -> np.ndarray:
        return np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))

    def _evaluate(p0: float, p1: float) -> tuple[float, float]:
        rotated = sig_traces * _ramp(p0, p1)
        profiles = _trace_profiles(rotated, positions)
        absorption, sign = _window_metrics(profiles)
        # 峰高加权均值：强迹线主导，避免弱迹线稀释到噪声地板
        return (
            float(np.average(absorption, weights=peak_weights)),
            float(np.average(sign, weights=peak_weights)),
        )

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
    if candidates is not None:
        candidates.append(
            len(np.arange(0.0, 360.0, coarse_step))
            + len(np.arange(-30.0, 30.0 + 1e-9, 10.0))
            + len(np.arange(-10.0, 10.0 + 1e-9, 2.5))
            + 2  # ±180 符号消歧
            + len(np.arange(-90.0, 91.0, 30.0))
            + len(np.arange(-30.0, 30.0 + 1e-9, 10.0))
            + len(np.arange(-10.0, 10.0 + 1e-9, 5.0))
        )
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


# ------------------------------------------------- 直接维 FT 谱 (p0, p1) 频域搜索


def _row_peak_positions(
    spectrum: np.ndarray,
    *,
    max_peaks: int = 8,
    margin: int = 0,
) -> tuple[np.ndarray, np.ndarray] | None:
    """锁定 1D 直接维谱 top-K 局部峰,返回 (下标, 峰高)。

    峰高须高于 5×拐角噪声(纯噪声迹线返回 None)。margin>0 时排除首尾
    margin 个点(真实数据直接维 FT 首尾常为 DC/Nyquist 伪影,可比真实
    峰强数倍)。
    """
    arr = np.asarray(spectrum, dtype=np.complex128)
    n = arr.shape[-1]
    if n < 8:
        return None
    mag = np.abs(arr)
    corner = slice(0, min(16, n))
    noise = float(np.std(mag[corner])) if n else 0.0
    if float(np.max(mag)) <= max(noise * 5.0, 1e-9):
        return None
    threshold = max(float(np.percentile(mag, 99.0)), noise * 5.0)
    interior = np.zeros(n, dtype=bool)
    interior[1:-1] = (mag[1:-1] >= mag[:-2]) & (mag[1:-1] >= mag[2:])
    if margin > 0:
        interior[:margin] = False
        interior[-margin:] = False
    candidates = np.where(interior & (mag > threshold))[0]
    if not candidates.size:
        return None
    order = np.argsort(mag[candidates])[::-1][:max_peaks]
    positions = candidates[order]
    return positions, mag[positions]


def _row_absorption(
    arr: np.ndarray,
    positions: np.ndarray,
    heights: np.ndarray,
    p0: float,
    p1: float,
    *,
    radius: int = 1,
) -> tuple[float, float]:
    """固定峰窗 ±radius 的峰高加权 (吸收度, 正负符号),与 NMRPipe PS 频域旋转一致。

    半径默认 1:SP 窗函数边缘使峰尾带非线性相位,±5 窗口吸收度在正确相位
    下反而下降(实测 0.46 vs 0.51);±1 窗口能正确区分(0.65 vs 0.47)。
    """
    n = arr.shape[-1]
    k = np.arange(n, dtype=float)
    ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
    rot = arr * ramp
    rows2d = np.repeat(rot.reshape(1, -1), positions.size, axis=0)
    profiles = _trace_profiles(rows2d, positions, radius=radius)
    absorption, sign = _window_metrics(profiles)
    weights = heights + 1e-12
    return (
        float(np.average(absorption, weights=weights)),
        float(np.average(sign, weights=weights)),
    )


def _row_p1_fit(
    arr: np.ndarray,
    positions: np.ndarray,
    heights: np.ndarray,
    *,
    coarse_step: float = 10.0,
) -> tuple[float, float] | None:
    """多峰迹线的 p1 拟合:旋转后各峰相位圆集中度最大。

    每条迹线峰值相位 = 公共 p0 + 该增量 t1(常数)+ p1·k/(n-1);对候选 p1
    旋转后,若 p1 正确,各峰相位应全部相等(p0+t1)——单位向量圆均值模
    |vec| 最大(相位集中度)。p1 与 p0/t1 天然解耦,吸收度指标在 p1 方向
    太平(小角度饱和)的问题在此不存在。返回 (p1, concentration)。
    """
    if positions.size < 2:
        return None
    n = arr.shape[-1]
    vals = arr[positions]
    weights = heights + 1e-12
    denom = float(np.sum(weights))

    def _concentration(p1: float) -> float:
        ramp = np.exp(-1j * np.deg2rad(p1 * positions / max(n - 1, 1)))
        unit = np.exp(1j * np.angle(vals * ramp))
        vec = np.sum(weights * unit) / denom
        return float(np.abs(vec))

    best_p1, best_conc = 0.0, -1.0
    for p1 in np.arange(-90.0, 91.0, coarse_step):
        conc = _concentration(float(p1))
        if conc > best_conc:
            best_conc, best_p1 = conc, float(p1)
    for span, step in ((30.0, 5.0), (10.0, 2.5)):
        for offset in np.arange(-span, span + 1e-9, step):
            conc = _concentration(best_p1 + offset)
            if conc > best_conc:
                best_conc, best_p1 = conc, best_p1 + offset
    return best_p1, best_conc


def _row_p0_at_p1(
    arr: np.ndarray,
    positions: np.ndarray,
    heights: np.ndarray,
    p1_signal: float,
) -> tuple[float, float]:
    """p1(信号斜坡)固定后单条迹线的 p0 校正。

    旋转移除信号斜坡后,各峰相位 = 常数(公共 p0 + t1);取其加权圆均值
    取反得 PS 校正 p0,±180 消歧取正峰解。返回 (p0, score);score 为
    (p0, -p1_signal) 校正下的峰窗加权吸收度。
    """
    n = arr.shape[-1]
    vals = arr[positions]
    ramp = np.exp(-1j * np.deg2rad(p1_signal * positions / max(n - 1, 1)))
    unit = np.exp(1j * np.angle(vals * ramp))
    weights = heights + 1e-12
    vec = np.sum(weights * unit) / max(float(np.sum(weights)), 1e-12)
    p0 = float((-np.rad2deg(np.angle(vec))) % 360.0)
    p1_corr = -p1_signal
    _a0, sign0 = _row_absorption(arr, positions, heights, p0, p1_corr)
    _a1, sign1 = _row_absorption(
        arr, positions, heights, (p0 + 180.0) % 360.0, p1_corr
    )
    if sign1 > sign0:
        p0 = (p0 + 180.0) % 360.0
    score, _sign = _row_absorption(arr, positions, heights, p0, p1_corr)
    return p0, score


def search_direct_spectrum_phase(
    traces: np.ndarray,
    *,
    max_rows: int = 128,
    p0_source: str = "first",
) -> tuple[float, float, float, float] | None:
    """直接维 FT 谱的 (p0, p1) 聚合搜索(全程 numpy,不重跑后端)。

    输入:(..., n) 复型谱,最后一维为直接维;每一行视为一个间接增量
    (切片式 fid 每文件一行;单文件 fid 每增量一行)。聚合策略:
    1) p1 共识:多峰迹线相位集中度拟合取中位数(t1 只是逐峰常数偏置,
       不影响 p1 斜坡;单峰迹线无法区分 p1,不参与);
    2) p0 锚点:p0_source="first" 取首条有峰的迹线(增量 0,t1=0,切片式
       NUS 直接维相位干净);"strongest" 取峰高最强的迹线(伪均匀谱:
       最强行对应真实间接频率,零填充旁瓣的常数相位偏置 δ≈0)。
    返回 (p0, p1, score, gain);无信号/点数不足返回 None。
    """
    arr = np.asarray(traces, dtype=np.complex128)
    if arr.ndim == 0 or arr.shape[-1] < 8:
        return None
    rows = arr.reshape(-1, arr.shape[-1])
    if rows.shape[0] > max_rows:
        index = np.linspace(0, rows.shape[0] - 1, max_rows).astype(int)
        rows = rows[index]
    infos: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for index in range(rows.shape[0]):
        peaks = _row_peak_positions(rows[index])
        if peaks is not None:
            infos.append((rows[index], peaks[0], peaks[1]))
    if not infos:
        return None
    p1_rows: list[float] = []
    for _arr, pos, heights in infos:
        fit = _row_p1_fit(_arr, pos, heights)
        if fit is not None:
            p1_rows.append(fit[0])
    # p1 拟合得到的是信号斜坡;PS 校正取反(与 p0 同为校正约定)
    p1_signal = float(np.median(p1_rows)) if p1_rows else 0.0
    p1 = -p1_signal
    # p0 锚点:first=首条有峰迹线(增量 0,t1=0);strongest=峰高最强迹线
    if p0_source == "strongest":
        anchor = max(
            range(len(infos)), key=lambda i: float(np.max(infos[i][2]))
        )
    else:
        anchor = 0
    _arr, pos, heights = infos[anchor]
    p0, score = _row_p0_at_p1(_arr, pos, heights, p1_signal)
    baseline, _sign = _row_absorption(_arr, pos, heights, 0.0, 0.0)
    gain = score - baseline
    return p0, p1, score, gain


def nus_direct_phase(
    fids: np.ndarray,
    points: list[tuple[int, ...]],
    n_f1: int,
    n_f2: int = 1,
    *,
    oversample: int = 8,
    refine_step: float = 0.02,
) -> tuple[float, float, float, float, int] | None:
    """NUS 直接维 (p0, p1) 校正:最强直接峰在各增量真实 F1/F2 频率处的相位。

    原理:切片 i 直接维峰 k* 的相位 = φ(k*) + ω1·Δt1·p1_i + ω2·Δt2·p2_i;
    沿增量对峰复值做非均匀 DFT,在真实 F1/F2 频率处 t1 调制精确抵消
    (δ=0),峰相位 = φ(k*) = φ0 + p1·k*/(n-1)。p1 由各切片多峰相位集中度
    拟合取中位数(0.2.88 机制)。全程 numpy,不跑 SMILE/后端。

    返回 (p0_corr, p1_corr, score, gain, kstar);无信号/点数不足返回 None。
    score/gain 用最强切片在 (p0_corr, p1_corr) 下的峰窗吸收度(半径 1)与
    零相位基线之差(门控用)。p0/p1 均为 PS 校正值(信号相位相反数)。
    """
    arr = np.asarray(fids, dtype=np.complex128)
    if arr.ndim != 2 or arr.shape[0] != len(points) or arr.shape[-1] < 8:
        return None
    if n_f1 <= 0:
        return None
    spectra = direct_ft_traces(arr, sp_off=0.45, sp_end=0.95, sp_pow=1)
    # 最强直接峰:各切片中位峰高最大的内部局部极大(排除首尾 DC/Nyquist
    # 伪影——真实数据可比真实峰强数倍,0.2.92 VM sampleI 实测 k=0 伪影
    # 4.9e8 vs 真实峰 8e7)
    margin = max(2, arr.shape[-1] // 64)
    med = np.median(np.abs(spectra), axis=0)
    interior = np.zeros(med.size, dtype=bool)
    interior[margin:-margin] = True
    local_max = np.zeros(med.size, dtype=bool)
    local_max[1:-1] = (med[1:-1] >= med[:-2]) & (med[1:-1] >= med[2:])
    cand = np.where(interior & local_max)[0]
    if not cand.size:
        cand = np.where(interior)[0]
    kstar = int(cand[int(np.argmax(med[cand]))])
    v = spectra[:, kstar]
    # 增量索引(模运算容错:1-based/复点单位差异)
    if n_f2 > 1:
        p1 = np.array(
            [int(pt[1]) % n_f1 if len(pt) >= 2 else 0 for pt in points],
            dtype=float,
        )
        p2 = np.array(
            [int(pt[0]) % n_f2 if len(pt) >= 1 else 0 for pt in points],
            dtype=float,
        )
    else:
        p1 = np.array([int(pt[0]) % n_f1 for pt in points], dtype=float)
        p2 = np.zeros(len(points), dtype=float)
    # 非均匀 DFT 粗网格(内存上限:粗格点 × 切片数)
    max_cells = 1_000_000
    os = oversample
    while (n_f1 * os) * (max(n_f2, 1) * os) > max_cells and os > 2:
        os //= 2
    f1_coarse = np.arange(n_f1 * os) / os
    if n_f2 > 1:
        f2_coarse = np.arange(n_f2 * os) / os
        F1, F2 = np.meshgrid(f1_coarse, f2_coarse, indexing="ij")
        arg = (
            2.0 * np.pi
            * (
                F1[..., None] * p1[None, None, :] / n_f1
                + F2[..., None] * p2[None, None, :] / n_f2
            )
        )
        V = np.sum(v[None, None, :] * np.exp(-1j * arg), axis=-1)
        idx = np.unravel_index(int(np.argmax(np.abs(V))), V.shape)
        f1pk, f2pk = float(f1_coarse[idx[0]]), float(f2_coarse[idx[1]])
    else:
        arg = 2.0 * np.pi * f1_coarse[:, None] * p1[None, :] / n_f1
        V = np.sum(v[None, :] * np.exp(-1j * arg), axis=-1)
        idx = int(np.argmax(np.abs(V)))
        f1pk, f2pk = float(f1_coarse[idx]), 0.0
    # 局部细化(峰附近细网格)
    span = 1.0
    if n_f2 > 1:
        f1s = np.arange(max(0.0, f1pk - span), min(n_f1, f1pk + span) + 1e-9, refine_step)
        f2s = np.arange(max(0.0, f2pk - span), min(n_f2, f2pk + span) + 1e-9, refine_step)
        F1f, F2f = np.meshgrid(f1s, f2s, indexing="ij")
        argf = (
            2.0 * np.pi
            * (
                F1f[..., None] * p1[None, None, :] / n_f1
                + F2f[..., None] * p2[None, None, :] / n_f2
            )
        )
        Vf = np.sum(v[None, None, :] * np.exp(-1j * argf), axis=-1)
        idf = np.unravel_index(int(np.argmax(np.abs(Vf))), Vf.shape)
        f1pk, f2pk = float(f1s[idf[0]]), float(f2s[idf[1]])
        phase = float(np.rad2deg(np.angle(Vf[idf])))
    else:
        f1s = np.arange(max(0.0, f1pk - span), min(n_f1, f1pk + span) + 1e-9, refine_step)
        argf = 2.0 * np.pi * f1s[:, None] * p1[None, :] / n_f1
        Vf = np.sum(v[None, :] * np.exp(-1j * argf), axis=-1)
        idf = int(np.argmax(np.abs(Vf)))
        f1pk = float(f1s[idf])
        phase = float(np.rad2deg(np.angle(Vf[idf])))
    # p1:各切片多峰相位集中度拟合取中位数(信号斜坡)
    p1_rows: list[float] = []
    for index in range(spectra.shape[0]):
        peaks = _row_peak_positions(spectra[index], margin=margin)
        if peaks is not None and peaks[0].size >= 2:
            fit = _row_p1_fit(spectra[index], peaks[0], peaks[1])
            if fit is not None:
                p1_rows.append(fit[0])
    p1_sig = float(np.median(p1_rows)) if p1_rows else 0.0
    # p0_corr = -(φ(k*) - p1_sig·k*/(n-1));p1_corr = -p1_sig
    p0_corr = (-(phase - p1_sig * kstar / max(spectra.shape[-1] - 1, 1))) % 360.0
    p1_corr = -p1_sig
    # ±180 消歧:NU-DFT 峰复值相位 = φ(k*)(t1 调制已在真实 F1 频率处精确
    # 抵消),p0_corr = -φ(k*) 是唯一解,与现有方法「取正峰解」语义一致。
    # 不能用单切片吸收符号判正负——该切片 t1 相位可能为 180°(峰反转),
    # 0.2.91 曾因此把 p0 误翻 180°。这里仅留数值守卫(正常恒为正实)。
    if float(np.real(Vf[idf] * np.exp(1j * np.deg2rad(p0_corr)))) < 0.0:
        p0_corr = (p0_corr + 180.0) % 360.0
    # 门控用相干 SNR:|V_peak|/(√N·mean|v|)——信号≈√N,噪声≈1
    v_mean = float(np.mean(np.abs(v))) + 1e-12
    score = float(np.abs(Vf[idf])) / (np.sqrt(len(points)) * v_mean)
    return p0_corr, p1_corr, score, score - 1.0, kstar


def _net_window_metric(profile: np.ndarray) -> float:
    """净吸收(正面积+负面积)/总绝对面积——与现有 uniform 优化评分一致。"""
    positive = float(np.clip(profile, 0.0, None).sum())
    negative = float(np.clip(profile, None, 0.0).sum())
    total = float(np.abs(profile).sum())
    return (positive + negative) / total if total else 0.0


def _symmetry_sign_metric(profile: np.ndarray) -> float:
    """峰窗口对称性 + 正峰约束(0..1)——模仿 nmrDraw 显示层人工调相。

    吸收峰实部偶对称(左=右)→ 1;色散峰奇对称 → 0;±180 反转峰同样对称,
    用净 Re 为负重罚(与现有方法正峰语义一致)。
    """
    f = np.asarray(profile, dtype=float)
    n = f.size
    if n < 2:
        return 0.0
    half = n // 2
    left = f[:half]
    right = f[n - half:][::-1]
    denom = 2.0 * (left**2 + right**2) + 1e-12
    sym = float(np.mean((left + right) ** 2 / denom))
    if n % 2 == 1:
        c = f[half]
        sym = float(
            np.mean(
                np.concatenate(
                    [np.asarray((left + right) ** 2 / denom), [c**2 / (c**2 + 1e-12)]]
                )
            )
        )
    return sym if float(np.sum(f)) >= 0.0 else sym * 0.05


def _signal_peak_windows(
    real: np.ndarray,
    *,
    axis: int = -1,
    snr: float = 10.0,
    global_frac: float = 0.05,
    margin: int = 8,
    max_peaks: int = 8,
) -> list[tuple[int, int]]:
    """信号行峰选择(用户方案:蛋白谱每行只有几个高耸峰,先排除噪音/伪影区域)。

    对每条迹线(沿 axis,非直接维组合)找局部极大峰,要求峰高 ≥
    max(snr×行噪音(MAD), global_frac×全局最大峰高),且每行峰数 ≤
    max_peaks(伪影行峰密被排除)。返回 [(row, peak_pos), ...];无干净行
    返回 []。
    """
    n = real.shape[axis]
    moved = np.moveaxis(real, axis, -1)
    traces = np.abs(moved).reshape(-1, n)
    gmax = float(np.max(traces))
    windows: list[tuple[int, int]] = []
    for row in range(traces.shape[0]):
        mag = traces[row]
        mad = float(np.median(np.abs(mag - np.median(mag)))) * 1.4826 + 1e-12
        lo, hi = margin, n - margin
        if hi <= lo + 2:
            continue
        local = np.zeros(n, dtype=bool)
        local[lo:hi] = (mag[lo:hi] >= mag[lo - 1 : hi - 1]) & (
            mag[lo:hi] > mag[lo + 1 : hi + 1]
        )
        peaks = np.where(local & (mag > max(snr * mad, global_frac * gmax)))[0]
        if 0 < len(peaks) <= max_peaks:
            for p in peaks:
                windows.append((row, int(p)))
    return windows


def search_direct_phase_on_spectrum(
    spectrum: np.ndarray,
    *,
    axis: int = -1,
    coarse_p0_step: float = 30.0,
    metric: str = "symmetry",
    radius: int = 12,
    min_windows: int = 5,
    prefer_p1_zero: bool = True,
    progress: Callable[[str], None] | None = None,
) -> tuple[float, float, float] | None:
    """谱上直接维相位评分搜索 (p0, p1)。

    axis 指定直接维所在轴(默认最后一维:终谱 F1×F2 的直接 F2;复型重构
    平面 recon.ft1 的布局 (F2, F1) 直接维在 axis 0——0.2.96)。

    0.2.95(nmrDraw 显示层调相思路,默认 metric="symmetry"):先用信号行峰
    选择排除噪音/伪影区域(每行少数高耸峰),再对锁定峰窗做频域旋转对称性
    评分(±180 正峰约束),近最优平台取最小修正。metric="net" 保留旧净吸收
    指标(±90° 平台)。返回 (p0, p1, score);无干净信号峰返回 None。
    """
    arr = np.asarray(spectrum)
    axis = axis if axis >= 0 else arr.ndim - 1
    if arr.ndim < 2 or arr.shape[axis] < 8:
        return None
    n = arr.shape[axis]
    real = np.real(arr) if np.iscomplexobj(arr) else arr
    if metric == "symmetry":
        windows = _signal_peak_windows(real, axis=axis)
        if len(windows) < min_windows:
            return None
        window_metric = _symmetry_sign_metric
    else:
        traces = np.moveaxis(real, axis, -1).reshape(-1, n)
        peak_mag = np.max(np.abs(traces), axis=-1)
        corner = tuple(slice(0, min(16, s)) for s in real.shape)
        noise = float(np.std(real[corner])) if real.size else 0.0
        threshold = max(float(np.percentile(real, 99.5)), noise * 5.0)
        idx = np.where(peak_mag > threshold)[0]
        if idx.size == 0:
            return None
        pos = np.argmax(np.abs(traces[idx]), axis=-1)
        windows = list(zip(idx.tolist(), pos.tolist()))
        window_metric = _net_window_metric
    comp = np.asarray(arr, dtype=np.complex128)
    # 窗口迹线预提取(2026-08-19):旋转是逐点复乘,先切片后旋转与先旋转后
    # 切片数学等价;只对窗口切片旋转,避免每次候选对整个数组旋转(真实数据
    # 首跑从数分钟降到数秒量级,结果逐位一致)
    flat = np.moveaxis(comp, axis, -1).reshape(-1, n)
    width = 2 * radius + 1
    seg_slices = [
        (max(0, peak - radius), min(n, peak + radius + 1))
        for _, peak in windows
    ]
    # 等宽零填充:窗口指标按元素求和/比值,零不贡献,与不等宽切片等价
    segments = np.zeros((len(windows), width), dtype=np.complex128)
    for j, ((i, _), (lo, hi)) in enumerate(zip(windows, seg_slices)):
        segments[j, : hi - lo] = flat[i, lo:hi]

    def _score(p0: float, p1: float) -> float:
        k = np.arange(n, dtype=float)
        base = np.deg2rad(p0 + p1 * k / max(n - 1, 1))
        seg_ramp = np.zeros((len(segments), width), dtype=np.complex128)
        for j, (lo, hi) in enumerate(seg_slices):
            seg_ramp[j, : hi - lo] = np.exp(1j * base[lo:hi])
        rot = np.real(segments * seg_ramp)
        vals = [window_metric(rot[j]) for j in range(len(segments))]
        if metric == "symmetry":
            return 100.0 * float(np.mean(vals))
        return 50.0 * (float(np.median(vals)) + 1.0)

    t0 = time.time()
    if progress is not None:
        progress("直接维相位搜索中,请稍候")
    # 粗网格 p0×p1(串行;并行化在真实运行中卡死,2026-08-19 回退)
    best = None
    for p0 in np.arange(0.0, 360.0, coarse_p0_step):
        for p1 in (-90, -60, -30, 0, 30, 60, 90):
            s = _score(float(p0), float(p1))
            if best is None or s > best[0]:
                best = (s, float(p0), float(p1))
    s0, p0, p1 = best
    for _ in range(2):
        for dp0 in (-15, -5, 0, 5, 15):
            for dp1 in (-15, -5, 0, 5, 15):
                ss = _score((p0 + dp0) % 360.0, p1 + dp1)
                if ss > s0:
                    best = (ss, (p0 + dp0) % 360.0, p1 + dp1)
                    s0, p0, p1 = best
    if prefer_p1_zero and abs(_score(p0, 0.0) - s0) < 1.0:
        p1 = 0.0
        s0 = _score(p0, 0.0)
    if metric == "symmetry":
        # 全圆近最优平台取「最小修正」(人工习惯:谱已接近好相位不乱加修正;
        # 真值远离 0 时平台中心在真值处,近最优集合不含 (0,0))
        near = []
        for dp0 in np.arange(-180.0, 181.0, 5.0):
            for dp1 in np.arange(-60.0, 61.0, 5.0):
                pc = (p0 + dp0) % 360.0
                qc = p1 + dp1
                sc = _score(pc, qc)
                if sc >= s0 - 5.0:
                    near.append((sc, pc, qc))
        if near:
            near.sort(
                key=lambda t: (
                    min(t[1], 360.0 - t[1]),
                    abs(t[2]),
                    -t[0],
                )
            )
            p0, p1 = near[0][1], near[0][2]
            s0 = _score(p0, p1)
    logger.info(
        "直接维相位搜索: shape=%s 串行完成, 耗时 %.1fs",
        tuple(comp.shape),
        time.time() - t0,
    )
    if progress is not None:
        progress(f"直接维相位搜索完成,耗时 {time.time() - t0:.1f} 秒")
    return p0, p1, s0
