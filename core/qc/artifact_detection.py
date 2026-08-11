"""伪影检测：镜像峰 / 孤立峰簇 / 脊状伪影 / 重建伪影（框架 §66）。

Phase 1：孤立峰簇检测（峰间距显著大于谱图尺度时视为可疑）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from core.qc import peak_detection


@dataclass
class ArtifactReport:
    mirror_peaks: int = 0
    isolated_peak_clusters: int = 0
    ridge_artifacts: int = 0
    warnings: list[str] = field(default_factory=list)
    score: float = 0.0


def detect(data: Any) -> ArtifactReport:
    """检测谱图伪影。"""
    arr = np.asarray(data)
    peaks = peak_detection.detect(arr)
    isolated = 0
    if len(peaks) > 1:
        positions = np.array([p.position for p in peaks])
        heights = np.array([p.height for p in peaks])
        threshold = 0.15 * max(arr.shape)
        for i, pos in enumerate(positions):
            comparable = heights >= 0.5 * heights[i]
            comparable[i] = False
            if not comparable.any():
                continue
            dists = np.linalg.norm(positions[comparable] - pos, axis=1)
            if float(np.min(dists)) > threshold:
                isolated += 1
    score = float(max(0.0, 100.0 - 20.0 * isolated))
    return ArtifactReport(isolated_peak_clusters=isolated, score=score)
