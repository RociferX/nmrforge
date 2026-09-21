"""Manually processed data source testing: param_schema structure + render_scripts deterministic."""

from __future__ import annotations

from pathlib import Path

from backend.script_generator import param_schema, render_scripts
from core.data.bruker_reader import read_dataset


def test_param_schema_structure() -> None:
    schema = param_schema()
    assert schema["type"] == "object"
    for key in ("zero_fill", "sampling", "stages"):
        assert key in schema["properties"]
    sampling = schema["properties"]["sampling"]["properties"]
    for flag in ("ft_neg", "ft_alt", "flip_f1", "auto_phase"):
        # 0.2.67:ft_neg/ft_alt Allow null (=automatic according to the collection method, keep the
        # default output unchanged).
        assert sampling[flag]["type"] in ("boolean", ["boolean", "null"])
        assert "default" in sampling[flag] and "description" in sampling[flag]
    assert sampling["ft_neg"]["default"] is None
    # Automatically according to the collection method.
    assert sampling["ft_alt"]["default"] is True
    assert sampling["flip_f1"]["default"] is False
    assert sampling["auto_phase"]["default"] is True
    stages = schema["properties"]["stages"]["items"]["properties"]
    for key in ("id", "tool", "macro", "params", "param_docs"):
        assert key in stages
    assert schema["default"]["zero_fill"] == 2


def test_render_scripts_uniform_deterministic(bruker_dir: Path) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    first = render_scripts(experiment, {"sampling": {"auto_phase": True}})
    second = render_scripts(experiment, {"sampling": {"auto_phase": True}})
    assert first == second  # Byte level consistency.
    assert sorted(first) == ["fid.com", "process.com"]
    assert first["fid.com"].startswith("#!/bin/csh")
    assert "nmrPipe -fn FT" in first["process.com"]
    assert "nmrPipe -fn SP" in first["process.com"]


def test_render_scripts_nus(bruker_dir: Path) -> None:
    experiment = read_dataset(bruker_dir / "nus_2d")
    scripts = render_scripts(experiment, {"nus": {"nsigma": 5.0, "thresh": 0.95}})
    assert "nus.com" in scripts
    assert "SMILE" in scripts["nus.com"]
    assert scripts["fid.com"].startswith("#!/bin/csh")
    again = render_scripts(experiment, {"nus": {"nsigma": 5.0, "thresh": 0.95}})
    assert again == scripts


def test_render_scripts_uniform_window_poly_time(bruker_dir: Path) -> None:
    """0.2.165:render_scripts Manual path transparent transmission
    window/direct_poly_time(uniform), consistent with the automatic final script (GM g1/g2
    Gaussian window + POLY -time before SP."""
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    scripts = render_scripts(
        experiment,
        {
            "window": {"F2": {"type": "gaussian", "g1": 4.0, "g2": 0.2}},
            "direct_poly_time": True,
        },
    )
    proc = scripts["process.com"]
    assert "| nmrPipe -fn GM -g1 4 -g2 0.2 \\" in proc
    assert proc.index("| nmrPipe -fn POLY -time") < proc.index("| nmrPipe -fn SP")


def test_render_scripts_nus_window_poly_time(bruker_dir: Path) -> None:
    """0.2.165:render_scripts Manual path transparent transmission window/direct_poly_time(NUS)."""
    experiment = read_dataset(bruker_dir / "nus_2d")
    scripts = render_scripts(
        experiment,
        {
            "nus": {"nsigma": 5.0, "thresh": 0.95},
            "window": {"F2": {"type": "gaussian", "g1": 3.0, "g2": 0.2}},
            "direct_poly_time": True,
        },
    )
    nus = scripts["nus.com"]
    # 0.2.199-patch11: direct dimension (F2) is fixed SP, gaussian is not used for SMILE step1.
    assert "GM" not in nus
    assert "| nmrPipe -fn SP -off 0.45 -end 0.98 -pow 1 -c 0.5 \\" in nus
    # 2D NUS:POLY -time should be in front of the window (time domain front).
    assert nus.index("| nmrPipe -fn POLY -time") < nus.index(
        "| nmrPipe -fn SP -off 0.45"
    )



def test_param_schema_ext_keys() -> None:
    schema = param_schema()
    for key in ("ext_lo", "ext_hi", "extract"):
        assert key in schema["properties"]
        assert "default" in schema["properties"][key]
    assert schema["default"]["ext_lo"] == "10.5"
    assert schema["default"]["ext_hi"] == "6.5"
    assert schema["default"]["extract"] is True



def test_param_schema_zero_fill_keys() -> None:
    schema = param_schema()
    zf = schema["properties"]["zero_fill"]
    assert zf["type"] == "integer" and zf["default"] == 2
    assert "linewidth_hz" in schema["properties"]
    assert schema["properties"]["linewidth_hz"]["type"] == "object"
    ppl = schema["properties"]["points_per_line"]
    assert ppl["type"] == "number" and ppl["default"] == 2.0
    assert schema["default"]["points_per_line"] == 2.0


def test_param_schema_baseline_key() -> None:
    schema = param_schema()
    assert "baseline" in schema["properties"]
    base = schema["default"]["baseline"]
    assert base["mode"] == "auto"
    assert base["axes"] == "all"
    assert base["enabled"] is True
