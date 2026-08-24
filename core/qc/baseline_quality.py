"""基线质量：slope / curvature / low-freq drift / 无峰区偏差 / 残差偏置（框架 §16）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class BaselineQuality:
    slope: float = 0.0
    curvature: float = 0.0
    drift: float = 0.0
    offset: float = 0.0
    stripe: float = 0.0
    score: float = 0.0
    needs_correction: bool = False


def _trace_edge_jumps(real: np.ndarray, axis: int, edge_fraction: float = 0.08) -> np.ndarray:
    """沿 axis 每迹两端均值(带状区)的相邻迹差——逐迹校正引入的条纹度量。

    若某条迹的基线被强峰拉偏,其端部均值会与相邻迹明显不同,形成一条
    贯穿谱图的条纹;该差值的分布(中位数/最大跳变)即条纹指标。
    """
    n = real.shape[axis]
    edge = max(int(n * edge_fraction), 2)
    moved = np.moveaxis(real, axis, -1)
    flat = moved.reshape(-1, n)
    left = flat[:, :edge].mean(axis=1)
    right = flat[:, -edge:].mean(axis=1)
    return np.abs(np.diff(0.5 * (left + right)))


def stripe_penalty(data: Any, axis: int | None = None) -> float:
    """逐迹条纹罚项(0..0.5):相邻迹端部均值最大跳变相对中位数的比值。

    ratio = max_jump / max(median_jump, 动态范围×1e-4)。ratio≤8 无罚;
    ratio 16 达半罚(0.25),≥32 封顶 0.5。稀疏强峰拉偏产生的单条条纹
    (少数迹跳变、其余一致)比值很大,会被显著惩罚;均匀噪声/平滑漂移
    的比值通常 <8,不惩罚。
    """
    arr = np.asarray(data)
    real = np.real(arr)
    if real.ndim < 2 or real.shape[-1] < 8:
        return 0.0
    axis = real.ndim - 1 if axis is None else int(axis)
    jumps = _trace_edge_jumps(real, axis)
    if jumps.size == 0:
        return 0.0
    med = float(np.median(jumps))
    floor = float(np.max(np.abs(real))) * 1e-4 + 1e-12
    ratio = float(np.max(jumps)) / max(med, floor)
    if ratio <= 8.0:
        return 0.0
    return float(np.clip((ratio - 8.0) / 48.0, 0.0, 0.5))


def evaluate(data: Any, axis: int | None = None) -> BaselineQuality:
    """评估基线质量(沿指定轴,缺省最后一个轴;取两端与中部均值,含条纹罚)。

    0.2.170:支持指定轴——谱图质量评估对每个存储轴分别评估取最差,
    不再只检最后一个轴(2D 间接维/3D F2、F3 的基线不平此前会漏报)。
    """
    arr = np.asarray(data)
    real = np.real(arr)
    max_abs = float(np.max(np.abs(real))) + 1e-12
    axis = real.ndim - 1 if axis is None else int(axis)
    n = real.shape[axis]
    edge = max(int(n * 0.08), 2)
    if n < 2 * edge:
        # 轴太短无法取两端/中部带,视为无基线问题
        return BaselineQuality(score=100.0)
    left = float(np.mean(np.take(real, np.arange(edge), axis=axis)))
    right = float(np.mean(np.take(real, np.arange(n - edge, n), axis=axis)))
    center = slice(n // 2 - edge, n // 2 + edge)
    mid = float(np.mean(np.take(real, np.arange(center.start, center.stop), axis=axis)))
    slope = (right - left) / max_abs
    offset = ((left + right) / 2.0) / max_abs
    curvature = abs(left + right - 2.0 * mid) / max_abs
    stripe = stripe_penalty(real, axis)
    score = float(
        np.clip(
            100.0
            * (
                1.0
                - min(
                    1.0,
                    abs(slope) * 4.0
                    + abs(offset) * 2.0
                    + curvature * 6.0
                    + stripe,
                )
            ),
            0.0,
            100.0,
        )
    )
    needs = abs(offset) > 0.02 or abs(slope) > 0.05 or curvature > 0.05 or stripe > 0.1
    return BaselineQuality(
        slope=float(slope),
        curvature=float(curvature),
        drift=float(abs(slope)),
        offset=float(offset),
        stripe=float(stripe),
        score=score,
        needs_correction=bool(needs),
    )


def worst_axis(data: Any) -> tuple[int, BaselineQuality]:
    """逐存储轴评估基线取最差(score 最低),返回 (轴号, 质量)。

    0.2.170:谱图质量评估用最差轴代表整谱基线水平,避免单轴漏报。
    """
    arr = np.asarray(data)
    real = np.real(arr)
    worst_idx, worst = -1, None
    for axis in range(real.ndim):
        m = evaluate(arr, axis=axis)
        if worst is None or m.score < worst.score:
            worst, worst_idx = m, axis
    if worst is None:
        worst = evaluate(arr)
    return worst_idx, worst
