"""维度映射：AcquisitionAxis → ProcessingAxis → DisplayAxis。

软件内部不把 F1/F2/F3 等同于显示 X/Y/Z；自动生成 AxisTransform
（transpose/reverse/sign/conjugation）并记录 why + confidence（框架 §9-10）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from core.data.internal_data_model import Experiment


class AxisKind(str, Enum):
    ACQUISITION = "acquisition"
    PROCESSING = "processing"
    DISPLAY = "display"


class TransformOp(str, Enum):
    TRANSPOSE = "transpose"
    REVERSE = "reverse"
    SIGN = "sign"
    CONJUGATION = "conjugation"


@dataclass
class AxisMapping:
    """单维映射记录。"""

    acquisition_axis: int
    processing_axis: str  # F1/F2/F3
    display_axis: str  # X/Y/Z
    nucleus: str
    transforms: list[TransformOp] = field(default_factory=list)
    why: str = ""
    confidence: float = 1.0


def map_dimensions(experiment: Experiment) -> list[AxisMapping]:
    """生成采集轴 → 处理轴 → 显示轴 的完整映射。"""
    raise NotImplementedError("Phase 1: 实现维度映射")
