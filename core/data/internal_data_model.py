"""统一内部数据模型。

所有上层算法只依赖本模型，不要直接散落读取 acqus/acqu2s/acqu3s。
对应新框架 §4：Experiment / Dimension / Sampling / ExperimentType。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class SamplingMode(StrEnum):
    UNIFORM = "uniform"
    NUS = "nus"
    UNCERTAIN = "uncertain"


class AxisRole(StrEnum):
    DIRECT = "direct"
    INDIRECT = "indirect"


@dataclass
class Dimension:
    """单个维度的声明信息（逻辑轴 F1/F2/F3）。"""

    logical_axis: str
    nucleus: str
    sf: float = 0.0
    sw: float = 0.0
    o1: float = 0.0
    o1p: float = 0.0
    td: int = 0
    ft_size: int = 0
    acquisition_mode: str = ""
    axis_direction: str = "increasing"
    role: AxisRole = AxisRole.INDIRECT


@dataclass
class Sampling:
    """采样方式（uniform / NUS / uncertain）。"""

    mode: SamplingMode = SamplingMode.UNIFORM
    nus_list: list[tuple[int, ...]] = field(default_factory=list)
    sampling_fraction: float = 1.0
    schedule_type: str = ""
    confidence: float = 1.0
    evidence: list[str] = field(default_factory=list)


@dataclass
class ExperimentType:
    """实验类型（多证据分类结果）。"""

    name: str = "generic_2d"
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)


@dataclass
class Experiment:
    """一次 Bruker 采集（一个数据集目录）的统一表示。"""

    dataset_id: str
    source_path: Path
    ndim: int = 2
    acquisition_order: list[str] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)
    sampling: Sampling = field(default_factory=Sampling)
    experiment_type: ExperimentType = field(default_factory=ExperimentType)
    acquisition_parameters: dict[str, Any] = field(default_factory=dict)
    processing_state: dict[str, Any] = field(default_factory=dict)
    segments: list[Path] = field(default_factory=list)  # 多段实验：各数据集目录

    @property
    def direct_dimension(self) -> Dimension | None:
        """返回直接维（role=DIRECT 的维度）。"""
        return next((d for d in self.dimensions if d.role is AxisRole.DIRECT), None)
