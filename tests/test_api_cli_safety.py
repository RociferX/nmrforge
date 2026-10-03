from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nmrforge_api import cli
from ui_support.i18n import tr


def test_sweep_rejects_study_reference_root_mismatch_before_running(tmp_path, monkeypatch, capsys):
    study = tmp_path / "study"
    reference_root = tmp_path / "other-study"
    study.mkdir()
    reference_root.mkdir()
    grid = tmp_path / "grid.json"
    grid.write_text('{"axes": {"zero_fill": [1]}}', encoding="utf-8")
    called = False

    def should_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("sweep must reject before running")

    monkeypatch.setattr(cli, "run_combination_study", should_not_run)
    status = cli.main(
        ["sweep", "--study", str(study), "--reference", str(reference_root), "--grid", str(grid)]
    )

    captured = capsys.readouterr()
    assert status == 2
    assert not called
    assert captured.out == ""
    assert tr(
        "--study root must match --reference root; cross-study sweeps are not supported "
        "({p0} != {p1})", p0=study.resolve(), p1=reference_root.resolve(),
    ) in captured.err


def test_sweep_progress_is_stderr_and_result_is_json_stdout(tmp_path, monkeypatch, capsys):
    study = tmp_path / "study"
    study.mkdir()
    grid = tmp_path / "grid.json"
    grid.write_text('{"axes": {"zero_fill": [1]}}', encoding="utf-8")

    def run(*args, progress, **kwargs):
        progress("workflow progress")
        return SimpleNamespace(
            summary={"reference_spec": str(study), "status_counts": {"ok": 1}},
            plan=SimpleNamespace(n_workflows=1),
            references={"A": SimpleNamespace(condition="A")},
            records={"status": "written"},
        )

    monkeypatch.setattr(cli, "run_combination_study", run)
    status = cli.main(
        ["sweep", "--study", str(study), "--reference", str(study), "--grid", str(grid)]
    )

    captured = capsys.readouterr()
    assert status == 0
    assert "workflow progress" in captured.err
    payload = json.loads(captured.out)
    assert payload["mode"] == "combination"
    assert payload["workflows"] == 1


def test_reference_condition_params_option_defaults_to_none():
    args = cli.build_parser().parse_args(["reference", "--study", "."])
    assert args.condition_params is None


def test_reference_merges_condition_params_and_reports_progress_to_stderr(
    tmp_path, monkeypatch, capsys
):
    condition_file = tmp_path / "condition-params.json"
    condition_file.write_text('{"A": {"zero_fill": 4}}', encoding="utf-8")
    dataset = SimpleNamespace(condition="A", key="a")
    session = SimpleNamespace(datasets=[dataset])
    session.dataset_by_condition = lambda label: dataset if label == "A" else None
    monkeypatch.setattr(cli, "open_study", lambda *args, **kwargs: session)
    monkeypatch.setattr(cli, "load_reference", lambda *args, **kwargs: None)
    captured_params = []

    def build_reference(*args, params, progress, **kwargs):
        captured_params.append(params)
        progress("reference progress")
        return SimpleNamespace(
            condition="A", dataset_key="a", frozen_spectrum="ref.ft2",
            script_path="ref.com", script_sha256="sha", phase_route=None,
            phase_record=lambda: {}, sampling=None, sampling_flags={}, sweep_supported=True,
        )

    monkeypatch.setattr(cli, "build_reference", build_reference)
    status = cli.main([
        "reference", "--study", str(tmp_path), "--condition-params", str(condition_file),
    ])

    captured = capsys.readouterr()
    assert status == 0
    assert captured_params == [{"zero_fill": 4}]
    assert "reference progress" in captured.err
    assert json.loads(captured.out)["condition"] == "A"


def test_reference_rejects_unknown_condition_before_building(tmp_path, monkeypatch, capsys):
    condition_file = tmp_path / "condition-params.json"
    condition_file.write_text('{"unknown": {"zero_fill": 4}}', encoding="utf-8")
    session = SimpleNamespace(datasets=[SimpleNamespace(condition="A")])
    monkeypatch.setattr(cli, "open_study", lambda *args, **kwargs: session)
    monkeypatch.setattr(
        cli, "build_reference",
        lambda *args, **kwargs: pytest.fail("must validate condition labels before building"),
    )

    status = cli.main([
        "reference", "--study", str(tmp_path), "--condition-params", str(condition_file),
    ])

    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == ""
    assert tr("condition parameters name unknown condition(s): {p0}", p0="unknown") in captured.err


def test_reference_preflights_cache_before_any_condition_runs(tmp_path, monkeypatch, capsys):
    from nmrforge_api.reference import ReferenceSpectrum, _reference_request

    targets = [SimpleNamespace(condition=label, key=label) for label in ("A", "B")]
    session = SimpleNamespace(datasets=targets)
    monkeypatch.setattr(cli, "open_study", lambda *args, **kwargs: session)
    cached = ReferenceSpectrum(dataset_key="B", exp_id="exp", data_id="data",
                               input_fingerprint=_reference_request({"zero_fill": 2})[1])
    monkeypatch.setattr(cli, "load_reference",
                        lambda _, target: cached if target.key == "B" else None)
    monkeypatch.setattr(cli, "build_reference",
                        lambda *args, **kwargs: pytest.fail("must preflight"))
    status = cli.main(["reference", "--study", str(tmp_path)])
    output = capsys.readouterr()
    assert status == 2 and output.out == ""
    assert "force=True" in output.err
