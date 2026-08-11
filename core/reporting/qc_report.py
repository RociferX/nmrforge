"""QC 报告：各分量分数、before/after 对比、决策与理由（框架 §47）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QcReport:
    dataset_id: str = ""
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
