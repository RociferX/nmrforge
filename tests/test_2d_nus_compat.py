"""2D NUS Compatible: the conversion follows the output form of bruker -AUTO + 2D leaves the
residual file. User ruling (2026-09-11): bruker -AUTO directly recognizes the 2D NUS directly as
a single file (`-out./test.fid`, the program only renames it to `{dataset_id}.fid`); the slice
stream is only 3D in the direct dimension Things that appear after processing, so **do not do**
such script rewriting as "2D forced single file", the script will always be based on the one
given by -AUTO. 2D sets aside sampling point and the residual relies on
`script_generator.build_2d_direct_only_script` to leave the file SMILE for input (2D single file
pipeline cannot cut slices)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.bruker_workflow import patch_fid_com
from core.data.bruker_reader import read_dataset

BRUKER = Path(__file__).resolve().parent / "fixtures" / "bruker"

_CONT = " \\"  # Line continuation at end of line: space + backslash.


def _auto_2d_nus_fid_com(out_line: str) -> str:
    """Bruker -AUTO script skeleton (unfold + bruk2pipe + mask) for 2D NUS."""
    return (
        "nusExpand.tcl -mode bruker -sampleCount 32 -avg -off 0" + _CONT + "\n"
        " -in ./ser -out ./ser_full -sample ./nuslist\n"
        "\n"
        "bruk2pipe -in ./ser_full" + _CONT + "\n"
        "  -xN 2048 -yN 254 -xT 1024 -yT 127" + _CONT + "\n"
        f"  {out_line}\n"
        "\n"
        "nusExpand.tcl -mask -noexpand -mode pipe -sampleCount 32 -avg -off 0"
        + _CONT + "\n"
        " -in ./test.fid -out ./mask.fid -sample ./nuslist\n"
    )


def test_2d_nus_auto_single_file_out_renamed_to_dataset() -> None:
    """-AUTO gave the order file (test.fid) -> renamed {dataset_id}.fid (there is an agreement and
    the form will not be changed)."""
    exp = read_dataset(BRUKER / "nus_2d")
    assert exp.ndim == 2

    patched, warnings = patch_fid_com(
        _auto_2d_nus_fid_com("-out ./test.fid -ov"), exp
    )

    assert f"-out ./{exp.dataset_id}.fid -ov" in patched
    assert any("out:" in w and "test.fid" in w for w in warnings)


def test_2d_nus_auto_slice_out_left_untouched() -> None:
    """-AUTO If you give sliced output, no 2D-specific rewriting will be done (-AUTO will always
    prevail)."""
    exp = read_dataset(BRUKER / "nus_2d")

    patched, _warnings = patch_fid_com(
        _auto_2d_nus_fid_com("-out ./fid/test%03d.fid -ov"), exp
    )

    assert "-out ./fid/test%03d.fid -ov" in patched


def test_3d_nus_fid_com_keeps_slice_stream() -> None:
    """3D: Sliced output is left intact (slicing only appears after 3D direct dimension
    processing)."""
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
    """2D uniform sampling (not NUS) output name rewriting behaviour does not regress."""
    exp = read_dataset(BRUKER / "hsqc_2d")
    assert exp.ndim == 2

    text = "bruk2pipe -in ./ser" + _CONT + "\n  -out ./test.fid\n"
    patched, _warnings = patch_fid_com(text, exp)

    assert f"-out ./{exp.dataset_id}.fid" in patched


def test_build_2d_direct_only_script_trims_before_smile() -> None:
    """2D direct dimension script: retain direct dimension processing, remove SMILE and after,
    single file output at the end."""
    from backend.script_generator import (
        build_2d_direct_only_script,
        generate_2d_nus_script,
    )

    exp = read_dataset(BRUKER / "nus_2d")
    script = generate_2d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft2"
    )
    direct = build_2d_direct_only_script(script)

    assert direct.endswith("| pipe2xyz -out nus2d/direct.ft1 -x -ov\n")
    assert "-fn SMILE" not in direct
    assert "-out e.ft2" not in direct
    assert "nus2d/recon.ft1" not in direct
    assert "| nmrPipe -fn EXT" in direct  # Direct dimension reserved during processing.
    assert "| nmrPipe -fn POLY -auto" in direct  # SMILE Keep the last step before.


def test_build_2d_direct_only_script_rejects_unknown_shape() -> None:
    """If it cannot be cut out, an empty string is returned (the caller skips and leaves the
    residual, and does not cut silently)."""
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
    """Create a 2D NUS dataset (without nuslist): ser has rows rows, and the complex points in keep
    are non-zero. dtype_code:TopSpin DTYPE(0=int32 / 1=float64 / 2=float32), write acqus."""
    import numpy as np

    ds = root / f"ds_{rows}_{len(keep)}_{dtype_code}"
    ds.mkdir(parents=True, exist_ok=True)
    (ds / "acqus").write_text(
        f"##$TD= {x_n}\n##$FnMODE= 0\n##$NusAMOUNT= 25\n##$NusTD= 0\n"
        f"##$DTYPE= {dtype_code}\n",
        encoding="utf-8",
    )
    (ds / "acqu2s").write_text(
        f"##$TD= {td_rows}\n##$FnMODE= 5\n##$NusTD= {td_rows}\n##$NUC1= <15N>\n",
        encoding="utf-8",
    )
    # Unknown DTYPE Usage example: Data is written as int32, the judgment should be rejected
    # directly because of unknown DTYPE.
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
    # The full sampling data has been downgraded to uniform(2026-09-14) during the reading stage;
    # the recovery function can still be called directly.
    assert exp.sampling.mode.value in ("nus", "uniform"), exp.sampling.mode
    logs: list[str] = []
    points = NMRPipeBackend(nmrpipe_bin="")._recover_dense_2d_nus(ds, exp, logs)
    return points, logs


def test_recover_dense_2d_nus_arbitrary_subset(tmp_path: Path) -> None:
    """Dense model: sampling point recovery from zero mode, supports arbitrary subsets (not "top N"
    prefix)."""
    keep = [0, 5, 37, 64, 100, 127]
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=keep)

    points, logs = _recover(ds)

    assert points == keep
    assert any("dense model" in line for line in logs)
    assert any("6/128" in line for line in logs)


def test_recover_dense_2d_nus_prefix(tmp_path: Path) -> None:
    """The prefix subset (a common way of creating data) is also restored to the real point set."""
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=list(range(32)))

    points, _logs = _recover(ds)

    assert points == list(range(32))


def test_recover_dense_2d_nus_sparse_is_refused(tmp_path: Path) -> None:
    """True sparse (number of rows < declared grid): sampling position is unknown -> Return None
    (report missing nuslist)."""
    ds = _make_2d_nus_dataset(tmp_path, rows=64, keep=list(range(32)))

    points, logs = _recover(ds)

    assert points is None
    assert any("sparse file" in line for line in logs)


def test_recover_dense_2d_nus_metadata_mismatch_is_refused(tmp_path: Path) -> None:
    """Number of rows > Declaration Grid: Metadata inconsistent with file -> Return None."""
    ds = _make_2d_nus_dataset(tmp_path, rows=512, keep=[0, 1, 2])

    points, logs = _recover(ds)

    assert points is None
    assert any("disagrees with the file" in line for line in logs)


def test_recover_dense_2d_nus_all_nonzero(tmp_path: Path) -> None:
    """Full grid without zero rows (NusAMOUNT marked NUS but the data is fully sampled) -> Uniform
    is determined after reading. 2026-09-14 (user "full sampling should go to uniform"):
    Degradation occurs in the data reading stage; the recovery function can still be called
    directly as a defensive entry, leaving a "full sampling" log."""
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=list(range(128)))
    exp = read_dataset(ds)
    assert exp.sampling.mode.value == "uniform", exp.sampling.mode
    assert exp.sampling.schedule_type == "full_sampling"
    assert exp.sampling.sampling_fraction == pytest.approx(1.0)
    assert any("actual full sampling" in line for line in exp.sampling.evidence)

    points, logs = _recover(ds)

    assert points == list(range(128))
    assert any("reconstructing from the full-sampling table" in line for line in logs)


def test_full_nuslist_is_uniform(tmp_path: Path) -> None:
    """Nuslist covers the entire grid -> actual full sampling -> uniform (no more SMILE)."""
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=list(range(128)))
    (ds / "nuslist").write_text(
        "\n".join(str(k) for k in range(128)) + "\n", encoding="utf-8"
    )
    exp = read_dataset(ds)
    assert exp.sampling.mode.value == "uniform"
    assert exp.sampling.schedule_type == "full_sampling"
    assert exp.sampling.sampling_fraction == pytest.approx(1.0)
    assert any(
        "nuslist covers the whole grid 128" in line for line in exp.sampling.evidence
    )


def test_partial_nuslist_stays_nus(tmp_path: Path) -> None:
    """The sampling schedule only covers some complex points -> still NUS(SMILE path remains
    unchanged)."""
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=[0, 5, 37])
    (ds / "nuslist").write_text("0\n5\n37\n", encoding="utf-8")
    exp = read_dataset(ds)
    assert exp.sampling.mode.value == "nus"
    assert exp.sampling.schedule_type == "nuslist"
    assert not any("actual full sampling" in line for line in exp.sampling.evidence)


@pytest.mark.parametrize(
    "coordinates",
    [list(range(127)) + [0], list(range(127)) + [999]],
    ids=["duplicate_missing", "out_of_range_missing"],
)
def test_malformed_full_length_nuslist_stays_nus(
    tmp_path: Path, coordinates: list[int]
) -> None:
    """Number of rows reaches grid but coordinates are repeated/When crossing the boundary, it does
    not count as covering the entire grid., must continue walking NUS."""
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=list(range(128)))
    (ds / "nuslist").write_text(
        "\n".join(str(value) for value in coordinates) + "\n", encoding="utf-8"
    )
    exp = read_dataset(ds)
    assert exp.sampling.mode.value == "nus"
    assert exp.sampling.schedule_type == "nuslist"
    assert not any("actual full sampling" in line for line in exp.sampling.evidence)


def _finalize(raw: Path, work: Path, ndim: int) -> tuple[bool, list[str]]:
    from backend.nmrpipe_backend import NMRPipeBackend

    logs: list[str] = []
    ok = NMRPipeBackend(nmrpipe_bin="")._finalize_converted_fid(
        raw, work, "d_001", logs, ndim=ndim
    )
    return ok, logs


def test_finalize_2d_single_file_in_fid_dir(tmp_path: Path) -> None:
    """2D: bruker writes the output into fid/ (name with %03d), which is only a single plane ->
    processed by single file."""
    raw = tmp_path / "raw"
    (raw / "fid").mkdir(parents=True)
    (raw / "fid" / "test%03d.fid").write_bytes(b"x" * 1024)
    work = tmp_path / "work"
    work.mkdir()

    ok, logs = _finalize(raw, work, 2)

    assert ok is True
    assert (work / "d_001.fid").is_file()
    assert not (raw / "fid" / "test%03d.fid").exists()
    assert any("single plane output" in line for line in logs)


def test_finalize_3d_keeps_slice_stream(tmp_path: Path) -> None:
    """3D: Real slice streams (multiple files) are still located according to the slice directory
    and do not change the behaviour."""
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
    assert any("sliced fid" in line for line in logs)


def test_finalize_2d_multi_slice_falls_back_to_stream(tmp_path: Path) -> None:
    """2D but there is more than one file in fid/: conservatively fall back to the original slice
    stream processing (no swallowing by mistake)."""
    raw = tmp_path / "raw"
    (raw / "fid").mkdir(parents=True)
    for index in (1, 2):
        (raw / "fid" / f"test{index:03d}.fid").write_bytes(b"x")
    work = tmp_path / "work"
    work.mkdir()

    ok, logs = _finalize(raw, work, 2)

    assert ok is True
    assert (work / "fid").is_dir()
    assert any("sliced fid" in line for line in logs)

def test_recover_dense_2d_nus_float64(tmp_path: Path) -> None:
    """DTYPE=1(float64): Press 8 byte/Count the number of rows of sampled values and read them
    correctly (previously hard-coding int32 would misjudge the number of lines)."""
    keep = [0, 3, 40, 127]
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=keep, dtype_code=1)

    points, logs = _recover(ds)

    assert points == keep
    assert any("f8" in line for line in logs)


def test_recover_dense_2d_nus_float32(tmp_path: Path) -> None:
    """DTYPE=2(float32): Also determined based on the number of bytes of the element."""
    keep = [1, 9, 64]
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=keep, dtype_code=2)

    points, logs = _recover(ds)

    assert points == keep
    assert any("f4" in line for line in logs)


def test_recover_dense_2d_nus_unknown_dtype_refused(tmp_path: Path) -> None:
    """DTYPE Unknown (9): No guess, judgment failed -> report missing sampling schedule."""
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=[0], dtype_code=9)

    points, logs = _recover(ds)

    assert points is None
    assert any("DTYPE" in line for line in logs)

def test_smile_scan_runs_chosen_mode_once(tmp_path: Path, monkeypatch) -> None:
    """Select the running method according to the sorting caliber: net true peak -> full sampling
    run; consistency -> set aside run; only run once for each candidate. user 2026-09-11: "You
    should choose full run or set aside part according to the required sorting method, rather
    than running twice."."""
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

    # Net true peak diameter (holdout_ratio=0) -> Run all samples once.
    ran_full: list[str] = []
    _install(monkeypatch, ran_full)
    scan_full = nb.NMRPipeBackend(nmrpipe_bin="").smile_scan(
        exp, {}, [{"nsigma": 3.0, "thresh": 0.9}],
        work_dir=tmp_path / "scan_full", holdout_ratio=0.0,
    )
    smile_full = [s for s in ran_full if "-fn SMILE" in s]
    assert len(smile_full) == 1  # Once per candidate.
    assert "nuslist_train" not in smile_full[0]

    # Consistency caliber (holdout_ratio>0) -> set aside for one run.
    ran_ho: list[str] = []
    _install(monkeypatch, ran_ho)
    scan_ho = nb.NMRPipeBackend(nmrpipe_bin="").smile_scan(
        exp, {}, [{"nsigma": 3.0, "thresh": 0.9}],
        work_dir=tmp_path / "scan_ho", holdout_ratio=0.5,
    )
    smile_ho = [s for s in ran_ho if "-fn SMILE" in s]
    assert len(smile_ho) == 1  # Once per candidate.
    assert "nuslist_train" in smile_ho[0]

    # Under both calibers, the scripts on the list are all fully sampled (for re-running).
    for scan in (scan_full, scan_ho):
        assert "nuslist_train" not in scan["candidates"][0]["script"]


