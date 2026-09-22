"""Import workflow test: Data hierarchy (raw link + SHA-256 + metadata + WorkflowRun
registration)."""

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


def _symlink_supported() -> bool:
    """Platform detection: os.symlink is available (VM/Linux is; Windows has no permissions by
    default)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "probe_src"
        dst = Path(tmp) / "probe_dst"
        src.write_text("x", encoding="utf-8")
        try:
            os.symlink(src, dst)
            return dst.is_symlink()
        except OSError:
            return False


def _make_segment_container(tmp_path: Path, bruker_dir: Path, n: int = 2) -> Path:
    """Construct a container directory: n segments of the same data (directly containing
    subdirectories of acqus)."""
    container = tmp_path / "segmented_data"
    for i in range(1, n + 1):
        shutil.copytree(bruker_dir / "hsqc_2d", container / f"s{i:02d}")
    return container


def test_import_segmented_container_single_entry(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Container import: multiple segments are merged into one DataEntry (clearly distinguished
    from batch import of multiple entries)."""
    from workflow.import_workflow import import_segmented_dataset

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    container = _make_segment_container(tmp_path, bruker_dir)
    result = import_segmented_dataset(manager, container, title="seg")
    assert result.data_id == "d_001"
    entry = manager.project.experiment("exp_001")
    assert entry is not None
    assert len(entry.data) == 1  # A DataEntry.
    data = entry.data[0]
    assert len(data.segments) == 2
    raw_dir = manager.data_dir("exp_001", "d_001", "raw")
    assert (raw_dir / "segments" / "01" / "acqus").is_file()
    assert (raw_dir / "segments" / "02" / "acqus").is_file()
    meta = json.loads(
        manager.data_metadata_path("exp_001", "d_001").read_text(encoding="utf-8")
    )
    assert len(meta["segments"]) == 2
    assert meta["segment_kind"] == "repeat_uniform"  # hsqc_2d Traditional sampling.
    assert "Repeat-experiment overlay" in meta["segment_kind_label"]


