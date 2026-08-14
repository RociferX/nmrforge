"""人工处理数据源测试:param_schema 结构 + render_scripts 确定性。"""

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
        # 0.2.67:ft_neg/ft_alt 允许 null(=按采集方式自动,保持默认输出不变)
        assert sampling[flag]["type"] in ("boolean", ["boolean", "null"])
        assert "default" in sampling[flag] and "description" in sampling[flag]
    assert sampling["ft_neg"]["default"] is None
    assert sampling["ft_alt"]["default"] is True  # True=按采集方式自动
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
    assert first == second  # 字节级一致
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



def test_param_schema_ext_keys() -> None:
    schema = param_schema()
    for key in ("ext_lo", "ext_hi", "extract"):
        assert key in schema["properties"]
        assert "default" in schema["properties"][key]
    assert schema["default"]["ext_lo"] == "11.0"
    assert schema["default"]["ext_hi"] == "6.0"
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
