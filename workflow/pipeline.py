"""管线执行器：按 DAG 拓扑顺序执行节点，支持缓存命中与失败隔离
（SUCCESS/WARNING/FAILED/SKIPPED，框架 §48）。
"""

from __future__ import annotations

from typing import Any

from core.planning.dependency_graph import ProcessingDag


class PipelineRunner:
    def __init__(self, dag: ProcessingDag, cache: dict[str, Any] | None = None) -> None:
        self.dag = dag
        self.cache = cache if cache is not None else {}

    def run(self) -> dict[str, Any]:
        """执行 DAG，返回 {node_id: output}。"""
        raise NotImplementedError("Phase 1: 实现管线执行")
