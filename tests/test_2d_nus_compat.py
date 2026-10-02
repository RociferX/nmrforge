"""2D NUS compatibility: conversion follows bruker -AUTO's output shape + 2D holdout residual
archiving.

User ruling (2026-09-11): for 2D NUS that bruker -AUTO can already recognise it emits a single
file (`-out ./test.fid`, which the program only renames to `{dataset_id}.fid`); the slice
stream is something that only appears for 3D after direct-dimension processing, so there is
**no** script rewriting such as "force a single file for 2D" -- the script always follows what
-AUTO gives.

The 2D holdout sampling-point residual is archived by
`script_generator.build_2d_direct_only_script` for SMILE input (the 2D single-file pipeline
cannot cut out slices).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.bruker_workflow import patch_fid_com
from core.data.bruker_reader import read_dataset

BRUKER = Path(__file__).resolve().parent / "fixtures" / "bruker"

_CONT = " \\"  # line continuation: space + backslash


def _auto_2d_nus_fid_com(out_line: str) -> str:
    """bruker -AUTO script skeleton for 2D NUS (expand + bruk2pipe + mask)."""
    return (
        "nusExpand.tcl -mode bruker -sampleCount 32 -avg -off 0" + _CONT + "\n"
        " -in ./ser -out ./ser_full -sample ./nuslist\n"
        "\n"
        "bruk2pipe -in ./ser_full" + _CONT + "\n"
        "  -xN 2048 -yN 254 -xT 1024 -yT 127" + _CONT + "\n"
        f"  {out_line}\n"
        "\n"
        "nusExpand.tcl -mask -noexpand -mode pipe -sampleCount 32 -avg -off 0" + _CONT + "\n"
        " -in ./test.fid -out ./mask.fid -sample ./nuslist\n"
    )


def test_2d_nus_auto_single_file_out_renamed_to_dataset() -> None:
    """The single file -AUTO emits (test.fid) → renamed to {dataset_id}.fid (existing
    convention, no shape change)."""
    exp = read_dataset(BRUKER / "nus_2d")
    assert exp.ndim == 2

    patched, warnings = patch_fid_com(_auto_2d_nus_fid_com("-out ./test.fid -ov"), exp)

    assert f"-out ./{exp.dataset_id}.fid -ov" in patched
    assert any("out:" in w and "test.fid" in w for w in warnings)


def test_2d_nus_auto_slice_out_left_untouched() -> None:
    """If -AUTO emits slice-style output, no 2D-specific rewriting is done (always follow -AUTO)."""
    exp = read_dataset(BRUKER / "nus_2d")

    patched, _warnings = patch_fid_com(_auto_2d_nus_fid_com("-out ./fid/test%03d.fid -ov"), exp)

    assert "-out ./fid/test%03d.fid -ov" in patched


def test_3d_nus_fid_com_keeps_slice_stream() -> None:
    """3D: slice-style output kept as-is (slices appear only after 3D direct-dim processing)."""
    exp = read_dataset(BRUKER / "nus_3d")
    assert exp.ndim == 3

    text = (
        "bruk2pipe -in ./ser_full" + _CONT + "\n"
        "  -xN 1024 -yN 166 -zN 4702 -xT 454 -yT 83 -zT 2351" + _CONT + "\n"
        "  -out ./fid/test%03d.fid -ov\n"
    )
    patched, _warnings = patch_fid_com(text, exp)

    assert "-out ./fid/test%03d.fid" in patched


def test_2d_uniform_fid_com_out_name_unchanged() -> None:
    """No regression in the output-name rewriting for 2D uniform (non-NUS) sampling."""
    exp = read_dataset(BRUKER / "hsqc_2d")
    assert exp.ndim == 2

    text = "bruk2pipe -in ./ser" + _CONT + "\n  -out ./test.fid\n"
    patched, _warnings = patch_fid_com(text, exp)

    assert f"-out ./{exp.dataset_id}.fid" in patched


def test_build_2d_direct_only_script_trims_before_smile() -> None:
    """2D direct-dimension archive script: keeps the direct-dimension processing, drops SMILE
    and everything after it, and appends a single-file output."""
    from backend.script_generator import (
        build_2d_direct_only_script,
        generate_2d_nus_script,
    )

    exp = read_dataset(BRUKER / "nus_2d")
    script = generate_2d_nus_script(exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft2")
    direct = build_2d_direct_only_script(script)

    assert direct.endswith("| pipe2xyz -out nus2d/direct.ft1 -x -ov\n")
    assert "-fn SMILE" not in direct
    assert "-out e.ft2" not in direct
    assert "nus2d/recon.ft1" not in direct
    assert "| nmrPipe -fn EXT" in direct  # direct-dimension processing stage kept
    assert "| nmrPipe -fn POLY -auto" in direct  # last step before SMILE kept


def test_build_2d_direct_only_script_rejects_unknown_shape() -> None:
    """Returns an empty string when it cannot be cut (the caller skips the holdout residual
    rather than mis-cutting silently)."""
    from backend.script_generator import build_2d_direct_only_script

    assert build_2d_direct_only_script("#!/bin/csh\necho hi\n") == ""


def _make_2d_nus_dataset(
    root: Path,
    *,
    rows: int,
    keep: list[int],
    x_n: int = 2048,
    td_rows: int = 256,
    dtype_code: int = 0,
) -> Path:
    """Build a 2D NUS dataset (no nuslist): ser has ``rows`` rows and the complex points in
    ``keep`` are non-zero.

    dtype_code: TopSpin DTYPE (0=int32 / 1=float64 / 2=float32), written into acqus.
    """
    import numpy as np

    ds = root / f"ds_{rows}_{len(keep)}_{dtype_code}"
    ds.mkdir(parents=True, exist_ok=True)
    (ds / "acqus").write_text(
        f"##$TD= {x_n}\n##$FnMODE= 0\n##$NusAMOUNT= 25\n##$NusTD= 0\n##$DTYPE= {dtype_code}\n",
        encoding="utf-8",
    )
    (ds / "acqu2s").write_text(
        f"##$TD= {td_rows}\n##$FnMODE= 5\n##$NusTD= {td_rows}\n##$NUC1= <15N>\n",
        encoding="utf-8",
    )
    # unknown-DTYPE use case: data is written as int32, but the decision must reject the
    # unknown DTYPE outright
    data = np.zeros((rows, x_n), dtype={0: "<i4", 1: "<f8", 2: "<f4"}.get(dtype_code, "<i4"))
    for k in keep:
        if 2 * k + 1 < rows:
            data[2 * k] = 7
            data[2 * k + 1] = -3
    data.tofile(ds / "ser")
    return ds


def _recover(ds: Path) -> tuple[list[int] | None, list[str]]:
    from backend.nmrpipe_backend import NMRPipeBackend

    exp = read_dataset(ds)

    assert exp.sampling.mode.value in ("nus", "uniform"), exp.sampling.mode
    logs: list[str] = []
    points = NMRPipeBackend(nmrpipe_bin="")._recover_dense_2d_nus(ds, exp, logs)
    return points, logs


def test_recover_dense_2d_nus_arbitrary_subset(tmp_path: Path) -> None:
    """Dense model: sampling points are recovered from the zero pattern, supporting any
    subset (not just a "first N" prefix)."""
    keep = [0, 5, 37, 64, 100, 127]
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=keep)

    points, logs = _recover(ds)

    assert points == keep
    assert any("密集模型" in line for line in logs)
    assert any("6/128" in line for line in logs)


def test_recover_dense_2d_nus_prefix(tmp_path: Path) -> None:
    """A prefix subset (the usual way test data is built) also recovers the true point set."""
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=list(range(32)))

    points, _logs = _recover(ds)

    assert points == list(range(32))


def test_recover_dense_2d_nus_sparse_is_refused(tmp_path: Path) -> None:
    """Truly sparse (rows < declared grid): sampling positions are unknown → returns None
    (reports the missing nuslist)."""
    ds = _make_2d_nus_dataset(tmp_path, rows=64, keep=list(range(32)))

    points, logs = _recover(ds)

    assert points is None
    assert any("稀疏文件" in line for line in logs)


def test_recover_dense_2d_nus_metadata_mismatch_is_refused(tmp_path: Path) -> None:
    "Rows > declared grid: metadata and the file disagree → returns None."
    ds = _make_2d_nus_dataset(tmp_path, rows=512, keep=[0, 1, 2, 200])

    points, logs = _recover(ds)

    assert points is None
    assert any("不一致" in line for line in logs)


def test_recover_dense_2d_nus_all_nonzero_without_schedule_is_ambiguous(
    tmp_path: Path,
) -> None:
    "Regression coverage: test recover dense 2d nus all nonzero without schedule is ambiguous."
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=list(range(128)))
    exp = read_dataset(ds)
    assert exp.sampling.mode.value == "nus", exp.sampling.mode
    assert exp.sampling.schedule_type == "params"

    points, logs = _recover(ds)

    assert points is None
    assert any("无法区分" in line or "impossible to distinguish" in line for line in logs)


def test_full_nuslist_is_uniform(tmp_path: Path) -> None:
    """The nuslist covers the whole grid → effectively full sampling → uniform (no SMILE)."""
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=list(range(128)))
    (ds / "nuslist").write_text("\n".join(str(k) for k in range(128)) + "\n", encoding="utf-8")
    exp = read_dataset(ds)
    assert exp.sampling.mode.value == "uniform"
    assert exp.sampling.schedule_type == "full_sampling"
    assert exp.sampling.sampling_fraction == pytest.approx(1.0)
    assert exp.sampling.schedule_file == "nuslist"
    assert exp.sampling.evidence


def test_partial_nuslist_stays_nus(tmp_path: Path) -> None:
    """The sampling list covers only some complex points → still NUS (SMILE path unchanged)."""
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=[0, 5, 37])
    (ds / "nuslist").write_text("0\n5\n37\n", encoding="utf-8")
    exp = read_dataset(ds)
    assert exp.sampling.mode.value == "nus"
    assert exp.sampling.schedule_type == "nuslist"
    assert not any("满采样" in line for line in exp.sampling.evidence)


@pytest.mark.parametrize(
    "coordinates",
    [list(range(127)) + [0], list(range(127)) + [999]],
    ids=["duplicate_missing", "out_of_range_missing"],
)
def test_malformed_full_length_nuslist_stays_nus(tmp_path: Path, coordinates: list[int]) -> None:
    """Reaching the grid row count with duplicate or out-of-range coordinates does not count
    as full coverage; the NUS path must still be taken."""
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=list(range(128)))
    (ds / "nuslist").write_text(
        "\n".join(str(value) for value in coordinates) + "\n", encoding="utf-8"
    )
    exp = read_dataset(ds)
    assert exp.sampling.mode.value == "nus"
    assert exp.sampling.schedule_type == "nuslist"
    assert not any("满采样" in line for line in exp.sampling.evidence)


def _finalize(raw: Path, work: Path, ndim: int) -> tuple[bool, list[str]]:
    from backend.nmrpipe_backend import NMRPipeBackend

    logs: list[str] = []
    ok = NMRPipeBackend(nmrpipe_bin="")._finalize_converted_fid(raw, work, "d_001", logs, ndim=ndim)
    return ok, logs


def test_finalize_2d_single_file_in_fid_dir(tmp_path: Path) -> None:
    """2D: bruker writing its output into fid/ (with a %03d name) is still a single plane →
    handled as a single file."""
    raw = tmp_path / "raw"
    (raw / "fid").mkdir(parents=True)
    (raw / "fid" / "test%03d.fid").write_bytes(b"x" * 1024)
    work = tmp_path / "work"
    work.mkdir()

    ok, logs = _finalize(raw, work, 2)

    assert ok is True
    assert (work / "d_001.fid").is_file()
    assert not (raw / "fid" / "test%03d.fid").exists()
    assert any("单平面输出" in line for line in logs)


def test_finalize_3d_keeps_slice_stream(tmp_path: Path) -> None:
    """3D: a real slice stream (multiple files) is still filed into the slice dir, unchanged."""
    raw = tmp_path / "raw"
    (raw / "fid").mkdir(parents=True)
    for index in (1, 2):
        (raw / "fid" / f"test{index:03d}.fid").write_bytes(b"x")
    work = tmp_path / "work"
    work.mkdir()

    ok, logs = _finalize(raw, work, 3)

    assert ok is True
    assert (work / "fid").is_dir()
    assert not (work / "d_001.fid").exists()
    assert any("切片式 fid" in line for line in logs)


def test_finalize_2d_multi_slice_falls_back_to_stream(tmp_path: Path) -> None:
    """2D with more than one file in fid/: conservatively fall back to the original
    slice-stream handling (files are not swallowed)."""
    raw = tmp_path / "raw"
    (raw / "fid").mkdir(parents=True)
    for index in (1, 2):
        (raw / "fid" / f"test{index:03d}.fid").write_bytes(b"x")
    work = tmp_path / "work"
    work.mkdir()

    ok, logs = _finalize(raw, work, 2)

    assert ok is True
    assert (work / "fid").is_dir()
    assert any("切片式 fid" in line for line in logs)


def test_recover_dense_2d_nus_float64(tmp_path: Path) -> None:
    """DTYPE=1 (float64): the row count is computed from 8 bytes per sample and read
    correctly (a hard-coded int32 used to misjudge the row count)."""
    keep = [0, 3, 40, 127]
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=keep, dtype_code=1)

    points, logs = _recover(ds)

    assert points == keep
    assert any("f8" in line for line in logs)


def test_recover_dense_2d_nus_float32(tmp_path: Path) -> None:
    """DTYPE=2 (float32): likewise decided from the element byte size."""
    keep = [1, 9, 64]
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=keep, dtype_code=2)

    points, logs = _recover(ds)

    assert points == keep
    assert any("f4" in line for line in logs)


def test_recover_dense_2d_nus_unknown_dtype_refused(tmp_path: Path) -> None:
    """Unknown DTYPE (9): no guessing, the decision fails → reports the missing schedule."""
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=[0], dtype_code=9)

    points, logs = _recover(ds)

    assert points is None
    assert any("DTYPE" in line for line in logs)


def test_smile_scan_runs_chosen_mode_once(tmp_path: Path, monkeypatch) -> None:
    """Pick the run mode from the ranking criterion: true-peak-only → full sampling;
    consistency → holdout; each candidate runs once.

    User 2026-09-11: "choose full or partial sampling according to the ranking method needed,
    instead of running twice".
    """
    from backend import nmrpipe_backend as nb
    from backend.runtime import CompletedProcess

    exp = read_dataset(BRUKER / "nus_2d")

    def _install(monkeypatch, ran: list[str]) -> None:
        def _fake_reconstruct(self, experiment, params=None, **kwargs):
            params = dict(params or {})
            sample = str(params.get("nuslist_file") or "nuslist")
            return {
                "success": True,
                "message": "fake",
                "logs": [],
                "script": (
                    "#!/bin/csh\nmkdir -p nus2d\n"
                    "nmrPipe -in e.fid | nmrPipe -fn SMILE -nDim 2 \\\n"
                    f"  -sample {sample} -sampleCount 3 \\\n"
                    "| pipe2xyz -out nus2d/recon.ft1 -x -ov \\\n"
                    "  -out cand.ft2 -ov\n"
                ),
                "script_path": "",
                "work_dir": "",
            }

        class _FakeCsh:
            def run(self, argv, *, cwd=None, timeout=3600, on_line=None):
                script = (Path(cwd) / argv[-1]).read_text(encoding="utf-8")
                ran.append(script)
                out = None
                for line in script.splitlines():
                    if "-out " in line:
                        out = line.split("-out ", 1)[1].split()[0]
                if out:
                    (Path(cwd) / out).write_bytes(b"x")
                return CompletedProcess("", "", "", 0)

        monkeypatch.setattr(nb.NMRPipeBackend, "reconstruct_nus", _fake_reconstruct)
        monkeypatch.setattr(nb, "CshRuntime", lambda: _FakeCsh())

    # true-peak criterion (holdout_ratio=0) → run full sampling once
    ran_full: list[str] = []
    _install(monkeypatch, ran_full)
    scan_full = nb.NMRPipeBackend(nmrpipe_bin="").smile_scan(
        exp,
        {},
        [{"nsigma": 3.0, "thresh": 0.9}],
        work_dir=tmp_path / "scan_full",
        holdout_ratio=0.0,
    )
    smile_full = [s for s in ran_full if "-fn SMILE" in s]
    assert len(smile_full) == 1  # once per candidate
    assert "nuslist_train" not in smile_full[0]

    # consistency criterion (holdout_ratio>0) → run the holdout once
    ran_ho: list[str] = []
    _install(monkeypatch, ran_ho)
    scan_ho = nb.NMRPipeBackend(nmrpipe_bin="").smile_scan(
        exp,
        {},
        [{"nsigma": 3.0, "thresh": 0.9}],
        work_dir=tmp_path / "scan_ho",
        holdout_ratio=0.5,
    )
    smile_ho = [s for s in ran_ho if "-fn SMILE" in s]
    assert len(smile_ho) == 1  # once per candidate
    assert "nuslist_train" in smile_ho[0]

    # under both criteria the ranked script is the full-sampling one (for reruns)
    for scan in (scan_full, scan_ho):
        assert "nuslist_train" not in scan["candidates"][0]["script"]


def test_metadata_nus_full_cartesian_without_schedule_stays_nus(
    tmp_path: Path,
) -> None:
    "Regression coverage: test metadata nus full cartesian without schedule stays nus."
    complete = _make_2d_nus_dataset(tmp_path, rows=256, keep=list(range(128)))
    exp_dense = read_dataset(complete)
    assert exp_dense.sampling.mode.value == "nus"
    assert exp_dense.sampling.schedule_type == "params"
    assert not any("满采样" in line for line in exp_dense.sampling.evidence)

    incomplete = _make_2d_nus_dataset(tmp_path, rows=256, keep=list(range(127)))
    exp_sparse = read_dataset(incomplete)
    assert exp_sparse.sampling.mode.value == "nus"

    assert exp_sparse.sampling.schedule_type == "zero_trace"
    assert not any("满采样" in line for line in exp_sparse.sampling.evidence)
