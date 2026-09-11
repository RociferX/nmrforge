"""2D NUS 兼容:转换跟随 bruker -AUTO 的输出形态 + 2D 留出残差留档。

用户裁定(2026-09-11):bruker -AUTO 对本就能识别的 2D NUS 直接给单文件
（`-out ./test.fid`，程序只把它改名成 `{dataset_id}.fid`）；切片流只是 3D
在直接维处理之后才出现的东西，因此**不做**「2D 强制单文件」这类脚本改写，
脚本一律以 -AUTO 给的为准。

2D 留出采样点残差靠 `script_generator.build_2d_direct_only_script` 留档
SMILE 输入（2D 单文件管道切不出切片）。
"""

from __future__ import annotations

from pathlib import Path

from backend.bruker_workflow import patch_fid_com
from core.data.bruker_reader import read_dataset

BRUKER = Path(__file__).resolve().parent / "fixtures" / "bruker"

_CONT = " \\"  # 行尾续行:空格 + 反斜杠


def _auto_2d_nus_fid_com(out_line: str) -> str:
    """bruker -AUTO 对 2D NUS 的脚本骨架(展开 + bruk2pipe + mask)。"""
    return (
        "nusExpand.tcl -mode bruker -sampleCount 32 -avg -off 0" + _CONT + "\n"
        " -in ./ser -out ./ser_full -sample ./nuslist\n"
        "\n"
        "bruk2pipe -in ./ser_full" + _CONT + "\n"
        "  -xN 2048 -yN 254 -xT 1024 -yT 127" + _CONT + "\n"
        f"  {out_line}\n"
        "\n"
        "nusExpand.tcl -mask -noexpand -mode pipe -sampleCount 32 -avg -off 0"
        + _CONT + "\n"
        " -in ./test.fid -out ./mask.fid -sample ./nuslist\n"
    )


def test_2d_nus_auto_single_file_out_renamed_to_dataset() -> None:
    """-AUTO 给的单文件(test.fid)→ 改名 {dataset_id}.fid(既有约定,不改形态)。"""
    exp = read_dataset(BRUKER / "nus_2d")
    assert exp.ndim == 2

    patched, warnings = patch_fid_com(
        _auto_2d_nus_fid_com("-out ./test.fid -ov"), exp
    )

    assert f"-out ./{exp.dataset_id}.fid -ov" in patched
    assert any("out:" in w and "test.fid" in w for w in warnings)


def test_2d_nus_auto_slice_out_left_untouched() -> None:
    """-AUTO 若给切片式输出,不做 2D 特有改写(一律以 -AUTO 为准)。"""
    exp = read_dataset(BRUKER / "nus_2d")

    patched, _warnings = patch_fid_com(
        _auto_2d_nus_fid_com("-out ./fid/test%03d.fid -ov"), exp
    )

    assert "-out ./fid/test%03d.fid -ov" in patched


def test_3d_nus_fid_com_keeps_slice_stream() -> None:
    """3D:切片式输出原样保留(切片只在 3D 直接维处理后出现)。"""
    exp = read_dataset(BRUKER / "nus_3d")
    assert exp.ndim == 3

    text = (
        "bruk2pipe -in ./ser_full" + _CONT + "\n"
        "  -xN 1024 -yN 166 -zN 4702 -xT 454 -yT 83 -zT 2351" + _CONT + "\n"
        "  -out ./fid/test%03d.fid -ov\n"
    )
    patched, _warnings = patch_fid_com(text, exp)

    assert "-out ./fid/test%03d.fid" in patched


def test_2d_uniform_fid_com_out_name_unchanged() -> None:
    """2D 均匀采样(非 NUS)输出名改写行为不回归。"""
    exp = read_dataset(BRUKER / "hsqc_2d")
    assert exp.ndim == 2

    text = "bruk2pipe -in ./ser" + _CONT + "\n  -out ./test.fid\n"
    patched, _warnings = patch_fid_com(text, exp)

    assert f"-out ./{exp.dataset_id}.fid" in patched


def test_build_2d_direct_only_script_trims_before_smile() -> None:
    """2D 直接维留档脚本:保留直接维处理、去掉 SMILE 及其后、末尾单文件输出。"""
    from backend.script_generator import (
        build_2d_direct_only_script,
        generate_2d_nus_script,
    )

    exp = read_dataset(BRUKER / "nus_2d")
    script = generate_2d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft2"
    )
    direct = build_2d_direct_only_script(script)

    assert direct.endswith("| pipe2xyz -out nus2d/direct.ft1 -x -ov\n")
    assert "-fn SMILE" not in direct
    assert "-out e.ft2" not in direct
    assert "nus2d/recon.ft1" not in direct
    assert "| nmrPipe -fn EXT" in direct  # 直接维处理阶段保留
    assert "| nmrPipe -fn POLY -auto" in direct  # SMILE 前最后一步保留


def test_build_2d_direct_only_script_rejects_unknown_shape() -> None:
    """切不出来时返回空串(调用方跳过留出残差,不静默错切)。"""
    from backend.script_generator import build_2d_direct_only_script

    assert build_2d_direct_only_script("#!/bin/csh\necho hi\n") == ""
