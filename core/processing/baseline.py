"""基线校正原语。

先检测后处理（slope/curvature/low-freq drift）；方法候选：
polynomial / spline / Whittaker / median-based / NMRPipe 兼容；
每次校正后比较 before/after 分数，变差自动回滚（框架 §16）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BaselineParams:
    method: str = "polynomial"
    axis: str = "F3"
    order: int = 1


def detect(data: Any) -> dict[str, float]:
    """返回基线问题指标（slope/curvature/drift/offset）。"""
    raise NotImplementedError("Phase 1: 实现基线检测")


def apply(data: Any, params: BaselineParams) -> Any:
    """应用基线校正，返回处理后的数据。"""
    raise NotImplementedError("Phase 1: 实现基线校正")
