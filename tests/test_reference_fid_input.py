"""Strict reference FID reuse: real backend branches with engine calls stubbed."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from backend.conversion_provenance import read_conversion_provenance
from backend.nmrpipe_backend import NMRPipeBackend
from backend.reference_fid import freeze_fid_input
from core.data.bruker_reader import read_dataset, read_segments
from core.planning.method_selector import select_method
from workflow import field_drift


def _input(tmp_path, bruker_dir, dataset="hsqc_2d", *, segmented=False, sliced=False):
    raw = tmp_path / "raw"
    shutil.copytree(bruker_dir / dataset, raw)
    if segmented:
        other = tmp_path / "other"
        shutil.copytree(raw, other)
        experiment = read_segments([raw, other])
    else:
        experiment = read_dataset(raw)
    backend = NMRPipeBackend(nmrpipe_bin="")
    work = tmp_path / "work"
    work.mkdir()
    backend.work_dir = str(work)
    base = work / "merged" if segmented else work
    if sliced:
        fid = base / "fid" / "test001.fid"
        fid.parent.mkdir(parents=True, exist_ok=True)
        fid.write_bytes(b"converted-1")
        fid.with_name("test002.fid").write_bytes(b"converted-2")
    else:
        base.mkdir(exist_ok=True)
        fid = base / f"{experiment.dataset_id}.fid"
        fid.write_bytes(b"converted-1")
    if dataset == "nus_2d":
        shutil.copy2(raw / "nuslist", work / "nuslist")
    if segmented:
        field_drift.write_field_drift_record(work, {"checked": True, "rounds": []})
    sources = experiment.segments or raw
    backend._record_conversion(work, experiment.dataset_id, sources, [], experiment=experiment)
    provenance = read_conversion_provenance(work)
    freeze_fid_input(work, experiment.dataset_id, provenance)
    context = {"provenance": provenance, "segment_shift_hz": []}
    return backend, experiment, work, fid, context


@pytest.mark.parametrize("dataset", ["hsqc_2d", "hnca_3d", "nus_2d"])
@pytest.mark.parametrize("segmented,sliced", [(False, False), (False, True),
                                             (True, False), (True, True)])
def test_valid_reference_inputs_resolve_without_changes(
    tmp_path, bruker_dir, dataset, segmented, sliced,
):
    backend, experiment, work, fid, context = _input(
        tmp_path, bruker_dir, dataset, segmented=segmented, sliced=sliced,
    )
    before = {path.relative_to(work): path.read_bytes() for path in work.rglob("*")
              if path.is_file()}
    name, count = backend.validate_reference_fid(experiment, work, context, {})
    assert "merged/" in name if segmented else "merged/" not in name
    assert "%03d" in name if sliced else name.endswith(fid.name)
    assert (count > 0) == (dataset == "nus_2d")
    assert before == {path.relative_to(work): path.read_bytes() for path in work.rglob("*")
                      if path.is_file()}


@pytest.mark.parametrize("damage", ["missing", "empty", "resize", "same_size_content",
                                    "record_missing", "record_changed", "raw_changed",
                                    "schedule_changed", "old_reference", "shift"])
def test_invalid_reference_requires_rebuild_without_repair(tmp_path, bruker_dir, damage):
    backend, experiment, work, fid, context = _input(tmp_path, bruker_dir, "nus_2d")
    if damage == "missing":
        fid.unlink()
    elif damage == "empty":
        fid.write_bytes(b"")
    elif damage == "resize":
        fid.write_bytes(b"bad")
    elif damage == "same_size_content":
        fid.write_bytes(b"X" * fid.stat().st_size)
    elif damage == "record_missing":
        backend._conversion_record_path(work, experiment.dataset_id).unlink()
    elif damage == "record_changed":
        path = backend._conversion_record_path(work, experiment.dataset_id)
        record = json.loads(path.read_text(encoding="utf-8"))
        record["created_at"] = "changed"
        path.write_text(json.dumps(record), encoding="utf-8")
    elif damage == "raw_changed":
        (Path(experiment.source_path) / "acqus").write_bytes(b"changed")
    elif damage == "schedule_changed":
        (work / "nuslist").write_bytes(b"0\n")
    elif damage == "old_reference":
        context["provenance"].pop("reference_fid")
    params = {"segment_shift_hz": [12.0]} if damage == "shift" else {}
    before = {path: path.read_bytes() for path in work.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="force=True"):
        backend.validate_reference_fid(experiment, work, context, params)
    assert before == {path: path.read_bytes() for path in work.rglob("*") if path.is_file()}


def test_uniform_slice_branch_never_converts(tmp_path, bruker_dir, monkeypatch):
    backend, experiment, work, _, context = _input(
        tmp_path, bruker_dir, "hnca_3d", sliced=True,
    )
    monkeypatch.setattr(backend, "_bin_dir", lambda: tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("conversion must not run")

    monkeypatch.setattr(backend, "_convert", forbidden)
    monkeypatch.setattr(backend, "_convert_segments", forbidden)
    seen = []

    def process(*args, **kwargs):
        seen.append(kwargs["in_file"])
        spectrum = work / "candidate.ft3"
        spectrum.write_bytes(b"spectrum")
        return True, [], spectrum

    monkeypatch.setattr(backend, "_process", process)
    result = backend.process(experiment, select_method(experiment),
                             params={"_reference_fid_only": context}, direct_phase_search=False)
    assert result["success"] and seen == ["fid/test%03d.fid"]


@pytest.mark.parametrize("segmented", [False, True])
def test_nus_strict_branch_never_cleans_converts_or_changes_input(
    tmp_path, bruker_dir, monkeypatch, segmented,
):
    backend, experiment, work, _, context = _input(
        tmp_path, bruker_dir, "nus_2d", segmented=segmented,
    )
    monkeypatch.setattr(backend, "_bin_dir", lambda: tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("reference inputs must not be rewritten")

    for method in ("_clean_source_nus", "_clean_work_nuslist", "_convert", "_convert_segments",
                   "_zero_bad_point_fid", "_write_merged_nuslist"):
        monkeypatch.setattr(backend, method, forbidden)
    before = {path: path.read_bytes() for path in work.rglob("*") if path.is_file()}
    result = backend.reconstruct_nus(
        experiment, {"_reference_fid_only": context, "direct_phase_search": False,
                     "display_phase_search": False}, script_only=True,
    )
    assert result["success"]
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize("dataset", ["hsqc_2d", "nus_2d"])
def test_missing_fid_backend_fails_before_engine_or_repair(
    tmp_path, bruker_dir, monkeypatch, dataset,
):
    backend, experiment, _, fid, context = _input(tmp_path, bruker_dir, dataset)
    fid.unlink()
    monkeypatch.setattr(backend, "_bin_dir", lambda: tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("no conversion or source cleanup is allowed")

    for method in ("_convert", "_convert_segments", "_clean_source_nus"):
        monkeypatch.setattr(backend, method, forbidden)
    params = {"_reference_fid_only": context}
    if dataset == "nus_2d":
        result = backend.reconstruct_nus(experiment, params, script_only=True)
    else:
        result = backend.process(experiment, select_method(experiment), params=params)
    assert not result["success"] and "force=True" in result["message"]
