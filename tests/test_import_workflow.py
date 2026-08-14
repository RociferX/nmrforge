"""导入工作流测试:Data 层级(raw 链接 + SHA-256 + metadata + WorkflowRun 登记)。"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from core.project import ExperimentStatus, ProjectManager
from core.project.manager import sha256_file
from workflow.import_workflow import (
    IMPORT_WORKFLOW_REF,
    ImportWorkflowError,
    import_bruker_dataset,
    import_data,
)


def _source(bruker_dir: Path) -> Path:
    return bruker_dir / "hsqc_2d"


def test_import_links_and_registers(tmp_path: Path, bruker_dir: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    result = import_bruker_dataset(manager, _source(bruker_dir), title="HSQC")

    assert result.experiment_id == "exp_001"
    assert result.data_id == "d_001"
    assert result.run_id.startswith("R-")
    assert result.warnings == []
    assert manager.project is not None

    entry = manager.project.experiment("exp_001")
    assert entry is not None
    assert len(entry.data) == 1
    data = entry.data[0]
    raw_dir = manager.data_dir("exp_001", "d_001", "raw")
    assert data.raw_dir == raw_dir.relative_to(manager.root).as_posix()
    assert data.source == str(_source(bruker_dir))
    assert (raw_dir / "acqus").is_file()
    assert (raw_dir / "acqu2s").is_file()
    # G2B-009:raw 只读文件为硬链接(同卷),不是复制
    assert not (raw_dir / "acqus").is_symlink()
    assert os.path.samefile(_source(bruker_dir) / "acqus", raw_dir / "acqus")
    assert os.path.samefile(_source(bruker_dir) / "acqu2s", raw_dir / "acqu2s")
    # 兼容只读属性指向 data[0]
    assert entry.source == data.raw_dir

    # metadata 落盘(schema 1.3:<exp>/<data>/metadata.json)
    metadata_path = manager.data_metadata_path("exp_001", "d_001")
    assert metadata_path.is_file()
    meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert meta["schema_version"] == "1.0"
    assert meta["experiment_id"] == "exp_001"
    assert meta["data_id"] == "d_001"
    assert meta["dataset"]["ndim"] == 2
    assert meta["copied_to"] == "exp_001/d_001/raw"
    assert meta["workflow_run_id"] == result.run_id
    assert "acqus" in meta["manifest"]["checksums"]
    assert meta["manifest"]["file_count"] == 2
    # G2B-009:metadata 记录链接统计(可查证导入方式)
    assert meta["link_stats"]["hardlink"] == 2
    assert meta["link_stats"]["copy"] == 0
    assert meta["link_stats"]["writable"] == 0

    # WorkflowRun 登记
    run = manager.project.run(result.run_id)
    assert run is not None
    assert run.workflow_ref == IMPORT_WORKFLOW_REF
    assert run.status == "success"
    assert run.inputs["sha256:acqus"] == sha256_file(raw_dir / "acqus")
    assert run.inputs["source_path"] == str(_source(bruker_dir))
    assert run.outputs["raw_dir"] == "exp_001/d_001/raw"
    assert run.outputs["metadata"] == "exp_001/d_001/metadata.json"

    # 状态机推进到 imported
    assert manager.infer_status("exp_001") is ExperimentStatus.IMPORTED

    # 调用方负责 save()
    manager.save()
    reopened = ProjectManager.open_project(tmp_path / "proj")
    assert reopened.project is not None
    assert reopened.project.experiment("exp_001").data[0].id == "d_001"
    assert reopened.project.run(result.run_id) is not None


def test_import_data_into_existing_experiment(
    tmp_path: Path, bruker_dir: Path
) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment(title="骨架")
    assert entry.status == ExperimentStatus.REGISTERED.value
    assert entry.data == []

    first = import_data(manager, entry.id, _source(bruker_dir))
    second = import_data(manager, entry.id, bruker_dir / "nus_2d")
    assert first.data_id == "d_001"
    assert second.data_id == "d_002"
    assert [d.id for d in entry.data] == ["d_001", "d_002"]
    assert entry.status == ExperimentStatus.IMPORTED.value
    assert manager.data_dir(entry.id, "d_001", "raw").is_dir()
    assert manager.data_dir(entry.id, "d_002", "raw").is_dir()
    assert manager.data_metadata_path(entry.id, "d_002").is_file()


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
    data = entry.data[0]
    assert data.raw_dir == ""
    assert data.source == str(_source(bruker_dir))
    assert not manager.data_dir("exp_001", "d_001", "raw").exists()
    assert result.raw_dir is None
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["copied_to"] is None
    assert manager.project.run(result.run_id) is not None
    assert manager.project.run(result.run_id).status == "success"


def test_import_source_inside_project_skips_copy(
    tmp_path: Path, bruker_dir: Path
) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    in_project_src = tmp_path / "proj" / "data_src"
    shutil.copytree(_source(bruker_dir), in_project_src)

    result = import_bruker_dataset(manager, in_project_src)
    assert manager.project is not None
    entry = manager.project.experiment("exp_001")
    assert entry is not None
    assert entry.data[0].raw_dir == ""
    assert not manager.data_dir("exp_001", "d_001", "raw").exists()
    assert any("跳过复制" in w for w in result.warnings)
    assert manager.project.run(result.run_id).status == "success"


def test_import_segments_are_linked(tmp_path: Path, bruker_dir: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    segment = bruker_dir / "nus_2d"
    result = import_bruker_dataset(
        manager, _source(bruker_dir), segments=[segment]
    )
    assert manager.project is not None
    entry = manager.project.experiment("exp_001")
    assert entry is not None
    seg_dir = manager.data_dir("exp_001", "d_001", "raw") / "segments" / "01"
    assert seg_dir.is_dir()
    assert (seg_dir / "acqus").is_file()
    assert os.path.samefile(segment / "acqus", seg_dir / "acqus")
    assert entry.data[0].segments == [str(seg_dir)]
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["segments"] == [str(seg_dir)]


def test_import_link_failure_falls_back_to_copy(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """硬链接/符号链接均失败时逐项回退复制,仍成功并记录 warnings。"""

    def _no_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("link disabled for test")

    monkeypatch.setattr("workflow.import_workflow.os.link", _no_link)
    monkeypatch.setattr("workflow.import_workflow.os.symlink", _no_link)
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    result = import_bruker_dataset(manager, _source(bruker_dir))
    assert manager.project is not None
    raw_dir = manager.data_dir("exp_001", "d_001", "raw")
    assert (raw_dir / "acqus").is_file()
    # 回退复制:文件为独立副本,不再与源 samefile
    assert not os.path.samefile(_source(bruker_dir) / "acqus", raw_dir / "acqus")
    assert any("回退复制" in w for w in result.warnings)
    assert manager.project.run(result.run_id).status == "success"


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
    assert manager.project.experiments == []  # 便捷入口回滚空白实验
    assert not manager.data_dir("exp_001", "d_001", "raw").exists()
    assert not manager.data_metadata_path("exp_001", "d_001").exists()
    # 审计保留失败的 run
    assert len(manager.project.workflow_runs) == 1
    assert manager.project.workflow_runs[0].status == "failed"


def test_import_data_failure_keeps_experiment(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """import_data 失败只回滚数据条目,不清除空白实验。"""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment(title="保留")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("metadata 写盘失败")

    monkeypatch.setattr(
        "workflow.import_workflow.atomic_write_json", _boom
    )
    with pytest.raises(RuntimeError, match="写盘失败"):
        import_data(manager, entry.id, _source(bruker_dir))
    assert manager.project.experiment(entry.id) is entry
    assert entry.data == []


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
    assert manager.data_dir("exp_001", "d_001", "raw").is_dir()
    assert manager.data_dir("exp_002", "d_001", "raw").is_dir()


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


def test_import_fid_com_is_copied_not_linked(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """fid.com 是后端可写文件:实体复制不链接(改动不污染源)。"""
    src = tmp_path / "src_with_fid"
    shutil.copytree(_source(bruker_dir), src)
    fid_com = src / "fid.com"
    fid_com.write_text("#!/bin/csh\n# user fid.com\n", encoding="utf-8")

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    result = import_bruker_dataset(manager, src)
    assert manager.project is not None
    raw_dir = manager.data_dir("exp_001", "d_001", "raw")
    assert (raw_dir / "fid.com").is_file()
    # 可写文件不是链接:与源不同文件,改写不污染源
    assert not os.path.samefile(src / "fid.com", raw_dir / "fid.com")
    (raw_dir / "fid.com").write_text("#!/bin/csh\n# patched\n", encoding="utf-8")
    assert fid_com.read_text(encoding="utf-8") == "#!/bin/csh\n# user fid.com\n"
    # 其余只读文件仍是硬链接
    assert os.path.samefile(src / "acqus", raw_dir / "acqus")
    run = manager.project.run(result.run_id)
    assert run is not None
    assert run.params["link_stats"]["writable"] == 1
    assert run.params["link_stats"]["hardlink"] > 0


def test_import_records_link_stats(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """WorkflowRun params 记录 link_stats(硬链接/符号链接/复制/可写)。"""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    result = import_bruker_dataset(manager, _source(bruker_dir))
    assert manager.project is not None
    run = manager.project.run(result.run_id)
    assert run is not None
    stats = run.params["link_stats"]
    assert set(stats) == {"hardlink", "symlink", "copy", "writable"}
    assert stats["hardlink"] > 0
    assert stats["copy"] == 0
    assert stats["writable"] == 0
    assert result.warnings == []
