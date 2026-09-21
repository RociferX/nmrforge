"""Bruker parameter parsing test."""

from __future__ import annotations

from pathlib import Path

from core.experiment.bruker_parser import parse_dataset_params, parse_param_file


def test_parse_acqus_scalars(bruker_dir: Path) -> None:
    params = parse_param_file(bruker_dir / "hsqc_2d" / "acqus")
    assert params["TD"] == 2048
    assert params["NUC1"] == "1H"
    assert params["PULPROG"] == "hsqcetfpf3gpsi.2"
    assert params["DSPFVS"] == 21
    assert params["FnMODE"] == 0
    assert isinstance(params["SW_h"], float)


def test_parse_acqu2s(bruker_dir: Path) -> None:
    params = parse_param_file(bruker_dir / "hsqc_2d" / "acqu2s")
    assert params["TD"] == 256
    assert params["NUC1"] == "15N"
    assert params["FnMODE"] == 5


def test_parse_dataset_params_order(bruker_dir: Path) -> None:
    parsed = parse_dataset_params(bruker_dir / "hsqc_2d")
    assert parsed["order"] == ["acqus", "acqu2s"]
    assert parsed["acqus"]["TD"] == 2048
    assert parsed["acqu2s"]["TD"] == 256
