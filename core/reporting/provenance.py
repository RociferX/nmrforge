"""参数溯源：每个最终参数记录 参数/值/来源/方法/置信度（框架 §46）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParameterProvenance:
    parameter: str
    value: object
    source: str = ""  # automatic_optimizer / user / preset / backend
    method: str = ""
    confidence: float = 0.0
    score: float = 0.0
    evidence: list[str] = field(default_factory=list)


@dataclass
class ProvenanceLog:
    entries: list[ParameterProvenance] = field(default_factory=list)

    def add(self, entry: ParameterProvenance) -> None:
        self.entries.append(entry)
