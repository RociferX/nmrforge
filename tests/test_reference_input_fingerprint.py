"""Complete reference input binding and fail-closed cache reuse."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nmrforge_api import (
    ReferenceError,
    add_dataset,
    build_reference,
    open_study,
    run_reference_study,
)
from nmrforge_api.direct_range import parse_direct_range
from nmrforge_api.reference import ReferenceSpectrum, _reference_request, validate_reference_input


def test_normalization_binds_all_inputs_and_equivalent_public_spellings() -> None:
    _, nested = _reference_request({"window": {"F1": {"off": 0.5}}, "ext_lo": 10.5,
                                    "ext_hi": 6.5, "phase_route": "none"})
    _, dotted = _reference_request({"window.F1.off": 0.5},
                                   parse_direct_range((6.5, 10.5)), "none")
    assert nested == dotted
    for params in ({"baseline": True}, {"zero_fill": 2}, {"window.F1.off": 0.75},
                   {"sampling.ft_neg_f1": True}, {"sweep_width_hz.F1": 1800}):
        assert _reference_request(params)[1] != _reference_request({})[1]


@pytest.mark.parametrize("params", [{"x": float("nan")}, {"x": float("inf")}, {"x": object()}])
def test_nonfinite_or_unserializable_request_is_rejected(params: dict) -> None:
    with pytest.raises(ReferenceError):
        _reference_request(params)


def test_legacy_and_tampered_fingerprints_fail_closed() -> None:
    reference = ReferenceSpectrum(dataset_key="A", exp_id="exp", data_id="data")
    with pytest.raises(ReferenceError, match="force=True"):
        validate_reference_input(reference, {})
    reference.input_fingerprint = _reference_request({})[1]
    validate_reference_input(ReferenceSpectrum.from_dict(reference.to_dict()), {})
    reference.input_fingerprint["params"]["window"] = {"F1": {"off": 0.75}}
    with pytest.raises(ReferenceError, match="force=True"):
        validate_reference_input(reference, {})


def test_window_change_is_not_silently_ignored(tmp_path: Path, bruker_dir: Path) -> None:
    from test_nmrforge_api import _FakeSweepBackend

    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "study", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    first = build_reference(session, params={"window.F1.off": 0.50}, phase_route="none")
    path = Path(first.frozen_spectrum).parent / "reference.json"
    original = path.read_bytes()
    calls = len(backend.process_calls)
    reused = build_reference(session, params={"window": {"F1": {"off": 0.50}}}, phase_route="none")
    assert reused.run_id == first.run_id and len(backend.process_calls) == calls
    with pytest.raises(ReferenceError, match="force=True"):
        build_reference(session, params={"window.F1.off": 0.75}, phase_route="none")
    assert path.read_bytes() == original and len(backend.process_calls) == calls
    rebuilt = build_reference(session, params={"window.F1.off": 0.75},
                              phase_route="none", force=True)
    assert rebuilt.run_id != first.run_id
    assert rebuilt.input_fingerprint["params"]["window"]["F1"]["off"] == 0.75
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["input_fingerprint"] == rebuilt.input_fingerprint


def test_study_preflights_all_conditions_before_rebuild(tmp_path: Path, bruker_dir: Path) -> None:
    from test_nmrforge_api import _FakeSweepBackend

    backend = _FakeSweepBackend()
    result = run_reference_study(tmp_path / "study", datasets={"A": bruker_dir / "hsqc_2d",
                                                             "B": bruker_dir / "hsqc_2d"},
                                 params={"phase_route": "none"}, backend=backend)
    calls = len(backend.process_calls)
    paths = [Path(ref.frozen_spectrum).parent / "reference.json"
             for ref in result.references.values()]
    # A has no cache; discovering B's mismatch must not process A first.
    paths[0].unlink()
    paths = paths[1:]
    originals = [path.read_bytes() for path in paths]
    with pytest.raises(ReferenceError, match="force=True"):
        run_reference_study(tmp_path / "study", params={"phase_route": "none"},
                            params_by_condition={"B": {"window.F1.off": 0.75}}, backend=backend)
    assert len(backend.process_calls) == calls
    assert [path.read_bytes() for path in paths] == originals


@pytest.mark.parametrize("params,route", [({}, "none"), ({"baseline": True}, None),
                                          ({"zero_fill": 2}, None),
                                          ({"ext_lo": 10.5, "ext_hi": 6.5}, None)])
def test_changed_or_omitted_input_requires_force(params: dict, route: str | None) -> None:
    reference = ReferenceSpectrum(dataset_key="A", exp_id="exp", data_id="data",
                                  input_fingerprint=_reference_request({"window.F1.off": 0.5})[1])
    with pytest.raises(ReferenceError, match="force=True"):
        validate_reference_input(reference, params, phase_route=route)
