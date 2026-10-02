"""Import workflow test: the Data hierarchy (raw link + SHA-256 + metadata +
WorkflowRun registration)."""

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


#: 2026-09-24 final decision: an indirect dimension we cannot judge (no pulse program /
#: F1EA / unlisted sequence / family conflict) does not get ``FT -neg``; the import
#: warning carries the same sentence (identical to the conversion log and step report).
#: The fixture has no ``pulseprogram``, so every dataset carries that line and assertions
#: filter it out first. **Expectations come from the single source** (not the English
#: literal: conftest pins the UI language to this tree's default).
def _mode_symbol_warnings(source: Path) -> list[str]:
    from core.data.bruker_reader import read_dataset
    from core.experiment.pulse_pathways import review_lines

    return review_lines(read_dataset(source))


def _other_warnings(warnings: list[str], source: Path) -> list[str]:
    expected = set(_mode_symbol_warnings(source))
    return [w for w in warnings if w not in expected]


def _symlink_supported() -> bool:
    """Platform probe: os.symlink is available (yes on VM/Linux; Windows has no permission
    by default)."""
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
    """Build a container directory: n segments of the same data (subdirectories that
    hold acqus directly)."""
    container = tmp_path / "segmented_data"
    for i in range(1, n + 1):
        shutil.copytree(bruker_dir / "hsqc_2d", container / f"s{i:02d}")
    return container


def test_import_segmented_container_single_entry(tmp_path: Path, bruker_dir: Path) -> None:
    """Container import: several segments merge into one DataEntry (clearly distinct
    from a batch import of many entries)."""
    from workflow.import_workflow import import_segmented_dataset

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    container = _make_segment_container(tmp_path, bruker_dir)
    result = import_segmented_dataset(manager, container, title="seg")
    assert result.data_id == "d_001"
    entry = manager.project.experiment("exp_001")
    assert entry is not None
    assert len(entry.data) == 1  # one DataEntry
    data = entry.data[0]
    assert len(data.segments) == 2
    raw_dir = manager.data_dir("exp_001", "d_001", "raw")
    assert (raw_dir / "segments" / "01" / "acqus").is_file()
    assert (raw_dir / "segments" / "02" / "acqus").is_file()
    meta = json.loads(manager.data_metadata_path("exp_001", "d_001").read_text(encoding="utf-8"))
    assert len(meta["segments"]) == 2
    assert meta["segment_kind"] == "repeat_uniform"  # hsqc_2d traditional sampling
    assert "重复实验叠加" in meta["segment_kind_label"]


def test_segmented_kinetics_is_blocked_before_container_sampling_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    "Regression coverage: test segmented kinetics is blocked before container sampling detection."
    from workflow.import_workflow import KineticsUnsupportedError, import_segmented_dataset

    container = tmp_path / "segmented_kinetics"
    for index in (1, 2):
        segment = container / f"s{index:02d}"
        segment.mkdir(parents=True)
        (segment / "acqus").write_text(
            "##$PULPROG= <XH2D_N_T1rho_180Hdec_top4.shex>\n##$VDLIST= <NCP_15NT1rho>\n",
            encoding="utf-8",
        )

    def _must_not_read(_path: Path) -> None:
        raise AssertionError("container sampling detection must not run for kinetics data")

    monkeypatch.setattr("core.data.bruker_reader.read_dataset_container", _must_not_read)
    manager = ProjectManager.create_project(tmp_path / "proj_kinetics", "demo")

    with pytest.raises(KineticsUnsupportedError, match="不支持导入"):
        import_segmented_dataset(manager, container, title="T1rho")

    assert manager.project is not None
    assert manager.project.experiments == []


def test_import_segmented_to_existing_experiment(tmp_path: Path, bruker_dir: Path) -> None:
    """G2B-011: with exp_id given, import into the current experiment type, no new type."""
    from workflow.import_workflow import import_segmented_dataset

    manager = ProjectManager.create_project(tmp_path / "proj_exp", "demo")
    entry = manager.create_experiment(title="target")
    container = _make_segment_container(tmp_path, bruker_dir)
    result = import_segmented_dataset(manager, container, exp_id=entry.id)
    assert result.experiment_id == entry.id
    assert manager.project is not None
    assert len(manager.project.experiments) == 1  # no new experiment type
    updated = manager.project.experiment(entry.id)
    assert updated is not None and len(updated.data) == 1
    assert len(updated.data[0].segments) == 2


