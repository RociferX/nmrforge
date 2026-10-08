"""Ensure combinations reuse the condition's frozen reference FID evidence."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_nmrforge_api import _FakeSweepBackend

from backend.nmrpipe_backend import NMRPipeBackend
from nmrforge_api import run_combination_study, run_reference_study
from nmrforge_api.errors import ReferenceError


@pytest.mark.parametrize(
    ("dataset_name", "sampling", "capture_name"),
    [("hsqc_2d", "uniform", "process_calls"), ("nus_2d", "nus", "reconstruct_calls")],
)
def test_combination_reuses_condition_reference_fid_provenance(
    tmp_path: Path,
    bruker_dir: Path,
    dataset_name: str,
    sampling: str,
    capture_name: str,
) -> None:
    backend = _FakeSweepBackend()
    root = tmp_path / f"study_{sampling}"
    reference_result = run_reference_study(
        root,
        bruker_dir / dataset_name,
        params={"phase_route": "none"},
        backend=backend,
    )
    reference = reference_result.reference("A")
    assert reference is not None
    assert reference.run_id
    assert reference.condition == "A"

    calls = getattr(backend, capture_name)
    reference_calls = list(calls)
    assert reference_calls
    assert all("_reference_fid_only" not in call["params"] for call in reference_calls)

    conversions_before_combination = backend.convert_calls
    combination_result = run_combination_study(
        f"{root}#A",
        combos=[{"zero_fill": 1}] if sampling == "uniform" else [{"nSigma": 5.0}],
        backend=backend,
    )

    new_calls = calls[len(reference_calls):]
    candidate_calls = (
        [call for call in new_calls if call.get("out_file")]
        if sampling == "nus"
        else new_calls
    )
    assert len(candidate_calls) == 1
    marker = candidate_calls[0]["params"].get("_reference_fid_only")
    assert marker == {
        "provenance": reference.conversion_provenance,
        "segment_shift_hz": reference.params.get("segment_shift_hz", []),
    }
    assert backend.convert_calls == conversions_before_combination

    # The combination's phase provenance must point to this condition's frozen reference run.
    run = combination_result.runs[0]
    assert run.condition == reference.condition
    assert any(
        phase.get("source") == f"reference_run:{reference.run_id}"
        for phase in run.phase.values()
    )


class _RecordedBackend(_FakeSweepBackend):
    """Fake processing with the real conversion-record validator."""

    def __init__(self):
        super().__init__()
        self.probe = NMRPipeBackend(nmrpipe_bin="")

    def convert_to_fid(self, experiment, data_dir, progress=None, params=None, **kwargs):
        from core.data.sweep_width import apply_sweep_width_overrides

        experiment = apply_sweep_width_overrides(experiment, params)
        result = super().convert_to_fid(experiment, data_dir, progress, params, **kwargs)
        self.probe._record_conversion(
            self._work(), experiment.dataset_id, experiment.segments or data_dir, [],
            experiment=experiment,
        )
        return result

    def validate_reference_fid(self, experiment, work, context, params):
        return self.probe.validate_reference_fid(experiment, work, context, params)


@pytest.mark.parametrize("damage", ["fid", "evidence", "source", "old_reference"])
def test_preflight_requires_rebuild_even_when_a_combination_is_cached(
    tmp_path, bruker_dir, damage,
):
    import json

    backend = _RecordedBackend()
    root = tmp_path / "study"
    reference = run_reference_study(
        root, bruker_dir / "hsqc_2d", backend=backend, phase_route="none",
    ).reference("A")
    result = run_combination_study(root, combos=[{"zero_fill": 1}], backend=backend)
    assert result.runs and not result.failed_runs
    calls = len(backend.process_calls)
    work = Path(reference.work_dir)
    if damage == "fid":
        (work / f"{reference.data_id}.fid").unlink()
    elif damage == "evidence":
        (work / f"{reference.data_id}.fid.conversion.json").write_text("{}", encoding="utf-8")
    elif damage == "source":
        (Path(result.session.dataset.raw_dir) / "acqus").unlink()
    else:
        path = result.session.reference_dir_for(result.session.dataset) / "reference.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["conversion_provenance"].pop("reference_fid")
        path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReferenceError, match="force=True"):
        run_combination_study(root, combos=[{"zero_fill": 1}], backend=backend)
    assert backend.convert_calls == 1
    assert len(backend.process_calls) == calls


def test_preflight_reuses_reference_explicit_conversion_width(tmp_path, bruker_dir):
    backend = _RecordedBackend()
    root = tmp_path / "study"
    reference = run_reference_study(
        root, bruker_dir / "hsqc_2d", backend=backend, phase_route="none",
        params={"sweep_width_hz": {"F1": 2200.0}},
    ).reference("A")
    assert reference.params["sweep_width_hz"] == {"F1": 2200.0}
    result = run_combination_study(root, combos=[{"zero_fill": 1}], backend=backend)
    assert not result.failed_runs and backend.convert_calls == 1
