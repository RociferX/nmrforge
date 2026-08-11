"""格式转换：Bruker/NUS → 内部数组 → 后端输入/输出。"""

from __future__ import annotations

from core.data.internal_data_model import Experiment


def to_processing_matrix(experiment: Experiment) -> object:
    """把 Experiment 转换为处理用矩阵（维度/轴序按 ProcessingPlan 约定）。"""
    raise NotImplementedError("Phase 1: 实现格式转换")