# --------------------------------------------- Phase 12: Sampling detection regression (metadata vs
# actual).
def test_metadata_nus_full_cartesian_degrades_to_uniform(
    tmp_path: Path,
) -> None:
    """Metadata claims NUS, actually complete Cartesian -> downgrade uniform. The same Bruker
    metadata (NusAMOUNT=25 / NusTD) only changes ``ser`` whether the full grid is non-zero: full
    grid -> determine full sampling and go uniform; one less complex point -> still press NUS
    (cannot regard "data" as full sampling, this is 2026-09-14 user the boundaries of the
    ruling)."""
    complete = _make_2d_nus_dataset(tmp_path, rows=256, keep=list(range(128)))
    exp_dense = read_dataset(complete)
    assert exp_dense.sampling.mode.value == "uniform"
    assert exp_dense.sampling.schedule_type == "full_sampling"
    assert exp_dense.sampling.sampling_fraction == pytest.approx(1.0)
    assert any("actual full sampling" in line for line in exp_dense.sampling.evidence)

    incomplete = _make_2d_nus_dataset(tmp_path, rows=256, keep=list(range(127)))
    exp_sparse = read_dataset(incomplete)
    assert exp_sparse.sampling.mode.value == "nus"
    assert exp_sparse.sampling.schedule_type == "params"
    assert not any("actual full sampling" in line for line in exp_sparse.sampling.evidence)
