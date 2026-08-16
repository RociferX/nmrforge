"""显示层逐维相位/基线/填零引擎。

本模块按「nmrDraw 人工查看实型谱」的思路实现:

1. 对实型谱的每个维度单独做希尔伯特变换,重建该维的虚部;
2. 在该维的一维显示层上估计相位:选峰,跨峰相位集中度拟合 p1,锚定峰
   圆均值拟合 p0,再用吸收形(峰偶对称 + 正峰约束)评分验证;
3. 对每个维度评估基线(两端/中部均值,避免峰干扰)和峰圆润度,给出基线
   阶数与是否填零的建议。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import hilbert


@dataclass(frozen=True)
class AxisPhaseEstimate:
    """一个维度的显示层相位搜索结果。"""

    axis: int
    p0: float
    p1: float
    score: float
    windows: int


@dataclass(frozen=True)
class BaselineEstimate:
    """一个维度的基线评估结果。"""

    axis: int
    mode: str  # "auto" | "order"
    order: int
    score: float


@dataclass(frozen=True)
class FillEstimate:
    """一个维度的峰圆润度/填零评估结果。"""

    axis: int
    fwhm_points: float
    suggested: bool
    ratio: float


def analytic_axis(real: np.ndarray, axis: int) -> np.ndarray:
    """沿指定轴做希尔伯特变换,返回解析信号(复型,nmrDraw 一维谱的虚部来源)。"""
    arr = np.asarray(real, dtype=float)
    if arr.shape[axis] < 4:
        return arr.astype(np.complex128)
    moved = np.moveaxis(arr, axis, -1)
    analytic = hilbert(moved, axis=-1)
    return np.moveaxis(analytic, -1, axis)


def _symmetry_score(profile: np.ndarray) -> float:
    """峰窗口对称性 + 正峰约束(0..1),吸收峰偶对称约 1,色散峰约 0。"""
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
        center = f[half]
        sym = float(
            np.mean(
                np.concatenate(
                    [np.asarray((left + right) ** 2 / denom), [center**2 / (center**2 + 1e-12)]]
                )
            )
        )
    return sym if float(np.sum(f)) >= 0.0 else sym * 0.05


def _row_peaks(
    row: np.ndarray, *, max_peaks: int = 8, margin: int = 0
) -> tuple[np.ndarray, np.ndarray] | None:
    """锁定一条 1D 谱的 top-K 局部峰,返回 (下标, 峰高)。"""
    arr = np.asarray(row, dtype=np.complex128)
    n = arr.shape[-1]
    if n < 8:
        return None
    mag = np.abs(arr)
    noise = float(np.std(mag[: min(16, n)])) if n else 0.0
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


def _p1_concentration(row: np.ndarray, positions: np.ndarray, heights: np.ndarray) -> float:
    """p1 相位集中度拟合:去掉候选斜坡后各峰单位向量圆均值模最大。"""
    n = row.shape[-1]
    vals = row[positions]
    weights = heights + 1e-12
    denom = float(np.sum(weights))

    def concentration(p1: float) -> float:
        ramp = np.exp(-1j * np.deg2rad(p1 * positions / max(n - 1, 1)))
        unit = np.exp(1j * np.angle(vals * ramp))
        return float(np.abs(np.sum(weights * unit) / denom))

    best_p1, best_conc = 0.0, -1.0
    for p1 in np.arange(-90.0, 91.0, 10.0):
        conc = concentration(float(p1))
        if conc > best_conc:
            best_conc, best_p1 = conc, float(p1)
    for span, step in ((30.0, 5.0), (10.0, 2.5)):
        for offset in np.arange(-span, span + 1e-9, step):
            conc = concentration(best_p1 + offset)
            if conc > best_conc:
                best_conc, best_p1 = conc, best_p1 + offset
    return best_p1


def search_axis_phase(
    real: np.ndarray,
    axis: int,
    *,
    radius: int = 12,
    min_windows: int = 3,
    max_rows: int = 128,
) -> AxisPhaseEstimate | None:
    """在实型谱的指定维度上做显示层相位估计(希尔伯特重建 + 逐行选峰)。"""
    arr = np.asarray(real, dtype=float)
    axis = axis if axis >= 0 else arr.ndim - 1
    n = arr.shape[axis]
    if arr.ndim < 2 or n < 8:
        return None
    analytic = analytic_axis(arr, axis)
    rows = np.moveaxis(analytic, axis, -1).reshape(-1, n)
    if rows.shape[0] > max_rows:
        index = np.linspace(0, rows.shape[0] - 1, max_rows).astype(int)
        rows = rows[index]
    infos: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for row in rows:
        peaks = _row_peaks(row)
        if peaks is not None and peaks[0].size >= 1:
            infos.append((row, peaks[0], peaks[1]))
    if len(infos) < min_windows:
        return None
    p1_signals = [
        _p1_concentration(row, positions, heights)
        for row, positions, heights in infos
        if positions.size >= 2
    ]
    p1_signal = float(np.median(p1_signals)) if p1_signals else 0.0
    p1 = -p1_signal
    anchor = max(range(len(infos)), key=lambda i: float(np.max(infos[i][2])))
    _arr, positions, heights = infos[anchor]
    vals = _arr[positions]
    ramp = np.exp(-1j * np.deg2rad(p1_signal * positions / max(n - 1, 1)))
    unit = np.exp(1j * np.angle(vals * ramp))
    weights = heights + 1e-12
    vec = np.sum(weights * unit) / max(float(np.sum(weights)), 1e-12)
    p0 = float((-np.rad2deg(np.angle(vec))) % 360.0)

    # 显示层评分:在锚点迹线内,按锁定的峰窗口对 analytic 旋转取实部评分
    anchor_rows = np.moveaxis(analytic, axis, -1).reshape(-1, n)
    anchor_row_idx = anchor
    def _anchor_score(candidate_p0: float, candidate_p1: float) -> float:
        k = np.arange(n, dtype=float)
        ramp = np.exp(1j * np.deg2rad(candidate_p0 + candidate_p1 * k / max(n - 1, 1)))
        rot = np.real(anchor_rows[anchor_row_idx] * ramp)
        values = []
        for peak in positions:
            lo, hi = max(0, int(peak) - radius), min(n, int(peak) + radius + 1)
            values.append(_symmetry_score(rot[lo:hi]))
        return 100.0 * float(np.mean(values))

    if (
        _anchor_score(p0, p1) < 10.0
        and _anchor_score((p0 + 180.0) % 360.0, p1) > _anchor_score(p0, p1)
    ):
        p0 = (p0 + 180.0) % 360.0
    best = (_anchor_score(p0, p1), p0, p1)
    for dp0 in (-10.0, -5.0, 0.0, 5.0, 10.0):
        for dp1 in (-10.0, -5.0, 0.0, 5.0, 10.0):
            candidate = _anchor_score((p0 + dp0) % 360.0, p1 + dp1)
            if candidate > best[0]:
                best = (candidate, (p0 + dp0) % 360.0, p1 + dp1)
    score, p0, p1 = best
    if abs(_anchor_score(p0, 0.0) - score) < 1.0:
        p1 = 0.0
        score = _anchor_score(p0, 0.0)
    if abs(p1) > 20.0:
        p1 = 0.0
    return AxisPhaseEstimate(
        axis=axis, p0=float(p0), p1=float(p1), score=float(score), windows=len(infos)
    )


def assess_baseline(real: np.ndarray, axis: int) -> BaselineEstimate:
    """评估一个维度的基线:两端与中部均值,归一化 slope/curvature。"""
    arr = np.asarray(real, dtype=float)
    n = arr.shape[axis]
    edge = max(int(n * 0.08), 2)
    left = float(np.mean(np.take(arr, np.arange(edge), axis=axis)))
    right = float(np.mean(np.take(arr, np.arange(n - edge, n), axis=axis)))
    start = n // 2 - edge
    mid = float(np.mean(np.take(arr, np.arange(start, start + 2 * edge), axis=axis)))
    max_abs = float(np.max(np.abs(arr))) + 1e-12
    slope = (right - left) / max_abs
    curvature = abs(left + right - 2.0 * mid) / max_abs
    offset = abs((left + right) / 2.0) / max_abs
    if curvature > 0.05 or abs(slope) > 0.05:
        mode, order = "order", 2 if curvature > 0.05 else 1
    else:
        mode, order = "auto", 0
    score = float(
        np.clip(
            100.0 * (1.0 - min(1.0, abs(slope) * 4.0 + offset * 2.0 + curvature * 6.0)),
            0.0,
            100.0,
        )
    )
    return BaselineEstimate(axis=axis, mode=mode, order=order, score=score)


def assess_fill(real: np.ndarray, axis: int, *, target_points: float = 4.0) -> FillEstimate:
    """用最强峰半高宽(FWHM)评估峰圆润度,建议是否填零。"""
    arr = np.asarray(real, dtype=float)
    others = tuple(i for i in range(arr.ndim) if i != axis)
    profile = np.max(arr, axis=others)
    peak = int(np.argmax(profile))
    half = 0.5 * float(profile[peak])
    left = peak
    while left > 0 and profile[left - 1] >= half:
        left -= 1
    right = peak
    while right < profile.size - 1 and profile[right + 1] >= half:
        right += 1
    fwhm = max(float(right - left + 1), 1.0)
    return FillEstimate(
        axis=axis,
        fwhm_points=fwhm,
        suggested=fwhm < target_points,
        ratio=fwhm / max(target_points, 1e-6),
    )


def inspect_spectrum(
    real: np.ndarray,
    *,
    axes: list[int] | None = None,
) -> dict[str, object]:
    """一次性对实型谱逐维做相位/基线/填零显示层评估。"""
    arr = np.asarray(real, dtype=float)
    selected = axes if axes is not None else list(range(arr.ndim))
    phases = [search_axis_phase(arr, axis) for axis in selected]
    baselines = [assess_baseline(arr, axis) for axis in selected]
    fills = [assess_fill(arr, axis) for axis in selected]
    return {
        "phases": phases,
        "baselines": baselines,
        "fills": fills,
    }
