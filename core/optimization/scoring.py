"""质量评分：总分 0-100 + 分量分数（多目标，不硬塞单一黑箱分数，框架 §40）。

分量：SNR / Resolution / Phase / Baseline / Artifact / NUS consistency / Peak stability。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScoreComponents:
    snr: float = 0.0
    resolution: float = 0.0
    phase: float = 0.0
    baseline: float = 0.0
    artifact: float = 0.0
    nus_consistency: float = 0.0
    peak_stability: float = 0.0


@dataclass
class QualityScore:
    overall: float = 0.0
    components: ScoreComponents = field(default_factory=ScoreComponents)
    weights: dict[str, float] = field(default_factory=dict)

    def compute(self) -> float:
        """加权合成 overall（Phase 1 落地）。"""
        raise NotImplementedError("Phase 1: 实现加权评分")
