"""Construction tool regression: full sampling Bruker 2D -> synthesis NUS (for sampleA
verification). Core invariant: complex point grid = acqu2s TD // Super complex component (States
256/2=128); nuslist first point must be 0 (nusExpand -off offset); ser only retains sampling
point FID."""

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
    rows_total = 256  # Incremental rows collected (Bruker TD semantics; 128 complex points x 2).
    x_n = 2048  # acqus TD:Direct dimension int32 number per row.
    rng = np.random.default_rng(7)
    data = rng.standard_normal((rows_total, x_n // 2)) + 1j * rng.standard_normal(
        (rows_total, x_n // 2)
    )
    interleaved = np.stack([data.real, data.imag], axis=-1).astype("<i4")
    interleaved.reshape(rows_total, x_n).tofile(src / "ser")
    return src


def test_make_nus_grid_td_div_mult(tmp_path: Path) -> None:
    """NusTD=TD (line unit, 256), nuslist first point 0, ser only retains sampling point FID."""
    tool = _load_tool()
    src = _make_dataset(tmp_path)
    out = tmp_path / "nus"
    assert tool.main([str(src), str(out), "--points", "32", "--seed", "42"]) == 0

    acqu2s = (out / "acqu2s").read_text(encoding="utf-8")
    acqus = (out / "acqus").read_text(encoding="utf-8")
    # Real NUS Convention: NusTD is the row (increment) unit (sampleJ: NusTD=292 ↔ nuslist max 145).
    assert tool._param(acqu2s, "NusTD") == 256
    assert tool._param(acqus, "NusAMOUNT") == 25
    nuslist = (out / "nuslist").read_text(encoding="utf-8").splitlines()
    assert len(nuslist) == 32
    assert nuslist[0] == "0"  # First point 0:nusExpand -off No offset.
    ser = np.fromfile(out / "ser", dtype="<i4")
    assert ser.size == 32 * 2 * 2048  # Sampling point x super complex row x direct dimension int32.
