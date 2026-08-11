"""逻辑轴（F1/F2/F3）→ numpy 轴索引。

内部数据约定：2D 数组形状 (F1, F2)，3D 形状 (F1, F2, F3)。
"""

from __future__ import annotations

_INDEX = {"F1": 0, "F2": 1, "F3": 2}


def axis_index(axis: str, ndim: int) -> int:
    """返回逻辑轴在 ndim 维数据中的 numpy 轴索引。"""
    axis = axis.upper()
    index = _INDEX.get(axis)
    if index is None or index >= ndim:
        raise ValueError(f"轴 {axis} 不存在于 {ndim} 维数据")
    return index
