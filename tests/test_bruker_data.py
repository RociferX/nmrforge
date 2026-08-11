"""Bruker 二进制数据读取测试（合成 ser/fid）。"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from core.data.bruker_reader import (
    BrukerDataError,
    read_data,
    read_dataset,
    read_segments,
)
from core.data.internal_data_model import SamplingMode
from core.processing.hypercomplex import HypercomplexParams, combine


def _write_ser(path: Path, fids: np.ndarray, byterda: int = 0) -> None:
    """把复 FID 数组写成 Bruker ser（int32 实虚交错）。fids 形状 (n_fids, td)。"""
    interleaved = np.stack([fids.real, fids.imag], axis=-1).astype(np.int32).reshape(-1)
    dtype = ">i4" if byterda else "<i4"
    interleaved.astype(dtype).tofile(path)


def _make_states_fids(td1: int, td2: int):
    """States 2D：S(k,t)，ser 中 FID 顺序为 R(k), I(k) 交错。"""
    k = np.arange(td1)[:, None]
    t = np.arange(td2)[None, :]
    s = (k * 10 + t) + 1j * (k * 10 + t + 50)
    fids = np.zeros((td1 * 2, td2), dtype=complex)
    fids[0::2] = s.real
    fids[1::2] = s.imag
    return s, fids


def _copy_fixture(bruker_dir: Path, name: str, tmp_path: Path) -> Path:
    dst = tmp_path / name
    shutil.copytree(bruker_dir / name, dst)
    return dst


def test_read_data_2d_states(tmp_path: Path, bruker_dir: Path) -> None:
    dst = _copy_fixture(bruker_dir, "hsqc_small", tmp_path)
    s, fids = _make_states_fids(16, 64)
    _write_ser(dst / "ser", fids, byterda=0)
    exp = read_dataset(dst)
    data = read_data(exp)
    assert data.matrix.shape == (32, 64)
    assert data.layout["F1"].mult == 2
    assert data.layout["F1"].n_fids == 32
    combined = combine(data.matrix, HypercomplexParams(axis="F1", mode="states"))
    assert np.allclose(combined, s)


def test_read_data_2d_big_endian(tmp_path: Path, bruker_dir: Path) -> None:
    dst = _copy_fixture(bruker_dir, "hsqc_small", tmp_path)
    acqus = dst / "acqus"
    text = acqus.read_text(encoding="utf-8")
    acqus.write_text(text.replace("##$BYTORDA= 0", "##$BYTORDA= 1"), encoding="utf-8")
    s, fids = _make_states_fids(16, 64)
    _write_ser(dst / "ser", fids, byterda=1)
    exp = read_dataset(dst)
    data = read_data(exp)
    assert data.byte_order == "big"
    combined = combine(data.matrix, HypercomplexParams(axis="F1", mode="states"))
    assert np.allclose(combined, s)


def test_read_data_3d(tmp_path: Path, bruker_dir: Path) -> None:
    dst = _copy_fixture(bruker_dir, "hnca_small", tmp_path)
    td1, td2, td3 = 4, 3, 8
    fids = np.zeros((td2 * 2 * td1 * 2, td3), dtype=complex)
    expected = np.zeros((td1 * 2, td2 * 2, td3), dtype=complex)
    for f2i in range(td2):
        for c2 in range(2):
            for f1i in range(td1):
                for c1 in range(2):
                    idx = (f2i * 2 + c2) * (td1 * 2) + (f1i * 2 + c1)
                    value = (f2i * 1000 + f1i * 100 + np.arange(td3)) + 1j * (c1 * 10 + c2)
                    fids[idx] = value
                    expected[f1i * 2 + c1, f2i * 2 + c2] = value
    _write_ser(dst / "ser", fids, byterda=0)
    exp = read_dataset(dst)
    data = read_data(exp)
    assert data.matrix.shape == (8, 6, 8)
    assert np.array_equal(data.matrix, expected)


def test_read_data_size_mismatch(tmp_path: Path, bruker_dir: Path) -> None:
    dst = _copy_fixture(bruker_dir, "hsqc_small", tmp_path)
    _write_ser(dst / "ser", np.zeros((2, 8), dtype=complex), byterda=0)
    exp = read_dataset(dst)
    with pytest.raises(BrukerDataError):
        read_data(exp)


def test_read_data_1d(tmp_path: Path, bruker_dir: Path) -> None:
    dst = tmp_path / "one_d"
    dst.mkdir()
    (dst / "acqus").write_text(
        "##$PULPROG= zg\n##$TD= 8\n##$NUC1= 1H\n##$PARMODE= 0\n##$BYTORDA= 0\n##END=\n",
        encoding="utf-8",
    )
    fid = (np.arange(8) + 1j * np.arange(8)[::-1]).astype(complex)
    _write_ser(dst / "fid", fid.reshape(1, -1), byterda=0)
    exp = read_dataset(dst)
    data = read_data(exp)
    assert data.matrix.shape == (8,)
    assert np.allclose(data.matrix, fid)


def test_read_segments_ok(tmp_path: Path, bruker_dir: Path) -> None:
    import shutil

    dst_a = tmp_path / "seg_a"
    dst_b = tmp_path / "seg_b"
    shutil.copytree(bruker_dir / "nus_2d", dst_a)
    shutil.copytree(bruker_dir / "nus_2d", dst_b)
    exp = read_segments([dst_a, dst_b])
    assert len(exp.segments) == 2
    assert exp.ndim == 2
    assert exp.sampling.mode is SamplingMode.NUS


def test_read_segments_mismatch(tmp_path: Path, bruker_dir: Path) -> None:
    import shutil

    dst_a = tmp_path / "seg_a"
    dst_b = tmp_path / "seg_b"
    shutil.copytree(bruker_dir / "nus_2d", dst_a)
    shutil.copytree(bruker_dir / "nus_2d", dst_b)
    acqu2s = dst_b / "acqu2s"
    text = acqu2s.read_text(encoding="utf-8")
    acqu2s.write_text(text.replace("##$TD= 256", "##$TD= 128"), encoding="utf-8")
    with pytest.raises(ValueError):
        read_segments([dst_a, dst_b])


def test_merge_nuslists(tmp_path: Path) -> None:
    from core.data.nus_reader import merge_nuslists

    a = tmp_path / "a.nuslist"
    b = tmp_path / "b.nuslist"
    a.write_text("1 1\n2 2\n3 3\n", encoding="utf-8")
    b.write_text("2 2\n4 4\n", encoding="utf-8")
    merged = merge_nuslists([a, b])
    assert merged == [(1, 1), (2, 2), (3, 3), (4, 4)]
