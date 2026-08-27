"""逻辑轴（F1/F2/F3）→ numpy 轴索引。

两套约定：
- axis_index：内部内存数组约定——2D (F1, F2)，3D (F1, F2, F3)；
- file_axis_index：NMRPipe 单文件/切片输出布局（0.2.199-补29 实证，
  sampleB ft3 与 sampleJ 手工切片）——2D (F1, F2)，3D (F2, F1, F3)。

3D 下两套约定 F1/F2 相反：生产布局把 F2（最快间接维）放第一轴。读最终谱/
复型预览等后端输出文件时必须用 file_axis_index，用错会把 F1/F2 评反。
"""

from __future__ import annotations

_INDEX = {"F1": 0, "F2": 1, "F3": 2}


_FILE_INDEX_3D = {"F2": 0, "F1": 1, "F3": 2}
_FILE_INDEX_2D = {"F1": 0, "F2": 1}


def axis_index(axis: str, ndim: int) -> int:
    """内部内存约定：逻辑轴在 ndim 维数据中的 numpy 轴索引。"""
    axis = axis.upper()
    index = _INDEX.get(axis)
    if index is None or index >= ndim:
        raise ValueError(f"轴 {axis} 不存在于 {ndim} 维数据")
    return index


def file_axis_index(axis: str, ndim: int) -> int:
    """NMRPipe 输出文件布局（实测）：逻辑轴在谱数组中的 numpy 轴索引。"""
    axis = axis.upper()
    index = (_FILE_INDEX_3D if ndim >= 3 else _FILE_INDEX_2D).get(axis)
    if index is None or index >= ndim:
        raise ValueError(f"轴 {axis} 不存在于 {ndim} 维数据")
    return index
