"""run ref 单一来源与批量参考参数精确匹配(0.2.199-补29hz-修24)。"""

from __future__ import annotations

from pathlib import Path

from core.project import ProjectManager


def _project(tmp_path: Path, name: str):
    manager = ProjectManager.create_project(tmp_path / name, name)
    exp = manager.create_experiment("HSQC")
    return manager, exp, manager.import_data(exp.id, "/fake/1")


def _record(manager, exp_id, data_id, ref, params=None, status="success") -> None:
    inputs = {"data_id": data_id} if data_id else {}
    run = manager.start_run(
        exp_id, workflow_ref=ref, inputs=inputs, params=dict(params or {})
    )
    manager.finish_run(run.run_id, status, message="test")


def test_step_run_refs_lives_in_core() -> None:
    """表本体在 core(workflow 层也要用),gui.pipeline_state 只 re-export。"""
    import core.project.run_refs as run_refs
    import gui.pipeline_state as pipeline_state

    assert pipeline_state.STEP_RUN_REFS is run_refs.STEP_RUN_REFS
    assert pipeline_state.ALL_STEP_RUN_REFS is run_refs.ALL_STEP_RUN_REFS
    assert set(run_refs.MANUAL_SPECTRUM_RUN_REFS) <= set(
        run_refs.STEP_RUN_REFS["spectrum"]
    )
    assert "phase_optimize_unified" in run_refs.STEP_RUN_REFS["spectrum"]


def test_batch_reference_params_exact_match(tmp_path: Path) -> None:
    """批量参考参数:精确 ref + 严格 data_id(子串同名 ref/无归属记录都不算)。"""
    from workflow.batch import _reference_spectrum_params

    manager, exp, d1 = _project(tmp_path, "batch_ref")
    d2 = manager.import_data(exp.id, "/fake/2")
    _record(manager, exp.id, d1.id, "process", params={"nthread": 1})
    # 名字含 process 的其它 ref 更晚:旧实现是子串匹配,会被误当谱图运行
    _record(manager, exp.id, d1.id, "process_preview", params={"nthread": 99})
    # 人工路径的谱图 ref 同样算参考(与 STEP_RUN_REFS 同源)
    _record(manager, exp.id, d1.id, "manual_process", params={"nthread": 6})
    # 别的数据 + 非谱图链 ref(smile_optimize)不参与
    _record(manager, exp.id, d2.id, "smile_optimize", params={"nthread": 8})
    # 无 data_id 的老记录:严格归属下不参与任何数据
    _record(manager, exp.id, "", "process", params={"nthread": 7})

    assert _reference_spectrum_params(manager, exp.id, d1.id) == {"nthread": 6}
    assert _reference_spectrum_params(manager, exp.id, d2.id) == {}
