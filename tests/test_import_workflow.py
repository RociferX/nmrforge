"""导入工作流测试:raw 复制 + SHA-256 + metadata/<id>.json + WorkflowRun 登记。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from core.project import ExperimentStatus, ProjectManager
from core.project.manager import sha256_file
from workflow.import_workflow import (
    IMPORT_WORKFLOW_REF,
    ImportWorkflowError,
    import_bruker_dataset,
)


def _source(bruker_dir: Path) -> Path:
    return bruker_dir / "hsqc_2d"


def test_import_copies_and_registers(tmp_path: Path, bruker_dir: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    result = import_bruker_dataset(manager, _source(bruker_dir), title="HSQC")

    assert result.experiment_id == "exp_001"
    assert result.run_id.startswith("R-")
    assert result.warnings == []
    assert manager.project is not None

    entry = manager.project.experiment("exp_001")
    assert entry is not None
    raw_dir = manager.dir_path("raw") / "exp_001"
    assert entry.source == str(raw_dir)
    assert (raw_dir / "acqus").is_file()
    assert (raw_dir / "acqu2s").is_file()

    # metadata 落盘
    metadata_path = manager.dir_path("metadata") / "exp_001.json"
    assert metadata_path.is_file()
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == "1.0"
    assert data["experiment_id"] == "exp_001"
    assert data["dataset"]["ndim"] == 2
    assert data["copied_to"] == "raw/exp_001"
    assert data["workflow_run_id"] == result.run_id
    assert "acqus" in data["manifest"]["checksums"]
    assert data["manifest"]["file_count"] == 2

    # WorkflowRun 登记
    run = manager.project.run(result.run_id)
    assert run is not None
    assert run.workflow_ref == IMPORT_WORKFLOW_REF
    assert run.status == "success"
    assert run.inputs["sha256:acqus"] == sha256_file(raw_dir / "acqus")
    assert run.inputs["source_path"] == str(_source(bruker_dir))
    assert run.outputs["raw_dir"] == "raw/exp_001"
    assert run.outputs["metadata"] == "metadata/exp_001.json"

    # 状态机推进到 imported
    assert manager.infer_status("exp_001") is ExperimentStatus.IMPORTED

    # 调用方负责 save()
    manager.save()
    reopened = ProjectManager.open_project(tmp_path / "proj")
    assert reopened.project is not None
    assert reopened.project.experiment("exp_001") is not None
    assert reopened.project.run(result.run_id) is not None


def test_import_requires_loaded_project(tmp_path: Path, bruker_dir: Path) -> None:
    manager = ProjectManager(tmp_path / "proj")
    with pytest.raises(ImportWorkflowError, match="未加载项目"):
        import_bruker_dataset(manager, _source(bruker_dir))


def test_import_rejects_non_bruker_source(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    bogus = tmp_path / "not_a_dataset"
    bogus.mkdir()
    (bogus / "readme.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(ImportWorkflowError, match="缺少 acqus"):
        import_bruker_dataset(manager, bogus)
    assert manager.project is not None
    assert manager.project.experiments == []


def test_import_no_copy_references_source(tmp_path: Path, bruker_dir: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    result = import_bruker_dataset(manager, _source(bruker_dir), copy=False)
    assert manager.project is not None
    entry = manager.project.experiment("exp_001")
    assert entry is not None
    assert entry.source == str(_source(bruker_dir))
    assert not (manager.dir_path("raw") / "exp_001").exists()
    assert result.raw_dir is None
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["copied_to"] is None
    assert manager.project.run(result.run_id) is not None
    assert manager.project.run(result.run_id).status == "success"


def test_import_source_inside_project_skips_copy(tmp_path: Path, bruker_dir: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    in_project_src = tmp_path / "proj" / "data_src"
    shutil.copytree(_source(bruker_dir), in_project_src)

    result = import_bruker_dataset(manager, in_project_src)
    assert manager.project is not None
    entry = manager.project.experiment("exp_001")
    assert entry is not None
    assert entry.source == str(in_project_src)
    assert not (manager.dir_path("raw") / "exp_001").exists()
    assert any("跳过复制" in w for w in result.warnings)
    assert manager.project.run(result.run_id).status == "success"


def test_import_segments_are_copied(tmp_path: Path, bruker_dir: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    segment = bruker_dir / "nus_2d"
    result = import_bruker_dataset(
        manager, _source(bruker_dir), segments=[segment]
    )
    assert manager.project is not None
    entry = manager.project.experiment("exp_001")
    assert entry is not None
    seg_dir = manager.dir_path("raw") / "exp_001" / "segments" / "01"
    assert seg_dir.is_dir()
    assert (seg_dir / "acqus").is_file()
    assert entry.segments == [str(seg_dir)]
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["segments"] == [str(seg_dir)]


def test_import_failure_rolls_back(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("metadata 写盘失败")

    monkeypatch.setattr(
        "workflow.import_workflow.atomic_write_json", _boom
    )
    with pytest.raises(RuntimeError, match="写盘失败"):
        import_bruker_dataset(manager, _source(bruker_dir))

    assert manager.project is not None
    assert manager.project.experiments == []  # 登记回滚
    assert not (manager.dir_path("raw") / "exp_001").exists()
    assert not (manager.dir_path("metadata") / "exp_001.json").exists()
    # 审计保留失败的 run
    assert len(manager.project.workflow_runs) == 1
    assert manager.project.workflow_runs[0].status == "failed"


def test_import_twice_creates_separate_entries(
    tmp_path: Path, bruker_dir: Path
) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    first = import_bruker_dataset(manager, _source(bruker_dir))
    second = import_bruker_dataset(manager, _source(bruker_dir))
    assert first.experiment_id == "exp_001"
    assert second.experiment_id == "exp_002"
    assert first.run_id != second.run_id
    assert manager.project is not None
    assert (manager.dir_path("raw") / "exp_001").is_dir()
    assert (manager.dir_path("raw") / "exp_002").is_dir()


def test_nus_import_records_nuslist_checksum(
    tmp_path: Path, bruker_dir: Path
) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    result = import_bruker_dataset(manager, bruker_dir / "nus_2d")
    assert "nuslist" in result.checksums
    assert manager.project is not None
    run = manager.project.run(result.run_id)
    assert run is not None
    assert run.inputs.get("sha256:nuslist") == result.checksums["nuslist"]
