"""NMRPipe 后端：把 ProcessingPlan 翻译为确定性 .com 脚本并执行（Phase 1 起实现）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.base import BackendCapabilities
from core.data.internal_data_model import Experiment
from core.planning.processing_plan import ProcessingPlan


@dataclass
class NMRPipeBackend:
    """NMRPipe 实现（占位）。"""

    capabilities: BackendCapabilities = BackendCapabilities(provider="nmrpipe")

    def health_check(self) -> dict[str, Any]:
        raise NotImplementedError("Phase 1: 实现 NMRPipe 健康检查")

    def process(self, experiment: Experiment, plan: ProcessingPlan) -> dict[str, Any]:
        raise NotImplementedError("Phase 1: 实现 NMRPipe 处理")

    def reconstruct_nus(self, experiment: Experiment, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Phase 3: 实现 SMILE 重建")
