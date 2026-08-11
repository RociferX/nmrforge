"""Apodization（窗口函数）处理原语。

候选窗口：EM / GM / sine / sine^2 / shifted sine / cosine / cosine^2 / custom。
自动选择采用 coarse candidates → quality score → local refinement（框架 §13）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ApodizationParams:
    window: str = "sine_bell"
    axis: str = "F3"
    params: dict[str, Any] = field(default_factory=dict)


def apply(data: Any, params: ApodizationParams) -> Any:
    """在指定维度应用窗口函数，返回处理后的数据。"""
    raise NotImplementedError("Phase 1: 实现 apodization")
