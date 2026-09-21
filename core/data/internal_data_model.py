"""Unified internal data model.

Every higher-level algorithm depends on this model only and must never scatter direct reads
of acqus/acqu2s/acqu3s around.
Corresponds to framework §4: Experiment / Dimension / Sampling / ExperimentType.
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
    """Declaration information for a single dimension (logical axis F1/F2/F3)."""

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
    """Sampling mode (uniform / NUS / uncertain)."""

    mode: SamplingMode = SamplingMode.UNIFORM
    nus_list: list[tuple[int, ...]] = field(default_factory=list)
    sampling_fraction: float = 1.0
    schedule_type: str = ""
    confidence: float = 1.0
    evidence: list[str] = field(default_factory=list)


@dataclass
class ExperimentType:
    """Experiment type (the result of multi-evidence classification)."""

    name: str = "generic_2d"
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)


@dataclass
class Experiment:
    """Unified representation of one Bruker acquisition (one dataset directory)."""

    dataset_id: str
    source_path: Path
    ndim: int = 2
    acquisition_order: list[str] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)
    sampling: Sampling = field(default_factory=Sampling)
    experiment_type: ExperimentType = field(default_factory=ExperimentType)
    acquisition_parameters: dict[str, Any] = field(default_factory=dict)
    processing_state: dict[str, Any] = field(default_factory=dict)
    segments: list[Path] = field(default_factory=list)  # multi-segment: each dataset directory

    @property
    def direct_dimension(self) -> Dimension | None:
        """Return the direct dimension (the one whose role is DIRECT)."""
        return next((d for d in self.dimensions if d.role is AxisRole.DIRECT), None)
