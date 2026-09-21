"""Structured QC audit record test (mission statement Phase 10). The requirements of Phase 10 are:
Any automatic modification of the original/The behaviour of intermediate data must be left
behind ``issue_detected / location / detection_rule / action_taken / before_state / after_state
/ timestamp / software_version`` records, that is, ``detect -> flag -> log -> optional
correction``, silent changes are prohibited. Here, not only the recording module itself is
tested, but also three real wiring points (bad point replacement, NusTD grid adjustment, source
deletion) are tested."""

from __future__ import annotations

from pathlib import Path

from core.audit.qc_audit import (
    AUDIT_FILENAME,
    REQUIRED_FIELDS,
    QcAction,
    QcAuditLog,
    audit_path,
    read_audit,
)


def _action(**overrides) -> QcAction:
    base = dict(
        issue_detected="test questions",
        location="somewhere row=1",
        detection_rule="test rules",
        action_taken="test_action",
        before_state={"value": 1.0},
        after_state={"value": 0.0},
    )
    base.update(overrides)
    return QcAction(**base)


# --------------------------------------------------------------- Logging module itself.

def test_record_has_every_field_phase10_requires(tmp_path: Path) -> None:
    log = QcAuditLog(tmp_path)
    written = log.record(_action())
    payload = written.to_dict()
    for field in REQUIRED_FIELDS:
        assert field in payload, field
    assert payload["timestamp"], "The timestamp must be stamped by the record layer"
    assert payload["software_version"], "Software versions must be stamped by the record layer"


def test_log_is_append_only_and_readable(tmp_path: Path) -> None:
    log = QcAuditLog(tmp_path)
    log.record(_action(location="row=1"))
    log.record(_action(location="row=2"))
    assert log.count == 2
    assert audit_path(tmp_path).name == AUDIT_FILENAME
    actions = read_audit(tmp_path)
    assert [a.location for a in actions] == ["row=1", "row=2"]
    assert len(log) == 2


def test_records_survive_a_new_reader(tmp_path: Path) -> None:
    QcAuditLog(tmp_path).record(_action())
    fresh = read_audit(tmp_path)
    assert len(fresh) == 1
    assert fresh[0].before_state == {"value": 1.0}
    assert fresh[0].after_state == {"value": 0.0}


def test_no_records_when_nothing_changed(tmp_path: Path) -> None:
    """There is no file without changes: "No records" must be equivalent to "unchanged" during
    review."""
    assert read_audit(tmp_path) == []
    assert not audit_path(tmp_path).exists()


def test_git_provenance_is_attached_when_available(tmp_path: Path) -> None:
    written = QcAuditLog(tmp_path).record(_action())
    from core.version import git_commit

    if git_commit():
        assert written.extra.get("git_commit") == git_commit()
        assert written.extra.get("git_commit_dirty") in {"0", "1"}


def test_disabled_log_writes_nothing_but_returns_the_record(tmp_path: Path) -> None:
    log = QcAuditLog(tmp_path, enabled=False)
    written = log.record(_action())
    assert written.timestamp
    assert not audit_path(tmp_path).exists()
    assert log.summary() == ""


def test_summary_lists_the_actions(tmp_path: Path) -> None:
    log = QcAuditLog(tmp_path)
    log.record(_action(action_taken="neighbour_interpolation"))
    log.record(_action(action_taken="nus_td_shrunk"))
    summary = log.summary()
    assert "2 entries" in summary
    assert "neighbour_interpolation" in summary and "nus_td_shrunk" in summary
    assert str(log.path) in summary


def test_malformed_line_is_skipped_not_fatal(tmp_path: Path) -> None:
    log = QcAuditLog(tmp_path)
    log.record(_action(location="row=1"))
    with audit_path(tmp_path).open("a", encoding="utf-8") as handle:
        handle.write("{not json}\n")
    log.record(_action(location="row=2"))
    assert [a.location for a in read_audit(tmp_path)] == ["row=1", "row=2"]


