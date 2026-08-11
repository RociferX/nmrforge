"""Apodization（窗口函数）处理原语。

候选窗口：em / gm / sine / shifted sine / cosine / cosine^2 / custom。
自动选择采用 coarse candidates → quality score → local refinement（框架 §13）。
Phase 1：沿指定逻辑轴应用窗口；坐标 t 归一化到 [0, 1]。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from core.processing.axes import axis_index


@dataclass
class ApodizationParams:
    window: str = "sine_bell"
    axis: str = "F3"
    params: dict[str, Any] = field(default_factory=dict)  # off/end/pow/c/lb/gb


def _window(name: str, n: int, params: dict[str, Any]) -> np.ndarray:
    off = float(params.get("off", 0.45))
    end = float(params.get("end", 0.95))
    power = float(params.get("pow", 1.0))
    t = np.linspace(0.0, 1.0, n)
    name = name.lower()
    if name == "em":
        lb = float(params.get("lb", 2.0))
        return np.exp(-np.pi * lb * t)
    if name == "gm":
        lb = float(params.get("lb", 2.0))
        gb = float(params.get("gb", 0.0))
        return np.exp(-np.pi * lb * t * (1.0 - gb * t))
    if name in ("sine_bell", "sine", "shifted_sine"):
        return np.sin(np.pi * (off + (end - off) * t)) ** power
    if name == "cosine":
        return np.cos(np.pi / 2.0 * (off + (end - off) * t)) ** power
    if name == "cosine_sq":
        return np.cos(np.pi / 2.0 * (off + (end - off) * t)) ** (2 * power)
    raise ValueError(f"未知窗口: {name}")


def apply(data: Any, params: ApodizationParams) -> np.ndarray:
    """在指定维度应用窗口函数，返回处理后的数据。"""
    arr = np.asarray(data)
    axis = axis_index(params.axis, arr.ndim)
    n = arr.shape[axis]
    w = _window(params.window, n, params.params)
    return np.apply_along_axis(lambda v: v * w, axis, arr)
