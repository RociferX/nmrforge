"""fid.com 交叉检查/修补测试（参考 NMRFlow 实现）。"""

from __future__ import annotations

from pathlib import Path

from backend.bruker_workflow import (
    cross_check_fid_com,
    expected_values,
    parse_fid_com,
    patch_fid_com,
)
from core.data.bruker_reader import read_dataset

FID_COM = (
    "bruk2pipe -in ./ser \\\n"
    "  -bad 0.0 -aswap -AMX -decim 32 -dspfvs 21 -grpdly 48 \\\n"
    "  -xN 1024 -yN 128 -xT 512 -yT 64 \\\n"
    "  -xSW 10000.000 -ySW 2834.467 \\\n"
    "  -xOBS 599.894 -yOBS 60.798 \\\n"
    "  -xCAR 4.703 -yCAR 118.500 \\\n"
    "  -xLAB 1H -yLAB 15N -xMODE DQD -yMODE Complex \\\n"
    "  -out fid\n"
)


def test_parse_fid_com() -> None:
    parsed = parse_fid_com(FID_COM)
    assert parsed["xN"] == "1024"
    assert parsed["yLAB"] == "15N"
    assert parsed["decim"] == "32"


def test_expected_values_2d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    values = expected_values(exp)
    assert values["xN"][0] == 2048.0
    assert values["xT"][0] == 1024.0
    assert values["xLAB"][0] == "1H"
    assert values["yN"][0] == 256.0
    assert values["yMODE"][0] == "Complex"
    assert values["decim"][0] == 32.0
    assert values["dspfvs"][0] == 21.0
    assert values["grpdly"][0] == 48.0


def test_expected_values_3d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hnca_3d")
    values = expected_values(exp)
    assert values["xN"][0] == 2048.0
    assert values["yN"][0] == 96.0
    assert values["zN"][0] == 128.0
    assert values["yMODE"][0] == "Complex"
    assert values["zMODE"][0] == "Complex"


def test_cross_check_finds_diff(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    parsed = parse_fid_com(FID_COM)
    warnings = cross_check_fid_com(parsed, exp)
    assert any("xN" in w for w in warnings)
    assert any("yN" in w for w in warnings)


def test_patch_fid_com(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    patched, warnings = patch_fid_com(FID_COM, exp)
    assert any("xN" in w for w in warnings)
    assert "-xN 2048" in patched
    assert "-yN 256" in patched
    assert "-xT 1024" in patched