def test_action_round_trips_through_dict() -> None:
    original = _action(extra={"row": 3, "truncated": True})
    again = QcAction.from_dict(original.to_dict())
    assert again.before_state == original.before_state
    assert again.extra == original.extra
    assert again.schema_version == original.schema_version


# --------------------------------------------------------------- Real wiring points.

def test_bad_point_repair_is_recorded(tmp_path: Path) -> None:
    """Bad point replacement: record detection rules, Before action and changes/The value after."""
    from workflow.direct_diagnostics import AUDIT_DETAIL_LIMIT, _record_bad_point_repair

    log = QcAuditLog(tmp_path)
    changed = [(17, 123.5 + 4j, 41.25 - 0.5j), (18, -9.0 + 0j, 0.5 + 0j)]
    _record_bad_point_repair(
        log, file_name="nus_2d.fid", row=3, changed=changed, backup_dir=str(tmp_path / "bak")
    )

    actions = read_audit(tmp_path)
    assert len(actions) == 1
    action = actions[0]
    assert action.action_taken == "neighbour_interpolation"
    assert action.location == "nus_2d.fid row=3"
    assert action.before_state["columns"] == [17, 18]
    assert action.before_state["real"] == [123.5, -9.0]
    assert action.after_state["real"] == [41.25, 0.5]
    assert action.before_state["count"] == 2
    assert action.extra["row"] == 3
    assert action.extra["backup_dir"].endswith("bak")
    assert action.extra["truncated"] is False
    assert AUDIT_DETAIL_LIMIT >= 2


def test_bad_point_repair_truncates_huge_lists(tmp_path: Path) -> None:
    """Huge records cannot be written for pathological data: only the number is left when the upper
    limit is exceeded and marked as truncated."""
    from workflow.direct_diagnostics import AUDIT_DETAIL_LIMIT, _record_bad_point_repair

    log = QcAuditLog(tmp_path)
    changed = [(i, complex(i), complex(0)) for i in range(AUDIT_DETAIL_LIMIT + 5)]
    _record_bad_point_repair(log, file_name="x.fid", row=0, changed=changed)
    action = read_audit(tmp_path)[0]
    assert action.before_state["count"] == AUDIT_DETAIL_LIMIT + 5
    assert len(action.before_state["columns"]) == AUDIT_DETAIL_LIMIT
    assert action.extra["truncated"] is True


def test_nus_grid_adjustment_is_recorded(tmp_path: Path, bruker_dir: Path) -> None:
    """Bad point cleaned NusTD shrink: Record axis name and before change/grid value after."""
    from backend.nmrpipe_backend import _apply_nus_grid_after_clean
    from core.data.bruker_reader import read_dataset

    experiment = read_dataset(bruker_dir / "nus_2d")
    experiment.acquisition_parameters.setdefault("acqu2s", {})["NusTD"] = 292
    log = QcAuditLog(tmp_path)

    logs = _apply_nus_grid_after_clean(experiment, [(0,), (145,)], audit=log)

    assert logs and "Grid adjustment" in logs[0]
    action = read_audit(tmp_path)[0]
    assert action.action_taken == "nus_td_shrunk"
    assert action.location == "acqu2s.NusTD"
    assert action.before_state == {"NusTD": 292}
    assert action.after_state == {"NusTD": 146}
    assert action.extra["dataset_id"] == experiment.dataset_id


