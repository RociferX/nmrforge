"""采集模式检测：States / States-TPPI / Echo-Antiecho / QF / magnitude。

基于 FnMODE/FnTYPE/AQ_mod 判定，输出 per-dimension acquisition_mode，
供 FT 标志（-alt/-neg）、翻转与相位处理使用（框架 §6.1）。
"""

from __future__ import annotations

from core.data.internal_data_model import Experiment

_FNMODE_TO_MODE = {
    0: "States",
    1: "TPPI",
    2: "States-TPPI",
    3: "QF",
    4: "Echo-Antiecho",
    5: "States-TPPI",  # TopSpin 4 的 States-TPPI（常见于 NUS 实验）
    6: "Echo-Antiecho",
}

_FNMODE_FT_ALT = {1, 2, 5}


def ft_alt_for(fnmode: int) -> bool:
    """FnMODE 1/2/5（TPPI / States-TPPI）间接维 FT 需要 -alt。"""
    return fnmode in _FNMODE_FT_ALT


def detect_modes(experiment: Experiment) -> dict[str, str]:
    """返回 {logical_axis: acquisition_mode}。"""
    params = experiment.acquisition_parameters
    if experiment.ndim >= 3:
        mapping = {"F3": "acqus", "F2": "acqu2s", "F1": "acqu3s"}
    else:
        mapping = {"F2": "acqus", "F1": "acqu2s"}
    modes: dict[str, str] = {}
    for logical, filename in mapping.items():
        block = params.get(filename)
        if not block:
            continue
        fnmode = block.get("FnMODE", 0)
        try:
            fnmode_int = int(fnmode)
        except (TypeError, ValueError):
            fnmode_int = 0
        modes[logical] = _FNMODE_TO_MODE.get(fnmode_int, f"unknown({fnmode_int})")
    return modes
