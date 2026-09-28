"""Bruker binary data reading tests (synthetic ser/fid)."""

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

#: Bruker pads every ser row to 1024 bytes — int32/float32 use 8 bytes per complex point
#: ⇒ 128 complex points (``core.data.ser_layout``; on real data d_015 ``TD=1612 → row
#: 1664``, ``356 → 384``).
ROW_POINTS = 128


def _pad_rows(fids: np.ndarray, row_points: int = ROW_POINTS) -> np.ndarray:
    """Pad a (n_fids, td) complex FID to whole row_points rows like real data (zeros at the end)."""
    if fids.shape[1] >= row_points:
        return fids
    padded = np.zeros((fids.shape[0], row_points), dtype=complex)
    padded[:, : fids.shape[1]] = fids
    return padded


def _write_ser(
    path: Path, fids: np.ndarray, byterda: int = 0, *, pad: bool = True
) -> None:
    """Write a complex FID array as Bruker ser (int32 real/imaginary interleaved); fids is
    (n_fids, td).

    ``pad=True`` (the default) pads every row to 1024 bytes like real data; ``pad=False``
    writes the raw unpadded rows, used only for negative cases where the layout cannot be
    resolved.
    """
    data = _pad_rows(fids) if pad else fids
    interleaved = np.stack([data.real, data.imag], axis=-1).astype(np.int32).reshape(-1)
    dtype = ">i4" if byterda else "<i4"
    interleaved.astype(dtype).tofile(path)


def _make_states_fids(td1: int, td2: int):
    """States 2D: S(k,t); the FID order in ser is R(k), I(k) interleaved."""
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
    # The row count comes from the file (32 rows); row length is the padded 128 points (TD=64)
    assert data.matrix.shape == (32, ROW_POINTS)
    assert np.array_equal(data.matrix[:, :64], fids)
    assert data.layout["F1"].mult == 2
    assert data.layout["F1"].n_fids == 32


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
    # The row count matches the declared grid (48 = (4×2)×(3×2)) ⇒ restore as
    # (n_f1, n_f2, row length); the row length includes padding
    assert data.matrix.shape == (8, 6, ROW_POINTS)
    assert np.array_equal(data.matrix[:, :, :td3], expected)


def test_read_data_size_mismatch(tmp_path: Path, bruker_dir: Path) -> None:
    """Raise when the layout cannot be resolved (row length not a multiple of 1024 bytes),
    never guess."""
    dst = _copy_fixture(bruker_dir, "hsqc_small", tmp_path)
    _write_ser(dst / "ser", np.zeros((2, 8), dtype=complex), byterda=0, pad=False)
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


def test_read_data_reads_a_real_shaped_uniform_2d(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Real-data shape (2026-09-24 review A3): 2D FnMODE=6, direct TD=2048, **1024 rows**.

    The old implementation expected 2048 rows from "rows = indirect TD × hypercomplex
    components" ⇒ ``ser size mismatch`` (exactly 2×, hit by 7/7 real datasets here), and
    ``gui.raw_quality._estimate_snr`` swallowed the exception, so the raw-data SNR stayed
    silently missing.
    """
    dst = _copy_fixture(bruker_dir, "hsqc_2d", tmp_path)
    rows, values = 1024, 2048  # 2048 sample values per row = 1024 complex points (TD count)
    (dst / "ser").write_bytes(b"\x00" * (rows * values * 8))
    exp = read_dataset(dst)
    exp.dimensions[0].td = values  # F2 (direct dim: acqus TD counts "real + imaginary")
    exp.dimensions[1].td = rows  # F1 (indirect dim) = the number of physical rows
    exp.acquisition_parameters["acqus"]["DTYPA"] = 2  # 8 bytes per value (real d_015 shape)
    data = read_data(exp)
    assert data.matrix.shape == (rows, values // 2)
    assert data.layout["F1"].n_fids == rows
    assert data.layout["F2"].td == values


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


def test_read_segments_sw_precision_tolerance(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.199-patch29cs: segmented SW_h written with a different precision (11904.762 vs
    11904.7619047619) counts as the same experiment (relative spectral-width tolerance);
    the old strict round(sw, 6) comparison produced false rejections."""
    import re as _re
    import shutil

    dst_a = tmp_path / "seg_a"
    dst_b = tmp_path / "seg_b"
    shutil.copytree(bruker_dir / "nus_2d", dst_a)
    shutil.copytree(bruker_dir / "nus_2d", dst_b)

    def _set_sw(path: Path, value: str) -> None:
        text = path.read_text(encoding="utf-8")
        path.write_text(
            _re.sub(r"(##\$SW_h= )[\d.]+", rf"\g<1>{value}", text, count=1),
            encoding="utf-8",
        )

    _set_sw(dst_a / "acqu2s", "11904.7619047619")
    _set_sw(dst_b / "acqu2s", "11904.762")
    exp = read_segments([dst_a, dst_b])
    assert len(exp.segments) == 2


def test_classify_segment_kind(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.199-patch29cu: uniform → repeat superposition; NUS same points → repeat
    superposition; NUS different points → segmentation."""
    import shutil

    from core.data.bruker_reader import classify_segment_kind
    from core.data.nus_reader import read_nuslist

    def _container(name: str, src: str, n: int = 2) -> Path:
        c = tmp_path / name
        for i in range(1, n + 1):
            shutil.copytree(bruker_dir / src, c / f"s{i:02d}")
        return c

    c_uniform = _container("c_uniform", "hsqc_2d")
    assert (
        classify_segment_kind([c_uniform / "s01", c_uniform / "s02"])
        == "repeat_uniform"
    )
    c_nus = _container("c_nus", "nus_2d")
    segs = [c_nus / "s01", c_nus / "s02"]
    assert classify_segment_kind(segs) == "repeat_nus"  # same nuslist
    # Drop the first line of the second nuslist → different sampling points → segmentation
    nus2 = c_nus / "s02" / "nuslist"
    points = read_nuslist(nus2)
    nus2.write_text(
        "\n".join(" ".join(str(v) for v in p) for p in points[1:]) + "\n",
        encoding="utf-8",
    )
    assert classify_segment_kind(segs) == "segmented_nus"
