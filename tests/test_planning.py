"""DAG 结构与早停逻辑的骨架测试。"""

from __future__ import annotations

from pathlib import Path

from core.planning.dependency_graph import PlanNode, ProcessingDag


def test_dag_add_and_invalidates() -> None:
    dag = ProcessingDag()
    dag.add_node(PlanNode(id="ft", operation="ft", params={"axis": "F3"}))
    dag.add_node(
        PlanNode(id="phase", operation="phase", depends_on=["ft"], invalidates=["baseline", "qc"])
    )
    assert set(dag.nodes) == {"ft", "phase"}
    assert dag.nodes["phase"].invalidates == ["baseline", "qc"]



def test_select_method_includes_baseline_nodes(bruker_dir: Path) -> None:
    """默认计划每维含 baseline 节点(mode=auto,enabled=True)。"""
    from core.data.bruker_reader import read_dataset
    from core.planning.method_selector import select_method

    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    baseline_nodes = [
        n for n in plan.dag.nodes.values() if n.operation == "baseline"
    ]
    assert len(baseline_nodes) == 2
    f2 = next(
        n for n in baseline_nodes if n.params.get("axis") == "F2"
    )
    assert f2.params["mode"] == "auto"
    assert f2.params["enabled"] is True
