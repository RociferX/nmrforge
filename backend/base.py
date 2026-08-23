"""ProcessingBackend 协议。

上层（GUI/Workflow/优化器）只依赖本协议，不接触 NMRPipe 语义。
NMRPipe 语义只存在于 backend 实现与运行时（框架 §49-50）。
"""

from __future__ import annotations

from collections.abc import Callable
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

    def process(
        self,
        experiment: Experiment,
        plan: ProcessingPlan,
        *,
        params: dict[str, Any] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """按处理计划执行处理，返回输出与指标。

        params 键:extract(bool,默认 True)/ext_lo(str,默认 "10.5")/
        ext_hi(str,默认 "6.5")(G2B-006,均匀路径生效)。
        """
        ...

    def convert_to_fid(
        self,
        experiment: Experiment,
        data_dir: Any,
        progress: Callable[[str], None] | None = None,
        fid_com_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """把 Bruker 数据目录转换为 NMRPipe fid(独立阶段,不生成谱)。

        fid_com_overrides:人工途径的参数覆盖(0.2.163-补13),分段数据
        逐段应用到 fid.com,转换/切片/合并/坏点清理仍按自动路径执行。
        返回稳定键:{success, fid_path, message, logs}(API_CONTRACT §8.3)。
        """
        ...

    def reconstruct_nus(
        self,
        experiment: Experiment,
        params: dict[str, Any] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """执行 NUS 重建(params 支持 extract,默认 True)。"""
        ...
