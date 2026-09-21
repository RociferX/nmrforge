"""Quality scoring: overall 0-100 plus component scores (multi-objective; no single
black-box score is forced on the result, framework §40).

Components: SNR / Resolution / Phase / Baseline / Artifact / NUS consistency /
Peak stability. Weights that are not implemented never enter the blend (``weights``
defaults to equal weight for every component).
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
        """Blend ``overall`` from the explicit weights, or from every component."""
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
