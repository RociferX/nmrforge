"""峰检测（用于 QC 与峰表，不是 assignment，框架 §19）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.ndimage import maximum_filter

from core.qc import noise


@dataclass
class Peak:
    position: tuple[float, ...] = ()
    height: float = 0.0
    volume: float = 0.0
    width: tuple[float, ...] = ()
    snr: float = 0.0
    sign: int = 1


@dataclass
class PeakDetectionParams:
    sigma_multiplier: float = 3.0
    min_snr: float = 3.0
    neighborhood: int = 3
    # 峰符号模式(0.2.199-补29ap,用户规则):
    #   positive  仅正峰(默认,保持旧行为);
    #   negative  仅负峰;
    #   both      正负峰都选(mixed 实验,如 HNCACB 13Cα/13Cβ 反相);
    #   dominant  只保留占多数的符号峰(uniform 实验;主符号由候选峰计数
    #             决定,平局按绝对强度总和,再平局取正——用户:「不用管
    #             正负,肯定是多的那些」)。
    sign_mode: str = "positive"


def _candidates(
    real: np.ndarray, sigma: float, params: PeakDetectionParams, sign: int
) -> list[Peak]:
    """提取 sign(+1/-1) 方向的候选峰:局部极大 + 强度>噪声×sigma + S/N 阈值。"""
    value = sign * real
    footprint = np.ones([params.neighborhood] * real.ndim, dtype=bool)
    maxima = maximum_filter(value, footprint=footprint, mode="constant")
    mask = (value == maxima) & (value > sigma * params.sigma_multiplier)
    peaks: list[Peak] = []
    for idx in np.argwhere(mask):
        val = float(real[tuple(idx)])
        snr_value = abs(val) / sigma if sigma > 0 else 0.0
        if snr_value >= params.min_snr:
            peaks.append(
                Peak(
                    position=tuple(float(i) for i in idx),
                    height=val,
                    snr=snr_value,
                    sign=sign,
                )
            )
    return peaks


def _keep_dominant(candidates: list[Peak]) -> list[Peak]:
    """dominant 模式:只保留候选峰更多的符号(平局按绝对强度总和,再平局取正)。"""
    counts: dict[int, int] = {1: 0, -1: 0}
    totals: dict[int, float] = {1: 0.0, -1: 0.0}
    for peak in candidates:
        s = 1 if peak.height >= 0 else -1
        counts[s] += 1
        totals[s] += abs(peak.height)
    dominant = 1
    for s in (-1, 1):
        if counts[s] > counts[dominant]:
            dominant = s
        elif counts[s] == counts[dominant] and totals[s] > totals[dominant]:
            dominant = s
    return [
        peak
        for peak in candidates
        if (1 if peak.height >= 0 else -1) == dominant
    ]


def detect(data: Any, params: PeakDetectionParams | None = None) -> list[Peak]:
    """局部极大值 + 强度>噪声×sigma + S/N 阈值(2D/3D 通用)。

    sign_mode 控制峰符号:uniform 实验(HSQC/COSY 等单符号)用 dominant 只
    保留主符号峰;mixed 实验(HNCACB 等正负共存)用 both 正负都选。
    Peak.height 保留真实符号(CSV Intensity 亦带符号),snr 取绝对值。
    """
    arr = np.asarray(data)
    params = params or PeakDetectionParams()
    sigma = noise.estimate(arr).global_sigma
    real = np.real(arr)
    mode = params.sign_mode
    signs = {
        "positive": (1,),
        "negative": (-1,),
        "both": (1, -1),
        "dominant": (1, -1),
    }.get(mode, (1,))
    candidates: list[Peak] = []
    for sign in signs:
        candidates.extend(_candidates(real, sigma, params, sign))
    if mode == "dominant" and candidates:
        candidates = _keep_dominant(candidates)
    candidates.sort(key=lambda peak: abs(peak.height), reverse=True)
    return candidates
