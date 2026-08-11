"""AutoProcessor 真实 Bruker 数据端到端（uniform 2D States）。"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np

from backend.native_backend import NativeBackend
from core.data.bruker_reader import read_dataset
from workflow.engine import AutoProcessor


def _write_states_ser(path: Path, td1: int, td2: int, seed: int = 3) -> None:
    """构造一个 2D 峰（F1 索引 1、F2 索引 2）+ 噪声的 States ser。"""
    rng = np.random.default_rng(seed)
    k = np.arange(td1)[:, None]
    t = np.arange(td2)[None, :]
    f2_bin, k1_bin = 24, 8
    s = (
        50000.0
        * np.exp(-t / (td2 / 2))
        * np.exp(-k / (td1 / 2))
        * np.exp(1j * 2 * np.pi * (f2_bin * t / td2 + k1_bin * k / td1))
    )
    s += rng.normal(0, 20, size=(td1, td2)) + 1j * rng.normal(0, 20, size=(td1, td2))
    fids = np.zeros((td1 * 2, td2), dtype=complex)
    fids[0::2] = s.real
    fids[1::2] = s.imag
    interleaved = np.stack([fids.real, fids.imag], axis=-1).astype(np.int32).reshape(-1)
    interleaved.astype("<i4").tofile(path)


def test_bruker_end_to_end_2d_states(tmp_path: Path, bruker_dir: Path) -> None:
    dst = tmp_path / "hsqc_small"
    shutil.copytree(bruker_dir / "hsqc_small", dst)
    _write_states_ser(dst / "ser", td1=16, td2=64)
    exp = read_dataset(dst)
    processor = AutoProcessor(backend=NativeBackend())
    result = processor.run(exp)
    assert result.status == "accept"
    assert result.quality is not None
    assert any("data_file=ser" in log for log in result.logs)
