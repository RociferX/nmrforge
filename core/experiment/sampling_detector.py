"""采样方式检测（uniform / NUS / uncertain）。

综合 nuslist、PULPROG、采样点数量、ser/ser_full、采集参数、FnMODE、实际数据长度；
metadata 矛盾时返回 uncertain（安全模式，不强行处理，框架 §6）。
"""

from __future__ import annotations

from core.data.internal_data_model import Experiment, Sampling


def detect(experiment: Experiment) -> Sampling:
    """返回采样方式（含 evidence 与 confidence）。"""
    raise NotImplementedError("Phase 1: 实现 NUS 检测")
