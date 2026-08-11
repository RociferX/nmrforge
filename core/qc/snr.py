"""SNR 指标：peak_height/sigma 与 peak_volume/noise；区分 global / median / top（框架 §18）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SnrMetrics:
    global_snr: float = 0.0
    median_peak_snr: float = 0.0
    top_peak_snr: float = 0.0
