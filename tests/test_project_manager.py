"""项目管理模块测试:生命周期/实验/样本/历史/运行记录/快照/模板/最近项目。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.project import (
    DEFAULT_DIRECTORIES,
    ExperimentStatus,
    JsonRecentProjectsStore,
    ProjectError,
    ProjectManager,
    WorkflowRun,
)
from core.project.manager import atomic_write_json, sha256_file


def test_create_project_layout(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    manager = ProjectManager.create_project(root, "demo", protein_name="GB1")

    # schema 1.3(§9.2):不预建扁平目录模板,文件系统即层级
    for rel in DEFAULT_DIRECTORIES:
        assert not (root / rel).exists(), rel
    project_file = root / "project.json"
    assert project_file.is_file()
    data = json.loads(project_file.read_text(encoding="utf-8"))
    assert data["schema_version"] == "1.3"
    assert data["name"] == "demo"
    assert data["protein"]["name"] == "GB1"
    assert data["created"] == data["updated"]
    assert manager.project is not None
    assert len(manager.project.processing_history) == 1
    assert manager.project.processing_history[0].action == "project_created"


def test_create_project_refuses_existing(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    ProjectManager.create_project(root, "a")
    with pytest.raises(ProjectError, match="已存在项目"):
        ProjectManager.create_project(root, "b")


def test_open_and_save_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    ProjectManager.create_project(root, "demo")
    opened = ProjectManager.open_project(root)
    assert opened.project is not None
    assert opened.project.name == "demo"
    assert opened.root == root.resolve()
    opened.project.experiment_type = "15N_HSQC"
    opened.save()
    reopened = ProjectManager.open_project(root)
    assert reopened.project is not None
    assert reopened.project.experiment_type == "15N_HSQC"
    assert reopened.project.updated >= reopened.project.created


def test_open_project_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="未找到 project.json"):
        ProjectManager.open_project(tmp_path / "nope")


def test_open_project_invalid_schema(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "project.json").write_text('{"name": 1}', encoding="utf-8")
    with pytest.raises(ProjectError, match="结构无效"):
        ProjectManager.open_project(root)


def test_save_without_project_raises(tmp_path: Path) -> None:
    manager = ProjectManager(tmp_path)
    with pytest.raises(ProjectError, match="未加载项目"):
        manager.save()


def test_experiment_crud_and_sequencing(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    e1 = manager.add_experiment("/sampleD", title="HSQC")
    e2 = manager.add_experiment("/sampleE", title="HNCACB")
    assert e1.id == "exp_001"
    assert e2.id == "exp_002"
    assert e1.status == ExperimentStatus.IMPORTED.value
    assert e1.imported_at
    assert manager.project is not None
    assert manager.project.experiment("exp_001") is e1

    manager.rename_experiment("exp_001", "15N HSQC")
    assert manager.project.experiment("exp_001").title == "15N HSQC"
    manager.set_experiment_notes("exp_002", "骨架实验")
    assert manager.project.experiment("exp_002").notes == "骨架实验"
    # 审计历史只追加
    actions = [h.action for h in manager.project.processing_history]
    assert actions == [
        "project_created",
        "experiment_created", "data_imported",
        "experiment_created", "data_imported",
        "experiment_renamed", "experiment_notes",
    ]


def test_add_experiment_unknown_sample_raises(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    with pytest.raises(ProjectError, match="样本不存在"):
        manager.add_experiment("/sampleD", sample_id="S999")


def test_infer_status_stages(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    exp = manager.add_experiment("/sampleD")
    assert manager.infer_status(exp.id) is ExperimentStatus.REGISTERED

    def _legacy_write(path: Path, content: str = "x") -> None:
        """模拟旧扁平布局产物(目录需显式创建,新项目不预建)。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    _legacy_write(manager.dir_path("metadata") / f"{exp.id}.json", "{}")
    assert manager.infer_status(exp.id) is ExperimentStatus.IMPORTED

    _legacy_write(manager.dir_path("spectra") / f"{exp.id}.ft2")
    assert manager.infer_status(exp.id) is ExperimentStatus.PROCESSED

    _legacy_write(manager.dir_path("peaks") / f"{exp.id}.csv", "")
    assert manager.infer_status(exp.id) is ExperimentStatus.PICKED

    _legacy_write(manager.dir_path("report") / f"{exp.id}.pdf")
    assert manager.infer_status(exp.id) is ExperimentStatus.ANALYZED


