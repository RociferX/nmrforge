"""Data group (schema 1.4) test: model serialization, ProjectManager group method, run_batch group
parsing and reference data parameter reuse."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.project import DataGroupEntry, ProjectError, ProjectManager
from workflow.batch import BatchError, run_batch


def _manager_with_data(
    tmp_path: Path, n: int = 3, source: str | None = None
) -> tuple[ProjectManager, str, list[str]]:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment(title="batch")
    data_ids = [
        manager.import_data(entry.id, source or f"/fake/{i}").id
        for i in range(n)
    ]
    manager.save()
    return manager, entry.id, data_ids


def test_group_model_roundtrip(tmp_path: Path) -> None:
    """DataGroupEntry serialization round trip + ExperimentEntry.groups drop(schema 1.4)."""
    manager, exp_id, data_ids = _manager_with_data(tmp_path)
    group = manager.create_data_group(
        exp_id, title="HSQC group", data_ids=data_ids[:2]
    )
    assert group.id == "G1"
    assert group.title == "HSQC group"
    manager.save()

    reopened = ProjectManager.open_project(tmp_path / "proj")
    assert reopened.project.schema_version == "1.4"
    entry = reopened.project.experiment(exp_id)
    assert entry is not None
    assert len(entry.groups) == 1
    restored = entry.groups[0]
    assert restored.id == "G1"
    assert restored.title == "HSQC group"
    assert restored.data_ids == data_ids[:2]
    assert DataGroupEntry.from_dict(restored.to_dict()) == restored


def test_create_group_validates_members(tmp_path: Path) -> None:
    """Create_data_group Verification members must belong to this experiment; the number contains
    history and is not reused."""
    manager, exp_id, data_ids = _manager_with_data(tmp_path)
    with pytest.raises(Exception):
        manager.create_data_group(exp_id, data_ids=["d_999"])
    group = manager.create_data_group(exp_id, data_ids=data_ids[:1])
    assert group.id == "G1"
    manager.delete_data_group(exp_id, "G1")
    # After deleting the group, the number is not reused -> G2 (the audit history includes G1).
    group2 = manager.create_data_group(exp_id)
    assert group2.id == "G2"


def test_group_membership_operations(tmp_path: Path) -> None:
    """Add/remove/group_of_data/delete group operation."""
    manager, exp_id, data_ids = _manager_with_data(tmp_path)
    group = manager.create_data_group(exp_id, data_ids=data_ids[:1])
    # add
    manager.add_to_group(exp_id, group.id, data_ids[1])
    assert manager.group_data_ids(exp_id, group.id) == data_ids[:2]
    assert manager.group_of_data(exp_id, data_ids[1]).id == group.id
    # Add idempotent.
    manager.add_to_group(exp_id, group.id, data_ids[1])
    assert len(manager.group_data_ids(exp_id, group.id)) == 2
    # remove
    manager.remove_from_group(exp_id, group.id, data_ids[1])
    assert manager.group_data_ids(exp_id, group.id) == data_ids[:1]
    assert manager.group_of_data(exp_id, data_ids[1]) is None
    # Illegal member.
    with pytest.raises(Exception):
        manager.add_to_group(exp_id, group.id, "d_999")
    # Delete group (data retention).
    manager.delete_data_group(exp_id, group.id)
    assert manager.group(exp_id, group.id) is None
    assert len(manager.data_groups(exp_id)) == 0
    assert len(manager.project.experiment(exp_id).data) == 3


def test_delete_data_keeps_group_membership_for_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29ex: Delete the data retention group reference (soft deletion), and restore
    the group without loss after recovery."""
    import shutil

    manager, exp_id, data_ids = _manager_with_data(tmp_path)
    manager.create_data_group(exp_id, data_ids=data_ids)
    trash = tmp_path / "trash"
    trash.mkdir()

    def fake(path, fallback_dir, rel=None):
        dest = trash / (rel or Path(path).name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
        return dest

    monkeypatch.setattr("core.project.manager.send_to_trash", fake)
    manager.delete_data(exp_id, data_ids[0])
    manager.save()
    group = manager.group(exp_id, "G1")
    assert group is not None
    assert group.data_ids == data_ids  # The group reference is retained and will be restored.
    with pytest.raises(ProjectError):
        manager.data(exp_id, data_ids[0])
    # Accessible again after recovery.
    (manager.data_base(exp_id, data_ids[0]) / "raw").mkdir(
        parents=True
    )
    manager.recover_trashed()
    assert manager.data(exp_id, data_ids[0]).id == data_ids[0]


class _FakeBackend:
    """Logs the fake backend called; spectrum logs params for reference data reuse assertions."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = str(work_dir)
        self.calls: list[tuple[str, str]] = []
        self.spectrum_params: dict[str, dict] = {}

    def _touch(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    def convert_to_fid(self, experiment, data_dir, progress=None) -> dict:
        self.calls.append(("convert_to_fid", experiment.dataset_id))
        fid_path = Path(self.work_dir) / f"{experiment.dataset_id}.fid"
        self._touch(fid_path)
        return {
            "success": True,
            "fid_path": str(fid_path),
            "message": "ok",
            "logs": [],
        }

    def process(
        self,
        experiment,
        plan,
        direct_phase_override=None,
        params=None,
        progress=None,
    ) -> dict:
        self.calls.append(("process", experiment.dataset_id))
        self.spectrum_params[experiment.dataset_id] = dict(params or {})
        spectrum = Path(self.work_dir) / f"{experiment.dataset_id}.ft2"
        self._touch(spectrum)
        return {
            "success": True,
            "spectrum_path": str(spectrum),
            "message": "ok",
            "logs": [],
        }

    def reconstruct_nus(self, experiment, params, progress=None) -> dict:
        self.calls.append(("reconstruct_nus", experiment.dataset_id))
        self.spectrum_params[experiment.dataset_id] = dict(params or {})
        spectrum = Path(self.work_dir) / f"{experiment.dataset_id}.ft2"
        self._touch(spectrum)
        return {
            "success": True,
            "spectrum_path": str(spectrum),
            "message": "ok",
            "logs": [],
        }


def test_run_batch_resolves_project_group(tmp_path: Path) -> None:
    """Run_batch Press project.json to resolve the members of the data group (G1)."""
    manager, exp_id, data_ids = _manager_with_data(tmp_path)
    manager.create_data_group(exp_id, data_ids=data_ids[:2])
    backend = _FakeBackend(tmp_path / "work")
    result = run_batch(manager, exp_id, "G1", ["fid"], backend)
    assert result["batch_id"] == "G1"
    assert result["data_ids"] == data_ids[:2]
    assert set(result["results"]) == set(data_ids[:2])
    # Old pipeline_state B prefix compatible parsing is still available.
    import json

    path = manager.data_base(exp_id, data_ids[2]) / ".pipeline_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "steps": {}, "batch": "B1"}),
        encoding="utf-8",
    )
    result_b = run_batch(manager, exp_id, "B1", ["fid"], backend)
    assert result_b["data_ids"] == [data_ids[2]]


def test_run_batch_empty_group_raises(tmp_path: Path) -> None:
    """Empty data group target throws BatchError."""
    manager, exp_id, _data_ids = _manager_with_data(tmp_path)
    manager.create_data_group(exp_id)
    backend = _FakeBackend(tmp_path / "work")
    with pytest.raises(BatchError):
        run_batch(manager, exp_id, "G1", ["fid"], backend)


def test_run_batch_reference_data_params(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Reference_data_id: Spectrum step reuses the valid parameters of the most recent successful
    run of the reference data."""
    manager, exp_id, data_ids = _manager_with_data(
        tmp_path, n=2, source=str(bruker_dir / "hsqc_2d")
    )
    manager.create_data_group(exp_id, data_ids=data_ids[:2])
    backend = _FakeBackend(tmp_path / "work")

    # First let the reference data (data_ids[0]) run the spectrum once and register the WorkflowRun
    # with params.
    from workflow.stepwise import generate_spectrum

    generate_spectrum(
        manager,
        exp_id,
        data_ids[0],
        backend,
        params={"phase_route": "none", "baseline": "poly"},
    )
    # Reference parameter is consumed at WorkflowRun.params(phase_route, retain baseline).
    ref_run = next(
        r
        for r in manager.project.workflow_runs
        if (r.inputs or {}).get("data_id") == data_ids[0]
        and r.workflow_ref == "process"
    )
    assert ref_run.params.get("baseline") == "poly"

    result = run_batch(
        manager,
        exp_id,
        "G1",
        ["fid", "spectrum"],
        backend,
        reference_data_id=data_ids[0],
        params={"phase_route": "none"},
    )
    assert result["summary"] == {"total": 2, "success": 2, "failed": 0}
    # The second data spectrum in the group uses the baseline parameter of the reference data.
    assert backend.spectrum_params[data_ids[1]].get("baseline") == "poly"


def test_run_batch_explicit_params_override_reference(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Explicit params override the reference parameter (the reference parameter is the base)."""
    manager, exp_id, data_ids = _manager_with_data(
        tmp_path, n=2, source=str(bruker_dir / "hsqc_2d")
    )
    manager.create_data_group(exp_id, data_ids=data_ids[:2])
    backend = _FakeBackend(tmp_path / "work")

    from workflow.stepwise import generate_spectrum

    generate_spectrum(
        manager,
        exp_id,
        data_ids[0],
        backend,
        params={"phase_route": "none", "baseline": "poly"},
    )
    result = run_batch(
        manager,
        exp_id,
        "G1",
        ["fid", "spectrum"],
        backend,
        reference_data_id=data_ids[0],
        params={"phase_route": "none", "baseline": "manual"},
    )
    assert result["summary"]["failed"] == 0
    assert backend.spectrum_params[data_ids[1]].get("baseline") == "manual"
def test_delete_data_group_with_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delete the group together with the data in the group: member soft deletion + recycle bin,
    group removal, audit record deleted_data_ids."""
    import shutil

    manager, exp_id, data_ids = _manager_with_data(tmp_path, n=3)
    manager.create_data_group(exp_id, data_ids=data_ids)
    trash = tmp_path / "trash"
    trash.mkdir()

    def fake(path, fallback_dir, rel=None):
        dest = trash / (rel or Path(path).name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
        return dest

    monkeypatch.setattr("core.project.manager.send_to_trash", fake)
    deleted = manager.delete_data_group_with_members(exp_id, "G1")
    assert deleted == data_ids

    # Group removed.
    assert manager.group(exp_id, "G1") is None
    assert len(manager.data_groups(exp_id)) == 0
    # All members in the group are soft deleted (recoverable).
    entry = manager.project.experiment(exp_id)
    assert all(d.trashed for d in entry.data)
    for data_id in data_ids:
        with pytest.raises(ProjectError):
            manager.data(exp_id, data_id)
    # Audit records contain deleted members.
    hist = [
        h
        for h in manager.project.processing_history
        if h.action == "data_group_deleted_with_members"
    ]
    assert hist
    assert hist[-1].fields.get("group_id") == "G1"
    assert hist[-1].fields.get("deleted_data_ids") == data_ids
def test_migrate_legacy_default_titles(tmp_path: Path) -> None:
    """Patch29hf: The old automatic default title (data group G1/sample data d_001) is migrated
    when it is opened, and the user-defined name does not change."""
    manager, exp_id, data_ids = _manager_with_data(tmp_path, n=2)
    # Legacy default titles are written by the Chinese trunk and must keep migrating.
    manager.create_data_group(
        exp_id, title="数据组 G1", data_ids=data_ids[:1]
    )
    group2 = manager.create_data_group(exp_id, title="comparison group", data_ids=data_ids[1:2])
    manager.data(exp_id, data_ids[0]).title = f"样品数据 {data_ids[0]}"
    manager.save()

    reopened = ProjectManager.open_project(tmp_path / "proj")
    assert reopened.group(exp_id, "G1").title == "Group G1"
    assert reopened.data(exp_id, data_ids[0]).title == f"Data {data_ids[0]}"
    # User custom title does not change.
    assert reopened.group(exp_id, group2.id).title == "comparison group"
    # Turn on old mode again -> no more changes (idempotent).
    reopened.save()
    again = ProjectManager.open_project(tmp_path / "proj")
    assert again.group(exp_id, "G1").title == "Group G1"
def test_run_batch_on_data_done_per_data(tmp_path: Path) -> None:
    """Patch29hf:on_data_done is triggered when each data is completed and carries the final
    status."""
    manager, exp_id, data_ids = _manager_with_data(tmp_path, n=2)
    manager.create_data_group(exp_id, data_ids=data_ids)
    backend = _FakeBackend(tmp_path / "work")
    done: list[dict] = []
    result = run_batch(
        manager,
        exp_id,
        "G1",
        ["fid"],
        backend,
        on_data_done=lambda per: done.append(per),
    )
    assert [d["data_id"] for d in done] == data_ids
    # Callbacks are made every time the data is completed, carrying their respective final status
    # (does not rely on fake backend success).
    assert all(d.get("data_id") and d.get("status") for d in done)
    assert set(result["results"]) == set(data_ids)


def test_run_batch_cancel_marks_remaining(tmp_path: Path) -> None:
    """Patch29hf: Stop button request cancellation -> remaining data mark canceled, the whole group
    is terminated."""
    from backend.runtime import clear_cancel, request_cancel

    manager, exp_id, data_ids = _manager_with_data(tmp_path, n=2)
    manager.create_data_group(exp_id, data_ids=data_ids)
    backend = _FakeBackend(tmp_path / "work")
    done: list[dict] = []
    clear_cancel()
    request_cancel()
    try:
        result = run_batch(
            manager,
            exp_id,
            "G1",
            ["fid"],
            backend,
            on_data_done=lambda per: done.append(per),
        )
    finally:
        clear_cancel()
    assert all(r["status"] == "cancelled" for r in result["results"].values())
    assert result["cancelled"] == data_ids
    assert result["summary"]["total"] == 2
    assert result["summary"]["success"] == 0
    assert len(done) == 2
