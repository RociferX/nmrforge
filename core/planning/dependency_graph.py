"""处理流程依赖图（DAG）与缓存语义。

对应框架 §11/§35-37：处理流程必须是依赖图而不是固定脚本；
每个节点声明 depends_on / invalidates / input_hash / output_hash，
参数未变时直接 cache hit 复用。
"""

from __future__ import annotations

import hashlib
import json
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
    message: str = ""


def make_cache_key(operation: str, params: dict[str, Any], input_hash: str = "") -> str:
    """根据操作 + 参数 + 输入哈希生成缓存键。"""
    payload = json.dumps([operation, params], sort_keys=True, default=str) + "|" + input_hash
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class ProcessingDag:
    """处理流程依赖图。"""

    nodes: dict[str, PlanNode] = field(default_factory=dict)

    def add_node(self, node: PlanNode) -> None:
        self.nodes[node.id] = node

    def execution_order(self) -> list[str]:
        """Kahn 拓扑排序；存在环时抛 ValueError。"""
        indegree = {nid: 0 for nid in self.nodes}
        dependents: dict[str, list[str]] = {nid: [] for nid in self.nodes}
        for nid, node in self.nodes.items():
            for dep in node.depends_on:
                if dep not in self.nodes:
                    raise ValueError(f"节点 {nid} 依赖不存在的节点 {dep}")
                dependents[dep].append(nid)
                indegree[nid] += 1
        queue = sorted(nid for nid, deg in indegree.items() if deg == 0)
        order: list[str] = []
        while queue:
            nid = queue.pop(0)
            order.append(nid)
            for dep in dependents[nid]:
                indegree[dep] -= 1
                if indegree[dep] == 0:
                    queue.append(dep)
                    queue.sort()
        if len(order) != len(self.nodes):
            raise ValueError("处理计划 DAG 存在环")
        return order

    def downstream(self, node_id: str) -> list[str]:
        """返回该节点下游（受 invalidates 影响）的所有节点 id（BFS）。"""
        node = self.nodes.get(node_id)
        if node is None:
            return []
        seen: list[str] = []
        stack = list(node.invalidates)
        while stack:
            current = stack.pop(0)
            if current in seen or current not in self.nodes:
                continue
            seen.append(current)
            stack.extend(self.nodes[current].invalidates)
        return seen
