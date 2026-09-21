"""Sampling parameter block implementation test (0.2.67):ft_neg/ft_alt/flip_f1 script
consumption."""

from __future__ import annotations

from pathlib import Path

from backend.script_generator import (
    generate_2d_nus_script,
    generate_process_script,
)
from core.data.bruker_reader import read_dataset
from core.planning.method_selector import select_method


def test_sampling_default_preserves_script(bruker_dir: Path) -> None:
    """The default (ft_neg/ft_alt=None=auto) keeps the output unchanged without sampling
    (regression key)."""
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
    """Ft_alt=False -> indirect dimension FT does not add -alt (the data F1=States-TPPI is added by
    default)."""
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
    """Ft_neg=True -> FT lines with -neg."""
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
    """Flip_f1=True -> F1 axis FT row with -neg (flip)."""
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
    """NUS script also consumes ft_alt (after closing F1 FT without -alt)."""
    exp = read_dataset(bruker_dir / "nus_2d")
    script = generate_2d_nus_script(
        exp,
        in_file="e.fid",
        nuslist="nuslist",
        out_file="e.ft2",
        sampling={"ft_alt": False},
    )
    assert "| nmrPipe -fn FT -alt" not in script