def test_delete_experiment_removes_artifacts_keeps_runs(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    exp = manager.add_experiment("/sampleD")
    (manager.dir_path("raw") / exp.id).mkdir(parents=True)
    (manager.dir_path("raw") / exp.id / "fid").write_bytes(b"fid")
    spectra_path = manager.dir_path("spectra") / f"{exp.id}.ft2"
    spectra_path.parent.mkdir(parents=True, exist_ok=True)
    spectra_path.write_bytes(b"ft2")
    (manager.dir_path("processing") / exp.id / "log.txt").parent.mkdir(parents=True)
    (manager.dir_path("processing") / exp.id / "log.txt").write_text("log", encoding="utf-8")

    run = manager.start_run(exp.id, workflow_ref="hsqc_standard")
    manager.finish_run(run.run_id, "success", outputs={"spectrum": "spectra/x.ft2"})

    manager.delete_experiment(exp.id)
    assert manager.project is not None
    assert manager.project.experiment(exp.id) is None
    assert not (manager.dir_path("raw") / exp.id).exists()
    assert not (manager.dir_path("spectra") / f"{exp.id}.ft2").exists()
    assert not (manager.dir_path("processing") / exp.id).exists()
    assert manager.project.run(run.run_id) is run  # 审计保留
    assert any(
        h.action == "experiment_deleted" for h in manager.project.processing_history
    )


def test_delete_experiment_outside_root_refused(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    exp = manager.add_experiment("/sampleD")
    # 篡改目录映射指向项目外
    outside = tmp_path / "outside"
    outside.mkdir()
    assert manager.project is not None
    manager.project.directories["raw"] = str(outside)
    with pytest.raises(ProjectError, match="超出项目目录"):
        manager.delete_experiment(exp.id)


def test_sample_crud_and_reference_protection(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    s1 = manager.add_sample(name="sample A", protein_name="GB1")
    s2 = manager.add_sample()
    assert s1.sample_id == "S001"
    assert s2.sample_id == "S002"
    assert s1.created

    manager.delete_sample(s2.sample_id)  # 无引用可删
    assert manager.project is not None
    assert manager.project.sample("S002") is None

    manager.add_experiment("/sampleD", sample_id=s1.sample_id)
    with pytest.raises(ProjectError, match="被实验引用"):
        manager.delete_sample(s1.sample_id)
    with pytest.raises(ProjectError, match="样本不存在"):
        manager.delete_sample("S999")


def test_workflow_run_lifecycle_and_sequencing(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    exp = manager.add_experiment("/sampleD")
    r1 = manager.start_run(exp.id, workflow_ref="hsqc_standard", params={"zero_fill": 2})
    r2 = manager.start_run(exp.id, workflow_ref="hsqc_standard")
    pattern = re.compile(r"^R-\d{8}-(\d{3})$")
    assert pattern.match(r1.run_id)
    assert pattern.match(r2.run_id)
    assert r1.run_id < r2.run_id
    assert r1.status == "running"
    assert r1.sample_id == ""  # 实验无样本
    assert r1.params == {"zero_fill": 2}

    finished = manager.finish_run(r1.run_id, "success", outputs={"ft2": "spectra/x.ft2"})
    assert finished.status == "success"
    assert finished.finished_at
    assert finished.outputs == {"ft2": "spectra/x.ft2"}
    assert manager.project is not None
    assert any(h.action == "run_finished" for h in manager.project.processing_history)


def test_start_run_unknown_experiment_raises(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    with pytest.raises(ProjectError, match="实验不存在"):
        manager.start_run("exp_999")


def test_snapshot_run_writes_scripts_and_params(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    exp = manager.add_experiment("/sampleD")
    run = manager.start_run(exp.id, workflow_ref="hsqc_standard")
    snapshot = manager.snapshot_run(
        run.run_id,
        {"process.com": "#!/bin/csh\necho hi\n", "fid.com": "#!/bin/csh\n"},
        params={"zero_fill": 2, "overrides": {"phase.p0": 90}},
    )
    assert snapshot.is_dir()
    assert (snapshot / "process.com").read_text(encoding="utf-8").startswith("#!/bin/csh")
    params = json.loads((snapshot / "params.json").read_text(encoding="utf-8"))
    assert params["zero_fill"] == 2
    assert run.snapshot_dir == snapshot.relative_to(manager.root).as_posix()
    assert "process.com" in run.scripts


def test_build_template_from_run(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    exp = manager.add_experiment("/sampleD")
    run = manager.start_run(
        exp.id,
        workflow_ref="hsqc_standard",
        params={"overrides": {"phase.p0": 90}, "nus": {"nSigma": 5}},
    )
    manager.finish_run(run.run_id, "success")
    target = manager.build_template_from_run(run.run_id)
    assert target.is_file()
    content = target.read_text(encoding="utf-8")
    assert "hsqc_standard" in content
    assert "phase.p0" in content
    assert "nSigma" in content


def test_build_template_refuses_failed_run(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    exp = manager.add_experiment("/sampleD")
    run = manager.start_run(exp.id)
    manager.finish_run(run.run_id, "failed")
    with pytest.raises(ProjectError, match="仅成功运行"):
        manager.build_template_from_run(run.run_id)


def test_atomic_write_json_and_sha256(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "data.json"
    atomic_write_json(target, {"a": [1, 2], "中文": "值"})
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data == {"a": [1, 2], "中文": "值"}
    assert not list(tmp_path.glob("*.tmp"))

    src = tmp_path / "fid"
    src.write_bytes(b"abc")
    assert sha256_file(src) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_recent_projects_store(tmp_path: Path) -> None:
    store = JsonRecentProjectsStore(tmp_path / "recent.json", max_entries=3)
    assert store.list() == []
    store.push("/p/1")
    store.push("/p/2")
    store.push("/p/1")  # 置顶去重
    assert store.list() == ["/p/1", "/p/2"]
    store.push("/p/3")
    store.push("/p/4")  # 超过 max_entries,淘汰最旧
    assert store.list() == ["/p/4", "/p/3", "/p/1"]
    store.remove("/p/3")
    assert "/p/3" not in store.list()
    # 损坏文件容错
    store.path.write_text("{broken", encoding="utf-8")
    assert store.list() == []


def test_recent_store_persists(tmp_path: Path) -> None:
    path = tmp_path / "recent.json"
    store = JsonRecentProjectsStore(path)
    store.push("/p/1")
    store2 = JsonRecentProjectsStore(path)
    assert store2.list() == ["/p/1"]


def test_default_directories_configurable(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(
        tmp_path / "proj", "demo", directories={"raw": "data/raw"}
    )
    # schema 1.3:目录映射仅作兼容解析,不预建目录
    assert manager.dir_path("raw") == (tmp_path / "proj" / "data" / "raw").resolve()
    assert not (tmp_path / "proj" / "data" / "raw").exists()


def test_workflow_run_model_roundtrip() -> None:
    run = WorkflowRun(
        run_id="R-20260811-001",
        experiment_id="exp_001",
        inputs={"fid": "abc"},
        outputs={"ft2": "spectra/x.ft2"},
    )
    restored = WorkflowRun.from_dict(run.to_dict())
    assert restored == run



def test_data_entry_roundtrip() -> None:
    from core.project import DataEntry

    data = DataEntry(
        id="d_001",
        source="/sampleD",
        raw_dir="raw/exp_001/d_001",
        segments=["/sampleE"],
        status="processed",
        metadata_path="metadata/exp_001-d_001.json",
        fid_path="processing/exp_001/d_001/exp_001.fid",
        spectrum_path="spectra/exp_001-d_001.ft2",
        checksums={"acqus": "abc"},
    )
    restored = DataEntry.from_dict(data.to_dict())
    assert restored == data


def test_create_experiment_blank_and_import_data(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment(title="空白")
    assert entry.status == ExperimentStatus.REGISTERED.value
    assert entry.data == []

    d1 = manager.import_data(entry.id, "/sampleD")
    d2 = manager.import_data(entry.id, "/sampleE", segments=["/sampleF"])
    assert d1.id == "d_001"
    assert d2.id == "d_002"
    assert entry.status == ExperimentStatus.IMPORTED.value
    assert entry.source == "/sampleD"  # 兼容属性 = data[0].source
    assert entry.imported_at == d1.imported_at
    with pytest.raises(ProjectError, match="数据不存在"):
        manager.data(entry.id, "d_999")


def test_set_data_fid_and_spectrum(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")
    manager.set_data_fid(entry.id, data.id, "processing/exp_001/d_001/exp_001.fid")
    assert data.status == "fid_ready"
    assert data.fid_path.endswith(".fid")
    manager.set_data_spectrum(entry.id, data.id, "spectra/exp_001-d_001.ft2")
    assert data.status == "processed"
    assert data.spectrum_path.endswith(".ft2")
    actions = [h.action for h in manager.project.processing_history]
    assert "data_fid" in actions and "data_spectrum" in actions


def test_delete_data_removes_artifacts(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")
    raw = manager.data_dir(entry.id, data.id, "raw")
    raw.mkdir(parents=True)
    (raw / "acqus").write_text("x", encoding="utf-8")
    manager.data_metadata_path(entry.id, data.id).write_text(
        "{}", encoding="utf-8"
    )
    spec = manager.data_dir(entry.id, data.id, "spectra") / f"{data.id}.ft2"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_bytes(b"x")

    manager.delete_data(entry.id, data.id)
    assert entry.data == []
    assert entry.status == ExperimentStatus.REGISTERED.value
    assert not raw.exists()
    assert not spec.exists()
    assert any(h.action == "data_deleted" for h in manager.project.processing_history)


def test_schema_1_1_migration_to_1_3(tmp_path: Path) -> None:
    """旧 project.json(source/segments 顶层)→ schema 1.3 的 data[0] 迁移。"""
    root = tmp_path / "proj"
    ProjectManager.create_project(root, "demo")
    manager = ProjectManager.open_project(root)
    entry = manager.add_experiment("/old/sampleD", title="旧实验")
    entry.data[0].metadata_path = "exp_001.json"
    manager.save()
    # 手工改写为 schema 1.1 旧结构
    import json

    path = root / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    old_entry = data["experiments"][0]
    old_entry["source"] = "/old/sampleD"
    old_entry["segments"] = ["/old/sampleE"]
    old_entry["imported_at"] = "2026-01-01T00:00:00+00:00"
    old_entry.pop("data")
    old_entry.pop("created_at")
    data["schema_version"] = "1.1"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    migrated = ProjectManager.open_project(root)
    assert migrated.project is not None
    assert migrated.project.schema_version == "1.3"
    migrated_entry = migrated.project.experiment("exp_001")
    assert migrated_entry is not None
    assert len(migrated_entry.data) == 1
    migrated_data = migrated_entry.data[0]
    assert migrated_data.id == "d_001"
    assert migrated_data.source == "/old/sampleD"
    assert migrated_data.segments == ["/old/sampleE"]
    assert migrated_data.imported_at == "2026-01-01T00:00:00+00:00"
    assert migrated_data.migrated_from_1_1 is True
    assert migrated_entry.source == "/old/sampleD"  # 兼容属性
    assert any(h.action == "project_migrated" for h in migrated.project.processing_history)
    # 保存后仍为 1.3
    migrated.save()
    reopened = ProjectManager.open_project(root)
    assert reopened.project.schema_version == "1.3"
    assert reopened.project.experiment("exp_001").data[0].id == "d_001"


def test_infer_status_aggregates_data(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    assert manager.infer_status(entry.id) is ExperimentStatus.REGISTERED
    data = manager.import_data(entry.id, "/sampleD")
    manager.data_metadata_path(entry.id, data.id).parent.mkdir(
        parents=True, exist_ok=True
    )
    manager.data_metadata_path(entry.id, data.id).write_text(
        "{}", encoding="utf-8"
    )
    assert manager.infer_status(entry.id) is ExperimentStatus.IMPORTED
    spec = manager.data_dir(entry.id, data.id, "spectra") / f"{data.id}.ft2"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_bytes(b"x")
    assert manager.infer_status(entry.id) is ExperimentStatus.PROCESSED
    peaks = manager.data_dir(entry.id, data.id, "peaks") / f"{data.id}.csv"
    peaks.parent.mkdir(parents=True, exist_ok=True)
    peaks.write_text("", encoding="utf-8")
    assert manager.infer_status(entry.id) is ExperimentStatus.PICKED
    report = manager.data_dir(entry.id, data.id, "report") / f"{data.id}.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("{}", encoding="utf-8")
    assert manager.infer_status(entry.id) is ExperimentStatus.ANALYZED



def test_data_entry_title_roundtrip() -> None:
    from core.project import DataEntry

    data = DataEntry(id="d_001", title="骨架 A", source="/sampleD")
    restored = DataEntry.from_dict(data.to_dict())
    assert restored == data
    assert restored.title == "骨架 A"
    # 旧数据缺 title:缺省空
    legacy = DataEntry.from_dict({"id": "d_001", "source": "/x"})
    assert legacy.title == ""


def test_rename_data_persists_and_audits(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")
    renamed = manager.rename_data(entry.id, data.id, "骨架 A")
    assert renamed is data
    assert data.title == "骨架 A"
    manager.save()
    reopened = ProjectManager.open_project(tmp_path / "proj")
    assert reopened.project.experiment(entry.id).data[0].title == "骨架 A"
    assert any(h.action == "data_renamed" for h in manager.project.processing_history)



def test_data_id_not_reused_after_delete(tmp_path: Path) -> None:
    """0.2.159:删除数据后重新导入,数据编号不复用(避免注释/运行记录沿用)。"""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    first = manager.import_data(entry.id, "/sampleD")
    assert first.id == "d_001"
    manager.delete_data(entry.id, first.id)
    second = manager.import_data(entry.id, "/sampleE")
    assert second.id == "d_002"
    third = manager.import_data(entry.id, "/sampleF")
    assert third.id == "d_003"


def test_experiment_id_not_reused_after_delete(tmp_path: Path) -> None:
    """0.2.159:删除实验后新建,实验编号不复用;新实验数据重新从 d_001 起。"""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    e1 = manager.create_experiment()
    assert e1.id == "exp_001"
    manager.import_data(e1.id, "/sampleD")
    manager.delete_experiment(e1.id)
    e2 = manager.create_experiment()
    assert e2.id == "exp_002"
    data = manager.import_data(e2.id, "/sampleE")
    assert data.id == "d_001"


def test_delete_data_cleans_data_notes(tmp_path: Path) -> None:
    """0.2.159:删除数据时清除 metadata.data_notes 中该数据的注释。"""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")
    meta = dict(entry.metadata or {})
    meta["data_notes"] = {data.id: {"notes": "旧注释"}}
    entry.metadata = meta
    manager.delete_data(entry.id, data.id)
    assert "data_notes" not in (entry.metadata or {})
