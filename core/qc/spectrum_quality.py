"""综合谱质量：把各 QC 分量汇总为 QualityScore，
并在处理前后比较决定 ACCEPT/ROLLBACK（框架 §47）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

from core.optimization.scoring import QualityScore, ScoreComponents
from core.qc import artifact_detection, baseline_quality, noise, peak_detection, phase_quality, snr


class QcDecision(str, Enum):
    ACCEPT = "accept"
    WARNING = "warning"
    ROLLBACK = "rollback"


@dataclass
class QualityResult:
    score: QualityScore = field(default_factory=QualityScore)
    decision: QcDecision = QcDecision.ACCEPT
    reasons: list[str] = field(default_factory=list)


def _clip100(value: float) -> float:
    return float(max(0.0, min(100.0, value)))


def evaluate(data: Any) -> QualityResult:
    """评估综合谱质量（SNR/相位/基线/伪影加权）。"""
    arr = np.asarray(data)
    sigma = noise.estimate(arr).global_sigma
    peaks = peak_detection.detect(arr)
    snr_metrics = snr.compute(arr, peaks, sigma)
    phase_metrics = phase_quality.evaluate(arr)
    baseline_metrics = baseline_quality.evaluate(arr)
    artifact_report = artifact_detection.detect(arr)

    components = ScoreComponents(
        snr=_clip100(snr_metrics.global_snr / 40.0 * 100.0),
        phase=phase_metrics.score,
        baseline=baseline_metrics.score,
        artifact=artifact_report.score,
    )
    score = QualityScore(
        components=components,
        weights={"snr": 1.0, "phase": 1.0, "baseline": 1.0, "artifact": 1.0},
    )
    overall = score.compute()

    reasons: list[str] = []
    if snr_metrics.global_snr < 10:
        reasons.append(f"全局 SNR 偏低（{snr_metrics.global_snr:.1f}）")
    if phase_metrics.negative_peak_fraction > 0.15:
        reasons.append(f"负峰比例偏高（{phase_metrics.negative_peak_fraction:.2f}）")
    if baseline_metrics.needs_correction:
        reasons.append("检测到基线倾斜/偏移，建议基线校正")
    if artifact_report.isolated_peak_clusters > 0:
        reasons.append(f"检测到 {artifact_report.isolated_peak_clusters} 个孤立峰簇")

    if overall >= 60.0:
        decision = QcDecision.ACCEPT
    elif overall >= 40.0:
        decision = QcDecision.WARNING
    else:
        decision = QcDecision.ROLLBACK
    if decision is not QcDecision.ACCEPT and not reasons:
        reasons.append(f"综合质量 {overall:.1f} 未达自动接受阈值")
    return QualityResult(score=score, decision=decision, reasons=reasons)
