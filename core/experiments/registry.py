"""实验模板注册表（ExperimentTemplate，框架 §43）。

模板只提供先验/约束/期望行为，具体参数由 optimizer 决定。
单一数据源 presets/*.yaml(0.2.111 起):from_yaml + load_presets
按显示名与文件 stem 双注册,Generic 等大小写/命名差异均可解析。
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
    # 峰符号约定:uniform=信号峰同号(HSQC/CBCA(CO)NH 等);
    # mixed=正负峰共存(HNCACB 等,13Cα/13Cβ 反相)。相位搜索用它做早约束。
    peak_sign: str = "uniform"
    # 化学位移分区符号先验:{核: {区名: {ppm: [lo, hi], sign: ±1}}}。
    # 用于 mixed 实验的 ±180° 绝对符号消歧(如 HNCACB 13C 轴 Cα/Cβ)。
    peak_sign_regions: dict[str, Any] = field(default_factory=dict)
    display_orientation: str = ""
    priors: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    processing_hints: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path) -> ExperimentTemplate:
        """从 presets/*.yaml 加载模板(单一数据源,0.2.111)。"""
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(
            name=str(data.get("name") or path.stem),
            phase_sensitive=bool(data.get("phase_sensitive", True)),
            direct_nucleus=str(data.get("direct_nucleus") or ""),
            indirect_nuclei=list(data.get("indirect_nuclei") or []),
            expected_peak_mode=data.get("expected_peak_mode") or "absorption",
            peak_sign=str(data.get("peak_sign") or "uniform"),
            peak_sign_regions=dict(data.get("peak_sign_regions") or {}),
            display_orientation=str(data.get("display_orientation") or ""),
            priors=dict(data.get("priors") or {}),
            constraints=dict(data.get("constraints") or {}),
            processing_hints=dict(data.get("processing_hints") or {}),
        )


REGISTRY: dict[str, ExperimentTemplate] = {}


def get(name: str) -> ExperimentTemplate | None:
    return REGISTRY.get(name)


def load_presets(presets_dir: Path | str | None = None) -> int:
    """从 presets/*.yaml 加载全部模板(单一数据源)。

    按模板显示名与 YAML 文件 stem 双注册(Generic 等命名差异可解析);
    返回加载数量。导入 core.experiments 时自动调用。
    """
    if presets_dir is None:
        from core.app_paths import resource_path

        presets_dir = resource_path("presets")
    REGISTRY.clear()
    count = 0
    for path in sorted(Path(presets_dir).glob("*.yaml")):
        tpl = ExperimentTemplate.from_yaml(path)
        REGISTRY[tpl.name] = tpl
        REGISTRY[path.stem] = tpl
        count += 1
    return count
