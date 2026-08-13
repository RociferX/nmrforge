"""构造工具回归:全采样 Bruker 2D → 合成 NUS(sampleA 验证用)。

核心不变量:复点网格 = acqu2s TD // 超复数分量(States 256/2=128);
nuslist 首点必须为 0(nusExpand -off 偏移);ser 只保留采样点 FID。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np


def _load_tool():
    path = (
        Path(__file__).resolve().parent.parent / "scripts" / "vm_sample_make_nus.py"
    )
    spec = importlib.util.spec_from_file_location("vm_sample_make_nus", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _make_dataset(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "acqus").write_text(
        "##$TD= 2048\n##$FnMODE= 0\n", encoding="utf-8"
    )
    (src / "acqu2s").write_text(
        "##$TD= 256\n##$FnMODE= 5\n", encoding="utf-8"
    )
    points_per_fid = 512
    n_fids_total = 256  # Bruker TD 已含 States 超复数行(128 复点 × 2)
    rng = np.random.default_rng(7)
    data = rng.standard_normal((n_fids_total, points_per_fid)) + 1j * rng.standard_normal(
        (n_fids_total, points_per_fid)
    )
    interleaved = np.stack([data.real, data.imag], axis=-1).astype("<i4")
    interleaved.tofile(src / "ser")
    return src


def test_make_nus_grid_td_div_mult(tmp_path: Path) -> None:
    """NusTD=TD//mult(128),nuslist 首点 0,ser 仅保留采样点 FID。"""
    tool = _load_tool()
    src = _make_dataset(tmp_path)
    out = tmp_path / "nus"
    assert tool.main([str(src), str(out), "--points", "32", "--seed", "42"]) == 0

    acqu2s = (out / "acqu2s").read_text(encoding="utf-8")
    acqus = (out / "acqus").read_text(encoding="utf-8")
    assert tool._param(acqu2s, "NusTD") == 128
    assert tool._param(acqus, "NusAMOUNT") == 25
    nuslist = (out / "nuslist").read_text(encoding="utf-8").splitlines()
    assert len(nuslist) == 32
    assert nuslist[0] == "0"  # 首点 0:nusExpand -off 不偏移
    ser = np.fromfile(out / "ser", dtype="<i4")
    assert ser.size == 32 * 2 * 512 * 2  # 采样点 × 超复数 × 点数 × 实虚
