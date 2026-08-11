"""处理计划：面向一个实验的完整处理方案（DAG + 方法选择 + 理由 + 置信度）。"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.planning.dependency_graph import ProcessingDag


@dataclass
class ProcessingPlan:
    experiment_id: str
    dag: ProcessingDag = field(default_factory=ProcessingDag)
    method_choices: dict[str, str] = field(default_factory=dict)
    rationale: dict[str, str] = field(default_factory=dict)  # 参数/选择 -> 理由
    confidence: float = 0.0
