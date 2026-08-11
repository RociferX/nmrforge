"""方法选择：根据实验类型与数据特征选择处理策略（窗口/相位/基线/校准方法）。"""

from __future__ import annotations

from core.data.internal_data_model import Experiment
from core.planning.processing_plan import ProcessingPlan


def select_method(experiment: Experiment) -> ProcessingPlan:
    """为实验生成处理计划（Phase 1 落地）。"""
    raise NotImplementedError("Phase 1: 实现方法选择")
