"""NUS 重建原语（后端抽象）。

只暴露统一的 reconstruct 接口；具体实现（SMILE / 未来 AI）在 backend/ 选择。
参数（iterations/threshold/regularization/sparsity/...）随 method 变化。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NusReconstructionParams:
    method: str = "smile"
    iterations: int = 200
    threshold: float = 0.9
    n_sigma: float = 5.0
    kwargs: dict[str, Any] = field(default_factory=dict)


def reconstruct(data: Any, params: NusReconstructionParams) -> Any:
    """重建间接维数据。"""
    raise NotImplementedError("Phase 3: 实现 NUS 重建")