def test_import_segmented_invalid_exp_id_raises(tmp_path: Path, bruker_dir: Path) -> None:
    """G2B-011: an invalid exp_id raises; nothing new is created."""
    from workflow.import_workflow import ImportWorkflowError, import_segmented_dataset

    manager = ProjectManager.create_project(tmp_path / "proj_bad", "demo")
    container = _make_segment_container(tmp_path, bruker_dir)
    with pytest.raises(ImportWorkflowError, match="实验类型不存在"):
        import_segmented_dataset(manager, container, exp_id="exp_999")
    assert manager.project is not None and len(manager.project.experiments) == 0


def test_import_container_rejected_unless_segmented(tmp_path: Path, bruker_dir: Path) -> None:
    """A container directory is not a single Bruker dataset: plain import must report
    an error instead of guessing segments/batch."""
    from workflow.import_workflow import ImportWorkflowError, import_data

    manager = ProjectManager.create_project(tmp_path / "proj2", "demo")
    entry = manager.create_experiment()
    container = _make_segment_container(tmp_path, bruker_dir)
    with pytest.raises(ImportWorkflowError):
        import_data(manager, entry.id, container)


def test_import_rejects_3d_nus_without_schedule_before_registration(
    tmp_path: Path, bruker_dir: Path
) -> None:
    "Regression coverage: test import rejects 3d nus without schedule before registration."
    src = tmp_path / "three_d_nus_without_schedule"
    shutil.copytree(bruker_dir / "nus_3d", src)
    (src / "nuslist").unlink()
    manager = ProjectManager.create_project(tmp_path / "proj_missing_schedule", "demo")
    entry = manager.create_experiment(title="HNCO")

    with pytest.raises(ImportWorkflowError):
        import_data(manager, entry.id, src)

    assert entry.data == []
    assert not manager.data_dir(entry.id, "d_001", "raw").exists()


def test_import_wrapper_rolls_back_empty_experiment_for_missing_3d_schedule(
    tmp_path: Path, bruker_dir: Path
) -> None:
    "Regression coverage: test import wrapper rolls back empty experiment for missing 3d schedule."
    src = tmp_path / "three_d_nus_without_schedule"
    shutil.copytree(bruker_dir / "nus_3d", src)
    (src / "nuslist").unlink()
    manager = ProjectManager.create_project(tmp_path / "proj_missing_schedule", "demo")

    with pytest.raises(ImportWorkflowError):
        import_bruker_dataset(manager, src, title="HNCO")

    assert manager.project is not None
    assert manager.project.experiments == []


def test_import_rejects_2d_nus_without_schedule_even_at_100_percent(
    tmp_path: Path, bruker_dir: Path
) -> None:
    "Regression coverage: test import rejects 2d nus without schedule even at 100 percent."
    src = tmp_path / "two_d_nus_without_schedule"
    shutil.copytree(bruker_dir / "nus_2d", src)
    (src / "nuslist").unlink()
    with (src / "acqus").open("a", encoding="utf-8") as handle:
        handle.write("##$FnTYPE= 2\n##$NusAMOUNT= 100\n")
    manager = ProjectManager.create_project(tmp_path / "proj_missing_2d_schedule", "demo")
    entry = manager.create_experiment(title="HSQC")

    with pytest.raises(ImportWorkflowError):
        import_data(manager, entry.id, src)

    assert entry.data == []
    assert not manager.data_dir(entry.id, "d_001", "raw").exists()


def test_import_rejects_segmented_nus_when_any_segment_lacks_schedule(
    tmp_path: Path, bruker_dir: Path
) -> None:
    "Regression coverage: test import rejects segmented nus when any segment lacks schedule."
    from workflow.import_workflow import ImportWorkflowError, import_segmented_dataset

    container = tmp_path / "segmented_nus_missing_schedule"
    shutil.copytree(bruker_dir / "nus_2d", container / "s01")
    shutil.copytree(bruker_dir / "nus_2d", container / "s02")
    (container / "s02" / "nuslist").unlink()
    manager = ProjectManager.create_project(tmp_path / "proj_segmented_missing", "demo")

    with pytest.raises(ImportWorkflowError):
        import_segmented_dataset(manager, container, title="segmented NUS")

    assert manager.project is not None
    assert manager.project.experiments == []


