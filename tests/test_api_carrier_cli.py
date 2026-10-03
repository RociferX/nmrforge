from __future__ import annotations

from types import SimpleNamespace

import pytest

import nmrforge_api.cli as cli
from nmrforge_api.session import DatasetRef


def _session(*, two_conditions: bool = False):
    datasets = [DatasetRef("exp_001", "d_001", condition="A")]
    if two_conditions:
        datasets.append(DatasetRef("exp_002", "d_002", condition="B"))
    return SimpleNamespace(
        datasets=datasets,
        manager=object(),
        root="fake-study",
        dataset_by_condition=lambda condition: next(
            (item for item in datasets if item.condition == condition), None
        ),
    )


def _patch_reference_cli(monkeypatch, *, session, built, available=None):
    monkeypatch.setattr(cli, "open_study", lambda *args, **kwargs: session)
    monkeypatch.setattr(cli, "load_reference", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "nmrforge_api.reference._reference_request",
        lambda *args, **kwargs: (None, None),
    )

    def read_experiment(_manager, _exp_id, data_id):
        axes = (available or {}).get(data_id, {"F1", "F2"})
        return SimpleNamespace(
            dimensions=[SimpleNamespace(logical_axis=axis) for axis in axes]
        )

    monkeypatch.setattr("workflow.stepwise.read_experiment", read_experiment)

    def build(_session, target, **kwargs):
        built.append((target.condition, kwargs["params"]))
        return SimpleNamespace(
            condition=target.condition,
            dataset_key=target.key,
            frozen_spectrum="spectrum.ft2",
            script_path="process.com",
            script_sha256="hash",
            phase_route="none",
            phase_record=lambda: {},
            sampling={},
            sampling_flags={},
            sweep_supported=True,
        )

    monkeypatch.setattr(cli, "build_reference", build)


def test_cli_carrier_values_override_common_then_condition_parameters(
    monkeypatch, capsys
) -> None:
    session = _session(two_conditions=True)
    built = []
    _patch_reference_cli(monkeypatch, session=session, built=built)
    monkeypatch.setattr(
        cli,
        "_load_mapping",
        lambda _path: {"carrier_ppm.F2": 4.0, "carrier_ppm": {"F1": 2.0}},
    )
    monkeypatch.setattr(
        cli,
        "_load_condition_params",
        lambda _path: {"A": {"carrier_ppm": {"F1": 7.0}}},
    )

    result = cli.main(
        [
            "reference",
            "--study",
            "fake-study",
            "--params",
            "params.yaml",
            "--condition-params",
            "conditions.json",
            "--carrier-ppm",
            "F2=0",
            "--carrier-ppm",
            "F1=-1.25",
            "--force",
        ]
    )

    assert result == 0
    assert built == [
        ("A", {"carrier_ppm": {"F1": 7.0, "F2": 0.0}}),
        ("B", {"carrier_ppm": {"F1": -1.25, "F2": 0.0}}),
    ]
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "values",
    [
        ["F1=1", "F1=2"],
        ["H=1"],
        ["F1=NaN"],
        ["F2=Inf"],
        ["F2=oops"],
        ["F2=1=2"],
        ["=1"],
    ],
)
def test_cli_rejects_invalid_or_duplicate_carrier_values(
    monkeypatch, values
) -> None:
    session = _session()
    built = []
    _patch_reference_cli(monkeypatch, session=session, built=built)
    argv = ["reference", "--study", "fake-study", "--force"]
    for value in values:
        argv.extend(["--carrier-ppm", value])

    assert cli.main(argv) == 2
    assert built == []


def test_cli_preflights_all_carrier_axes_before_building_any_reference(
    monkeypatch
) -> None:
    session = _session(two_conditions=True)
    built = []
    _patch_reference_cli(
        monkeypatch,
        session=session,
        built=built,
        available={"d_001": {"F1", "F2"}, "d_002": {"F2"}},
    )

    assert (
        cli.main(
            [
                "reference",
                "--study",
                "fake-study",
                "--carrier-ppm",
                "F1=0",
                "--force",
            ]
        )
        == 2
    )
    assert built == []


def test_cli_rejects_carrier_override_for_peak_table_only_rebuild(
    monkeypatch
) -> None:
    session = _session()
    monkeypatch.setattr(cli, "open_study", lambda *args, **kwargs: session)

    assert (
        cli.main(
            [
                "reference",
                "--study",
                "fake-study",
                "--rebuild-peak-tables",
                "--carrier-ppm",
                "F2=0",
            ]
        )
        == 2
    )
