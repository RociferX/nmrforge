"""超复数间接维合并（States / States-TPPI）。

Bruker ser 的间接维以两套 FID（R/I 或 ±）存储（mult=2）；
States 组合：S = R + i*I（读取时 R/I 交错：偶数 FID = R，奇数 = I）。
Echo-Antiecho 组合涉及梯度符号约定，Phase 1 未实现（建议走 NMRPipe 后端）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.processing.axes import axis_index


@dataclass
class HypercomplexParams:
    axis: str = "F1"
    mode: str = "states"  # states / states_tppi / echo_antiecho


def combine(data: Any, params: HypercomplexParams) -> np.ndarray:
    """把超复数分量轴（长度 2*td）合并为复数据（长度 td）。"""
    arr = np.asarray(data)
    axis = axis_index(params.axis, arr.ndim)
    n = arr.shape[axis]
    if n % 2 != 0:
        raise ValueError(f"{params.axis} 轴长度 {n} 不是偶数，无法合并超复数分量")
    if params.mode == "echo_antiecho":
        raise NotImplementedError(
            "Echo-Antiecho 超复数合并待实现（建议走 NMRPipe 后端转换）"
        )
    if params.mode not in ("states", "states_tppi"):
        raise ValueError(f"未知超复数模式: {params.mode}")
    moved = np.moveaxis(arr, axis, -1)
    paired = moved.reshape(moved.shape[:-1] + (n // 2, 2))
    combined = paired[..., 0] + 1j * paired[..., 1]
    return np.moveaxis(combined, -1, axis)
