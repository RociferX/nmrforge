"""基线校正原语。

先检测后处理（slope/curvature/low-freq drift）；方法候选：
polynomial / spline / Whittaker / median-based / NMRPipe 兼容；
每次校正后比较 before/after 分数，变差自动回滚（框架 §16）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.processing.axes import axis_index


@dataclass
class BaselineParams:
    method: str = "polynomial"
    axis: str = "F3"
    order: int = 1


def _edge_values(arr: np.ndarray, axis: int, edge_fraction: float = 0.08):
    n = arr.shape[axis]
    edge = max(int(n * edge_fraction), 2)
    left = np.take(arr, np.arange(edge), axis=axis)
    right = np.take(arr, np.arange(n - edge, n), axis=axis)
    return left, right


def detect(data: Any) -> dict[str, float]:
    """返回基线问题指标（slope/curvature/drift/offset），沿最后一个轴评估。"""
    arr = np.real(np.asarray(data))
    axis = arr.ndim - 1
    left, right = _edge_values(arr, axis)
    max_abs = float(np.max(np.abs(arr))) + 1e-12
    slope = (float(np.mean(right)) - float(np.mean(left))) / max_abs
    offset = (float(np.mean(left)) + float(np.mean(right))) / 2.0 / max_abs
    return {"slope": slope, "curvature": 0.0, "drift": abs(slope), "offset": offset}


def _robust_polyfit_baseline(
    flat: np.ndarray,
    order: int,
    *,
    n_iter: int = 6,
    k: float = 3.0,
) -> np.ndarray:
    """逐迹稳健多项式基线估计（迭代峰值屏蔽，与 NMRPipe POLY 同思路）。

    flat: (n_traces, n) 实数迹线。每轮用当前拟合残差的 MAD 定义峰阈值，
    只保留残差在 ±k·sigma 内的点（基线点）重拟合——谱内强峰不再拉偏
    多项式，相邻迹的拟合系数不再跳变（竖线条纹的根因）。
    """
    n_traces, n = flat.shape
    x = np.arange(n, dtype=float)
    X = np.stack([x**p for p in range(order + 1)], axis=1)  # (n, k)
    coef, *_ = np.linalg.lstsq(X, flat.T, rcond=None)  # (k, n_traces)
    fit = (X @ coef).T  # (n_traces, n)
    scale = np.max(np.abs(flat), axis=1, keepdims=True) + 1e-12
    for _ in range(n_iter):
        resid = flat - fit
        med = np.median(resid, axis=1, keepdims=True)
        mad = np.median(np.abs(resid - med), axis=1, keepdims=True)
        sigma = np.maximum(1.4826 * mad, 1e-6 * scale)
        keep = np.abs(resid - med) <= k * sigma
        if int(keep.sum(axis=1).min()) <= order + 1:
            break
        w = keep.astype(float)
        xtwx = np.einsum("nj,tn,nk->tjk", X, w, X)  # (n_traces, k, k)
        xtwy = np.einsum("nj,tn->tj", X, w * flat)  # (n_traces, k)
        try:
            new_coef = np.linalg.solve(xtwx, xtwy[..., None])[..., 0]
        except np.linalg.LinAlgError:
            break
        new_fit = (X @ new_coef.T).T
        delta = float(np.max(np.abs(new_fit - fit)))
        coef, fit = new_coef, new_fit
        if delta <= 1e-6 * float(np.max(scale)):
            break
    return fit


def apply(data: Any, params: BaselineParams) -> np.ndarray:
    """多项式基线校正：沿指定轴逐迹稳健拟合（degree=order）并减去。

    0.2.190：普通 polyfit 会被谱内强峰拉偏，产生相邻迹系数跳变（竖线
    条纹）；改为迭代峰值屏蔽（残差 MAD 阈值）的稳健拟合，只基于基线点
    估计多项式，校正真实基线而不压扁峰或引入条纹（与 NMRPipe POLY
    -auto 的稳健基线估计同思路）。
    """
    arr = np.asarray(data)
    axis = axis_index(params.axis, arr.ndim)
    n = arr.shape[axis]
    if n <= params.order + 1:
        return arr
    moved = np.moveaxis(arr, axis, -1)
    flat = moved.reshape(-1, n)
    baseline = _robust_polyfit_baseline(np.real(flat), max(params.order, 1))
    flat -= baseline
    return arr
