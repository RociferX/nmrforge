"""Zero filling 处理原语。

区分「采集分辨率」与「数字插值」；候选 1×/2×/4×，避免无意义的大矩阵（框架 §14/§65）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ZeroFillParams:
    size: str = "auto"  # auto / 明确点数
    axis: str = "F3"


def apply(data: Any, params: ZeroFillParams) -> Any:
    """在指定维度补零，返回处理后的数据。"""
    raise NotImplementedError("Phase 1: 实现 zero fill")
