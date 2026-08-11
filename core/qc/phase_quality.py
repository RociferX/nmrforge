"""相位质量：吸收度比例 / 负峰比例 / 峰对称性 / 实虚残差（框架 §15/§20）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.qc import peak_detection


@dataclass
class PhaseQuality:
    absorption_fraction: float = 0.0
    negative_peak_fraction: float = 0.0
    symmetry: float = 0.0
    score: float = 0.0


def evaluate(data: Any) -> PhaseQuality:
    """评估相位质量（吸收度占比 + 负峰比例 + 镜像对称性）。"""
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

    symmetry = 0.5
    if real.size > 1 and float(np.std(real)) > 1e-12:
        mirrored = np.flip(real)
        corr = float(np.corrcoef(real.ravel(), mirrored.ravel())[0, 1])
        if not np.isnan(corr):
            symmetry = max(0.0, min(1.0, (corr + 1.0) / 2.0))

    score = float(
        np.clip(
            100.0 * (0.5 * absorption + 0.3 * (1.0 - neg_fraction) + 0.2 * symmetry),
            0.0,
            100.0,
        )
    )
    return PhaseQuality(
        absorption_fraction=float(absorption),
        negative_peak_fraction=float(neg_fraction),
        symmetry=float(symmetry),
        score=score,
    )
