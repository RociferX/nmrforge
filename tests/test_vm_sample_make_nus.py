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
    rows_total = 256  # 采集到的增量行(Bruker TD 语义;128 复点 × 2)
    x_n = 2048  # acqus TD:直接维每行 int32 数
    rng = np.random.default_rng(7)
    data = rng.standard_normal((rows_total, x_n // 2)) + 1j * rng.standard_normal(
        (rows_total, x_n // 2)
    )
    interleaved = np.stack([data.real, data.imag], axis=-1).astype("<i4")
    interleaved.reshape(rows_total, x_n).tofile(src / "ser")
    return src


def test_make_nus_grid_td_div_mult(tmp_path: Path) -> None:
    """NusTD=TD(行单位,256),nuslist 首点 0,ser 仅保留采样点 FID。"""
    tool = _load_tool()
    src = _make_dataset(tmp_path)
    out = tmp_path / "nus"
    assert tool.main([str(src), str(out), "--points", "32", "--seed", "42"]) == 0

    acqu2s = (out / "acqu2s").read_text(encoding="utf-8")
    acqus = (out / "acqus").read_text(encoding="utf-8")
    # 真实 NUS 约定:NusTD 是行(增量)单位(sampleJ: NusTD=292 ↔ nuslist max 145)
    assert tool._param(acqu2s, "NusTD") == 256
    assert tool._param(acqus, "NusAMOUNT") == 25
    nuslist = (out / "nuslist").read_text(encoding="utf-8").splitlines()
    assert len(nuslist) == 32
    assert nuslist[0] == "0"  # 首点 0:nusExpand -off 不偏移
    ser = np.fromfile(out / "ser", dtype="<i4")
    assert ser.size == 32 * 2 * 2048  # 采样点 × 超复数行 × 直接维 int32
