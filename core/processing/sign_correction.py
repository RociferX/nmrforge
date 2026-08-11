"""符号修正原语（FID/谱域符号、axis flip、conjugation）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SignCorrectionParams:
    sign_flip: bool = False
    conjugate: bool = False
    reverse: bool = False


def apply(data: Any, params: SignCorrectionParams) -> Any:
    """应用符号修正，返回处理后的数据。"""
    raise NotImplementedError("Phase 1: 实现符号修正")
