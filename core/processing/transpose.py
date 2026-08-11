"""转置原语（处理轴重排）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TransposeParams:
    order: tuple[str, ...] = ("F1", "F2", "F3")


def apply(data: Any, params: TransposeParams) -> Any:
    """按 order 重排数据轴，返回处理后的数据。"""
    raise NotImplementedError("Phase 1: 实现转置")
