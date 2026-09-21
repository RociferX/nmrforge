"""Phase correction primitives (p0/p1).

Automatic phase optimisation lives in core/optimization/ and core/qc/phase_quality;
parameters carry source and confidence (framework §46).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.processing.axes import axis_index


@dataclass
class PhaseParams:
    p0: float = 0.0
    p1: float = 0.0
    axis: str = "F3"
    source: str = ""  # automatic_optimizer / user / preset
    confidence: float = 0.0


def apply(data: Any, params: PhaseParams) -> np.ndarray:
    """Apply phase correction: angle = p0 + p1 * k / (N-1) (degrees)."""
    arr = np.asarray(data)
    axis = axis_index(params.axis, arr.ndim)
    n = arr.shape[axis]
    k = np.arange(n)
    angle = np.deg2rad(params.p0 + params.p1 * k / max(n - 1, 1))
    shape = [1] * arr.ndim
    shape[axis] = n
    return arr * np.exp(1j * angle.reshape(shape))
