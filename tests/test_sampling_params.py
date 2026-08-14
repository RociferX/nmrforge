"""sampling 参数块落地测试(0.2.67):ft_neg/ft_alt/flip_f1 脚本消费。"""

from __future__ import annotations

from pathlib import Path

from backend.script_generator import (
    generate_2d_nus_script,
    generate_process_script,
)
from core.data.bruker_reader import read_dataset
from core.planning.method_selector import select_method


def test_sampling_default_preserves_script(bruker_dir: Path) -> None:
    """默认(ft_neg/ft_alt=None=auto)保持无 sampling 时输出不变(回归关键)。"""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    base = generate_process_script(exp, plan, in_file="f.fid", out_file="o.ft2")
    with_sampling = generate_process_script(
        exp,
        plan,
        in_file="f.fid",
        out_file="o.ft2",
        sampling={"ft_neg": None, "ft_alt": None, "flip_f1": False},
    )
    assert base == with_sampling


def test_sampling_ft_alt_off(bruker_dir: Path) -> None:
    """ft_alt=False → 间接维 FT 不加 -alt(该数据 F1=States-TPPI 默认加)。"""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    script = generate_process_script(
        exp,
        plan,
        in_file="f.fid",
        out_file="o.ft2",
        sampling={"ft_alt": False},
    )
    assert "| nmrPipe -fn FT -alt" not in script
    assert "| nmrPipe -fn FT \\" in script or "| nmrPipe -fn FT -neg" in script


def test_sampling_ft_neg_on(bruker_dir: Path) -> None:
    """ft_neg=True → FT 行带 -neg。"""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    script = generate_process_script(
        exp,
        plan,
        in_file="f.fid",
        out_file="o.ft2",
        sampling={"ft_neg": True},
    )
    assert " -neg" in script


def test_sampling_flip_f1(bruker_dir: Path) -> None:
    """flip_f1=True → F1 轴 FT 行带 -neg(翻转)。"""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    script = generate_process_script(
        exp,
        plan,
        in_file="f.fid",
        out_file="o.ft2",
        sampling={"flip_f1": True},
    )
    assert " -neg" in script


def test_sampling_nus_ft_alt_off(bruker_dir: Path) -> None:
    """NUS 脚本同样消费 ft_alt(关闭后 F1 FT 无 -alt)。"""
    exp = read_dataset(bruker_dir / "nus_2d")
    script = generate_2d_nus_script(
        exp,
        in_file="e.fid",
        nuslist="nuslist",
        out_file="e.ft2",
        sampling={"ft_alt": False},
    )
    assert "| nmrPipe -fn FT -alt" not in script
