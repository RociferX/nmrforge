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
    radius: int = 5,
    max_traces: int = 128,
    coarse_p0_step: float = 30.0,
) -> AxisPhaseEstimate | None:
    """在实型谱的指定维度上做显示层相位估计。

    评价方法与进阶版(optimize_phase_sequential)一致:基线固定迹线中位数
    净吸收。唯一区别是候选谱的虚部来源——进阶版由真实后端生成复型谱,
    这里由希尔伯特重建实型谱虚部后,在显示层旋转取实部。

    流程:
    1. 在 PS(0,0) 实型谱上锁定信号迹线和每条迹线最强峰位;
    2. 希尔伯特重建该维解析信号;
    3. 对候选 (p0, p1) 做频域旋转取实部;
    4. 在锁定的固定迹线/峰位窗口上算净吸收中位数(与进阶版同一指标);
    5. 网格 + 细化搜索使统计评分最高的 (p0, p1)。
    """
    axis = axis if axis >= 0 else np.asarray(real).ndim - 1
    if np.iscomplexobj(real):
        analytic = np.asarray(real, dtype=np.complex128)
    else:
        analytic = analytic_axis(np.asarray(real, dtype=float), axis)
    n = analytic.shape[axis]
    if analytic.ndim < 2 or n < 8:
        return None

    analytic_rows = np.moveaxis(analytic, axis, -1).reshape(-1, n)
    trace_mag = np.max(np.abs(analytic_rows), axis=-1)
    locked = np.argsort(trace_mag)[::-1][:max_traces]
    locked = locked[trace_mag[locked] > 0]
    if locked.size == 0:
        return None
    positions = np.argmax(np.abs(analytic_rows[locked]), axis=-1).astype(int)

    def _score(p0: float, p1: float) -> float:
        k = np.arange(n, dtype=float)
        ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
        rotated = np.real(analytic_rows[locked] * ramp)
        metrics = []
        for index, peak in enumerate(positions):
            lo = max(0, int(peak) - radius)
            hi = min(n, int(peak) + radius + 1)
            profile = rotated[index, lo:hi]
            positive = float(np.clip(profile, 0.0, None).sum())
            negative = float(np.clip(profile, None, 0.0).sum())
            total = float(np.abs(profile).sum()) + 1e-12
            metrics.append((positive + negative) / total)
        median = float(np.median(metrics)) if metrics else 0.0
        return 50.0 * (median + 1.0)

    # 与进阶版 uniform 相同:粗搜/细化只搜 p0(p1 固定 0),末尾小 p1 精修
    best = None
    for p0 in np.arange(0.0, 360.0, coarse_p0_step):
        score = _score(float(p0), 0.0)
        if best is None or score > best[0]:
            best = (score, float(p0), 0.0)
    assert best is not None
    score, p0, p1 = best
    for _ in range(2):
        for dp0 in (-15.0, -5.0, 0.0, 5.0, 15.0):
            candidate = _score((p0 + dp0) % 360.0, 0.0)
            if candidate > score:
                score, p0, p1 = candidate, (p0 + dp0) % 360.0, 0.0
    for dp1 in (-22.5, -10.0, 0.0, 10.0, 22.5):
        candidate = _score(p0, p1 + dp1)
        if candidate > score:
            score, p1 = candidate, p1 + dp1
    if abs(_score(p0, 0.0) - score) < 1.0:
        p1 = 0.0
        score = _score(p0, 0.0)
    # 近最优平台取最小修正(人工习惯:谱已接近好相位时不乱加修正)
    near = []
    for dp0 in np.arange(-180.0, 181.0, 5.0):
        for dp1 in np.arange(-20.0, 21.0, 5.0):
            pc = (p0 + dp0) % 360.0
            qc = p1 + dp1
            sc = _score(pc, qc)
            if sc >= score - 5.0:
                near.append((sc, pc, qc))
    if near:
        near.sort(key=lambda item: (min(item[1], 360.0 - item[1]), abs(item[2]), -item[0]))
        score, p0, p1 = near[0]
    if abs(p1) > 20.0:
        p1 = 0.0
    return AxisPhaseEstimate(
        axis=axis, p0=float(p0), p1=float(p1), score=float(score), windows=int(locked.size)
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
    arr = np.asarray(real)
    selected = axes if axes is not None else list(range(arr.ndim))
    real_arr = np.real(arr).astype(float)
    phases = [search_axis_phase(arr, axis) for axis in selected]
    baselines = [assess_baseline(real_arr, axis) for axis in selected]
    fills = [assess_fill(real_arr, axis) for axis in selected]
    return {
        "phases": phases,
        "baselines": baselines,
        "fills": fills,
    }
