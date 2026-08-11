"""Bruker 数据读取：定位数据集、读取 ser/fid、结合参数生成内部数据模型。"""

from __future__ import annotations

from pathlib import Path

from core.data.internal_data_model import Experiment


def read_dataset(path: Path) -> Experiment:
    """读取一个 Bruker 数据集目录并生成 Experiment（不做语义判断）。"""
    raise NotImplementedError("Phase 1: 实现 Bruker 数据集读取")
