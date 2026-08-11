"""采集模式检测：States / States-TPPI / Echo-Antiecho / QF / magnitude。

基于 FnMODE/FnTYPE/AQ_mod 判定，输出 per-dimension acquisition_mode，
供 FT 标志（-alt/-neg）、翻转与相位处理使用。
"""

from __future__ import annotations

from core.data.internal_data_model import Experiment


def detect_modes(experiment: Experiment) -> dict[str, str]:
    """返回 {logical_axis: acquisition_mode}。"""
    raise NotImplementedError("Phase 1: 实现采集模式检测")
