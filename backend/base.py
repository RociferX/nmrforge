"""ProcessingBackend 协议。

上层（GUI/Workflow/优化器）只依赖本协议，不接触 NMRPipe 语义。
NMRPipe 语义只存在于 backend 实现与运行时（框架 §49-50）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from core.data.internal_data_model import Experiment
from core.planning.processing_plan import ProcessingPlan


@dataclass
class BackendCapabilities:
    """后端能力声明。"""

    provider: str = "nmrpipe"
    supports_nus: bool = True
    supports_phase_optimization: bool = False
    features: list[str] = field(default_factory=list)


@runtime_checkable
class ProcessingBackend(Protocol):
    """处理后端统一接口。"""

    capabilities: BackendCapabilities

    def health_check(self) -> dict[str, Any]:
        """返回后端健康状态（工具可用性/版本）。"""
        ...

    def process(self, experiment: Experiment, plan: ProcessingPlan) -> dict[str, Any]:
        """按处理计划执行处理，返回输出与指标。"""
        ...

    def reconstruct_nus(self, experiment: Experiment, params: dict[str, Any]) -> dict[str, Any]:
        """执行 NUS 重建。"""
        ...
