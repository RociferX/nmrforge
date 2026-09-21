"""Fid.com cross check/patch test (refer to NMRFlow implementation)."""

from __future__ import annotations

from pathlib import Path

from backend.bruker_workflow import (
    apply_fid_com_overrides,
    cross_check_fid_com,
    expected_values,
    parse_fid_com,
    patch_fid_com,
    patch_fid_out_name,
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


def test_patch_fid_out_name_single(bruker_dir: Path) -> None:
    """0.2.163-patch13: Single file output name test.fid -> {dataset_id}.fid(automatic/manual
    alignment)."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    text = "bruk2pipe -in ./ser \\n  -out ./test.fid\n"
    patched, warnings = patch_fid_com(text, exp)
    assert f"-out ./{exp.dataset_id}.fid" in patched
    assert any("out" in w and "test.fid" in w for w in warnings)


def test_patch_fid_out_name_slice_kept() -> None:
    """Sliced output (fid/test%03d.fid) retains the bruker name and is not rewritten."""
    text = "bruk2pipe -in ./ser \\n  -out fid/test%03d.fid\n"
    patched, warnings = patch_fid_out_name(text, "d_001")
    assert "-out fid/test%03d.fid" in patched
    assert warnings == []


def test_apply_fid_com_overrides() -> None:
    """Manual parameter overwriting: only replace existing parameters, output name/The structure
    does not move, unknown keys will be skipped."""
    text = (
        "bruk2pipe -in ./ser \\n"
        "  -ySW 2834.467 -yCAR 118.500 \\n"
        "  -out ./d_001.fid\n"
    )
    patched, warnings = apply_fid_com_overrides(
        text, {"ySW": "2800.000", "nope": "1"}
    )
    assert "-ySW 2800.000" in patched
    assert "-yCAR 118.500" in patched
    assert "-out ./d_001.fid" in patched
    assert any("ySW" in w and "applied" in w for w in warnings)
    assert any("nope" in w and "not found" in w for w in warnings)


def test_patch_fid_com_nus_keeps_x_force_grid(
    bruker_dir: Path,
) -> None:
    """0.2.195:NUS Under xN/xT keep fid.com original value (ser row size after padding), yN/zN
    press NusTD grid correction, and force nusExpand to use the same grid."""
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "nus_3d")
    text = (
        "nusExpand.tcl -mode bruker -sampleCount 2 -off 0 \\\n"
        " -in ./ser -out ./ser_full -sample ./nuslist\n\n"
        "bruk2pipe -in ./ser_full \\\n"
        "  -xN 1024 -yN 166 -zN 4702 -xT 454 -yT 83 -zT 2351 \\\n"
        "  -out fid\n"
    )
    patched, warnings = patch_fid_com(text, exp)
    # XN/xT is not overridden by acqus TD.
    assert "-xN 1024" in patched
    assert "-xT 454" in patched
    # YN/zN corrected to NusTD grid(effective_td).
    from backend.bruker_workflow import _effective_td

    td = _effective_td(exp)
    assert f"-yN {td[1]}" in patched
    assert f"-zN {td[2]}" in patched
    # NusExpand forces the same grid (first call).
    first = patched.splitlines()[0]
    assert f"-yT {td[1] // 2}" in first
    assert f"-zT {td[2] // 2}" in first
    assert any("nusExpand grid" in w for w in warnings)


def test_patch_nus_expand_count() -> None:
    from backend.bruker_workflow import patch_nus_expand_count

    text = (
        "nusExpand.tcl -mode bruker -sampleCount 2 -off 0 \\n"
        " -in ./ser -out ./ser_full -sample ./nuslist\n\n"
        "nusExpand.tcl -mask -noexpand -sampleCount 2 -in ./test.fid \\n"
        " -out ./mask.fid -sample ./nuslist\n"
    )
    patched, warnings = patch_nus_expand_count(text, 700)
    assert len(warnings) == 2
    assert "-sampleCount 700" in patched
    assert "-sampleCount 2" not in patched
    assert all("sampleCount" in w for w in warnings)
