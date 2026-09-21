"""Bruker ser/fid element type (DTYPE/BYTORDA) test. user 2026-09-11: "ser file seems to be dynamic
byte output, so it is not necessarily int32" -- TopSpin's `##$DTYPE`:0=int32 / 1=float64 /
2=float32,`##$BYTORDA`:0=little endian / 1=big endian; unknown DTYPE Don’t guess, report an
error explicitly."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from core.data.bruker_dtype import (
    UnknownBrukerDtype,
    point_bytes,
    sample_dtype,
    sample_itemsize,
)
from core.data.bruker_reader import BrukerDataError, read_data, read_dataset


def test_sample_dtype_codes_and_endianness() -> None:
    assert sample_dtype({"DTYPE": 0, "BYTORDA": 0}) == np.dtype("<i4")
    assert sample_dtype({"DTYPE": 1, "BYTORDA": 0}) == np.dtype("<f8")
    assert sample_dtype({"DTYPE": 2, "BYTORDA": 0}) == np.dtype("<f4")
    assert sample_dtype({"DTYPE": 0, "BYTORDA": 1}) == np.dtype(">i4")
    assert sample_dtype({"DTYPE": 1, "BYTORDA": 1}) == np.dtype(">f8")


def test_sample_dtype_defaults_to_int32() -> None:
    """DTYPE/BYTORDA Default -> int32 little endian (consistent with existing data)."""
    assert sample_dtype({}) == np.dtype("<i4")
    assert sample_dtype(None) == np.dtype("<i4")
    assert sample_itemsize({}) == 4
    assert point_bytes({}) == 8
    assert point_bytes({"DTYPE": 1}) == 16


def test_sample_dtype_unknown_code_is_refused() -> None:
    """Unknown DTYPE (such as 7) is not processed silently as int32, and an error is reported
    directly."""
    with pytest.raises(UnknownBrukerDtype):
        sample_dtype({"DTYPE": 7})


def _copy_fixture(bruker_dir: Path, name: str, tmp_path: Path) -> Path:
    dst = tmp_path / name
    shutil.copytree(bruker_dir / name, dst)
    return dst


@pytest.mark.parametrize(
    "dtype_code,np_dtype",
    [(0, "<i4"), (1, "<f8"), (2, "<f4")],
)
def test_read_data_honours_dtype(
    tmp_path: Path, bruker_dir: Path, dtype_code: int, np_dtype: str
) -> None:
    """All three types of DTYPE can be read correctly (previously hard-coded int32, float Data will
    be read incorrectly/Size misjudgment)."""
    dst = _copy_fixture(bruker_dir, "hsqc_small", tmp_path)
    acqus = dst / "acqus"
    text = acqus.read_text(encoding="utf-8")
    acqus.write_text(
        text.replace("##$BYTORDA= 0", f"##$BYTORDA= 0\n##$DTYPE= {dtype_code}"),
        encoding="utf-8",
    )
    td1, td2 = 16, 64
    k = np.arange(td1)[:, None]
    t = np.arange(td2)[None, :]
    s = (k * 10 + t) + 1j * (k * 10 + t + 50)
    fids = np.zeros((td1 * 2, td2), dtype=complex)
    fids[0::2] = s.real
    fids[1::2] = s.imag
    interleaved = np.stack([fids.real, fids.imag], axis=-1).reshape(-1)
    interleaved.astype(np_dtype).tofile(dst / "ser")

    exp = read_dataset(dst)
    data = read_data(exp)

    assert data.matrix.shape == (td1 * 2, td2)
    assert np.allclose(data.matrix.real, fids.real, atol=1.0)
    assert np.allclose(data.matrix.imag, fids.imag, atol=1.0)


def test_read_data_unknown_dtype_raises(tmp_path: Path, bruker_dir: Path) -> None:
    """Unknown DTYPE is exposed as a BrukerDataError during the read phase (no silent read
    errors)."""
    dst = _copy_fixture(bruker_dir, "hsqc_small", tmp_path)
    acqus = dst / "acqus"
    text = acqus.read_text(encoding="utf-8")
    acqus.write_text(
        text.replace("##$BYTORDA= 0", "##$BYTORDA= 0\n##$DTYPE= 9"),
        encoding="utf-8",
    )
    (dst / "ser").write_bytes(b"\x00" * 64 * 8)

    exp = read_dataset(dst)
    with pytest.raises(BrukerDataError):
        read_data(exp)