def test_import_segmented_to_existing_experiment(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """G2B-011: exp_id is imported into the current experiment type when specified, and does not
    create a new experiment type."""
    from workflow.import_workflow import import_segmented_dataset

    manager = ProjectManager.create_project(tmp_path / "proj_exp", "demo")
    entry = manager.create_experiment(title="target")
    container = _make_segment_container(tmp_path, bruker_dir)
    result = import_segmented_dataset(manager, container, exp_id=entry.id)
    assert result.experiment_id == entry.id
    assert manager.project is not None
    assert len(manager.project.experiments) == 1  # Do not create a new experiment type.
    updated = manager.project.experiment(entry.id)
    assert updated is not None and len(updated.data) == 1
    assert len(updated.data[0].segments) == 2


def test_import_segmented_invalid_exp_id_raises(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """G2B-011: Illegal exp_id throws an error and does not create a new one."""
    from workflow.import_workflow import ImportWorkflowError, import_segmented_dataset

    manager = ProjectManager.create_project(tmp_path / "proj_bad", "demo")
    container = _make_segment_container(tmp_path, bruker_dir)
    with pytest.raises(ImportWorkflowError, match="experiment type does not exist"):
        import_segmented_dataset(manager, container, exp_id="exp_999")
    assert manager.project is not None and len(manager.project.experiments) == 0


def test_import_container_rejected_unless_segmented(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """The container directory is not a single Bruker dataset: ordinary import should report an
    error, Not automatically treated as segments/batch."""
    from workflow.import_workflow import ImportWorkflowError, import_data

    manager = ProjectManager.create_project(tmp_path / "proj2", "demo")
    entry = manager.create_experiment()
    container = _make_segment_container(tmp_path, bruker_dir)
    with pytest.raises(ImportWorkflowError):
        import_data(manager, entry.id, container)
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
    # G2B-009: raw read-only file is a link (symbolic link takes precedence, Windows falls back to
    # hard link), not copy.
    if _symlink_supported():
        assert (raw_dir / "acqus").is_symlink()
    assert os.path.samefile(_source(bruker_dir) / "acqus", raw_dir / "acqus")
    assert os.path.samefile(_source(bruker_dir) / "acqu2s", raw_dir / "acqu2s")
    # Compatible read-only attribute points to data[0].
    assert entry.source == data.raw_dir

    # Metadata placement (schema 1.3:<exp>/<data>/metadata.json).
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
    # G2B-009:metadata record link statistics (import method can be verified).
    if _symlink_supported():
        assert meta["link_stats"]["symlink"] == 2
    else:
        assert meta["link_stats"]["hardlink"] == 2
    assert meta["link_stats"]["copy"] == 0
    assert meta["link_stats"]["writable"] == 0

    # WorkflowRun registration.
    run = manager.project.run(result.run_id)
    assert run is not None
    assert run.workflow_ref == IMPORT_WORKFLOW_REF
    assert run.status == "success"
    assert run.inputs["sha256:acqus"] == sha256_file(raw_dir / "acqus")
    assert run.inputs["source_path"] == str(_source(bruker_dir))
    assert run.outputs["raw_dir"] == "exp_001/d_001/raw"
    assert run.outputs["metadata"] == "exp_001/d_001/metadata.json"

    # The state machine advances to imported.
    assert manager.infer_status("exp_001") is ExperimentStatus.IMPORTED

    # The caller is responsible for save().
    manager.save()
    reopened = ProjectManager.open_project(tmp_path / "proj")
    assert reopened.project is not None
    assert reopened.project.experiment("exp_001").data[0].id == "d_001"
    assert reopened.project.run(result.run_id) is not None


def test_import_data_into_existing_experiment(
    tmp_path: Path, bruker_dir: Path
) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment(title="skeleton")
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
    with pytest.raises(ImportWorkflowError, match="project is not loaded"):
        import_bruker_dataset(manager, _source(bruker_dir))


def test_import_rejects_non_bruker_source(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    bogus = tmp_path / "not_a_dataset"
    bogus.mkdir()
    (bogus / "readme.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(ImportWorkflowError, match="acqus is missing"):
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
    assert any("skip copying" in w for w in result.warnings)
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
    """Hard link/Fallback copying item by item when all symbolic links fail, still succeeds and
    logs warnings."""

    def _no_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("link disabled for test")

    monkeypatch.setattr("workflow.import_workflow.os.link", _no_link)
    monkeypatch.setattr("workflow.import_workflow.os.symlink", _no_link)
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    result = import_bruker_dataset(manager, _source(bruker_dir))
    assert manager.project is not None
    raw_dir = manager.data_dir("exp_001", "d_001", "raw")
    assert (raw_dir / "acqus").is_file()
    # Fallback copy: file is an independent copy and is no longer the same file as the source.
    assert not os.path.samefile(_source(bruker_dir) / "acqus", raw_dir / "acqus")
    assert any("could not be linked" in w for w in result.warnings)
    assert manager.project.run(result.run_id).status == "success"


def test_import_failure_rolls_back(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Metadata disk writing failed")

    monkeypatch.setattr(
        "workflow.import_workflow.atomic_write_json", _boom
    )
    with pytest.raises(RuntimeError, match="Metadata disk writing failed"):
        import_bruker_dataset(manager, _source(bruker_dir))

    assert manager.project is not None
    assert manager.project.experiments == []  # Convenient entrance to roll back blank experiments.
    assert not manager.data_dir("exp_001", "d_001", "raw").exists()
    assert not manager.data_metadata_path("exp_001", "d_001").exists()
    # Audit retains failed runs.
    assert len(manager.project.workflow_runs) == 1
    assert manager.project.workflow_runs[0].status == "failed"


def test_import_data_failure_keeps_experiment(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Import_data Failure only rolls back data entries and does not clear blank experiments."""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment(title="reserve")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Metadata disk writing failed")

    monkeypatch.setattr(
        "workflow.import_workflow.atomic_write_json", _boom
    )
    with pytest.raises(RuntimeError, match="Metadata disk writing failed"):
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


def test_import_writable_raw_names_copied_not_linked(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Fid.com/profY.dat/profYZ.dat Is backend writable/touch file: Entity copy is not linked (changes do not
    pollute the source)."""
    src = tmp_path / "src_with_fid"
    shutil.copytree(_source(bruker_dir), src)
    fid_com = src / "fid.com"
    fid_com.write_text("#!/bin/csh\n# user fid.com\n", encoding="utf-8")
    prof_y = src / "profY.dat"
    prof_y.write_text("profile", encoding="utf-8")
    prof_yz = src / "profYZ.dat"
    prof_yz.write_text("profile", encoding="utf-8")

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    result = import_bruker_dataset(manager, src)
    assert manager.project is not None
    raw_dir = manager.data_dir("exp_001", "d_001", "raw")
    # Writable file is not a link: unlike source file, rewriting does not pollute the source.
    for name in ("fid.com", "profY.dat", "profYZ.dat"):
        assert (raw_dir / name).is_file()
        assert not os.path.samefile(src / name, raw_dir / name)
    (raw_dir / "fid.com").write_text("#!/bin/csh\n# patched\n", encoding="utf-8")
    assert fid_com.read_text(encoding="utf-8") == "#!/bin/csh\n# user fid.com\n"
    (raw_dir / "profY.dat").write_text("patched", encoding="utf-8")
    assert prof_y.read_text(encoding="utf-8") == "profile"
    (raw_dir / "profYZ.dat").write_text("patched", encoding="utf-8")
    assert prof_yz.read_text(encoding="utf-8") == "profile"
    # The remaining read-only files are still links (symbolic links take precedence, Windows falls
    # back to hard links).
    assert os.path.samefile(src / "acqus", raw_dir / "acqus")
    if _symlink_supported():
        assert (raw_dir / "acqus").is_symlink()
    run = manager.project.run(result.run_id)
    assert run is not None
    assert run.params["link_stats"]["writable"] == 3
    assert (
        run.params["link_stats"]["symlink"] > 0
        or run.params["link_stats"]["hardlink"] > 0
    )


def test_import_records_link_stats(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """WorkflowRun params record link_stats(hard link/symbolic link/copy/writable)."""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    result = import_bruker_dataset(manager, _source(bruker_dir))
    assert manager.project is not None
    run = manager.project.run(result.run_id)
    assert run is not None
    stats = run.params["link_stats"]
    assert set(stats) == {"hardlink", "symlink", "copy", "writable"}
    assert stats["symlink"] > 0 or stats["hardlink"] > 0
    assert stats["copy"] == 0
    assert stats["writable"] == 0
    assert result.warnings == []


def test_user_experiment_type_write_failure_keeps_traceback(tmp_path, monkeypatch, caplog):
    """An OS write failure stays observable without changing the GUI bool contract."""
    import logging
    from types import SimpleNamespace

    from workflow import import_workflow

    path = tmp_path / "metadata.json"
    path.write_text('{"dataset": {}}', encoding="utf-8")
    original = path.read_bytes()
    manager = SimpleNamespace(
        root=tmp_path,
        project=SimpleNamespace(experiment=lambda exp_id: SimpleNamespace(data=[])),
        data_metadata_path=lambda exp_id, data_id: path,
    )

    def locked(*args):
        raise PermissionError("metadata is temporarily locked")

    monkeypatch.setattr(import_workflow, "atomic_write_json", locked)
    with caplog.at_level(logging.DEBUG, logger="workflow.import_workflow"):
        assert not import_workflow.apply_user_experiment_type(manager, "exp", "data", "HSQC")
    assert path.read_bytes() == original
    record = next(r for r in caplog.records if "persist user experiment type" in r.message)
    assert record.exc_info[0] is PermissionError
