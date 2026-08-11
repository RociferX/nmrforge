"""DAG 结构与早停逻辑的骨架测试。"""

from __future__ import annotations

from core.optimization.early_stopping import EarlyStopping
from core.planning.dependency_graph import PlanNode, ProcessingDag


def test_dag_add_and_invalidates() -> None:
    dag = ProcessingDag()
    dag.add_node(PlanNode(id="ft", operation="ft", params={"axis": "F3"}))
    dag.add_node(
        PlanNode(id="phase", operation="phase", depends_on=["ft"], invalidates=["baseline", "qc"])
    )
    assert set(dag.nodes) == {"ft", "phase"}
    assert dag.nodes["phase"].invalidates == ["baseline", "qc"]


def test_early_stopping() -> None:
    stopper = EarlyStopping(improvement_threshold=0.01, max_no_improvement=2)
    assert stopper.should_stop(100.0, 100.2) is False  # 0.2% < 1%，第一次无改善
    assert stopper.should_stop(100.2, 100.1) is True  # 连续两次无改善 → 停止
