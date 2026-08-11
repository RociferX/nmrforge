"""处理流程控制器测试:自动化调用链 + 人工接口占位。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.project import ProjectManager
from gui.processing import ProcessingController
from workflow.engine import RunResult


def _manager_with_experiment(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    manager.add_experiment("/fake/bruker/1", title="HSQC")
    manager.save()
    return manager


class FakeAutoProcessor:
    def __init__(self, backend) -> None:
        self.backend = backend

    def run(self, experiment) -> RunResult:
        assert experiment is not None
        return RunResult(
            status="success", logs=["后端处理完成"], report=Path("spectra/x.ft2")
        )


def test_auto_run_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager_with_experiment(tmp_path)
    entry = manager.project.experiment("exp_001")
    monkeypatch.setattr(
        "gui.processing.read_dataset", lambda path: {"id": str(path)}
    )
    monkeypatch.setattr(
        "backend.factory.create_backend", lambda config: object()
    )
    monkeypatch.setattr("gui.processing.AutoProcessor", FakeAutoProcessor)
    controller = ProcessingController()
    result = controller.auto_run_sync(entry)
    assert result["status"] == "success"
    assert "后端处理完成" in result["logs"]
    assert result["experiment_id"] == "exp_001"


def test_auto_run_sync_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager_with_experiment(tmp_path)
    entry = manager.project.experiment("exp_001")
    monkeypatch.setattr(
        "gui.processing.read_dataset", lambda path: (_ for _ in ()).throw(OSError("no data"))
    )
    controller = ProcessingController()
    with pytest.raises(OSError, match="no data"):
        controller.auto_run_sync(entry)


def test_manual_interfaces_are_placeholders() -> None:
    controller = ProcessingController()
    with pytest.raises(NotImplementedError):
        controller.manual_param_table()
    with pytest.raises(NotImplementedError):
        controller.manual_script_editor()
