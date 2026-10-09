"""NMRPipe script generation tests (determinism + SOFTWARE_SUMMARY §6.2 parameter requirements)."""

from __future__ import annotations

from pathlib import Path

from backend.script_generator import (
    effective_td,
    generate_convert_script,
    generate_process_script,
)
from core.data.bruker_reader import read_dataset
from core.planning.method_selector import select_method


def _convert(exp) -> str:
    return generate_convert_script(exp)


def test_convert_script_2d_required_params(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    script = _convert(exp)
    assert script.startswith("#!/bin/csh")
    assert "-xMODE DQD" in script
    assert "-yMODE States-TPPI" in script  # FnMODE=5 → explicit keyword (2026-09-24 revision 2)
    # 2026-09-24: the format switches follow the bruker -AUTO rules — the fixture has
    # no DTYPA (non-float), so -noi2f/-ws 8 is omitted; only DECIM=32>1 adds
    # -AMX -decim -dspfvs -grpdly
    assert "-noi2f" not in script and "-ws 8" not in script
    assert "-aswap" in script and "-noaswap" not in script  # BYTORDA=0
    assert "-decim 32" in script and "-dspfvs 21" in script and "-grpdly 48" in script
    assert "-DMX" not in script
    assert "-aq2D Complex" in script  # FnMODE 5 (keyword form; numeric form deprecated)
    assert "-xN 2048" in script
    assert "-yN 256" in script
    assert "-ndim 2" in script
    assert "\r" not in script  # LF line endings


def test_convert_script_nus3d_td1_uses_nustd(bruker_dir: Path) -> None:
    """zN must use NusTD when acqu3s TD=1 (otherwise fid.com writes one file, not slices)."""
    exp = read_dataset(bruker_dir / "nus_3d")
    assert exp.ndim == 3
    assert exp.sampling.mode.value == "nus"
    td = effective_td(exp)
    assert td[2] == 128  # NusTD, not 1
    assert td[1] == 48  # acqu2s NusTD
    script = _convert(exp)
    assert "-zN 128" in script
    assert __import__("re").search(r"-zN 1(?![0-9])", script) is None
    assert "-yN 48" in script
    assert "-ndim 3" in script
    assert "-yMODE States-TPPI" in script  # F2=acqu2s FnMODE=5
    assert "-zMODE States" in script  # F1=acqu3s FnMODE=4


def test_convert_script_echo_antiecho_mode(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    exp.acquisition_parameters["acqu2s"]["FnMODE"] = 6
    script = _convert(exp)
    assert "-yMODE Echo-AntiEcho" in script  # F1 FnMODE=6 (official enum E-A)
    assert "-aq2D Complex" in script  # FnMODE 6 → Complex (deposited script writes States; same)


def test_nus_direct_window_always_sp() -> None:
    """0.2.199-patch11: the direct-dimension window is always SP (SMILE needs a decaying tail)."""
    from backend.script_generator import _nus_direct_window_line

    assert "SP" in _nus_direct_window_line(None, 2)
    assert "SP" in _nus_direct_window_line({"type": "none"}, 2)
    assert "SP" in _nus_direct_window_line({"type": "gaussian", "g1": 8.0, "g2": 15.0}, 2)
    assert "SP" in _nus_direct_window_line({"type": "exp", "lb": 5.0}, 1)
    sb = _nus_direct_window_line(
        {"type": "sine_bell", "off": 0.5, "end": 0.98, "pow": 2, "c": 0.5},
        2,
    )
    assert "SP -off 0.5" in sb


def test_real_modes_nus_rejected(bruker_dir: Path) -> None:
    """The SMILE (NUS) path explicitly rejects real indirect dimensions
    (reconstruction only supports complex encoding).

    A Bruker NUS sampler always generates FnMODE States-TPPI/Echo-Antiecho;
    TPPI/QSEQ/QF carry no quadrature information, so SMILE -nDim 3 cannot
    reconstruct them. The entry point raises instead of silently generating
    a wrong complex script (the uniform path already supports these modes).
    """
    from backend.script_generator import (
        generate_3d_nus_script,
        generate_nus_finalize_script,
    )

    exp = read_dataset(bruker_dir / "hsqc_2d")
    exp.acquisition_parameters["acqu2s"]["FnMODE"] = 3  # TPPI
    import pytest

    with pytest.raises(NotImplementedError, match="real/magnitude"):
        generate_nus_finalize_script(exp, planes="recon.ft1", out_file="e.ft2")
    exp3 = read_dataset(bruker_dir / "nus_3d")
    exp3.acquisition_parameters["acqu3s"]["FnMODE"] = 1  # QF
    with pytest.raises(NotImplementedError, match="FnMODE=1"):
        generate_3d_nus_script(exp3, in_file="e.fid", nuslist="nuslist", out_file="e.ft3")


def test_real_modes_uniform_scripts(bruker_dir: Path) -> None:
    """The uniform path generates correct scripts for real/magnitude modes (TPPI/QSEQ/QF).

    Basis: the NMRPipe official templates (bruk2pipe ACQUISITION MODES + the
    notilt2.com magnitude example) — TPPI→-yMODE TPPI/-aq2D TPPI/FT -real;
    QSEQ→-yMODE Sequential/-aq2D Complex/FT -bruk; QF→-yMODE Real/-aq2D
    Magnitude/FT+MC. 2026-09-24: -aq2D now uses keywords (the numeric form is
    deprecated by bruk2pipe and prints a warning). The real-family -yT is not
    halved (notilt2.com -yT ySize). magnitude has no phase node.
    """
    td_y = None
    for mode, mode_kw, aq2d, ft_line, has_mc in (
        (3, "-yMODE TPPI", "-aq2D TPPI", "| nmrPipe -fn FT -real \\", False),
        (2, "-yMODE Sequential", "-aq2D Complex", "| nmrPipe -fn FT -bruk \\", False),
        (1, "-yMODE Real", "-aq2D Magnitude", "| nmrPipe -fn FT \\", True),
    ):
        exp = read_dataset(bruker_dir / "hsqc_2d")
        td_y = int(exp.acquisition_parameters["acqu2s"]["TD"])
        exp.acquisition_parameters["acqu2s"]["FnMODE"] = mode
        plan = select_method(exp)
        script = generate_process_script(exp, plan, in_file="a.fid", out_file="a.ft2")
        assert ft_line in script, (mode, ft_line)
        assert ("| nmrPipe -fn MC \\" in script) == has_mc, mode
        assert script.count("| nmrPipe -fn PS") == (1 if mode == 1 else 2)  # QF: direct dim only
        conv = _convert(exp)
        assert mode_kw in conv, (mode, mode_kw)  # converted MODE keyword
        assert aq2d in conv, (mode, aq2d)
        assert f"-yT {td_y}" in conv  # real family: -yT is not halved
        assert "-yT " + str(td_y // 2) not in conv
    assert td_y is not None


def test_convert_script_states_mode(bruker_dir: Path) -> None:
    """Official enum FnMODE=4=States: conversion writes ``States`` + aq2D Complex (≡ States)."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    exp.acquisition_parameters["acqu2s"]["FnMODE"] = 4
    script = _convert(exp)
    assert "-yMODE States" in script  # States, not Echo-AntiEcho (explicit keyword)
    assert "-aq2D Complex" in script  # FnMODE 4 → Complex


def test_convert_script_3d_y_mode_from_acqu2s(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hnca_3d")
    script = _convert(exp)
    assert "-yMODE States-TPPI" in script  # F2=acqu2s FnMODE=5
    assert "-aq2D Complex" in script  # FnMODE 5 → Complex


def test_process_script_2d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    script = generate_process_script(exp, plan, in_file="test.fid", out_file="out.ft2")
    assert "xyz2pipe -in test.fid -x" in script
    assert "| nmrPipe -fn SP" in script
    assert "| nmrPipe -fn ZF" in script
    assert "| nmrPipe -fn FT" in script
    assert "| nmrPipe -fn PS" in script
    assert script.count("| nmrPipe -fn TP") == 2  # after direct + indirect dims (back-transposed)
    assert "| pipe2xyz -out out.ft2 -x" in script
    assert "\r" not in script


def test_process_script_3d_two_tps(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hnca_3d")
    plan = select_method(exp)
    script = generate_process_script(exp, plan, in_file="test.fid", out_file="out.ft3")
    assert script.count("| nmrPipe -fn TP") == 2
    assert "| pipe2xyz -out out.ft3 -x" in script
    # 3D single pass: add ZTP to move the slow dimension (F1) onto the FT axis + a final
    # TP; output layout (F2,F1,F3)
    assert script.count("| nmrPipe -fn ZTP") == 1
    # 3D first indirect dimension F2 (acqu2s FnMODE=5 States-TPPI) → FT -alt
    # (handedness undecidable ⇒ no -neg, 2026-09-24 second revision); second
    # indirect dimension F1 (acqu3s FnMODE=4 States) → no flag
    assert "| nmrPipe -fn FT -alt \\" in script
    assert "-neg" not in script
    assert "| nmrPipe -fn FT \\" in script


def test_scripts_deterministic(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    assert _convert(exp) == _convert(exp)
    assert generate_process_script(exp, plan, in_file="a.fid", out_file="a.ft2") == (
        generate_process_script(exp, plan, in_file="a.fid", out_file="a.ft2")
    )


def test_preview_script_2d_f2_keeps_complex(bruker_dir: Path) -> None:
    """F2 preview: F2 PS has no -di, indirect dimension F1 has -di, no zero filling."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    from backend.script_generator import generate_preview_script

    script = generate_preview_script(
        exp, plan, in_file="test.fid", out_file="p_F2.ft2", preview_axis="F2"
    )
    assert "| nmrPipe -fn PS -p0 0 -p1 0 \\" in script  # F2 has no -di
    assert "| nmrPipe -fn PS -p0 0 -p1 0 -di \\" in script  # F1 has -di
    assert "| nmrPipe -fn ZF" not in script  # no zero filling (same params as old phase candidates)
    assert script.count("| nmrPipe -fn PS") == 2
    assert script.count("| nmrPipe -fn POLY") == 1  # the searched axis F2 skips POLY
    assert "| pipe2xyz -out p_F2.ft2 -x" in script


def test_preview_script_2d_f1_fixed_phases_only_other_axes(bruker_dir: Path) -> None:
    """F1 preview: preview_axis itself keeps PS(0,0) with no -di; fixed phases
    apply only to the other axes."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    from backend.script_generator import generate_preview_script

    script = generate_preview_script(
        exp,
        plan,
        in_file="test.fid",
        out_file="p_F1.ft2",
        preview_axis="F1",
        fixed_phases={"F1": (10.0, -2.0), "F2": (5.0, 0.0)},
    )
    # the preview axis F1 must be PS(0,0) with no -di; the F1 fixed phase
    # passed in must not be applied
    assert "| nmrPipe -fn PS -p0 0 -p1 0 \\" in script
    assert "| nmrPipe -fn PS -p0 5 -p1 0 -di \\" in script  # F2 fixed phase applied
    assert "p0 10" not in script
    assert script.count("| nmrPipe -fn POLY") == 1  # the searched axis F1 skips POLY


def test_preview_script_3d(bruker_dir: Path) -> None:
    """3D F2 preview: F3/F1 have -di, F2 does not; no zero filling; 2 TPs."""
    exp = read_dataset(bruker_dir / "hnca_3d")
    plan = select_method(exp)
    from backend.script_generator import generate_preview_script

    script = generate_preview_script(
        exp, plan, in_file="test.fid", out_file="p_F2.ft3", preview_axis="F2"
    )
    assert script.count("| nmrPipe -fn PS") == 3
    assert script.count("| nmrPipe -fn PS -p0 0 -p1 0 -di \\") == 2
    assert "| nmrPipe -fn PS -p0 0 -p1 0 \\" in script
    assert script.count("| nmrPipe -fn TP") == 2
    assert script.count("| nmrPipe -fn ZTP") == 1  # slow-dimension ZTP + final TP → (F2,F1,F3)
    assert "| nmrPipe -fn ZF" not in script
    assert script.count("| nmrPipe -fn POLY") == 2  # the searched axis F2 skips POLY


def test_2d_nus_script(bruker_dir: Path) -> None:
    """Two stages: stage1 nmrPipe -in + SMILE (-sample None, -xT complex-point
    grid); stage2 nmrPipe -in recon.ft1 + FT -alt + -out -ov."""
    exp = read_dataset(bruker_dir / "nus_2d")
    from backend.script_generator import generate_2d_nus_script

    script = generate_2d_nus_script(
        exp,
        in_file="exp.fid",
        nuslist="nuslist",
        out_file="exp.ft2",
        nuslist_count=5,
        ext_lo="9.0",
        ext_hi="7.5",
        nsigma=7.0,
        thresh=0.85,
    )
    assert script.startswith("#!/bin/csh")
    assert "nmrPipe -in exp.fid \\" in script
    assert "| nmrPipe -fn SMILE -nDim 2" in script
    assert "-sample nuslist" in script  # pass the sampling list to SMILE when present
    assert "-sample None" in generate_2d_nus_script(
        exp, in_file="exp.fid", nuslist="", out_file="exp.ft2"
    )
    assert "-sampleCount 5" in script
    assert "-maxIter 1500" in script  # 5/128=3.9% -> lowest tier 1500
    assert "-xCT" not in script  # ordinary experiments get no cross terms (CT only)
    assert "-thresh 0.85" in script
    assert "-x1 9.0ppm -xn 7.5ppm" in script
    assert "-xT 128" in script  # F1 complex-point grid 256//2
    assert "| nmrPipe -fn FT -alt \\" in script  # States indirect dimension
    assert "nmrPipe -in nus2d/recon.ft1 \\" in script
    assert "  -out exp.ft2 -ov" in script
    assert "| pipe2xyz -out exp.ft2" not in script
    assert "\r" not in script


def test_smile_max_iter_tiers() -> None:
    """0.2.137: SMILE -maxIter is tiered by sampling rate (>0.5->300, >0.3->600,
    >0.15->1000, otherwise 1500)."""
    from backend.script_generator import smile_max_iter

    assert smile_max_iter(0.6) == 300
    assert smile_max_iter(0.5) == 600  # boundary: only >0.5 gives 300
    assert smile_max_iter(0.4) == 600
    assert smile_max_iter(0.3) == 1000  # boundary: only >0.3 gives 600
    assert smile_max_iter(0.2) == 1000
    assert smile_max_iter(0.15) == 1500  # boundary: only >0.15 gives 1000
    assert smile_max_iter(0.0) == 1500


def test_smile_cross_term_args_only_by_ct(bruker_dir: Path) -> None:
    """0.2.138: -xCT/-yCT depends only on whether the experiment is CT, not on sampling rate."""
    exp2 = read_dataset(bruker_dir / "nus_2d")
    exp3 = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import smile_cross_term_args

    assert smile_cross_term_args(exp2) == ""  # not added for an ordinary 2D
    assert smile_cross_term_args(exp3) == ""  # not added for an ordinary 3D
    # constant-time: the indirect dimension explicitly disables cross terms
    exp2.acquisition_parameters.setdefault("acqus", {})["PULPROG"] = "hsqcctetgpsp.2"
    assert smile_cross_term_args(exp2) == "-xCT 1 "
    exp3.acquisition_parameters.setdefault("acqus", {})["PULPROG"] = "cthsqcetgp.2"
    assert smile_cross_term_args(exp3) == "-xCT 1 -yCT 1 "


def test_3d_nus_script_max_iter_by_sampling(bruker_dir: Path) -> None:
    """-maxIter tracks the sampling rate: 0.6->300, 0.1->1500; without a count it
    falls back to the sf metadata."""
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import effective_td, generate_3d_nus_script

    td = effective_td(exp)
    grid = int(td[1]) * int(td[2])
    base = dict(in_file="e.fid", nuslist="nuslist", out_file="e.ft3")
    assert "-maxIter 300" in generate_3d_nus_script(exp, **base)  # sf=1.0 fallback
    hi = generate_3d_nus_script(exp, nuslist_count=int(grid * 0.6), **base)
    assert "-maxIter 300" in hi
    assert "-xCT" not in hi  # ordinary experiment (not CT): no cross-term arguments
    lo = generate_3d_nus_script(exp, nuslist_count=max(1, int(grid * 0.1)), **base)
    assert "-maxIter 1500" in lo
    assert "-xCT" not in lo  # ordinary experiment (not CT): no cross-term arguments
    exp.acquisition_parameters.setdefault("acqus", {})["PULPROG"] = "cthsqcetgp.2"
    ct = generate_3d_nus_script(exp, nuslist_count=int(grid * 0.8), **base)
    assert "-xCT 1 -yCT 1 -thresh" in ct  # CT experiments disable cross terms even at high sampling


def test_smile_max_mem_optional(bruker_dir: Path) -> None:
    """0.2.199-patch14: -maxMem is passed in by the caller from available memory;
    the line is omitted by default."""
    exp2 = read_dataset(bruker_dir / "nus_2d")
    exp3 = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import (
        generate_2d_nus_script,
        generate_3d_nus_script,
    )

    base2 = dict(in_file="e.fid", nuslist="nuslist", out_file="e.ft2")
    base3 = dict(in_file="e.fid", nuslist="nuslist", out_file="e.ft3")
    assert "-maxMem" not in generate_2d_nus_script(exp2, **base2)
    assert "-maxMem" not in generate_3d_nus_script(exp3, **base3)
    s2 = generate_2d_nus_script(exp2, max_mem=12.0, **base2)
    s3 = generate_3d_nus_script(exp3, max_mem=12.0, **base3)
    assert "-maxMem 12" in s2
    assert "-maxMem 12" in s3


def test_nus_finalize_script_2d(bruker_dir: Path) -> None:
    """Reconstruction-plane finalize (2D): nmrPipe -in + FT -alt + POLY + -out -ov,
    with PS configurable per dimension."""
    exp = read_dataset(bruker_dir / "nus_2d")
    from backend.script_generator import generate_nus_finalize_script

    script = generate_nus_finalize_script(exp, planes="nus2d/recon.ft1", out_file="e.ft2")
    assert "nmrPipe -in nus2d/recon.ft1 \\" in script
    assert "| nmrPipe -fn FT -alt \\" in script
    assert "| nmrPipe -fn PS -p0 0 -p1 0 -di" in script
    assert "| nmrPipe -fn POLY -auto" in script  # matches the verification s2.com
    assert "  -out e.ft2 -ov" in script
    assert "| nmrPipe -fn SMILE" not in script  # does not re-run SMILE
    phased = generate_nus_finalize_script(
        exp,
        planes="nus2d/recon.ft1",
        out_file="e.ft2",
        phases={"F1": (12.0, -3.0)},
    )
    assert "| nmrPipe -fn PS -p0 12 -p1 -3 -di" in phased
    assert "-neg" not in phased  # 2D indirect dim (F1) gets no -neg (unlike 3D F2)


def test_nus_finalize_script_3d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_nus_finalize_script

    phased = generate_nus_finalize_script(
        exp,
        planes="nus3d_rc/test%04d.ft1",
        out_file="e.ft3",
        phases={"F2": (12.0, -3.0), "F1": (5.0, 2.0)},
    )
    assert "xyz2pipe -in nus3d_rc/test%04d.ft1 -x" in phased
    assert "| nmrPipe -fn PS -p0 12 -p1 -3 -di" in phased
    assert "| nmrPipe -fn PS -p0 5 -p1 2 -di" in phased
    assert phased.count("| nmrPipe -fn TP") == 2
    # F2 (acqu2s FnMODE=5 States-TPPI) → FT -alt (handedness undecidable ⇒ no -neg);
    # F1 (acqu3s FnMODE=4 States) → no flag
    assert "| nmrPipe -fn FT -alt \\" in phased
    assert "-neg" not in phased
    assert "| nmrPipe -fn FT \\" in phased


def test_2d_nus_script_extract_off(bruker_dir: Path) -> None:
    """extract=False writes no EXT line; True keeps the current behaviour (default 6-11 ppm)."""
    exp = read_dataset(bruker_dir / "nus_2d")
    from backend.script_generator import generate_2d_nus_script

    on = generate_2d_nus_script(exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft2")
    assert "| nmrPipe -fn EXT" in on
    assert "-x1 10.5ppm -xn 6.5ppm" in on
    off = generate_2d_nus_script(
        exp,
        in_file="e.fid",
        nuslist="nuslist",
        out_file="e.ft2",
        extract=False,
    )
    assert "| nmrPipe -fn EXT" not in off


def test_3d_nus_script_extract_off(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    off = generate_3d_nus_script(
        exp,
        in_file="e.fid",
        nuslist="nuslist",
        out_file="e.ft3",
        extract=False,
    )
    assert "| nmrPipe -fn EXT" not in off
    on = generate_3d_nus_script(exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft3")
    assert "| nmrPipe -fn EXT" in on


def test_3d_nus_script(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(
        exp,
        in_file="exp.fid",
        nuslist="nuslist",
        out_file="exp.ft3",
        nuslist_count=4,
        ext_lo="9.0",
        ext_hi="7.5",
    )
    assert "-fn SMILE -nDim 3" in script
    assert "-sample nuslist" in script
    assert "| pipe2xyz -out exp.ft3 -x" in script
    assert script.count("| nmrPipe -fn TP") == 2
    assert "\r" not in script
    # F2 (acqu2s FnMODE=5 States-TPPI) → FT -alt (handedness undecidable ⇒ no -neg);
    # F1 (acqu3s FnMODE=4 States) → no flag
    assert "| nmrPipe -fn FT -alt \\" in script
    assert "-neg" not in script
    assert "| nmrPipe -fn FT \\" in script
    assert generate_3d_nus_script(
        exp, in_file="exp.fid", nuslist="nuslist", out_file="exp.ft3"
    ) == generate_3d_nus_script(exp, in_file="exp.fid", nuslist="nuslist", out_file="exp.ft3")


def test_3d_nus_script_smile_tuning(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(
        exp,
        in_file="exp.fid",
        nuslist="nuslist",
        out_file="exp.ft3",
        nsigma=5.0,
        thresh=0.99,
        smile_scaling=True,
        smile_report=2,
    )
    assert "-nSigma 5" in script
    assert "-thresh 0.99" in script
    assert "-xApod" not in script  # SMILE carries no window (step3 post-processing handles it)
    assert "-xP0 0 -xP1 0" in script  # F2 passes the default phase explicitly
    assert "-xAlt" in script  # F2=States-TPPI: consistent with step3 FT -alt
    assert "-xNeg" not in script  # handedness undecidable (no pulse program) ⇒ no negation
    assert "-yNeg" not in script  # F1=States: no direction flag
    assert "-yAlt" not in script  # F1=States: no direction flag
    assert "-scaling 1" in script
    assert "-report 2" in script


def test_select_smile_params_is_one_tier_for_every_fraction() -> None:
    from backend.script_generator import select_smile_params

    assert select_smile_params(0.04) == (5.0, 0.95)
    assert select_smile_params(0.3) == (5.0, 0.95)
    assert select_smile_params(0.8) == (5.0, 0.95)


def test_3d_nus_script_default_smile_params(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(exp, in_file="exp.fid", nuslist="nuslist", out_file="exp.ft3")
    assert "-xApod" not in script  # SMILE carries no window (step3 post-processing handles it)
    assert "-xP0 0 -xP1 0" in script  # F2 passes the default phase explicitly
    assert "-xAlt" in script  # F2=States-TPPI: consistent with step3 FT -alt
    assert "-xNeg" not in script  # handedness undecidable (no pulse program) ⇒ no negation
    assert "-yNeg" not in script  # F1=States: no direction flag
    assert "-yAlt" not in script  # F1=States: no direction flag
    assert "-scaling 1" in script


def test_smile_direction_flags_match_step3(bruker_dir: Path) -> None:
    """The direction flags inside the SMILE command come from the same source as
    the step3 FT line (the same _FT_FLAGS + sampling override): in the nus_3d
    fixture F2=States-TPPI (5) gives -xAlt and step3 FT -alt (handedness
    undecidable ⇒ no -neg); F1=States (4) has no y flag and step3 FT has none
    either; for 2D F1=5 it is -xAlt and the final FT is -alt; a flip_f1
    override stays in sync between SMILE and step3.
    """
    from backend.script_generator import generate_2d_nus_script, generate_3d_nus_script

    exp3 = read_dataset(bruker_dir / "nus_3d")
    s = generate_3d_nus_script(exp3, in_file="e.fid", nuslist="nuslist", out_file="e.ft3")
    assert "-xAlt" in s and "| nmrPipe -fn FT -alt \\" in s
    assert "-xNeg" not in s and "-neg" not in s
    assert "-yNeg" not in s and "-yAlt" not in s
    s = generate_3d_nus_script(
        exp3,
        in_file="e.fid",
        nuslist="nuslist",
        out_file="e.ft3",
        sampling={"flip_f1": True},
    )
    assert "-yNeg" in s and "| nmrPipe -fn FT -neg" in s  # F1 flip stays in sync
    exp2 = read_dataset(bruker_dir / "hsqc_2d")
    s2 = generate_2d_nus_script(exp2, in_file="e.fid", nuslist="nuslist", out_file="e.ft2")
    assert "-xAlt" in s2 and "| nmrPipe -fn FT -alt" in s2
    assert "-xNeg" not in s2


def test_process_script_direct_phase(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    script = generate_process_script(
        exp,
        plan,
        in_file="a.fid",
        out_file="a.ft2",
        direct_phase={"F2": (12.0, -3.0)},
    )
    assert "| nmrPipe -fn PS -p0 12 -p1 -3 -di \\" in script


def test_3d_nus_script_direct_phase(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(
        exp,
        in_file="e.fid",
        nuslist="nuslist",
        out_file="e.ft3",
        direct_phase=(12.0, -3.0),
    )
    assert "| nmrPipe -fn PS -p0 12 -p1 -3 -di \\" in script


def test_smile_phase_args_follow_logical_axes_and_keep_direct_phase_separate(
    bruker_dir: Path,
) -> None:
    """SMILE P0/P1 match indirect-axis PS; direct phase stays in its own PS stage."""
    from backend.script_generator import generate_2d_nus_script, generate_3d_nus_script

    exp2 = read_dataset(bruker_dir / "nus_2d")
    base2 = dict(nuslist="nuslist", out_file="e.ft2")
    # A single-file 2D NUS script maps SMILE x to F1; direct F2 phase is separate.
    s2 = generate_2d_nus_script(
        exp2, in_file="e.fid", direct_phase=(12.0, -3.0),
        phases={"F1": (-8.0, 2.5)}, **base2
    )
    smile2 = s2.split("-fn SMILE", 1)[1].split("| pipe2xyz", 1)[0]
    assert "-xP0 -8 -xP1 2.5" in smile2
    assert "| nmrPipe -fn PS -p0 -8 -p1 2.5 -di" in s2
    assert "| nmrPipe -fn PS -p0 12 -p1 -3 -di" in s2
    # The multi-file/sliced input path shares the same SMILE axis mapping.
    sliced2 = generate_2d_nus_script(
        exp2, in_file="planes/test%03d.fid", direct_phase=(12.0, -3.0),
        phases={"F1": (-8.0, 2.5)}, **base2,
    )
    smile2_sliced = sliced2.split("-fn SMILE", 1)[1].split("| pipe2xyz", 1)[0]
    assert "-xP0 -8 -xP1 2.5" in smile2_sliced
    assert "-xP0 12" not in smile2_sliced

    exp3 = read_dataset(bruker_dir / "nus_3d")
    base3 = dict(nuslist="nuslist", out_file="e.ft3")
    default3 = generate_3d_nus_script(exp3, in_file="e.fid", **base3)
    default_smile3 = default3.split("-fn SMILE", 1)[1].split("| pipe2xyz", 1)[0]
    assert "-xP0 0 -xP1 0" in default_smile3  # x is F2
    assert "-yP0 0 -yP1 0" in default_smile3  # y is F1

    s3 = generate_3d_nus_script(
        exp3, in_file="e.fid", direct_phase=(-21.0, 4.0),
        phases={"F2": (-11.0, 3.25), "F1": (7.0, -2.5)}, **base3,
    )
    smile3 = s3.split("-fn SMILE", 1)[1].split("| pipe2xyz", 1)[0]
    assert "-xP0 -11 -xP1 3.25" in smile3
    assert "-yP0 7 -yP1 -2.5" in smile3
    assert "| nmrPipe -fn PS -p0 -11 -p1 3.25 -di" in s3
    assert "| nmrPipe -fn PS -p0 7 -p1 -2.5 -di" in s3
    assert "| nmrPipe -fn PS -p0 -21 -p1 4 -di" in s3
    assert "-xP0 -21" not in smile3 and "-yP0 -21" not in smile3

    # Direction overrides remain orthogonal to phase arguments.
    flipped = generate_3d_nus_script(
        exp3, sampling={"ft_neg": True}, phases={"F2": (1.0, -1.0)},
        in_file="e.fid", **base3
    )
    flipped_smile = flipped.split("-fn SMILE", 1)[1].split("| pipe2xyz", 1)[0]
    assert "-xNeg" in flipped_smile and "-xP0 1 -xP1 -1" in flipped_smile
    assert "-xAlt" in flipped_smile  # sampling neg override preserves inferred alt
    assert "-yP0 0 -yP1 0" in flipped_smile


def test_process_script_ext_default_6_11(bruker_dir: Path) -> None:
    """The processing script adds EXT after the direct-dimension FT; the default
    region is 6-11 ppm (after PS, before TP)."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    script = generate_process_script(exp, plan, in_file="a.fid", out_file="a.ft2")
    lines = script.splitlines()
    ps_index = next(i for i, line in enumerate(lines) if "| nmrPipe -fn PS" in line)
    ext_index = next(i for i, line in enumerate(lines) if "| nmrPipe -fn EXT" in line)
    tp_index = next(i for i, line in enumerate(lines) if "| nmrPipe -fn TP" in line)
    assert ps_index < ext_index < tp_index
    assert "| nmrPipe -fn EXT -x1 10.5ppm -xn 6.5ppm -sw -round 2" in script


def test_process_script_extract_disabled_and_custom(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    off = generate_process_script(exp, plan, in_file="a.fid", out_file="a.ft2", extract=False)
    assert "| nmrPipe -fn EXT" not in off
    custom = generate_process_script(
        exp,
        plan,
        in_file="a.fid",
        out_file="a.ft2",
        ext_lo="9.0",
        ext_hi="7.5",
    )
    assert "| nmrPipe -fn EXT -x1 9.0ppm -xn 7.5ppm" in custom


def test_process_script_baseline_default_and_overrides(bruker_dir: Path) -> None:
    """POLY -auto on every dimension by default; the baseline config can disable
    it or change the order."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    default = generate_process_script(exp, plan, in_file="a.fid", out_file="a.ft2")
    assert default.count("| nmrPipe -fn POLY -auto") == 2  # F2 + F1
    off = generate_process_script(
        exp,
        plan,
        in_file="a.fid",
        out_file="a.ft2",
        baseline={"F2": {"enabled": False}},
    )
    assert off.count("| nmrPipe -fn POLY") == 1
    ordered = generate_process_script(
        exp,
        plan,
        in_file="a.fid",
        out_file="a.ft2",
        baseline={"F1": {"mode": "order", "order": 2}},
    )
    assert "| nmrPipe -fn POLY -ord 2" in ordered


def test_2d_nus_script_baseline_insert(bruker_dir: Path) -> None:
    """Two-stage baseline: stage1 F2 POLY comes after EXT, stage2 F1 POLY after PS."""
    exp = read_dataset(bruker_dir / "nus_2d")
    from backend.script_generator import generate_2d_nus_script

    script = generate_2d_nus_script(exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft2")
    assert script.count("| nmrPipe -fn POLY -auto") == 2
    stage2 = script.split("# stage 2:")[1].splitlines()
    ps_i = next(i for i, line in enumerate(stage2) if "| nmrPipe -fn PS" in line)
    assert "| nmrPipe -fn POLY" in stage2[ps_i + 1]
    off = generate_2d_nus_script(
        exp,
        in_file="e.fid",
        nuslist="nuslist",
        out_file="e.ft2",
        baseline={"F1": {"enabled": False}},
    )
    assert off.count("| nmrPipe -fn POLY") == 1


def test_3d_nus_script_baseline_insert(bruker_dir: Path) -> None:
    """NUS 3D: POLY is inserted after the direct-dimension EXT and after each of
    the F2/F1 PS lines (3 lines in total)."""
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft3")
    assert script.count("| nmrPipe -fn POLY -auto") == 3


def test_effective_td_2d_nus_complex_grid(bruker_dir: Path) -> None:
    """2D NUS: F1 uses the complex-point grid TD//mult; an acqu2s NusTD=TD is not trusted."""
    exp = read_dataset(bruker_dir / "nus_2d")
    exp.acquisition_parameters["acqu2s"]["NusTD"] = 256  # some datasets have NusTD=TD
    from backend.script_generator import effective_td

    td = effective_td(exp)
    assert td[1] == 128  # 256 // 2 (States)
    exp3 = read_dataset(bruker_dir / "nus_3d")
    td3 = effective_td(exp3)
    assert td3[1] == 48 and td3[2] == 128  # 3D keeps NusTD (already a complex-point count)


def test_effective_td_compact_2d_nus_uses_full_nustd(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_2d")
    exp.dimensions[1].td = 62
    exp.acquisition_parameters["acqu2s"].update(TD=62, NusTD=124, FnMODE=6)
    assert effective_td(exp)[1] == 62


def test_process_script_window_and_zero_fill_overrides(
    bruker_dir: Path,
) -> None:
    """Window/zero-fill overrides: a GM (g1/g2) window replaces SP; the ZF line is
    omitted when F1 is not zero-filled."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    from backend.script_generator import generate_process_script

    script = generate_process_script(
        exp,
        plan,
        in_file="a.fid",
        out_file="a.ft2",
        window={"F2": {"type": "gaussian", "g1": 4.0, "g2": 0.2}},
        zero_fill={"F1": {"mode": "none"}},
    )
    assert "| nmrPipe -fn GM -g1 4 -g2 0.2 \\" in script
    assert script.count("| nmrPipe -fn SP") == 1  # F1 still uses the default SP
    # F1 is not zero-filled: no ZF line after FT (F2 keeps the default 2×TD zero fill)
    assert script.count("| nmrPipe -fn ZF") == 1
    off = generate_process_script(
        exp,
        plan,
        in_file="a.fid",
        out_file="a.ft2",
        zero_fill={"F2": {"mode": "none"}, "F1": {"mode": "none"}},
    )
    assert "| nmrPipe -fn ZF" not in off


def _pow2_ge(value: int) -> int:
    return 1 << max(0, int(value) - 1).bit_length()


def test_zero_fill_plan_direct_and_indirect(bruker_dir: Path) -> None:
    """Direct dimension SI=2×TD; the indirect dimension follows the target digital
    resolution and is limited by 1/AQ (0.2.39)."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    from backend.script_generator import effective_td, zero_fill_plan

    td = effective_td(exp)
    plan = zero_fill_plan(exp)
    # direct dimension: power of two of 2×TD
    assert plan["F2"]["size"] == _pow2_ge(2 * td[0])
    # indirect dimension: at least TD, at most next_pow2(2×TD) (points_per_line defaults to 2)
    assert td[1] <= plan["F1"]["size"] <= _pow2_ge(2 * td[1])
    # narrower lines get more zero filling (a broad 60 Hz line relaxes the target
    # spacing and shrinks SI)
    narrow = zero_fill_plan(exp, linewidth_hz={"F1": 5.0})
    wide = zero_fill_plan(exp, linewidth_hz={"F1": 60.0})
    assert narrow["F1"]["size"] >= wide["F1"]["size"]
    # override semantics: 0=auto; int k=k×TD for the indirect dimension; a per-axis none disables it
    assert zero_fill_plan(exp, 0) == zero_fill_plan(exp)
    fixed = zero_fill_plan(exp, 2)
    assert fixed["F2"]["size"] == _pow2_ge(2 * td[0])
    assert fixed["F1"]["size"] == _pow2_ge(2 * td[1])
    off = zero_fill_plan(exp, {"F1": {"mode": "none"}})
    assert off["F1"]["mode"] == "none" and off["F1"]["size"] is None
    assert off["F2"]["size"] == plan["F2"]["size"]


def test_2d_nus_script_zero_fill_plan(bruker_dir: Path) -> None:
    """Two-stage NUS ZF uses the zero-fill plan: direct dimension 2×TD, the indirect
    dimension dynamic per the reconstruction grid."""
    exp = read_dataset(bruker_dir / "nus_2d")
    from backend.script_generator import (
        effective_td,
        generate_2d_nus_script,
        zero_fill_plan,
    )

    plan = zero_fill_plan(exp)
    script = generate_2d_nus_script(exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft2")
    assert f"| nmrPipe -fn ZF -zf -size {plan['F2']['size']} \\" in script
    assert f"| nmrPipe -fn ZF -size {plan['F1']['size']} \\" in script
    # the NUS indirect dimension uses the reconstructed complex-point grid as TD
    # (independent of the SMILE reconstruction)
    td = effective_td(exp)
    assert plan["F1"]["size"] >= td[1]
    # indirect dimension disabled: stage2 emits no ZF line
    off = generate_2d_nus_script(
        exp,
        in_file="e.fid",
        nuslist="nuslist",
        out_file="e.ft2",
        zero_fill={"F1": {"mode": "none"}},
    )
    assert "| nmrPipe -fn ZF -size" not in off
    assert "| nmrPipe -fn ZF -zf -size" in off  # direct dimension kept


def test_finalize_script_zero_fill_plan(bruker_dir: Path) -> None:
    """The indirect-dimension ZF of finalize (reused by phase optimisation) uses the
    plan SI and can be disabled."""
    exp = read_dataset(bruker_dir / "nus_2d")
    from backend.script_generator import (
        generate_nus_finalize_script,
        zero_fill_plan,
    )

    plan = zero_fill_plan(exp)
    script = generate_nus_finalize_script(exp, planes="nus2d/recon.ft1", out_file="e.ft2")
    assert f"| nmrPipe -fn ZF -size {plan['F1']['size']} \\" in script
    off = generate_nus_finalize_script(
        exp,
        planes="nus2d/recon.ft1",
        out_file="e.ft2",
        zero_fill={"F1": {"mode": "none"}},
    )
    assert "| nmrPipe -fn ZF" not in off


def test_3d_nus_script_phases_baked(bruker_dir: Path) -> None:
    """Full-script final run: the direct-dimension phase comes after EXT (the same
    normalisation as the in-memory rotation of the recon plane), and the indirect-
    dimension phases are baked into step3 PS with no redundant PS(0,0) line."""
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(
        exp,
        in_file="e.fid",
        nuslist="nuslist",
        out_file="e.ft3",
        direct_phase=(12.0, -3.0),
        phases={"F2": (10.0, -5.0), "F1": (20.0, 3.0)},
    )
    lines = script.splitlines()
    ext_i = next(i for i, line in enumerate(lines) if "| nmrPipe -fn EXT" in line)
    step1_ps = next(
        i for i, line in enumerate(lines) if "| nmrPipe -fn PS -p0 12 -p1 -3 -di" in line
    )
    assert step1_ps > ext_i  # direct-dimension phase after EXT
    assert "| nmrPipe -fn PS -p0 10 -p1 -5 -di \\" in script
    assert "| nmrPipe -fn PS -p0 20 -p1 3 -di \\" in script
    assert script.count("| nmrPipe -fn PS") == 3  # step1 + F2 + F1, no redundant 0 line


def test_2d_nus_script_phases_baked(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_2d")
    from backend.script_generator import generate_2d_nus_script

    script = generate_2d_nus_script(
        exp,
        in_file="e.fid",
        nuslist="nuslist",
        out_file="e.ft2",
        direct_phase=(12.0, -3.0),
        phases={"F1": (20.0, 3.0)},
    )
    lines = script.splitlines()
    ext_i = next(i for i, line in enumerate(lines) if "| nmrPipe -fn EXT" in line)
    step1_ps = next(
        i for i, line in enumerate(lines) if "| nmrPipe -fn PS -p0 12 -p1 -3 -di" in line
    )
    assert step1_ps > ext_i
    assert "| nmrPipe -fn PS -p0 20 -p1 3 -di \\" in script
    assert script.count("| nmrPipe -fn PS") == 2


def test_3d_nus_script_window(bruker_dir: Path) -> None:
    """NUS windows: the direct dimension (step1) and indirect dimension (step3) are
    configured separately; no window is inserted by default."""
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(
        exp,
        in_file="e.fid",
        nuslist="nuslist",
        out_file="e.ft3",
        window={
            "F3": {"type": "gaussian", "g1": 3.0, "g2": 0.2},
            "F2": {"type": "sine_bell_squared"},
            "F1": {"type": "sine_bell", "off": 0.3, "end": 0.9},
        },
    )
    # 0.2.199-patch11: the direct dimension is fixed to SP (SMILE requirement);
    # gaussian is not used for step1
    assert "GM" not in script
    assert "| nmrPipe -fn SP -off 0.45 -end 0.98 -pow 2 -c 0.5 \\" in script
    assert "| nmrPipe -fn SP -off 0.45 -end 0.95 -pow 2 -c 0.5 \\" in script
    assert "| nmrPipe -fn SP -off 0.3 -end 0.9 -pow 1 -c 0.5 \\" in script
    lines = script.splitlines()
    f2_sp = next(i for i, line in enumerate(lines) if "pow 2 -c 0.5" in line)
    f2_zf = next(
        i for i, line in enumerate(lines) if line.startswith("| nmrPipe -fn ZF") and i > f2_sp
    )
    f2_ft = next(i for i, line in enumerate(lines) if "| nmrPipe -fn FT" in line and i > f2_zf)
    assert f2_sp < f2_zf < f2_ft  # the window precedes ZF/FT
    plain = generate_3d_nus_script(exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft3")
    assert "| nmrPipe -fn GM" not in plain
    assert plain.count("| nmrPipe -fn SP") == 1  # only the step1 direct-dimension default window


def test_nus_finalize_script_3d_window(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_nus_finalize_script

    script = generate_nus_finalize_script(
        exp,
        planes="nus3d_rc/test%04d.ft1",
        out_file="e.ft3",
        phases={"F2": (10.0, -5.0), "F1": (20.0, 3.0)},
        window={
            "F2": {"type": "sine_bell"},
            "F1": {"type": "gaussian", "g1": 4.0},
        },
    )
    assert "| nmrPipe -fn SP -off 0.45 -end 0.95 -pow 1 -c 0.5 \\" in script
    assert "| nmrPipe -fn GM -g1 4 -g2 15 \\" in script
    lines = script.splitlines()
    sp_idx = next(i for i, line in enumerate(lines) if "pow 1 -c 0.5" in line)
    zf_idx = next(i for i, line in enumerate(lines) if "| nmrPipe -fn ZF" in line and i > sp_idx)
    ft_idx = next(i for i, line in enumerate(lines) if "| nmrPipe -fn FT" in line and i > zf_idx)
    assert sp_idx < zf_idx < ft_idx
    gm_idx = next(i for i, line in enumerate(lines) if "GM -g1 4" in line)
    zf2_idx = next(i for i, line in enumerate(lines) if "| nmrPipe -fn ZF" in line and i > gm_idx)
    ft2_idx = next(i for i, line in enumerate(lines) if "| nmrPipe -fn FT" in line and i > zf2_idx)
    assert gm_idx < zf2_idx < ft2_idx


def test_convert_script_format_flags_follow_bruker_auto(bruker_dir: Path) -> None:
    """The fallback conversion script's format switches follow the bruker -AUTO rules
    (2026-09-24 parameter source table).

    ``-aswap/-noaswap`` is decided by BYTORDA (1 → -noaswap); ``-AMX -decim -dspfvs
    -grpdly`` is written only when DECIM>1; ``-noi2f``/``-ws 8 -noi2f`` is decided by
    DTYPA (1/2) — the old rule wrote ``-ws 8 -noi2f`` whenever DSPFVS=21, which
    misdeclares the word length for TSR3-style data with "DSPFVS=21 but DTYPA=0
    (int32)".
    """
    big_endian = read_dataset(bruker_dir / "hsqc_2d")
    big_endian.acquisition_parameters["acqus"]["BYTORDA"] = 1
    big_endian.acquisition_parameters["acqus"]["DTYPA"] = 2
    script = _convert(big_endian)
    assert "-noaswap" in script and "-aswap" not in script
    assert "-ws 8" in script and "-noi2f" in script

    float32 = read_dataset(bruker_dir / "hsqc_2d")
    float32.acquisition_parameters["acqus"]["DTYPA"] = 1
    script32 = _convert(float32)
    assert "-noi2f" in script32 and "-ws 8" not in script32

    no_dsp = read_dataset(bruker_dir / "hsqc_2d")
    no_dsp.acquisition_parameters["acqus"]["DECIM"] = 1
    script_no_dsp = _convert(no_dsp)
    assert "-AMX" not in script_no_dsp
    assert "-decim" not in script_no_dsp
    assert "-grpdly" not in script_no_dsp


def test_read_fid_com_correction_records_skips_specialised_keys(tmp_path: Path) -> None:
    """Before the correction list reaches a report, the sweep-width/carrier-frequency
    specials and the out rename are skipped (so no value is reported twice)."""
    import json

    from backend.nmrpipe_backend import read_fid_com_correction_records

    work = tmp_path / "process"
    work.mkdir()
    (work / "d_001.fid_com_corrections.json").write_text(
        json.dumps(
            {
                "lines": [
                    "xLAB: fid.com=HN -> acqus=1H (corrected)",
                    "ySW: fid.com=2000.000 -> acqus=1824.535 (corrected)",
                    "out: ./test.fid -> d_001.fid (corrected)",
                ]
            }
        ),
        encoding="utf-8",
    )
    assert read_fid_com_correction_records(work) == ["xLAB: fid.com=HN -> acqus=1H (corrected)"]


def test_real_mode_ft_flags_honour_the_sampling_override(bruker_dir: Path) -> None:
    """D: the FT line of the real family (TPPI/QSEQ/QF) also goes through `_ft_flags`
    — `sampling.ft_neg` can add -neg.

    Previously these three branches hard-coded the flags, silently dropping
    `sampling.ft_neg`/`ft_alt`/`flip_f1`.
    """
    for mode, base in ((3, "-real"), (2, "-bruk"), (1, "")):
        exp = read_dataset(bruker_dir / "hsqc_2d")
        exp.acquisition_parameters["acqu2s"]["FnMODE"] = mode
        plan = select_method(exp)
        plain = generate_process_script(exp, plan, in_file="a.fid", out_file="a.ft2")
        # the real family's direction flag is on the **indirect-dimension** FT line
        # (the last one)
        plain_ft = [line for line in plain.splitlines() if "-fn FT" in line][-1]
        assert "-neg" not in plain_ft, mode
        if base:
            assert base in plain_ft, (mode, plain_ft)

        flipped = generate_process_script(
            exp,
            plan,
            in_file="a.fid",
            out_file="a.ft2",
            sampling={"ft_neg": True},
        )
        flipped_ft = [line for line in flipped.splitlines() if "-fn FT" in line]
        assert any("-neg" in line for line in flipped_ft), (mode, flipped_ft)
