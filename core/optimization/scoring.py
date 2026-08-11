"""质量评分：总分 0-100 + 分量分数（多目标，不硬塞单一黑箱分数，框架 §40）。

分量：SNR / Resolution / Phase / Baseline / Artifact / NUS consistency / Peak stability。
未实现的权重不参与合成（weights 缺省为全部分量等权）。
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
        """加权合成 overall（仅统计显式权重或全部分量）。"""
        if self.weights:
            keys = [k for k, w in self.weights.items() if w > 0]
            weights = {k: self.weights[k] for k in keys}
        else:
            weights = {k: 1.0 for k in ScoreComponents.__dataclass_fields__}
        total = sum(weights.values())
        if total <= 0:
            self.overall = 0.0
            return self.overall
        overall = sum(getattr(self.components, k) * w for k, w in weights.items()) / total
        self.overall = float(overall)
        return self.overall
