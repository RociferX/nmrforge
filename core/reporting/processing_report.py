"""处理报告：数据来源 / 实验识别 / 参数 / 版本 / QC / 警告 / 耗时 / 缓存命中 / 优化轨迹。

可导出 report.json / report.html / processing_recipe.json（论文可重复性，框架 §54-55）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProcessingReport:
    dataset_id: str = ""
    experiment_type: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    provenance: list[Any] = field(default_factory=list)
    software_version: str = ""
    backend_versions: dict[str, str] = field(default_factory=dict)
    qc: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    execution_time_s: float = 0.0
    cache_hits: int = 0
    optimizer_trajectory: list[dict[str, Any]] = field(default_factory=list)

    def to_recipe(self) -> dict[str, Any]:
        """导出 processing_recipe.json 结构（同数据 + 同参数 = 同结果）。"""
        raise NotImplementedError("Phase 4: 实现 recipe 导出")

    def to_html(self, path: str) -> None:
        """导出 HTML 报告。"""
        raise NotImplementedError("Phase 4: 实现 HTML 报告导出")
