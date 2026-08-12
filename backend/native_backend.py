"""Native Python 后端：无 NMRPipe 依赖的处理路径（长期目标，框架 §49）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.base import BackendCapabilities
from core.data.internal_data_model import Experiment
from core.planning.processing_plan import ProcessingPlan


@dataclass
class NativeBackend:
    """纯 Python 处理实现（占位）。"""

    capabilities: BackendCapabilities = field(
        default_factory=lambda: BackendCapabilities(provider="native", supports_nus=False)
    )

    def health_check(self) -> dict[str, Any]:
        raise NotImplementedError("Phase 1: 实现 Native 健康检查")

    def process(self, experiment: Experiment, plan: ProcessingPlan) -> dict[str, Any]:
        raise NotImplementedError("Phase 1: 实现 Native 处理")

    def convert_to_fid(self, experiment: Experiment, data_dir: Any) -> dict[str, Any]:
        raise NotImplementedError("Native 后端暂不支持 Bruker→fid 转换")

    def reconstruct_nus(self, experiment: Experiment, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Native 后端暂不支持 NUS 重建")