def test_import_links_and_registers(tmp_path: Path, bruker_dir: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    result = import_bruker_dataset(manager, _source(bruker_dir), title="HSQC")

    assert result.experiment_id == "exp_001"
    assert result.data_id == "d_001"
    assert result.run_id.startswith("R-")
    assert _other_warnings(result.warnings, _source(bruker_dir)) == []
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
    # G2B-009: raw read-only files are links (symlink first, hard link as the Windows
    # fallback), not copies
    if _symlink_supported():
        assert (raw_dir / "acqus").is_symlink()
    assert os.path.samefile(_source(bruker_dir) / "acqus", raw_dir / "acqus")
    assert os.path.samefile(_source(bruker_dir) / "acqu2s", raw_dir / "acqu2s")
    # The compatibility read-only attribute points at data[0]
    assert entry.source == data.raw_dir

    # metadata is written to disk (schema 1.3: <exp>/<data>/metadata.json)
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
    # G2B-009: metadata records link statistics (the import method is auditable)
    if _symlink_supported():
        assert meta["link_stats"]["symlink"] == 2
    else:
        assert meta["link_stats"]["hardlink"] == 2
    assert meta["link_stats"]["copy"] == 0
    assert meta["link_stats"]["writable"] == 0

    # WorkflowRun registration
    run = manager.project.run(result.run_id)
    assert run is not None
    assert run.workflow_ref == IMPORT_WORKFLOW_REF
    assert run.status == "success"
    assert run.inputs["sha256:acqus"] == sha256_file(raw_dir / "acqus")
    assert run.inputs["source_path"] == str(_source(bruker_dir))
    assert run.outputs["raw_dir"] == "exp_001/d_001/raw"
    assert run.outputs["metadata"] == "exp_001/d_001/metadata.json"

    # The state machine advances to imported
    assert manager.infer_status("exp_001") is ExperimentStatus.IMPORTED

    # The caller is responsible for save()
    manager.save()
    reopened = ProjectManager.open_project(tmp_path / "proj")
    assert reopened.project is not None
    assert reopened.project.experiment("exp_001").data[0].id == "d_001"
    assert reopened.project.run(result.run_id) is not None


def test_import_data_into_existing_experiment(tmp_path: Path, bruker_dir: Path) -> None:
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


def test_import_source_inside_project_skips_copy(tmp_path: Path, bruker_dir: Path) -> None:
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
    result = import_bruker_dataset(manager, _source(bruker_dir), segments=[segment])
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
    """When hard and symbolic links both fail, copy item by item instead: the import
    still succeeds and records warnings."""

    def _no_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("link disabled for test")

    monkeypatch.setattr("workflow.import_workflow.os.link", _no_link)
    monkeypatch.setattr("workflow.import_workflow.os.symlink", _no_link)
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    result = import_bruker_dataset(manager, _source(bruker_dir))
    assert manager.project is not None
    raw_dir = manager.data_dir("exp_001", "d_001", "raw")
    assert (raw_dir / "acqus").is_file()
    # Copy fallback: the file is an independent copy, no longer samefile with the source
    assert not os.path.samefile(_source(bruker_dir) / "acqus", raw_dir / "acqus")
    assert any("回退复制" in w for w in result.warnings)
    assert manager.project.run(result.run_id).status == "success"


def test_import_failure_rolls_back(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("metadata 写盘失败")

    monkeypatch.setattr("workflow.import_workflow.atomic_write_json", _boom)
    with pytest.raises(RuntimeError, match="写盘失败"):
        import_bruker_dataset(manager, _source(bruker_dir))

    assert manager.project is not None
    assert manager.project.experiments == []  # convenience entry rolls back the blank experiment
    assert not manager.data_dir("exp_001", "d_001", "raw").exists()
    assert not manager.data_metadata_path("exp_001", "d_001").exists()
    # Audit keeps the failed run
    assert len(manager.project.workflow_runs) == 1
    assert manager.project.workflow_runs[0].status == "failed"


def test_import_data_failure_keeps_experiment(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An import_data failure rolls back only the data entry, not the blank experiment."""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment(title="保留")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("metadata 写盘失败")

    monkeypatch.setattr("workflow.import_workflow.atomic_write_json", _boom)
    with pytest.raises(RuntimeError, match="写盘失败"):
        import_data(manager, entry.id, _source(bruker_dir))
    assert manager.project.experiment(entry.id) is entry
    assert entry.data == []


def test_import_twice_creates_separate_entries(tmp_path: Path, bruker_dir: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    first = import_bruker_dataset(manager, _source(bruker_dir))
    second = import_bruker_dataset(manager, _source(bruker_dir))
    assert first.experiment_id == "exp_001"
    assert second.experiment_id == "exp_002"
    assert first.run_id != second.run_id
    assert manager.project is not None
    assert manager.data_dir("exp_001", "d_001", "raw").is_dir()
    assert manager.data_dir("exp_002", "d_001", "raw").is_dir()


def test_nus_import_records_nuslist_checksum(tmp_path: Path, bruker_dir: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    result = import_bruker_dataset(manager, bruker_dir / "nus_2d")
    assert "nuslist" in result.checksums
    assert manager.project is not None
    run = manager.project.run(result.run_id)
    assert run is not None
    assert run.inputs.get("sha256:nuslist") == result.checksums["nuslist"]


def test_nus_import_records_explicit_schedule_checksum_and_metadata(
    tmp_path: Path, bruker_dir: Path
) -> None:
    "Regression coverage: test nus import records explicit schedule checksum and metadata."
    src = tmp_path / "named_schedule"
    shutil.copytree(bruker_dir / "nus_2d", src)
    (src / "nuslist").replace(src / "CANH")
    with (src / "acqus").open("a", encoding="utf-8") as handle:
        handle.write("##$NUSLIST= <CANH>\n")
    manager = ProjectManager.create_project(tmp_path / "proj_named", "demo")

    result = import_bruker_dataset(manager, src)
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    run = manager.project.run(result.run_id)

    assert "CANH" in result.checksums
    assert run is not None
    assert run.inputs["sha256:CANH"] == result.checksums["CANH"]
    assert metadata["dataset"]["sampling"]["schedule_file"] == "CANH"
    assert metadata["dataset"]["sampling"]["schedule_source"]
    assert metadata["dataset"]["sampling"]["evidence"]


def test_import_writable_raw_names_copied_not_linked(tmp_path: Path, bruker_dir: Path) -> None:
    """fid.com/profY.dat/profYZ.dat are backend-writable/touched files: copied for
    real, not linked, so edits do not pollute the source."""
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
    # Writable files are not links: they are different files from the source, so
    # rewriting them does not pollute the source
    for name in ("fid.com", "profY.dat", "profYZ.dat"):
        assert (raw_dir / name).is_file()
        assert not os.path.samefile(src / name, raw_dir / name)
    (raw_dir / "fid.com").write_text("#!/bin/csh\n# patched\n", encoding="utf-8")
    assert fid_com.read_text(encoding="utf-8") == "#!/bin/csh\n# user fid.com\n"
    (raw_dir / "profY.dat").write_text("patched", encoding="utf-8")
    assert prof_y.read_text(encoding="utf-8") == "profile"
    (raw_dir / "profYZ.dat").write_text("patched", encoding="utf-8")
    assert prof_yz.read_text(encoding="utf-8") == "profile"
    # The other read-only files are still links (symlink first, hard-link fallback on Windows)
    assert os.path.samefile(src / "acqus", raw_dir / "acqus")
    if _symlink_supported():
        assert (raw_dir / "acqus").is_symlink()
    run = manager.project.run(result.run_id)
    assert run is not None
    assert run.params["link_stats"]["writable"] == 3
    assert run.params["link_stats"]["symlink"] > 0 or run.params["link_stats"]["hardlink"] > 0


def test_import_records_link_stats(tmp_path: Path, bruker_dir: Path) -> None:
    """WorkflowRun params record link_stats (hard link/symlink/copy/writable)."""
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
    assert _other_warnings(result.warnings, _source(bruker_dir)) == []


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


def test_import_warns_about_a_stale_indirect_sweep_width(tmp_path: Path, bruker_dir: Path) -> None:
    """Report the sweep-width decision already at import time (when acqus SW_h
    contradicts SW×SFO1)."""
    src = tmp_path / "stale_sw"
    shutil.copytree(bruker_dir / "hsqc_2d", src)
    acqu2s = src / "acqu2s"
    keep = [
        line
        for line in acqu2s.read_text(encoding="utf-8").splitlines()
        if not line.startswith(("##$SW_h=", "##$SFO1="))
    ]
    keep += ["##$SW= 30", "##$SW_h= 2000", "##$SFO1= 60.81782065611"]
    acqu2s.write_text("\n".join(keep) + "\n", encoding="utf-8", newline="\n")

    manager = ProjectManager.create_project(tmp_path / "proj_sw", "demo")
    result = import_bruker_dataset(manager, src)
    assert any("2000" in w and "1824.5" in w for w in result.warnings), result.warnings
    # A self-consistent dataset must not carry this warning (other import warnings unchanged)
    manager2 = ProjectManager.create_project(tmp_path / "proj_ok", "demo")
    clean = import_bruker_dataset(manager2, _source(bruker_dir))
    assert _other_warnings(clean.warnings, _source(bruker_dir)) == []


def _label(key: str) -> str:
    "Regression coverage:  label."
    from ui_support.i18n import tr

    return tr(key).split("{")[0]


def test_import_streams_stage_lines_before_completion(tmp_path: Path, bruker_dir: Path) -> None:
    "Regression coverage: test import streams stage lines before completion."
    manager = ProjectManager.create_project(tmp_path / "proj_progress", "demo")
    source = tmp_path / "with_ser"
    shutil.copytree(_source(bruker_dir), source)
    (source / "ser").write_bytes(b"\x00" * 4096)
    metadata_path = manager.data_metadata_path("exp_001", "d_001")
    seen: list[tuple[str, bool]] = []

    def on_progress(message: str) -> None:
        seen.append((message, metadata_path.is_file()))

    result = import_bruker_dataset(manager, source, title="HSQC", progress=on_progress)
    lines = [message for message, _ in seen]

    assert lines[0].startswith(_label("== import start: {p0} → {p1} =="))
    assert lines[-1].startswith(_label("== import completed: {p0} ({p1} file(s), {p2}) =="))
    assert result.data_id in lines[-1]

    middle = lines[1:-1]
    assert len(middle) >= 2
    for key in (
        "◆ parameters: {p0}",
        "◆ data: {p0} {p1}",
        "◆ raw: {p0} file(s) → {p1} ({p2})",
        "◆ fingerprints: {p0} key file(s), manifest {p1} file(s) / {p2}",
        "◆ import record: {p0} (run {p1})",
    ):
        assert any(line.startswith(_label(key)) for line in middle), key

    raw_prefix = _label("◆ raw: {p0} file(s) → {p1} ({p2})")
    raw_index = next(i for i, line in enumerate(lines) if line.startswith(raw_prefix))
    record_index = next(
        i
        for i, line in enumerate(lines)
        if line.startswith(_label("◆ import record: {p0} (run {p1})"))
    )
    assert record_index > raw_index
    assert seen[raw_index][1] is False
    assert seen[record_index][1] is True
    assert seen[-1][1] is True


def test_segmented_import_reports_segment_count(tmp_path: Path, bruker_dir: Path) -> None:
    "Regression coverage: test segmented import reports segment count."
    from ui_support.i18n import tr
    from workflow.import_workflow import import_segmented_dataset

    manager = ProjectManager.create_project(tmp_path / "proj_seg_progress", "demo")
    container = _make_segment_container(tmp_path, bruker_dir)
    lines: list[str] = []
    result = import_segmented_dataset(manager, container, title="seg", progress=lines.append)

    assert lines[0].startswith(_label("== import start: {p0} → {p1} =="))
    segments_line = next(line for line in lines if line.startswith(_label("◆ segments: {p0}")))
    assert "2" in segments_line
    assert tr("Repeat-experiment overlay (uniform, identical parameters)") in segments_line
    assert lines[-1].startswith(_label("== import completed: {p0} ({p1} file(s), {p2}) =="))
    assert result.data_id in lines[-1]
