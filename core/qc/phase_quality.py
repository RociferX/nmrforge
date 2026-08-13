"""相位质量：吸收度比例 / 连续负面积 / 谱熵 / 负峰比例 / 峰对称性。

评分依据（成熟文献方案）：
- 负面积最小化（de Brouwer et al., JMR 201 (2009) 230-238）：先做稳健基线
  校正再统计负值面积——相位误差把吸收能量摊到色散边瓣，负面积随误差一阶
  上升，是 ±5° 邻域区分度最高的连续指标；
- 谱熵最小化（Ernst 1966；Chen 2002 高效版）：相位正确时谱最集中，
  正部熵最低，方向与负面积一致；
- 吸收度（实虚平衡）与负峰计数（180° 反相兜底）作为补充。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.qc import peak_detection


@dataclass
class PhaseQuality:
    absorption_fraction: float = 0.0
    negative_peak_fraction: float = 0.0
    negative_area_fraction: float = 0.0
    entropy: float = 0.0
    symmetry: float = 0.0
    score: float = 0.0


def negative_area_fraction(real: Any) -> float:
    """连续负面积比例：稳健基线扣除后，负值面积 / 总绝对值面积。

    de Brouwer 2009 的黄金标准指标。基线用中位数估计（峰只占少数点，
    中位数稳健）；相位误差 → 色散负边瓣 → 比例一阶上升。
    返回 [0, 1]，0 = 无负值区域，1 = 全部为负。
    """
    arr = np.asarray(np.real(real), dtype=float)
    corrected = arr - float(np.median(arr))
    total = float(np.sum(np.abs(corrected))) + 1e-12
    neg = float(np.sum(np.minimum(corrected, 0.0)))
    return float(-neg / total)


def spectral_entropy(real: Any) -> float:
    """正部谱熵（Ernst 最小熵，归一化到 [0, 1]）。

    基线扣除后取正部为能量分布：均匀分布熵=1，单点集中=0。
    相位错误把能量摊开 → 熵上升；实型终谱下方向与负面积一致。
    """
    arr = np.asarray(np.real(real), dtype=float)
    pos = np.maximum(arr - float(np.median(arr)), 0.0)
    total = float(np.sum(pos))
    if total <= 1e-12 or pos.size < 2:
        return 1.0
    p = pos.ravel() / total
    p = p[p > 0]
    n = p.size
    if n < 2:
        return 0.0
    return float(-np.sum(p * np.log(p)) / np.log(n))


def evaluate(data: Any) -> PhaseQuality:
    """评估相位质量（吸收度 + 连续负面积 + 谱熵 + 负峰 + 对称性）。

    评分 = 100 × (0.25×吸收度 + 0.40×(1−负面积) + 0.20×(1−熵)
    + 0.15×(1−负峰比例))。终谱为实型（D005）时吸收度恒 1，区分主要靠
    负面积与熵；负峰计数兜底 180° 反相。镜像对称性不再计分：90° 色散谱
    反而高度对称，旧公式会把它误判为高相位质量（字段保留供调用方读取）。
    """
    arr = np.asarray(data)
    real = np.real(arr)
    imag = np.imag(arr)
    abs_real = float(np.mean(np.abs(real))) + 1e-12
    abs_imag = float(np.mean(np.abs(imag))) + 1e-12
    absorption = abs_real / (abs_real + abs_imag)

    positive = peak_detection.detect(arr)
    negative = peak_detection.detect(-real)
    total = len(positive) + len(negative)
    neg_fraction = len(negative) / total if total else 0.0

    na = negative_area_fraction(real)
    ent = spectral_entropy(real)

    symmetry = 0.5
    if real.size > 1 and float(np.std(real)) > 1e-12:
        mirrored = np.flip(real)
        corr = float(np.corrcoef(real.ravel(), mirrored.ravel())[0, 1])
        if not np.isnan(corr):
            symmetry = max(0.0, min(1.0, (corr + 1.0) / 2.0))

    score = float(
        np.clip(
            100.0
            * (
                0.25 * absorption
                + 0.40 * (1.0 - na)
                + 0.20 * (1.0 - ent)
                + 0.15 * (1.0 - neg_fraction)
            ),
            0.0,
            100.0,
        )
    )
    return PhaseQuality(
        absorption_fraction=float(absorption),
        negative_peak_fraction=float(neg_fraction),
        negative_area_fraction=float(na),
        entropy=float(ent),
        symmetry=float(symmetry),
        score=score,
    )
