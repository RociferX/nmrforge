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
) -> tuple[np.ndarray, np.ndarray] | None:
    """锁定 1D 直接维谱 top-K 局部峰,返回 (下标, 峰高)。

    峰高须高于 5×拐角噪声(纯噪声迹线返回 None)。
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
