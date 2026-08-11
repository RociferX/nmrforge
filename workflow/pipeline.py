"""管线执行器：按 DAG 拓扑顺序执行节点，支持缓存命中与失败隔离
（SUCCESS/WARNING/FAILED/SKIPPED，框架 §48）。
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import numpy as np

from core.planning.dependency_graph import NodeStatus, ProcessingDag, make_cache_key
from workflow.operations import DEFAULT_OPERATIONS


def _quick_hash(data: Any) -> str:
    """轻量内容哈希（形状 + dtype + 实部和，用于缓存键）。"""
    if data is None:
        return "none"
    arr = np.asarray(data)
    payload = f"{arr.shape}:{arr.dtype}:{float(np.real(np.sum(arr)))}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class PipelineRunner:
    def __init__(
        self,
        dag: ProcessingDag,
        operations: dict[str, Any] | None = None,
        cache: dict[str, Any] | None = None,
        input_data: Any = None,
    ) -> None:
        self.dag = dag
        self.operations = operations or DEFAULT_OPERATIONS
        self.cache = cache if cache is not None else {}
        self.input_data = input_data
        self.cache_hits = 0

    def run(self) -> dict[str, Any]:
        """执行 DAG，返回 {node_id: output}；失败节点下游自动跳过。"""
        outputs: dict[str, Any] = {}
        for node_id in self.dag.execution_order():
            node = self.dag.nodes[node_id]
            if any(self.dag.nodes[d].status is NodeStatus.FAILED for d in node.depends_on):
                node.status = NodeStatus.SKIPPED
                node.message = "上游失败，跳过"
                continue
            input_data = outputs[node.depends_on[-1]] if node.depends_on else self.input_data
            node.input_hash = _quick_hash(input_data)
            node.cache_key = make_cache_key(node.operation, node.params, node.input_hash)
            if node.cache_key in self.cache:
                outputs[node_id] = self.cache[node.cache_key]
                node.status = NodeStatus.SUCCESS
                node.message = "cache hit"
                self.cache_hits += 1
                continue
            fn = self.operations.get(node.operation)
            if fn is None:
                node.status = NodeStatus.FAILED
                node.message = f"未注册操作 {node.operation}"
                continue
            try:
                start = time.perf_counter()
                output = fn(input_data, node.params)
                node.execution_time_s = time.perf_counter() - start
                node.status = NodeStatus.SUCCESS
                node.output_hash = _quick_hash(output)
                node.message = ""
                outputs[node_id] = output
                self.cache[node.cache_key] = output
            except Exception as exc:  # noqa: BLE001
                node.status = NodeStatus.FAILED
                node.message = str(exc)
        return outputs
