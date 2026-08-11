"""参数空间定义与优化预算（框架 §61-62）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParameterSpace:
    """参数 -> (最小值, 最大值, 步长)；类别参数 -> 候选列表。"""

    ranges: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    categorical: dict[str, list[str]] = field(default_factory=dict)
    # 参数依赖：修改某参数会 invalidates 哪些下游节点
    dependency: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class OptimizationBudget:
    """优化预算：候选数 / 时长 / early stopping。"""

    max_candidates: int = 8
    max_runtime_s: float = 900.0
    early_stop_improvement: float = 0.01
    max_no_improvement: int = 2
