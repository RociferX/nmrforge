"""2D NUS 兼容:2D 不存在切片流(用户 2026-09-11 约束,0.2.199-补29hz-修9)。

bruker -AUTO 对带 nuslist 的 2D NUS 仍会生成 mask 形态的 fid.com
(-out ./fid/test%03d.fid + nusExpand -mask)。2D 只有一个平面,bruk2pipe
不展开 %03d(VM 实测写出字面名 test%03d.fid),下游 xyz2pipe 找不到切片,
fid.com rc=1、转换直接失败。修好后 2D 一律单文件输出,mask 阶段一并移除;
3D 的切片流是正常形态,必须原样保留。
"""

from __future__ import annotations

from pathlib import Path

from backend.bruker_workflow import patch_fid_com
from core.data.bruker_reader import read_dataset

BRUKER = Path(__file__).resolve().parent / "fixtures" / "bruker"

_CONT = " \\"  # 行尾续行:空格 + 反斜杠


def _strip2d_fid_com() -> str:
    """2D NUS 的 mask 形态 fid.com(bruker -AUTO 原样)。"""
    return (
        "nusExpand.tcl -mode bruker -sampleCount 16 -off 0" + _CONT + "\n"
        " -in ./ser -out ./ser_full -sample ./nuslist\n"
        "\n"
        "bruk2pipe -in ./ser_full" + _CONT + "\n"
        "  -xN 2048 -yN 128 -xT 1024 -yT 64" + _CONT + "\n"
        "  -out ./fid/test%03d.fid -ov\n"
        "\n"
        "xyz2pipe -in ./fid/test%03d.fid -noWr" + _CONT + "\n"
        "| nusExpand.tcl -mask -noexpand -mode pipe -sampleCount 16 -off 0"
        + _CONT + "\n"
        "  -in stdin -out ./mask/test%03d.fid -sample ./nuslist\n"
    )


def _slice3d_fid_com() -> str:
    """3D NUS 的切片式 fid.com(切片流是 3D 的正常形态)。"""
    return (
        "bruk2pipe -in ./ser_full" + _CONT + "\n"
        "  -xN 1024 -yN 166 -zN 4702 -xT 454 -yT 83 -zT 2351" + _CONT + "\n"
        "  -out ./fid/test%03d.fid -ov\n"
    )


def test_2d_nus_fid_com_forces_single_file_out() -> None:
    """2D:切片 -out → 单文件 {dataset_id}.fid;mask 阶段移除。"""
    exp = read_dataset(BRUKER / "nus_2d")
    assert exp.ndim == 2

    patched, warnings = patch_fid_com(_strip2d_fid_com(), exp)

    assert f"-out ./{exp.dataset_id}.fid" in patched
    assert "%03d" not in patched
    assert any("2D 无切片流" in w for w in warnings)
    assert any("mask" in w for w in warnings)


def test_3d_nus_fid_com_keeps_slice_stream() -> None:
    """3D:切片式输出原样保留(不能把 3D 也改成单文件)。"""
    exp = read_dataset(BRUKER / "nus_3d")
    assert exp.ndim == 3

    patched, warnings = patch_fid_com(_slice3d_fid_com(), exp)

    assert "-out ./fid/test%03d.fid" in patched
    assert not any("2D 无切片流" in w for w in warnings)


def test_2d_uniform_fid_com_out_name_unchanged() -> None:
    """2D 均匀采样(非 NUS)输出名改写行为不回归。"""
    exp = read_dataset(BRUKER / "hsqc_2d")
    assert exp.ndim == 2

    text = "bruk2pipe -in ./ser" + _CONT + "\n  -out ./test.fid\n"
    patched, warnings = patch_fid_com(text, exp)

    assert f"-out ./{exp.dataset_id}.fid" in patched
    assert not any("2D 无切片流" in w for w in warnings)
