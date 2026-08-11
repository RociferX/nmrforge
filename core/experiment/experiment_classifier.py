"""实验类型识别：规则 + 参数 + pulse program + 命名的多证据系统。

优先级：pulse program → 核组合 → 维度顺序 → FnMODE → 实验参数 → 数据集命名。
置信度约定：>0.9 自动处理；0.6-0.9 自动 + warning；<0.6 走 Generic 并请求用户确认。
"""

from __future__ import annotations

from core.data.internal_data_model import Experiment, ExperimentType


def classify(experiment: Experiment) -> ExperimentType:
    """返回实验类型（名称 + 置信度 + 证据链）。"""
    raise NotImplementedError("Phase 2: 实现多证据实验分类")
