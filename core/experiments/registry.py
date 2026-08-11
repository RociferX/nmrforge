"""实验模板注册表（ExperimentTemplate，框架 §43）。

模板只提供先验/约束/期望行为，具体参数由 optimizer 决定。
支持从 presets/*.yaml 加载。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExperimentTemplate:
    name: str
    phase_sensitive: bool = True
    direct_nucleus: str = "1H"
    indirect_nuclei: list[str] = field(default_factory=list)
    expected_peak_mode: str = "absorption"
    display_orientation: str = ""
    priors: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    processing_hints: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path) -> ExperimentTemplate:
        """从 presets/*.yaml 加载模板。"""
        raise NotImplementedError("Phase 2: 实现 YAML 模板加载")

    def validate(self, experiment_type: str) -> bool:
        """检查模板是否适用于某实验类型。"""
        raise NotImplementedError("Phase 2: 实现模板匹配")


REGISTRY: dict[str, ExperimentTemplate] = {}


def register(template: ExperimentTemplate) -> None:
    REGISTRY[template.name] = template


def get(name: str) -> ExperimentTemplate | None:
    return REGISTRY.get(name)
