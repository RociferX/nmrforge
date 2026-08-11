"""轴计划：处理顺序 / 显示方向 / FT 标志等按维度约定。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AxisPlan:
    """每个处理轴的执行约定。"""

    axis: str  # F1/F2/F3
    processing_order: int = 0
    display_direction: str = "increasing"
    ft_alt: bool = False
    ft_neg: bool = False
    flip: bool = False


@dataclass
class AxisPlanSet:
    plans: dict[str, AxisPlan] = field(default_factory=dict)