def test_nus_grid_adjustment_without_audit_still_returns_logs(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """The behaviour remains unchanged when the audit object is not passed (the wiring is
    additional)."""
    from backend.nmrpipe_backend import _apply_nus_grid_after_clean
    from core.data.bruker_reader import read_dataset

    experiment = read_dataset(bruker_dir / "nus_2d")
    experiment.acquisition_parameters.setdefault("acqu2s", {})["NusTD"] = 292
    logs = _apply_nus_grid_after_clean(experiment, [(0,), (145,)])
    assert logs
    assert read_audit(tmp_path) == []


def test_source_clean_removal_is_recorded(tmp_path: Path, bruker_dir: Path) -> None:
    """Source deletion bad point: recording action contains.bak backup semantics, and before
    changes/later sampling point number."""
    from backend.nmrpipe_backend import _record_source_clean
    from core.data.bruker_reader import read_dataset

    experiment = read_dataset(bruker_dir / "nus_2d")
    log = QcAuditLog(tmp_path)
    _record_source_clean(log, experiment, [bruker_dir / "nus_2d"], 144, [(300,)], True)

    action = read_audit(tmp_path)[0]
    assert action.action_taken.startswith("removed_from_source_ser_and_nuslist")
    assert "ser" in action.location and "nuslist" in action.location
    assert action.before_state == {"sampling_points": 145, "bad_points": 1}
    assert action.after_state == {"sampling_points": 144}
    assert action.extra["source_removed"] is True


def test_source_clean_without_removal_says_so(tmp_path: Path, bruker_dir: Path) -> None:
    """When the source cannot be deleted (the original data is not changed), it must be written
    truthfully as reported_only."""
    from backend.nmrpipe_backend import _record_source_clean
    from core.data.bruker_reader import read_dataset

    experiment = read_dataset(bruker_dir / "nus_2d")
    log = QcAuditLog(tmp_path)
    _record_source_clean(log, experiment, [bruker_dir / "nus_2d"], 144, [(300,)], False)
    action = read_audit(tmp_path)[0]
    assert action.action_taken.startswith("reported_only")
    assert action.extra["source_removed"] is False


def test_source_clean_without_bad_points_records_nothing(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Without bad points, there are no changes, and no noise records should be generated."""
    from backend.nmrpipe_backend import _record_source_clean
    from core.data.bruker_reader import read_dataset

    experiment = read_dataset(bruker_dir / "nus_2d")
    log = QcAuditLog(tmp_path)
    _record_source_clean(log, experiment, [bruker_dir / "nus_2d"], 146, [], False)
    assert read_audit(tmp_path) == []


def test_bad_point_repair_writes_audit_file_end_to_end(
    tmp_path: Path, nmrpipe_fid_template: Path, bruker_dir: Path
) -> None:
    """End-to-end: When diagnosing the actual disk change, qc_audit.jsonl appears in the working
    directory (Phase 10 core commitment). It shares the ``nmrpipe_fid_template`` fixture with
    ``test_direct_diagnostics.py``, so this link will be executed on any machine (including
    VM/CI)."""
    import numpy as np

    from core.data.bruker_reader import read_dataset
    from workflow.direct_diagnostics import _read_fid_raw, run_direct_diagnostics

    fid = tmp_path / "nus_2d.fid"
    fid.write_bytes(nmrpipe_fid_template.read_bytes())
    got = _read_fid_raw(fid)
    assert got is not None
    data, fdsize, _specnum, header = got
    row = int(np.argmax(np.sum(np.abs(data) ** 2, axis=1)))
    raw = bytearray(fid.read_bytes())
    re_off = header + row * fdsize * 8 + 128 * 4
    spike = complex(data[row, 128]) * 40.0
    np.frombuffer(raw, dtype="<f4", offset=re_off, count=1)[0] = spike.real
    np.frombuffer(raw, dtype="<f4", offset=re_off + fdsize * 4, count=1)[0] = spike.imag
    fid.write_bytes(bytes(raw))

    experiment = read_dataset(bruker_dir / "nus_2d")
    result = run_direct_diagnostics(tmp_path, experiment)
    assert result.repaired_badpoints >= 1
    assert audit_path(tmp_path).is_file(), "Changes must be recorded in a structured record"

    actions = read_audit(tmp_path)
    action = next(a for a in actions if a.extra.get("row") == row)
    assert action.action_taken == "neighbour_interpolation"
    assert action.before_state["count"] >= 1
    assert action.detection_rule
    assert action.timestamp and action.software_version
