"""Processing dependency graph (DAG) and cache semantics.

Corresponds to framework §11/§35-37: the processing flow must be a dependency graph, not a
fixed script; every node declares depends_on / invalidates / input_hash / output_hash and is
reused as a cache hit while its parameters are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ui_support.i18n import tr


class NodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanNode:
    """One processing node inside the DAG."""

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


@dataclass
class ProcessingDag:
    """The processing dependency graph."""

    nodes: dict[str, PlanNode] = field(default_factory=dict)

    def add_node(self, node: PlanNode) -> None:
        self.nodes[node.id] = node

    def execution_order(self) -> list[str]:
        """Kahn topological sort; raises ValueError when the graph contains a cycle."""
        indegree = {nid: 0 for nid in self.nodes}
        dependents: dict[str, list[str]] = {nid: [] for nid in self.nodes}
        for nid, node in self.nodes.items():
            for dep in node.depends_on:
                if dep not in self.nodes:
                    raise ValueError(tr(
                        "node {p0} Depends on non-existent node "
                        "{p1}",
                        p0=nid,
                        p1=dep,
                    ))
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
            raise ValueError(tr("There is a loop in processing plan DAG"))
        return order

    def downstream(self, node_id: str) -> list[str]:
        """Return the ids of all downstream nodes (those affected through invalidates) in BFS
        order."""
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
