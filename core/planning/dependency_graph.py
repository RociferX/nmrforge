"""处理流程依赖图（DAG）与缓存语义。

对应框架 §11/§35-37：处理流程必须是依赖图而不是固定脚本；
每个节点声明 depends_on / invalidates / input_hash / output_hash，
参数未变时直接 cache hit 复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanNode:
    """DAG 中的单个处理节点。"""

    id: str
    operation: str  # apodization / zero_fill / ft / phase / baseline / nus_reconstruct ...
    params: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    invalidates: list[str] = field(default_factory=list)
    input_hash: str = ""
    output_hash: str = ""
    cache_key: str = ""
    execution_time_s: float = 0.0
    quality_metrics: dict[str, float] = field(default_factory=dict)
    status: NodeStatus = NodeStatus.PENDING


@dataclass
class ProcessingDag:
    """处理流程依赖图。"""

    nodes: dict[str, PlanNode] = field(default_factory=dict)

    def add_node(self, node: PlanNode) -> None:
        self.nodes[node.id] = node

    def execution_order(self) -> list[str]:
        """拓扑排序（Phase 1 落地）。"""
        raise NotImplementedError("Phase 1: 实现 DAG 拓扑排序")

    def downstream(self, node_id: str) -> list[str]:
        """返回该节点下游（受其 invalidates 影响）的所有节点 id。"""
        raise NotImplementedError("Phase 1: 实现下游节点枚举")
