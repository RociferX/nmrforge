"""伪影检测：镜像峰 / 孤立峰簇 / 脊状伪影 / 重建伪影（框架 §66）。

Phase 1：孤立峰检测——按谱自身峰密度归一化,只对「异常孤立」的峰扣分。
0.2.199-补29el:旧实现用绝对距离阈值(0.15×max(shape))判孤立,稀疏谱/3D 谱
天然峰间距大被系统性误判为孤立峰簇(伪影 30-69 分,目视正常);且完全跳过
「无任何可比峰」的峰(真正孤立的强伪峰反而漏检)。现改为:
  每峰期望间距 s = (谱体积 / 可比峰数)^(1/维数);
  dmin > 5×s 且(近轴边缘 ≤2% 或极端孤立 >8×s)才标记;
  无可比峰的强峰仅在整体峰数 ≥20(密集谱语境)时标记;
  弱峰(snr<5)不标记(噪声峰不是伪影)。
每个孤立峰按超出期望间距程度扣 5–10 分(单个最多 10,2026-09-09 用户)。
真实谱常规稀疏/3D 分布不再误报;密集谱中注入的孤立强伪峰仍被抓。
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
    """检测谱图伪影(孤立峰,按谱自身峰密度归一化,0.2.199-补29el)。"""
    arr = np.asarray(data)
    peaks = peak_detection.detect(arr)
    isolated = 0
    penalty = 0.0
    if len(peaks) > 1:
        positions = np.array([p.position for p in peaks])
        heights = np.array([p.height for p in peaks])
        snrs = np.array([p.snr for p in peaks])
        ndim = arr.ndim
        volume = float(np.prod(arr.shape))
        total = len(peaks)
        for i, pos in enumerate(positions):
            if snrs[i] < 5.0:
                continue  # 弱峰(噪声峰)不是伪影证据
            comparable = heights >= 0.5 * heights[i]
            comparable[i] = False
            m = int(comparable.sum())
            edge_dist = float(
                min(min(p, arr.shape[a] - 1 - p) for a, p in enumerate(pos))
            )
            if m == 0:
                # 无任何可比峰:只有整体峰数足够(密集谱语境)才可疑;
                # 稀疏谱中孤立是正常分布,不误报
                if total >= 20:
                    isolated += 1
                    penalty += 10.0
                continue
            dists = np.linalg.norm(positions[comparable] - pos, axis=1)
            dmin = float(np.min(dists))
            spacing = (volume / m) ** (1.0 / ndim)
            near_edge = edge_dist <= 0.02 * max(arr.shape)
            if dmin > 5.0 * spacing and (
                near_edge or dmin > 8.0 * spacing
            ):
                isolated += 1
                excess = min(
                    1.0,
                    (dmin - 5.0 * spacing) / max(5.0 * spacing, 1e-9),
                )
                penalty += 10.0 * (0.5 + 0.5 * excess)
    score = float(max(0.0, 100.0 - penalty))
    return ArtifactReport(isolated_peak_clusters=isolated, score=score)
