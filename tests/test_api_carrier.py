"""Explicit carrier input, actual conversion commands and reference FID invariants."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from backend.bruker_workflow import _axis_letters, parse_fid_com
from backend.carrier_override import apply_carrier_request
from backend.nmrpipe_backend import NMRPipeBackend
from backend.runtime import CompletedProcess
from backend.script_generator import generate_convert_script
from core.data.bruker_reader import read_dataset
from core.data.carrier import merge_carrier_params, normalize_carrier_ppm
from nmrforge_api import (
    DatasetError,
    ReferenceError,
    SweepError,
    add_dataset,
    build_reference,
    open_study,
    run_reference_study,
)
from nmrforge_api.reference import _reference_request
from nmrforge_api.sweep import _condition_base_params, plan_sweep, validate_axes


@pytest.mark.parametrize("value", [{}, None, [], {"N": 120}, {"F1": True},
                                   {"F2": "4.7"}, {"F1": float("nan")},
                                   {"F1": float("inf")}])
def test_invalid_carrier_is_rejected(value) -> None:
    with pytest.raises(ValueError, match="carrier_ppm"):
        normalize_carrier_ppm(value)


def test_zero_negative_and_keyword_precedence() -> None:
    assert merge_carrier_params({"carrier_ppm": {"F1": 120, "F2": 4.7}}, {"F2": 0}) == {
        "carrier_ppm": {"F1": 120, "F2": 0},
    }
    assert normalize_carrier_ppm({"F1": -1.5, "F2": 0}) == {"F1": -1.5, "F2": 0}
    with pytest.raises(ValueError, match="F3"):
        normalize_carrier_ppm({"F3": 1}, axes={"F1", "F2"})


@pytest.mark.parametrize("dataset", ["hsqc_2d", "nus_2d", "hnca_3d", "nus_3d"])
def test_actual_script_mapping_preserves_unrequested_axes(dataset, bruker_dir: Path) -> None:
    # 3D fixtures use two heteronuclei; axes, not nucleus names, determine CAR mapping.
    experiment = read_dataset(bruker_dir / dataset)
    original = copy.deepcopy(experiment)
    script = generate_convert_script(experiment)
    before = parse_fid_com(script)
    request = {"F1": 0.0, f"F{experiment.ndim}": -0.25}
    patched, audit = apply_carrier_request(script, experiment, request)
    parsed = parse_fid_com(patched)
    letters = _axis_letters(experiment.ndim)
    for axis, ppm in request.items():
        key = f"{letters[axis]}CAR"
        assert float(parsed[key]) == ppm
        assert audit["axes"][axis] == {"requested": ppm, "resolved": ppm,
                                      "source": "explicit_ppm", "conversion_key": key}
    for key, value in before.items():
        if key not in {f"{letters[axis]}CAR" for axis in request}:
            assert parsed[key] == value
    assert audit["script_sha256"] == hashlib.sha256(patched.encode()).hexdigest()
    assert experiment == original


def test_missing_carrier_option_fails_closed(bruker_dir: Path) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    with pytest.raises(ValueError, match="lacks"):
        apply_carrier_request("bruk2pipe -xCAR 4.7\n", experiment, {"F1": 120})


@pytest.mark.parametrize("text", ["# -yCAR 120\nbruk2pipe -xCAR 4.7\n",
                                  "bruk2pipe -xCAR 4.7 # -yCAR 120\n",
                                  "bruk2pipe -yCAR 120 -yCAR 121\n"])
def test_comments_and_duplicate_options_are_not_execution_evidence(text, bruker_dir: Path) -> None:
    with pytest.raises(ValueError, match="unique"):
        apply_carrier_request(text, read_dataset(bruker_dir / "hsqc_2d"), {"F1": 120})


@pytest.mark.parametrize("dataset,auto", [("hsqc_2d", True), ("nus_2d", True),
                                         ("hnca_3d", True), ("nus_3d", True),
                                         ("hsqc_2d", False), ("hnca_3d", False)])
def test_conversion_auto_and_fallback_use_explicit_carrier(
    dataset, auto, bruker_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    raw = tmp_path / "raw"
    shutil.copytree(bruker_dir / dataset, raw)
    experiment = read_dataset(raw)
    work = tmp_path / "work"
    work.mkdir()
    original = {path.name: path.read_bytes() for path in raw.glob("acqu*s")}
    backend = NMRPipeBackend(nmrpipe_bin="")
    monkeypatch.setattr("backend.nmrpipe_backend.find_tool",
                        lambda *args: Path("bruker") if auto else None)
    monkeypatch.setattr(backend, "_finalize_converted_fid", lambda *args, **kwargs: True)
    executed = []

    class Runtime:
        def run(self, argv, *, cwd=None, **kwargs):
            if argv[:2] == ["bruker", "-AUTO"]:
                (Path(cwd) / "fid.com").write_text(generate_convert_script(experiment),
                                                   encoding="utf-8")
            else:
                executed.append(Path(argv[1]).read_text(encoding="utf-8"))
            return CompletedProcess("", "", "", 0)

    request = {"F1": 121.125, f"F{experiment.ndim}": 0.0}
    assert backend._convert_dir(Runtime(), experiment, raw, work, "nus" in dataset, [],
                                carrier_ppm=request)
    assert len(executed) == 1
    parsed = parse_fid_com(executed[0])
    for axis, ppm in request.items():
        assert float(parsed[f"{_axis_letters(experiment.ndim)[axis]}CAR"]) == ppm
    audit = json.loads((work / f"{experiment.dataset_id}.carrier.json").read_text(encoding="utf-8"))
    explicit = audit["explicit_carrier"]
    assert explicit["command_evidence"] == executed[0]
    assert explicit["script_sha256"] == hashlib.sha256(executed[0].encode()).hexdigest()
    assert {axis: row["resolved"] for axis, row in explicit["axes"].items()} == request
    assert original == {path.name: path.read_bytes() for path in raw.glob("acqu*s")}


def test_build_reference_passes_carrier_and_binds_cache(tmp_path: Path, bruker_dir: Path) -> None:
    from test_nmrforge_api import _FakeSweepBackend

    class Backend(_FakeSweepBackend):
        def convert_to_fid(self, experiment, data_dir, **kwargs):
            self.last_conversion_params = copy.deepcopy(kwargs["params"])
            return super().convert_to_fid(experiment, data_dir, **kwargs)

    backend = Backend()
    session = open_study(tmp_path / "study", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    first = build_reference(session, carrier_ppm={"F1": 120, "F2": 0}, phase_route="none")
    assert backend.last_conversion_params["carrier_ppm"] == {"F1": 120, "F2": 0}
    assert first.params["carrier_ppm"] == first.sweep_params["carrier_ppm"] == {"F1": 120, "F2": 0}
    same = build_reference(session, params={"carrier_ppm.F1": 120.0, "carrier_ppm.F2": 0},
                           phase_route="none")
    assert first.run_id == same.run_id and backend.convert_calls == 1
    with pytest.raises(ReferenceError, match="force=True"):
        build_reference(session, carrier_ppm={"F1": 122, "F2": 0}, phase_route="none")
    assert backend.convert_calls == 1
    rebuilt = build_reference(session, carrier_ppm={"F1": 122, "F2": 0},
                              phase_route="none", force=True)
    assert rebuilt.run_id != first.run_id and backend.convert_calls == 2
    assert rebuilt.params["carrier_ppm"]["F1"] == 122


@pytest.mark.parametrize("dataset", ["hsqc_2d", "hnca_3d"])
def test_all_segments_execute_same_carrier_request(
    dataset, bruker_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    segments = [tmp_path / "raw1", tmp_path / "raw2"]
    for segment in segments:
        shutil.copytree(bruker_dir / dataset, segment)
    experiment = read_dataset(segments[0])
    experiment.segments = [str(path) for path in segments]
    backend = NMRPipeBackend(nmrpipe_bin="")
    monkeypatch.setattr("backend.nmrpipe_backend.find_tool", lambda *args: Path("bruker"))
    monkeypatch.setattr(backend, "_merge_single_fid", lambda *args, **kwargs: True)

    def finalize(raw, work, dataset_id, logs, **kwargs):
        (work / f"{dataset_id}.fid").write_bytes(b"mock fid")
        return True

    monkeypatch.setattr(backend, "_finalize_converted_fid", finalize)
    executed = []

    class Runtime:
        def run(self, argv, *, cwd=None, **kwargs):
            if argv[0] == "bruker":
                (Path(cwd) / "fid.com").write_text(generate_convert_script(experiment),
                                                   encoding="utf-8")
            else:
                executed.append(Path(argv[1]).read_text(encoding="utf-8"))
            return CompletedProcess("", "", "", 0)

    request = {"F1": 121.5, f"F{experiment.ndim}": 0.0}
    ok, logs = backend._convert_segments(Runtime(), experiment, tmp_path / "work", [],
                                         carrier_ppm=request)
    assert ok, logs
    assert len(executed) == 2
    for script in executed:
        parsed = parse_fid_com(script)
        for axis, ppm in request.items():
            assert float(parsed[f"{_axis_letters(experiment.ndim)[axis]}CAR"]) == ppm


@pytest.mark.parametrize("segmented", [False, True])
def test_conversion_entry_forwards_carrier_params(
    segmented, bruker_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    (bruker_dir / "hsqc_2d" / "ser").touch()
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    if segmented:
        experiment.segments = [str(bruker_dir / "hsqc_2d")]
    backend = NMRPipeBackend(nmrpipe_bin="")
    monkeypatch.setattr(backend, "_bin_dir", lambda: Path("nmrpipe"))
    monkeypatch.setattr(backend, "_work_path", lambda *args: tmp_path / "work")
    monkeypatch.setattr(backend, "_record_conversion", lambda *args, **kwargs: None)
    monkeypatch.setattr("backend.nmrpipe_backend.load_or_run_direct_diagnostics",
                        lambda *args: None)
    calls = []

    def convert(*args, **kwargs):
        calls.append(kwargs)
        return True, []

    monkeypatch.setattr(backend, "_convert_segments" if segmented else "_convert", convert)
    result = backend.convert_to_fid(experiment, bruker_dir / "hsqc_2d",
                                    params={"carrier_ppm": {"F1": 120, "F2": 0}})
    assert result["success"]
    assert calls[0]["carrier_ppm"] == {"F1": 120, "F2": 0}


def test_conditions_override_public_keyword_per_axis(tmp_path: Path, bruker_dir: Path) -> None:
    from test_nmrforge_api import _FakeSweepBackend

    result = run_reference_study(
        tmp_path / "study", datasets={"A": bruker_dir / "hsqc_2d", "B": bruker_dir / "hsqc_2d"},
        params={"carrier_ppm.F1": 119, "carrier_ppm.F2": 4.7}, carrier_ppm={"F1": 120},
        params_by_condition={"B": {"carrier_ppm.F2": 0}}, phase_route="none",
        backend=_FakeSweepBackend(), write=False,
    )
    by_condition = {ref.condition: ref.params["carrier_ppm"] for ref in result.references.values()}
    assert by_condition == {"A": {"F1": 120, "F2": 4.7}, "B": {"F1": 120, "F2": 0}}


def test_unknown_axis_preflight_before_any_backend_work(tmp_path: Path, bruker_dir: Path) -> None:
    from test_nmrforge_api import _FakeSweepBackend

    backend = _FakeSweepBackend()
    with pytest.raises(DatasetError, match="F3"):
        run_reference_study(
            tmp_path / "study", datasets={"A": bruker_dir / "hsqc_2d", "B": bruker_dir / "hsqc_2d"},
            carrier_ppm={"F1": 120}, params_by_condition={"B": {"carrier_ppm.F3": 1}},
            backend=backend,
        )
    assert backend.convert_calls == 0


def test_empty_condition_override_is_rejected_even_with_common_carrier(
    tmp_path: Path, bruker_dir: Path,
) -> None:
    from test_nmrforge_api import _FakeSweepBackend

    backend = _FakeSweepBackend()
    with pytest.raises(DatasetError, match="nonempty"):
        run_reference_study(tmp_path / "study", dataset=bruker_dir / "hsqc_2d",
                            carrier_ppm={"F1": 120}, params_by_condition={"A": {"carrier_ppm": {}}},
                            backend=backend)
    assert backend.convert_calls == 0


@pytest.mark.parametrize("key", ["carrier_ppm", "carrier_ppm.F1"])
def test_sweep_cannot_change_conversion_carrier(key) -> None:
    with pytest.raises(SweepError, match="conversion"):
        validate_axes({key: [120]}, sampling="uniform")


def test_base_overrides_carrier_are_locked(tmp_path: Path, bruker_dir: Path) -> None:
    from test_nmrforge_api import _FakeSweepBackend

    session = open_study(tmp_path / "study", backend=_FakeSweepBackend())
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, carrier_ppm={"F1": 120}, phase_route="none")
    plan = plan_sweep(reference, axes={"zero_fill": [2]})
    assert _condition_base_params(plan, reference)["carrier_ppm"] == {"F1": 120}
    plan.base_overrides = {"carrier_ppm.F1": 121}
    with pytest.raises(SweepError, match="frozen"):
        _condition_base_params(plan, reference)


def test_invalid_carrier_is_reference_error_before_fingerprint() -> None:
    with pytest.raises(ReferenceError, match="finite"):
        _reference_request({"carrier_ppm.F1": True})
