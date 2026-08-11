"""DAG 执行 / 缓存 / 失败隔离测试。"""

from __future__ import annotations

import numpy as np
import pytest

from core.planning.dependency_graph import PlanNode, ProcessingDag
from workflow.pipeline import PipelineRunner


def _double(data: object, params: dict) -> np.ndarray:
    return np.asarray(data) * 2


def _boom(data: object, params: dict) -> np.ndarray:
    raise RuntimeError("boom")


def test_topological_order() -> None:
    dag = ProcessingDag()
    dag.add_node(PlanNode(id="a", operation="double"))
    dag.add_node(PlanNode(id="b", operation="double", depends_on=["a"]))
    dag.add_node(PlanNode(id="c", operation="double", depends_on=["a"]))
    dag.add_node(PlanNode(id="d", operation="double", depends_on=["b", "c"]))
    order = dag.execution_order()
    assert order.index("a") < order.index("b") < order.index("d")
    assert order.index("a") < order.index("c") < order.index("d")


def test_cycle_detected() -> None:
    dag = ProcessingDag()
    dag.add_node(PlanNode(id="a", operation="double", depends_on=["b"]))
    dag.add_node(PlanNode(id="b", operation="double", depends_on=["a"]))
    with pytest.raises(ValueError):
        dag.execution_order()


def test_downstream_via_invalidates() -> None:
    dag = ProcessingDag()
    dag.add_node(PlanNode(id="ft", operation="double"))
    dag.add_node(
        PlanNode(id="phase", operation="double", depends_on=["ft"], invalidates=["baseline", "qc"])
    )
    dag.add_node(PlanNode(id="baseline", operation="double", depends_on=["phase"]))
    dag.add_node(PlanNode(id="qc", operation="double", depends_on=["baseline"]))
    assert dag.downstream("phase") == ["baseline", "qc"]


def test_cache_hit() -> None:
    calls = {"n": 0}

    def counting(data: object, params: dict) -> np.ndarray:
        calls["n"] += 1
        return np.asarray(data) + 1

    dag = ProcessingDag()
    dag.add_node(PlanNode(id="a", operation="count"))
    runner = PipelineRunner(dag, operations={"count": counting}, input_data=np.zeros(4))
    out1 = runner.run()
    out2 = runner.run()
    assert calls["n"] == 1
    assert runner.cache_hits == 1
    assert np.array_equal(out1["a"], out2["a"])


def test_failure_isolation() -> None:
    dag = ProcessingDag()
    dag.add_node(PlanNode(id="a", operation="boom"))
    dag.add_node(PlanNode(id="b", operation="double", depends_on=["a"]))
    runner = PipelineRunner(
        dag, operations={"boom": _boom, "double": _double}, input_data=np.zeros(2)
    )
    outputs = runner.run()
    assert outputs == {}
    assert dag.nodes["a"].status.value == "failed"
    assert dag.nodes["b"].status.value == "skipped"
