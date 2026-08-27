"""综合谱质量：把各 QC 分量汇总为 QualityScore，
并在处理前后比较决定 ACCEPT/ROLLBACK（框架 §47）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np

from core.optimization.scoring import QualityScore, ScoreComponents
from core.qc import artifact_detection, baseline_quality, noise, peak_detection, phase_quality, snr


class QcDecision(StrEnum):
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


def _resolution_penalty(shape: tuple[int, ...], min_shape: tuple[int, ...]) -> float:
    """数字分辨率惩罚(0.2.47):任一维实际点数低于最低要求按比例扣分。

    填零不改变真实频率分辨率(由 AQ 决定),但过低的数字点距会损失峰位/
    线宽可测性;用于阻止优化嵌入选择会减半分辨率的填零模式。返回 0..20。
    """
    penalty = 0.0
    for actual, required in zip(shape, min_shape):
        if required <= 0:
            continue
        ratio = actual / required
        if ratio < 1.0:
            penalty = max(penalty, 20.0 * (1.0 - ratio))
    return penalty


def evaluate(
    data: Any,
    *,
    min_shape: tuple[int, ...] | None = None,
    sign_mode: str = "uniform",
) -> QualityResult:
    """评估综合谱质量（SNR/相位/基线/伪影加权）。

    sign_mode: "uniform"(同号峰,HSQC 等)或 "mixed"(正负峰共存,
    HNCACB 等)——相位评分与相位优化同源(0.2.172),mixed 时负峰视为
    正常,不再误报。

    min_shape 非 None 时按各维最低点数施加分辨率惩罚(如填零后 SI 下限)。
    """
    arr = np.asarray(data)
    sigma = noise.estimate(arr).global_sigma
    peaks = peak_detection.detect(arr)
    snr_metrics = snr.compute(arr, peaks, sigma)
    phase_metrics = phase_quality.evaluate(arr, sign_mode=sign_mode)
    _worst_axis, baseline_metrics = baseline_quality.worst_axis(arr)
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
    resolution_penalty = (
        _resolution_penalty(arr.shape, min_shape) if min_shape is not None else 0.0
    )
    if resolution_penalty > 0:
        # 惩罚写回 score.overall,保证调用方读到的是含分辨率惩罚的综合分
        overall = float(max(0.0, overall - resolution_penalty))
        score.overall = overall

    reasons: list[str] = []
    if resolution_penalty > 0:
        reasons.append(
            f"数字分辨率不足(shape={tuple(arr.shape)}, 低于最低要求 "
            f"{tuple(min_shape)}, 扣 {resolution_penalty:.1f} 分)"
        )
    if snr_metrics.global_snr < 10:
        reasons.append(f"全局 SNR 偏低（{snr_metrics.global_snr:.1f}）")
    # 0.2.199-补29z:正负峰共存谱(mixed,如 CBCA(CO)NH/HNN)负峰比例
    # 天然约一半,不报告「负峰比例偏高」
    if (
        sign_mode != "mixed"
        and phase_metrics.negative_peak_fraction > 0.15
    ):
        reasons.append(f"负峰比例偏高（{phase_metrics.negative_peak_fraction:.2f}）")
    if baseline_metrics.needs_correction:
        reasons.append("检测到基线倾斜/偏移(最差存储轴),建议基线校正")
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
