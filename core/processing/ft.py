"""傅里叶变换处理原语（含 -alt/-neg/翻转标志，框架 §10/§6.1）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class FtParams:
    axis: str = "F3"
    alt: bool = False
    neg: bool = False


def apply(data: Any, params: FtParams) -> Any:
    """在指定维度做 FT，返回处理后的数据。"""
    raise NotImplementedError("Phase 1: 实现 FT")
