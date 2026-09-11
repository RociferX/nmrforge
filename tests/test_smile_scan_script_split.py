"""SMILE 参数扫描:终跑脚本切分与候选输出命名(0.2.199-补29hz-修3 第 1 步)。

用户方案:SMILE 优化以终跑脚本为模板只换 SMILE 参数——先跑一次直接维得到
切片文件,再以切片为输入跑 25 次「SMILE + 间接维」,每次评估完即删除候选谱,
最后留排序表 + 前三脚本。本测试固定「脚本能切、切完仍自洽、候选输出名唯一」
这三件事(不依赖 NMRPipe,纯文本)。
"""

from __future__ import annotations

from pathlib import Path

from backend.script_generator import (
    generate_2d_nus_script,
    generate_3d_nus_script,
    rename_nus_scan_output,
    split_nus_script,
)
from core.data.bruker_reader import read_dataset

BRUKER = Path(__file__).resolve().parent / "fixtures" / "bruker"


def _script_3d() -> str:
    exp = read_dataset(BRUKER / "nus_3d")
    return generate_3d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft3"
    )


def _script_2d() -> str:
    exp = read_dataset(BRUKER / "nus_2d")
    return generate_2d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft2"
    )


def test_3d_script_splits_at_slice_boundary() -> None:
    """3D:前半段到切片写出为止,后半段从切片读回开始(含 SMILE)。"""
    prefix, suffix = split_nus_script(_script_3d())

    assert prefix.rstrip().splitlines()[-1].strip() == (
        "| pipe2xyz -out nus3d_1/test%04d.ft1 -z"
    )
    assert suffix.splitlines()[0].strip().startswith(
        "xyz2pipe -in nus3d_1/test%04d.ft1 -x"
    )
    assert "-fn SMILE" not in prefix  # 直接维段不执行 SMILE
    assert "-fn SMILE" in suffix   # 第二段承担 SMILE + 间接维
    assert "-out e.ft3" in suffix


def test_2d_single_file_has_no_split_and_falls_back() -> None:
    """2D 单文件脚本没有切片流(直接维处理与 SMILE 同管道),不做切分。"""
    prefix, suffix = split_nus_script(_script_2d())
    assert (prefix, suffix) == ("", "")


def test_rename_only_rewrites_final_output() -> None:
    """候选输出重命名只改终谱那一行,中间产物名保持不变。"""
    _prefix, suffix = split_nus_script(_script_3d())
    renamed = rename_nus_scan_output(suffix, "cand07.ft3")
    lines = [ln.strip() for ln in renamed.splitlines() if "-out " in ln]

    assert lines[-1].endswith("-out cand07.ft3 -x")
    # SMILE 中间平面仍写回 nus3d_rc(否则 step3 读不到)
    assert any("nus3d_rc/test%04d.ft1" in ln for ln in lines)
    assert "cand07.ft3" not in lines[0]


def test_split_is_stable_for_changed_smile_params() -> None:
    """只改 SMILE 参数时切点不变(扫描 25 组共用同一份切片)。"""
    exp = read_dataset(BRUKER / "nus_3d")
    base = dict(in_file="e.fid", nuslist="nuslist", out_file="e.ft3")
    first = split_nus_script(generate_3d_nus_script(exp, nsigma=3.0, thresh=0.90, **base))
    second = split_nus_script(generate_3d_nus_script(exp, nsigma=7.0, thresh=0.99, **base))

    assert first[0] == second[0]  # 直接维段与 SMILE 参数无关
    assert "-nSigma 3" in first[1] and "-nSigma 7" in second[1]
