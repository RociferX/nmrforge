"""运行记录归属判定(0.2.199-补29hz-修)。

背景:补29hi 之前的版本写下的峰挑选/分析运行记录没有 inputs.data_id,
Pipeline 的 _last_run_for 原来对这类记录一概接受,多数据实验里一条老失败
会被算到**所有**数据头上;项目树却用严格匹配,两边还会显示不一致。
用户 2026-09-10 确认:老项目产物一律重新生成,不做兼容回退。
现在统一为 ProjectManager.last_run_for_data 严格判定 + 一处 refs 表。
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager  # noqa: E402
from gui.pipeline_panel import compute_data_step_statuses  # noqa: E402
from gui.pipeline_state import ALL_STEP_RUN_REFS, STEP_RUN_REFS  # noqa: E402
from gui.project_tree import ProjectTreePanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def host(qapp: QApplication):
    """控件宿主:测试结束整体销毁,避免顶层控件残留(Qt 收尾崩溃)。"""
    from qtcompat.QtWidgets import QWidget

    widget = QWidget()
    yield widget
    widget.deleteLater()
    QApplication.processEvents()


def _project(tmp_path: Path, name: str):
    manager = ProjectManager.create_project(tmp_path / name, name)
    exp = manager.create_experiment("HSQC")
    return manager, exp, manager.import_data(exp.id, "/fake/1")


def _record(manager, exp_id: str, data_id: str, ref: str, status: str) -> None:
    inputs = {"data_id": data_id} if data_id else {}
    run = manager.start_run(exp_id, workflow_ref=ref, inputs=inputs)
    manager.finish_run(run.run_id, status, message="test")


def test_strict_match_finds_exact_data_id(tmp_path: Path) -> None:
    manager, exp, d1 = _project(tmp_path, "own_exact")
    _record(manager, exp.id, d1.id, "pick_peaks", "failed")
    run = manager.last_run_for_data(exp.id, d1.id, ("pick_peaks",))
    assert run is not None and run.status == "failed"


def test_legacy_run_without_data_id_is_ignored(tmp_path: Path) -> None:
    """空 data_id 的老记录不再算到任何数据头上(不保留兼容回退)。"""
    manager, exp, d1 = _project(tmp_path, "own_legacy")
    d2 = manager.import_data(exp.id, "/fake/2")
    _record(manager, exp.id, "", "pick_peaks", "failed")

    assert manager.last_run_for_data(exp.id, d1.id, ("pick_peaks",)) is None
    for data in (d1, d2):
        statuses = compute_data_step_statuses(manager, exp.id, data.id)
        assert statuses["peaks"] != "FAILED", data.id


def test_failure_marks_only_its_own_data(tmp_path: Path) -> None:
    manager, exp, d1 = _project(tmp_path, "own_split")
    d2 = manager.import_data(exp.id, "/fake/2")
    _record(manager, exp.id, d2.id, "process", "failed")

    assert compute_data_step_statuses(manager, exp.id, d2.id)["spectrum"] == "FAILED"
    assert compute_data_step_statuses(manager, exp.id, d1.id)["spectrum"] != "FAILED"


def test_tree_and_pipeline_agree(
    tmp_path: Path, qapp: QApplication, host
) -> None:
    """项目树「失败」与 Pipeline 步骤 FAILED 必须同源。"""
    manager, exp, d1 = _project(tmp_path, "own_agree")
    d2 = manager.import_data(exp.id, "/fake/2")
    _record(manager, exp.id, d2.id, "pick_peaks", "failed")

    tree = ProjectTreePanel(manager, parent=host)
    assert tree._data_last_run_failed(exp.id, d2.id) is True
    assert tree._data_last_run_failed(exp.id, d1.id) is False
    assert compute_data_step_statuses(manager, exp.id, d2.id)["peaks"] == "FAILED"
    assert compute_data_step_statuses(manager, exp.id, d1.id)["peaks"] != "FAILED"


def test_step_refs_table_is_single_source(tmp_path: Path) -> None:
    """步骤 ref 表只有一份,且覆盖四步 + 可选 SMILE(分析已删除)。"""
    assert set(STEP_RUN_REFS) == {"fid", "spectrum", "smile", "peaks"}
    assert "phase_optimize_unified" in STEP_RUN_REFS["spectrum"]
    assert set(ALL_STEP_RUN_REFS) == {
        ref for refs in STEP_RUN_REFS.values() for ref in refs
    }
    from gui.pipeline_panel import _step_refs

    for step, refs in STEP_RUN_REFS.items():
        assert _step_refs(step) == refs
