"""维度映射：AcquisitionAxis → ProcessingAxis → DisplayAxis。

软件内部不把 F1/F2/F3 等同于显示 X/Y/Z；自动生成 AxisTransform
（transpose/reverse/sign/conjugation）并记录 why + confidence（框架 §9-10）。
Phase 1：采集轴→处理轴按 Bruker 标准顺序；显示轴按实验模板 orientation。
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
    display_axis: str = ""  # X/Y/Z
    nucleus: str = ""
    transforms: list[TransformOp] = field(default_factory=list)
    why: str = ""
    confidence: float = 1.0


# 常见实验的显示方向（模板 orientation：X/Y/Z 对应的核）
_ORIENTATIONS: dict[str, list[str]] = {
    "HSQC": ["1H", "15N"],
    "HMQC": ["1H", "15N"],
    "HNCO": ["13C", "15N", "1H"],
    "HNCA": ["13C", "15N", "1H"],
    "HN(CO)CA": ["13C", "15N", "1H"],
    "HNCACB": ["13C", "15N", "1H"],
    "CBCA(CO)NH": ["13C", "15N", "1H"],
    "CBCANH": ["13C", "15N", "1H"],
    "HNHA": ["1H", "15N", "1H"],
    "COSY": ["1H", "1H"],
    "TOCSY": ["1H", "1H"],
    "NOESY": ["1H", "1H"],
    "ROESY": ["1H", "1H"],
    "HMBC": ["13C", "1H"],
}

_DISPLAY_AXES = ["X", "Y", "Z"]


def _assign_display_axes(
    experiment: Experiment, mappings: list[AxisMapping]
) -> None:
    orientation = _ORIENTATIONS.get(experiment.experiment_type.name)
    used_display: set[str] = set()
    used_processing: set[str] = set()
    if orientation:
        for index, nucleus in enumerate(orientation):
            for m in mappings:
                if m.nucleus == nucleus and m.processing_axis not in used_processing:
                    m.display_axis = _DISPLAY_AXES[index]
                    used_display.add(m.display_axis)
                    used_processing.add(m.processing_axis)
                    m.why += (
                        f"；显示轴：{experiment.experiment_type.name} 模板 "
                        f"orientation={orientation}"
                    )
                    m.confidence = max(m.confidence, 0.95)
                    break
    remaining = [a for a in _DISPLAY_AXES if a not in used_display]
    for m in mappings:
        if not m.display_axis and remaining:
            m.display_axis = remaining.pop(0)
            m.why += "；显示轴：兜底（直接维优先 X）"


def map_dimensions(experiment: Experiment) -> list[AxisMapping]:
    """生成采集轴 → 处理轴 → 显示轴 的完整映射。"""
    if experiment.ndim >= 3:
        plan = [("F3", "acqus"), ("F2", "acqu2s"), ("F1", "acqu3s")]
    elif experiment.ndim == 2:
        plan = [("F2", "acqus"), ("F1", "acqu2s")]
    else:
        plan = [("F2", "acqus")]

    file_axis = {"acqus": 1, "acqu2s": 2, "acqu3s": 3}
    mappings: list[AxisMapping] = []
    for logical, filename in plan:
        block = experiment.acquisition_parameters.get(filename, {})
        mappings.append(
            AxisMapping(
                acquisition_axis=file_axis[filename],
                processing_axis=logical,
                nucleus=str(block.get("NUC1", "")),
                transforms=[],
                why="采集轴→处理轴：Bruker 标准顺序（acqus=直接维，acqu2s/acqu3s=间接维）",
                confidence=0.9,
            )
        )
    _assign_display_axes(experiment, mappings)
    return mappings
