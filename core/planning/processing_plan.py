"""Processing plan: the complete processing scheme for one experiment
(DAG + method choices + rationale + confidence)."""

from __future__ import annotations

from dataclasses import dataclass, field

from core.planning.dependency_graph import ProcessingDag


@dataclass
class ProcessingPlan:
    experiment_id: str
    dag: ProcessingDag = field(default_factory=ProcessingDag)
    method_choices: dict[str, str] = field(default_factory=dict)
    rationale: dict[str, str] = field(default_factory=dict)  # parameter/choice -> reason
    confidence: float = 0.0
