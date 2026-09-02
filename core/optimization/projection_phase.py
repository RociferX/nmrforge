"""投影平面逐维相位校正(0.2.199-补29k,用户方案)。

3D 人工调相看三个投影面:直接维的 1D 谱 = 间接维1 点数 + 间接维2 点数
(F1-F3 投影每条 F1 一条 F3 迹线,F2-F3 投影每条 F2 一条 F3 迹线),逐条
调好相位取统计最佳。每维独立:投影时另外两维按当前相位校正后做复求和,
投影才相干(未校正的其它维相位会让复求和抵消)。

实现:给定全复 3D 谱(已按其它维当前相位旋转),对目标维
- 沿另两个轴复求和得到两张投影平面;
- 从两张投影平面抽目标维迹线(另一维每点一条);
- 逐条迹线锁峰 → p1 相位集中度(多峰)→ p0 圆均值;跨迹线统计共识。
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from core.optimization.phase_consensus import (
    search_axis_phase_consensus,
)


def projected_traces(
    complex3d: np.ndarray,
    axis: int,
) -> np.ndarray:
    """目标维(axis)的投影迹线集合。

    沿另外两个轴分别复求和,得到两张投影平面;每张平面按另一维每点
    抽一条沿 axis 的迹线,拼接返回 (n_traces, n_axis)。
    """
    arr = np.asarray(complex3d, dtype=np.complex128)
    other = [a for a in range(arr.ndim) if a != axis]
    traces: list[np.ndarray] = []
    for keep in other:
        proj = arr.sum(axis=keep)
        # 去掉 keep 后,目标维在投影平面里的轴序号需重映射
        proj_axis = axis if axis < keep else axis - 1
        moved = np.moveaxis(proj, proj_axis, -1)
        traces.append(moved.reshape(-1, moved.shape[-1]))
    return np.concatenate(traces, axis=0)


def search_projected_axis(
    complex3d: np.ndarray,
    axis: int,
    *,
    max_traces: int = 512,
    min_peaks: int = 2,
    margin: int = 8,
    max_peaks: int = 8,
    sign_mode: str = "uniform",
    progress: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> tuple[float, float, float] | None:
    """在投影平面迹线上做逐条调相 + 统计共识(人工投影思路)。

    返回 (p0, p1, score);无干净迹线返回 None。
    """
    traces = projected_traces(complex3d, axis)
    if traces.shape[0] > max_traces:
        idx = np.linspace(0, traces.shape[0] - 1, max_traces).astype(int)
        traces = traces[idx]
    # 把迹线集合组织成 2D 数组调用共识搜索(轴=-1)
    return search_axis_phase_consensus(
        traces,
        -1,
        min_peaks=min_peaks,
        margin=margin,
        max_peaks=max_peaks,
        sign_mode=sign_mode,
        progress=progress,
        cancel=cancel,
    )


__all__ = ["projected_traces", "search_projected_axis"]
