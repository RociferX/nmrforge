"""NMRPipe 脚本生成测试（确定性 + SOFTWARE_SUMMARY §6.2 参数要求）。"""

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
    assert "-yMODE Complex" in script
    assert "-ws 8" in script and "-noi2f" in script  # DSPFVS=21
    assert "-DMX" not in script
    assert "-aq2D 2" in script  # FnMODE 5
    assert "-xN 2048" in script
    assert "-yN 256" in script
    assert "-ndim 2" in script
    assert "\r" not in script  # LF 行尾


def test_convert_script_nus3d_td1_uses_nustd(bruker_dir: Path) -> None:
    """acqu3s TD=1 时 zN 必须用 NusTD（否则 fid.com 输出单文件而非切片）。"""
    exp = read_dataset(bruker_dir / "nus_3d")
    assert exp.ndim == 3
    assert exp.sampling.mode.value == "nus"
    td = effective_td(exp)
    assert td[2] == 128  # NusTD，不是 1
    assert td[1] == 48  # acqu2s NusTD
    script = _convert(exp)
    assert "-zN 128" in script
    assert __import__("re").search(r"-zN 1(?![0-9])", script) is None
    assert "-yN 48" in script
    assert "-ndim 3" in script
    assert "-yMODE Complex" in script  # F2=acqu2s FnMODE=5
    assert "-zMODE Complex" in script


def test_convert_script_echo_antiecho_mode(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    exp.acquisition_parameters["acqu2s"]["FnMODE"] = 4
    script = _convert(exp)
    assert "-yMODE Echo-AntiEcho" in script  # F1 FnMODE=4
    assert "-aq2D 3" in script  # FnMODE 4 → 3


def test_convert_script_3d_y_mode_from_acqu2s(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hnca_3d")
    script = _convert(exp)
    assert "-yMODE Complex" in script  # F2=acqu2s FnMODE=5
    assert "-aq2D 2" in script  # FnMODE 5 → 2


def test_process_script_2d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    script = generate_process_script(exp, plan, in_file="test.fid", out_file="out.ft2")
    assert "xyz2pipe -in test.fid -x" in script
    assert "| nmrPipe -fn SP" in script
    assert "| nmrPipe -fn ZF" in script
    assert "| nmrPipe -fn FT" in script
    assert "| nmrPipe -fn PS" in script
    assert script.count("| nmrPipe -fn TP") == 1
    assert "| pipe2xyz -out out.ft2 -x" in script
    assert "\r" not in script


def test_process_script_3d_two_tps(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hnca_3d")
    plan = select_method(exp)
    script = generate_process_script(exp, plan, in_file="test.fid", out_file="out.ft3")
    assert script.count("| nmrPipe -fn TP") == 2
    assert "| pipe2xyz -out out.ft3 -x" in script


def test_scripts_deterministic(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    assert _convert(exp) == _convert(exp)
    assert generate_process_script(exp, plan, in_file="a.fid", out_file="a.ft2") == (
        generate_process_script(exp, plan, in_file="a.fid", out_file="a.ft2")
    )


def test_2d_nus_script(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_2d")
    from backend.script_generator import generate_2d_nus_script

    script = generate_2d_nus_script(
        exp, in_file="exp.fid", nuslist="nuslist", out_file="exp.ft2",
        nuslist_count=5, ext_lo="9.0", ext_hi="7.5", nsigma=7.0, thresh=0.85,
    )
    assert script.startswith("#!/bin/csh")
    assert "xyz2pipe -in exp.fid -x" in script
    assert "-fn SMILE -nDim 2" in script
    assert "-sample nuslist" in script
    assert "-sampleCount 5" in script
    assert "-x1 9.0ppm -xn 7.5ppm" in script
    assert "| pipe2xyz -out exp.ft2 -x" in script
    assert "\r" not in script


def test_3d_nus_script(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(
        exp, in_file="exp.fid", nuslist="nuslist", out_file="exp.ft3",
        nuslist_count=4, ext_lo="9.0", ext_hi="7.5",
    )
    assert "-fn SMILE -nDim 3" in script
    assert "-sample nuslist" in script
    assert "| pipe2xyz -out exp.ft3 -x" in script
    assert script.count("| nmrPipe -fn TP") == 2
    assert "\r" not in script
    assert generate_3d_nus_script(
        exp, in_file="exp.fid", nuslist="nuslist", out_file="exp.ft3"
    ) == generate_3d_nus_script(
        exp, in_file="exp.fid", nuslist="nuslist", out_file="exp.ft3"
    )


def test_3d_nus_script_smile_tuning(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(
        exp, in_file="exp.fid", nuslist="nuslist", out_file="exp.ft3",
        nsigma=5.0, thresh=0.99, smile_xq3=2.0, smile_scaling=True, smile_report=2,
    )
    assert "-nSigma 5" in script
    assert "-thresh 0.99" in script
    assert "-xQ3 2" in script
    assert "-scaling 1" in script
    assert "-report 2" in script


def test_select_smile_params_low_sampling() -> None:
    from backend.script_generator import select_smile_params

    assert select_smile_params(0.04) == (5.0, 0.95)
    assert select_smile_params(0.3) == (5.0, 0.95)
    assert select_smile_params(0.8) == (5.0, 0.95)


def test_3d_nus_script_default_smile_params(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(
        exp, in_file="exp.fid", nuslist="nuslist", out_file="exp.ft3"
    )
    assert "-xQ3 2" in script
    assert "-scaling 1" in script


def test_process_script_direct_phase(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    script = generate_process_script(
        exp, plan, in_file="a.fid", out_file="a.ft2",
        direct_phase={"F2": (12.0, -3.0)},
    )
    assert "| nmrPipe -fn PS -p0 12 -p1 -3 -di \\" in script


def test_3d_nus_script_direct_phase(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft3",
        direct_phase=(12.0, -3.0),
    )
    assert "| nmrPipe -fn PS -p0 12 -p1 -3 -di \\" in script
