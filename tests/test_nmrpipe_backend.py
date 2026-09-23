"""NMRPipe backend behaviour test (no NMRPipe on Windows, verify graceful degradation)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import backend.nmrpipe_backend as npb
from backend.factory import create_backend
from backend.nmrpipe_backend import NMRPipeBackend
from backend.nmrpipe_finder import find_nmrpipe_bin
from core.data.bruker_reader import read_dataset
from core.planning.method_selector import select_method
from workflow import field_drift


def test_converted_fid_reuse_follows_the_raw_fingerprint(tmp_path: Path) -> None:
    """Reuse follows the raw fingerprint: a change re-converts, a missing record still logs."""
    backend = NMRPipeBackend(nmrpipe_bin="")
    work = tmp_path / "process"
    work.mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "acqus").write_text("##TITLE= test\n", encoding="utf-8")
    (raw / "ser").write_bytes(b"1" * 64)
    (work / "d_001.fid").write_bytes(b"x" * 32)
    logs: list[str] = []

    # no record yet (legacy project): reuse, but say in the log that nothing was verified
    assert backend._converted_fid_is_current(work, "d_001", raw, logs) is True
    assert logs

    backend._record_conversion(work, "d_001", raw, logs)
    assert backend._conversion_record_path(work, "d_001").is_file()
    logs.clear()
    assert backend._converted_fid_is_current(work, "d_001", raw, logs) is True

    # the raw data changed (for example the source-level NUS cleanup): convert again
    (raw / "ser").write_bytes(b"2" * 64)
    logs.clear()
    assert backend._converted_fid_is_current(work, "d_001", raw, logs) is False
    assert logs

    # truncated fid: convert again
    backend._record_conversion(work, "d_001", raw, logs)
    (work / "d_001.fid").write_bytes(b"x")
    logs.clear()
    assert backend._converted_fid_is_current(work, "d_001", raw, logs) is False


class _FakeCshRuntime:
    """Fake csh: always rc=0; the converted products are written by the fake _convert."""

    def run(self, args, cwd=None, timeout=None):
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _conversion_fingerprint(work: Path, dataset_id: str) -> str:
    """Read the raw fingerprint back out of the conversion record (language-neutral evidence)."""
    record = work / f"{dataset_id}.fid.conversion.json"
    return json.loads(record.read_text(encoding="utf-8"))["raw_fingerprint"]


def _reconstruct_params() -> dict[str, object]:
    """Turn both phase searches off so the NUS path reaches script generation directly."""
    return {"direct_phase_search": False, "display_phase_search": False}


def test_reconstruct_nus_reconverts_only_when_the_raw_fingerprint_changed(
    bruker_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single-dataset NUS: reuse follows the raw fingerprint, so rewritten raw re-converts."""
    exp = read_dataset(bruker_dir / "nus_2d")
    raw = Path(exp.source_path)
    calls: list[str] = []
    monkeypatch.setattr(npb, "CshRuntime", _FakeCshRuntime)
    monkeypatch.setattr(NMRPipeBackend, "_bin_dir", lambda self: tmp_path)

    def fake_convert(self, runtime, experiment, r, work, fid_com_overrides=None):
        calls.append("convert")
        work.mkdir(parents=True, exist_ok=True)
        (work / f"{experiment.dataset_id}.fid").write_bytes(b"fid-v1")
        return True, ["fake conversion"]

    monkeypatch.setattr(NMRPipeBackend, "_convert", fake_convert)
    backend = NMRPipeBackend(nmrpipe_bin="")
    params = _reconstruct_params()

    first = backend.reconstruct_nus(exp, dict(params), script_only=True)
    assert first["success"] is True
    assert len(calls) == 1
    work = Path(first["work_dir"])
    fingerprint = _conversion_fingerprint(work, exp.dataset_id)

    second = backend.reconstruct_nus(exp, dict(params), script_only=True)
    assert second["success"] is True
    assert len(calls) == 1  # unchanged raw -> the converted product is reused, no new conversion
    assert _conversion_fingerprint(work, exp.dataset_id) == fingerprint

    # source-level cleanup rewrote raw (for example a nuslist with bad points removed)
    (raw / "nuslist").write_text("1 1\n2 3\n4 5\n", encoding="utf-8")
    third = backend.reconstruct_nus(exp, dict(params), script_only=True)
    assert third["success"] is True
    assert len(calls) == 2
    assert _conversion_fingerprint(work, exp.dataset_id) != fingerprint


def test_convert_segments_gates_the_automatic_drift_check(tmp_path: Path) -> None:
    """Multi-part conversion: automatic inter-part drift by default; a manual
    segment_shift_hz keeps the scripts untouched."""
    from types import SimpleNamespace

    from core.data.internal_data_model import AxisRole, Dimension, Experiment

    def fake_convert_dir(
        self, runtime, experiment, raw_dir, dest_work, is_nus, logs, fid_com_overrides=None
    ):
        dest_work.mkdir(parents=True, exist_ok=True)
        (dest_work / f"{experiment.dataset_id}.fid").write_bytes(b"converted")
        return True

    class _FakeRuntime:
        def run(self, args, cwd=None, timeout=None):  # noqa: ARG002
            name = args[args.index("-out") + 1]
            (Path(cwd) / name).write_bytes(b"shifted")
            return SimpleNamespace(returncode=0)

    calls: list[bool] = []
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(NMRPipeBackend, "_convert_dir", fake_convert_dir)
    monkeypatch.setattr(NMRPipeBackend, "_merge_single_fid", lambda *a, **k: True)
    monkeypatch.setattr(
        NMRPipeBackend,
        "_correct_group_drift",
        lambda self, runtime, experiment, work, logs: calls.append(True),
    )
    try:
        exp = Experiment(
            dataset_id="d_900",
            source_path=tmp_path,
            ndim=2,
            dimensions=[
                Dimension(logical_axis="F2", nucleus="1H", td=64, role=AxisRole.DIRECT),
                Dimension(logical_axis="F1", nucleus="15N", td=8),
            ],
            segments=[tmp_path / "a", tmp_path / "b"],
        )
        backend = NMRPipeBackend(nmrpipe_bin="")

        ok, logs = backend._convert_segments(_FakeRuntime(), exp, tmp_path / "auto", [])
        assert ok is True
        assert calls == [True]

        calls.clear()
        ok, logs = backend._convert_segments(
            _FakeRuntime(), exp, tmp_path / "manual", [0.0, 12.5]
        )
        assert ok is True
        assert calls == []
        assert any("segment_shift_hz" in line for line in logs)
    finally:
        monkeypatch.undo()


def test_multi_segment_reuse_requires_the_field_drift_record(tmp_path: Path) -> None:
    """Multi-part reuse: a record without an inter-part drift conclusion means convert again."""
    backend = NMRPipeBackend(nmrpipe_bin="")
    work = tmp_path / "process"
    work.mkdir()
    raws = []
    for name in ("raw", "raw2"):
        raw = tmp_path / name
        raw.mkdir()
        (raw / "acqus").write_text("##TITLE= test\n", encoding="utf-8")
        raws.append(raw)
    (work / "d_001.fid").write_bytes(b"x" * 32)
    logs: list[str] = []

    # an old record (converted before this feature): no drift field -> convert again
    backend._record_conversion(work, "d_001", raws, logs)
    logs.clear()
    assert (
        backend._converted_fid_is_current(
            work, "d_001", raws, logs, require_field_drift=True
        )
        is False
    )
    assert any("field drift" in line for line in logs)

    # with the drift conclusion -> reuse; the single-dataset path (default False) is unaffected
    field_drift.write_field_drift_record(work, {"checked": True, "rounds": []})
    backend._record_conversion(work, "d_001", raws, logs)
    logs.clear()
    assert (
        backend._converted_fid_is_current(
            work, "d_001", raws, logs, require_field_drift=True
        )
        is True
    )
    assert backend._converted_fid_is_current(work, "d_001", raws, logs) is True


def test_reconstruct_nus_segments_reuse_follows_the_raw_fingerprint(
    bruker_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Segmented NUS: the merged product is reused on the same fingerprint; a change re-converts."""
    exp = read_dataset(bruker_dir / "nus_2d")
    base = Path(exp.source_path)
    segments: list[Path] = []
    for name in ("s1", "s2"):
        segment = base.parent / name
        segment.mkdir()
        for filename in ("acqus", "acqu2s", "nuslist"):
            (segment / filename).write_bytes((base / filename).read_bytes())
        segments.append(segment)
    exp.segments = segments

    merge_calls: list[tuple[float, ...]] = []
    monkeypatch.setattr(npb, "CshRuntime", _FakeCshRuntime)
    monkeypatch.setattr(NMRPipeBackend, "_bin_dir", lambda self: tmp_path)

    def fake_convert_segments(
        self, runtime, experiment, work, shifts, fid_com_overrides=None
    ):
        merge_calls.append(tuple(shifts))
        (work / "merged" / "fid").mkdir(parents=True, exist_ok=True)
        (work / "merged" / "fid" / "test001.fid").write_bytes(b"merged-v1")
        (work / "nuslist").write_text("1 1\n2 3\n4 5\n", encoding="utf-8")
        # the real _convert_segments leaves an inter-part drift conclusion behind for multiple
        # parts (2026-09-23); the fake must do the same or the reuse check would treat a record
        # without a drift conclusion as an old record and convert again
        field_drift.write_field_drift_record(work, {"checked": True, "rounds": []})
        return True, ["fake segment conversion"]

    monkeypatch.setattr(NMRPipeBackend, "_convert_segments", fake_convert_segments)
    monkeypatch.setattr(
        NMRPipeBackend,
        "_write_merged_nuslist",
        lambda self, work, segment_dirs, experiment, logs: (3, []),
    )
    backend = NMRPipeBackend(nmrpipe_bin="")
    params = _reconstruct_params()

    first = backend.reconstruct_nus(exp, dict(params), script_only=True)
    assert first["success"] is True
    assert len(merge_calls) == 1
    work = Path(first["work_dir"])
    fingerprint = _conversion_fingerprint(work, exp.dataset_id)

    second = backend.reconstruct_nus(exp, dict(params), script_only=True)
    assert second["success"] is True
    assert len(merge_calls) == 1  # unchanged raw -> the merged product is reused
    assert _conversion_fingerprint(work, exp.dataset_id) == fingerprint

    (segments[0] / "nuslist").write_text("1 1\n2 3\n", encoding="utf-8")
    third = backend.reconstruct_nus(exp, dict(params), script_only=True)
    assert third["success"] is True
    assert len(merge_calls) == 2
    assert _conversion_fingerprint(work, exp.dataset_id) != fingerprint


_NO_NMRPIPE = find_nmrpipe_bin() is None


def test_factory_returns_nmrpipe_backend() -> None:
    backend = create_backend({"backend": {"provider": "nmrpipe"}})
    assert isinstance(backend, NMRPipeBackend)


@pytest.mark.skipif(not _NO_NMRPIPE, reason=
    "NMRPipe has been installed on this machine, skip the missing path test")
def test_health_check_missing_nmrpipe() -> None:
    backend = NMRPipeBackend(nmrpipe_bin="")
    health = backend.health_check()
    assert health["ok"] is False
    assert "nmrPipe" in health["message"]


@pytest.mark.skipif(not _NO_NMRPIPE, reason=
    "NMRPipe has been installed on this machine, skip the missing path test")
def test_process_missing_nmrpipe_graceful(bruker_dir: Path, tmp_path: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_small")
    plan = select_method(exp)
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.process(exp, plan)
    assert result["success"] is False
    assert "not found" in result["message"]


def test_process_bad_explicit_bin_graceful(bruker_dir: Path, tmp_path: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_small")
    plan = select_method(exp)
    backend = NMRPipeBackend(nmrpipe_bin=str(tmp_path))
    result = backend.process(exp, plan)
    assert result["success"] is False


@pytest.mark.skipif(not _NO_NMRPIPE, reason=
    "NMRPipe has been installed on this machine, skip the missing path test")
def test_reconstruct_nus_missing_nmrpipe_graceful(bruker_dir: Path, tmp_path: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.reconstruct_nus(exp, {})
    assert result["success"] is False


def test_process_accepts_extract_params(bruker_dir: Path) -> None:
    """Process accepts params(extract/ext_lo/ext_hi) and degrades gracefully without NMRPipe."""
    exp = read_dataset(bruker_dir / "hsqc_small")
    plan = select_method(exp)
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.process(
        exp,
        plan,
        params={"extract": False, "ext_lo": "9.0", "ext_hi": "7.5"},
    )
    assert result["success"] is False


def test_reconstruct_nus_accepts_extract(bruker_dir: Path) -> None:
    """Reconstruct_nus accepts extract parameter and degrades gracefully without NMRPipe."""
    exp = read_dataset(bruker_dir / "nus_3d")
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.reconstruct_nus(exp, {"extract": False})
    assert result["success"] is False


def test_reconstruct_nus_rejects_uniform(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_small")
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.reconstruct_nus(exp, {})
    assert result["success"] is False
    assert "non-NUS" in result["message"]


@pytest.mark.skipif(not _NO_NMRPIPE, reason=
    "NMRPipe has been installed on this machine, skip the missing path test")
def test_finalize_converted_fid_slice_form(tmp_path: Path) -> None:
    """0.2.80: bruker slice output (fid/test%03d.fid) is accepted and returned to work/fid/."""
    backend = NMRPipeBackend(nmrpipe_bin="")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "fid").mkdir()
    for i in (1, 2, 3):
        (raw / "fid" / f"test{i:03d}.fid").write_bytes(b"x")
    dest = tmp_path / "work"
    dest.mkdir()
    logs: list[str] = []
    assert backend._finalize_converted_fid(raw, dest, "exp", logs)
    assert len(list((dest / "fid").glob("test*.fid"))) == 3
    assert not (dest / "exp.fid").exists()
    assert any("sliced fid" in line for line in logs)


def test_finalize_converted_fid_single_file(tmp_path: Path) -> None:
    """Single file test.fid paths remain compatible (non-sliced bruker output)."""
    backend = NMRPipeBackend(nmrpipe_bin="")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "test.fid").write_bytes(b"x")
    dest = tmp_path / "work"
    dest.mkdir()
    logs: list[str] = []
    assert backend._finalize_converted_fid(raw, dest, "exp", logs)
    assert (dest / "exp.fid").is_file()


def _write_plane(
    out: Path, arr: np.ndarray, *, f1_size: int = 30, f3_size: int = 4
) -> None:
    """Use nmrglue to write a 3D reconstruction plane (The first axis is real/Virtual interleaving,
    consistent with nus3d_rc). The nus3d_rc plane is a complex data (n_dir, n_f1)
    Reality/virtual interleaved storage, read back as (2·n_dir, n_f1) real data; the head uses
    FDSIZE=n_f1, FDSPECNUM=n_dir(nmrglue 2D plane reading convention, checked against the real
    plane head)."""
    import nmrglue as ng

    dic = ng.pipe.create_empty_dic()
    dic.update(
        {
            "FDSIZE": float(arr.shape[1]),
            "FDSPECNUM": float(arr.shape[0]),
            "FDREALSIZE": float(2 * arr.shape[0]),
            "FDF1LABEL": "15N",
            "FDF1TDSIZE": float(f1_size),
            "FDF2LABEL": "1H",
            "FDF2TDSIZE": 1024.0,
            "FDF3LABEL": "13C",
            "FDF3TDSIZE": 75.0,
            "FDF3SIZE": float(f3_size),
            "FDFILECOUNT": float(f3_size),
            "FDDIMCOUNT": 3.0,
            "FDF2FTFLAG": 1.0,
            "FDF2QUADFLAG": 1.0,
        }
    )
    ng.pipe.write(str(out), dic, arr.astype(np.complex64), overwrite=True)


def _synthetic_3d_planes(
    work: Path, n_dir: int = 64, *, theta: float = 0.0
) -> Path:
    """Synthesize 3D complex plane: one plane for each direct dimension point, direct dimension =
    plane number (0.2.199-patch17). The plane is a complex (n_i0, n_i1) Reality/virtual
    interleaved storage; the direct trace of the signal at several (i0, i1) positions (along the
    plane number) is a clean absorption Lorentzian, which can be rotated theta with a known
    phase."""
    import nmrglue as ng

    rng = np.random.default_rng(7)
    n_i0, n_i1 = 16, 16
    plane_dir = work / "nus3d_rc"
    plane_dir.mkdir(parents=True, exist_ok=True)
    k = np.arange(n_dir, dtype=float)
    signals = np.zeros((n_i0, n_i1, n_dir), dtype=np.complex128)
    for i0, i1 in ((3, 3), (3, 11), (8, 8), (11, 3), (11, 11), (6, 13)):
        peak = 16 + i0 + i1  # The peak position falls within margin(8)..n-margin(56).
        signals[i0, i1, :] = 400.0 / (1.0 + ((k - peak) / 4.0) ** 2)
    if theta:
        ramp = np.exp(1j * np.deg2rad(theta + 12.0 * k / (n_dir - 1)))
        signals = signals * ramp[None, None, :]
    noise = rng.normal(0, 0.05, size=(n_i0, n_i1, n_dir))
    noise = noise + 1j * rng.normal(0, 0.05, size=noise.shape)
    data = signals + noise
    for p in range(n_dir):
        _write_plane(
            plane_dir / f"test{p + 1:04d}.ft1", data[:, :, p], f3_size=n_dir
        )
    dic, data0 = ng.pipe.read(str(plane_dir / "test0001.ft1"))
    assert np.asarray(data0).dtype == np.float32, "Planes should be real data interleaved storage"
    assert np.asarray(data0).shape == (2 * n_i0, n_i1), "interleaved complex layout error"
    return plane_dir


def test_display_phase_search_3d_unpacks_interleaved(
    tmp_path: Path,
) -> None:
    """0.2.98 + 0.2.199-patch17: The 3D display layer phase search first unpacks the replica, and
    then scores along the plane sequence number (direct dimension, axis=-1). The main
    reconstruction presses PS(0,0) to output, and the minimum correction when the clean peak is
    near zero phase should return (0,0); Unpacking failed/Direct scoring of interleaved data
    will report errors or return meaningless results.."""
    from backend.nmrpipe_backend import NMRPipeBackend

    backend = NMRPipeBackend(nmrpipe_bin="")
    plane_dir = _synthetic_3d_planes(tmp_path)
    logs: list[str] = []
    experiment = type("Exp", (), {"ndim": 3})()
    est = backend._display_phase_search(experiment, tmp_path, logs)
    assert est is not None, f"logs={logs}"
    p0, p1, score = est
    assert score >= 30.0
    assert abs(p0) <= 5.0, est
    assert abs(p1) <= 5.0, est
    assert plane_dir.is_dir()


def test_apply_direct_phase_3d_rotates_copy_not_source(
    tmp_path: Path,
) -> None:
    """0.2.98: 3D fill phase writes nus3d_rc_ph/ copy and finalize, the source plane remains
    unchanged."""
    import nmrglue as ng

    from backend.nmrpipe_backend import NMRPipeBackend

    backend = NMRPipeBackend(nmrpipe_bin="")
    plane_dir = _synthetic_3d_planes(tmp_path)
    before = {
        path.name: np.asarray(ng.pipe.read(str(path))[1]).copy()
        for path in sorted(plane_dir.glob("test*.ft1"))
    }
    calls: list[tuple[Path | str | None, str | None]] = []

    def fake_finalize(experiment, *, work_dir=None, planes=None, **kwargs):
        calls.append((work_dir, planes))
        return {"success": True, "spectrum_path": str(tmp_path / "out.ft3")}

    backend.finalize_nus = fake_finalize  # type: ignore[method-assign]
    logs: list[str] = []
    experiment = type("Exp", (), {"ndim": 3})()
    ok = backend._apply_direct_phase(experiment, tmp_path, 33.0, 12.0, logs)
    assert ok
    assert calls and calls[0][0] == tmp_path
    assert calls[0][1] == "nus3d_rc_ph/test%04d.ft1"
    out_dir = tmp_path / "nus3d_rc_ph"
    assert out_dir.is_dir()
    assert len(list(out_dir.glob("test*.ft1"))) == len(
        list(plane_dir.glob("test*.ft1"))
    )
    # Source plane remains unchanged byte by byte.
    for path in sorted(plane_dir.glob("test*.ft1")):
        after = np.asarray(ng.pipe.read(str(path))[1])
        assert np.array_equal(after, before[path.name])



def test_finalize_converted_fid_missing(tmp_path: Path) -> None:
    """Fails (not silently) when there is neither test.fid nor slice."""
    backend = NMRPipeBackend(nmrpipe_bin="")
    raw = tmp_path / "raw"
    raw.mkdir()
    dest = tmp_path / "work"
    dest.mkdir()
    assert not backend._finalize_converted_fid(raw, dest, "exp", [])


def test_reconstruct_nus_segments_missing_nmrpipe(bruker_dir: Path, tmp_path: Path) -> None:
    import shutil

    from core.data.bruker_reader import read_segments

    dst_a = tmp_path / "seg_a"
    dst_b = tmp_path / "seg_b"
    shutil.copytree(bruker_dir / "nus_3d", dst_a)
    shutil.copytree(bruker_dir / "nus_3d", dst_b)
    # 0.2.199-patch29dz: Pre-checking requirements acqus+ser (fixture simplified directory no ser,
    # real must have).
    (dst_a / "ser").touch()
    (dst_b / "ser").touch()
    exp = read_segments([dst_a, dst_b])
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.reconstruct_nus(exp, {})
    assert result["success"] is False

def test_write_merged_nuslist_detects_bad_points(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Bad point detection: out-of-bounds points + cross-segment duplicate points are removed from
    the merged nuslist and ⚠ prompted."""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_segments

    seg1 = tmp_path / "s1"
    seg2 = tmp_path / "s2"
    shutil.copytree(bruker_dir / "nus_3d", seg1)
    shutil.copytree(bruker_dir / "nus_3d", seg2)
    nl1 = (seg1 / "nuslist").read_text(encoding="utf-8").splitlines()
    nl2 = (seg2 / "nuslist").read_text(encoding="utf-8").splitlines()
    first2 = nl2[0]
    nl1 = [first2] + nl1
    nl2 = nl2 + ["1000 1000"]
    (seg1 / "nuslist").write_text("\n".join(nl1) + "\n", encoding="utf-8")
    (seg2 / "nuslist").write_text("\n".join(nl2) + "\n", encoding="utf-8")
    exp = read_segments([seg1, seg2])
    backend = NMRPipeBackend()
    logs: list[str] = []
    count, bad = backend._write_merged_nuslist(tmp_path, [seg1, seg2], exp, logs)
    assert (1000, 1000) in bad
    assert tuple(int(v) for v in first2.split()) in bad
    joined = "\n".join(logs)
    assert "⚠ Sampling bad point detected" in joined
    assert "out of bounds" in joined
    assert "repeat" in joined
    written = (tmp_path / "nuslist").read_text(encoding="utf-8").splitlines()
    assert all(tuple(int(v) for v in line.split()) not in bad for line in written)
    assert count == len(written)

def test_ser_point_layout_derives_bytes(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.195: ser layout is derived according to sampling parameters (direct dimension TD
    completion + word length + redundancy)."""
    import shutil

    from backend.nmrpipe_backend import _ser_point_layout

    src = tmp_path / "nus"
    shutil.copytree(bruker_dir / "nus_2d", src)
    exp = read_dataset(src)
    # Nus_2d direct TD=2048 -> padding 2048 -> 1024 multipoint x 2 x 8 bytes = 16384.
    layout = _ser_point_layout(exp, 4 * 16384, 4)
    assert layout == (16384, 16384, 1)
    # Redundant 4 vectors/point.
    layout2 = _ser_point_layout(exp, 4 * 4 * 16384, 4)
    assert layout2 == (4 * 16384, 16384, 4)
    # Not divisible/Unable to determine -> None.
    assert _ser_point_layout(exp, 4 * 100, 4) is None


def test_zero_bad_point_fid_states_slices(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.195: bad point cleared by States layout falls on slice 2*f1+1/2*f1+2 row 2*f2/2*f2+1, no
    longer misuse test{f1}."""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_dataset

    src = tmp_path / "nus"
    shutil.copytree(bruker_dir / "nus_3d", src)
    exp = read_dataset(src)
    work = tmp_path / "work"
    slice_dir = work / "merged" / "fid"
    slice_dir.mkdir(parents=True)
    for z in (3, 7, 8):
        (slice_dir / f"test{z:03d}.fid").write_bytes(b"x")

    read_targets: list[str] = []
    write_targets: list[str] = []

    def fake_read(path):
        read_targets.append(Path(path).name)
        return {"FDSIZE": 454, "FDSPECNUM": 170}, np.ones(
            (170, 454), dtype=np.complex64
        )

    def fake_write(path, dic, arr, overwrite=False):
        write_targets.append(Path(path).name)

    monkeypatch.setattr("nmrglue.pipe.read", fake_read)
    monkeypatch.setattr("nmrglue.pipe.write", fake_write)

    backend = NMRPipeBackend()
    logs: list[str] = []
    backend._zero_bad_point_fid(
        work, [(5, 3)], logs, dataset_id=exp.dataset_id
    )
    joined = "\n".join(logs)
    # States:Complex point (f2=5, f1=3) -> slice 7/8 row 10/11.
    assert sorted(write_targets) == ["test007.fid", "test008.fid"]
    assert "test007.fid" in joined and "test008.fid" in joined
    assert "test003.fid" not in joined


def test_nus_grid_from_points_and_apply(tmp_path: Path) -> None:
    """0.2.197: bad point is removed and then deduced and reduced according to the actual sampling
    range NusTD."""
    from backend.nmrpipe_backend import (
        _apply_nus_grid_after_clean,
        _nus_grid_from_points,
    )
    from core.data.internal_data_model import (
        AxisRole,
        Dimension,
        Experiment,
        Sampling,
        SamplingMode,
    )

    assert _nus_grid_from_points([]) is None
    assert _nus_grid_from_points([(10,), (63,)]) == [64]
    assert _nus_grid_from_points([(5, 3), (82, 25)]) == [83, 26]

    exp3 = Experiment(
        dataset_id="x",
        source_path=tmp_path,
        ndim=3,
        dimensions=[
            Dimension(logical_axis="F3", nucleus="1H", td=908, role=AxisRole.DIRECT),
            Dimension(logical_axis="F2", nucleus="15N", td=170, role=AxisRole.INDIRECT),
            Dimension(logical_axis="F1", nucleus="13C", td=52, role=AxisRole.INDIRECT),
        ],
        sampling=Sampling(mode=SamplingMode.NUS),
        acquisition_parameters={"acqu2s": {"NusTD": 170}, "acqu3s": {"NusTD": 52}},
    )
    logs = _apply_nus_grid_after_clean(exp3, [(5, 3), (82, 25)])
    assert exp3.acquisition_parameters["acqu2s"]["NusTD"] == 166
    # F1 actual range 26 -> 26 x 2=52, unchanged.
    assert exp3.acquisition_parameters["acqu3s"]["NusTD"] == 52
    assert any("170 → 166" in line for line in logs)

    exp2 = Experiment(
        dataset_id="y",
        source_path=tmp_path,
        ndim=2,
        dimensions=[
            Dimension(logical_axis="F2", nucleus="1H", td=2048, role=AxisRole.DIRECT),
            Dimension(logical_axis="F1", nucleus="15N", td=64, role=AxisRole.INDIRECT),
        ],
        sampling=Sampling(mode=SamplingMode.NUS),
        acquisition_parameters={"acqu2s": {"NusTD": 64}},
    )
    logs2 = _apply_nus_grid_after_clean(exp2, [(10,), (20,)])
    assert exp2.acquisition_parameters["acqu2s"]["NusTD"] == 21
    assert any("64 → 21" in line for line in logs2)


def test_clean_work_nuslist_single_dataset(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Single NUS data bad point cleaning: out-of-boundary point elimination + ⚠ prompt (all NUS
    data are unified)."""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_dataset

    src = tmp_path / "nus"
    shutil.copytree(bruker_dir / "nus_2d", src)
    # Inject out-of-bounds points (nus_2d F1 complex point grid upper limit td//2).
    lines = (src / "nuslist").read_text(encoding="utf-8").splitlines()
    (src / "nuslist").write_text("\n".join(lines) + "\n1000\n", encoding="utf-8")
    exp = read_dataset(src)
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy2(src / "nuslist", work / "nuslist")
    backend = NMRPipeBackend()
    logs: list[str] = []
    count, bad = backend._clean_work_nuslist(work, exp, logs)
    assert (1000,) in bad
    assert count == len(lines)
    joined = "\n".join(logs)
    assert "⚠ Sampling bad point detected" in joined
    written = (work / "nuslist").read_text(encoding="utf-8").splitlines()
    assert len(written) == len(lines)


def test_clean_source_nus_single_removes_with_backup(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.124: The bad point is deleted and backed up at the source ser/nuslist, and no longer
    waits for the generated FID to be cleared."""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend

    raw = tmp_path / "raw"
    shutil.copytree(bruker_dir / "nus_2d", raw)
    lines = (raw / "nuslist").read_text(encoding="utf-8").splitlines()
    (raw / "nuslist").write_text("\n".join(lines) + "\n1000\n", encoding="utf-8")
    n = len(lines) + 1
    # 0.2.195: ser bytes change with sampling parameters (direct dimension TD completion + word
    # length), constructed according to nus_2d fixture (direct TD=2048, double precision word length
    # 8): 2048//2 x 2 x 8 = 16384 byte/point.
    row_bytes = 16384
    ser = b"".join(bytes([i % 256]) * row_bytes for i in range(n))
    (raw / "ser").write_bytes(ser)
    exp = read_dataset(raw)
    backend = NMRPipeBackend()
    logs: list[str] = []
    count, bad, removed = backend._clean_source_nus(exp, [raw], logs)
    assert removed is True
    assert count == len(lines)
    assert (1000,) in bad
    assert (raw / "ser.bak").is_file()
    assert (raw / "ser.bak").stat().st_size == len(ser)
    kept = (raw / "ser").read_bytes()
    assert len(kept) == len(ser) - row_bytes  # Delete the whole bad point (last line).
    assert kept == ser[: len(lines) * row_bytes]
    written = (raw / "nuslist").read_text(encoding="utf-8").splitlines()
    assert all(tuple(int(v) for v in line.split()) != (1000,) for line in written)
    assert (raw / "nuslist.bak").is_file()
    joined = "\n".join(logs)
    assert "source" in joined and "backup" in joined


def test_clean_source_nus_breaks_link_external_untouched(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """When 0.2.124:raw/ser is a hard link, source deletion will not contaminate the external
    original."""
    import os
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend

    raw = tmp_path / "raw"
    shutil.copytree(bruker_dir / "nus_2d", raw)
    lines = (raw / "nuslist").read_text(encoding="utf-8").splitlines()
    (raw / "nuslist").write_text("\n".join(lines) + "\n1000\n", encoding="utf-8")
    n = len(lines) + 1
    row_bytes = 16384
    ser = b"".join(bytes([i % 256]) * row_bytes for i in range(n))
    external = tmp_path / "external_ser"
    external.write_bytes(ser)
    os.link(external, raw / "ser")
    exp = read_dataset(raw)
    backend = NMRPipeBackend()
    logs: list[str] = []
    _count, _bad, removed = backend._clean_source_nus(exp, [raw], logs)
    assert removed is True
    assert external.read_bytes() == ser  # External originals remain unchanged.
    assert (raw / "ser").stat().st_size == len(ser) - row_bytes
    assert (raw / "ser.bak").stat().st_size == len(ser)


def test_clean_source_nus_repeat_nus_keeps_same_points(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.199-patch29cx: Repeat the experiment and superimpose (NUS the same point) and the same
    point across the segments is not a bad point, ser is not cleared."""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_segments

    seg1 = tmp_path / "s1"
    seg2 = tmp_path / "s2"
    shutil.copytree(bruker_dir / "nus_2d", seg1)
    shutil.copytree(bruker_dir / "nus_2d", seg2)
    nl = (seg1 / "nuslist").read_text(encoding="utf-8")
    (seg2 / "nuslist").write_text(nl, encoding="utf-8")  # The two sampling points are the same.
    n = len(nl.splitlines())
    row_bytes = 16384
    ser = b"".join(bytes([i % 256]) * row_bytes for i in range(n))
    (seg1 / "ser").write_bytes(ser)
    (seg2 / "ser").write_bytes(ser)
    exp = read_segments([seg1, seg2])
    backend = NMRPipeBackend()
    logs: list[str] = []
    count, bad, removed = backend._clean_source_nus(exp, [seg1, seg2], logs)
    assert removed is False
    assert bad == []
    assert count == n
    assert (seg1 / "ser").stat().st_size == len(ser)
    assert (seg2 / "ser").stat().st_size == len(ser)
    assert not (seg1 / "ser.bak").exists()


def test_write_merged_nuslist_repeat_nus_dedups(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.199-patch29cx: Repeatedly superimpose and merge nuslist to remove duplicates and retain
    unique points, without bad points."""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_segments

    seg1 = tmp_path / "s1"
    seg2 = tmp_path / "s2"
    shutil.copytree(bruker_dir / "nus_2d", seg1)
    shutil.copytree(bruker_dir / "nus_2d", seg2)
    nl = (seg1 / "nuslist").read_text(encoding="utf-8")
    (seg2 / "nuslist").write_text(nl, encoding="utf-8")
    n = len(nl.splitlines())
    exp = read_segments([seg1, seg2])
    backend = NMRPipeBackend()
    logs: list[str] = []
    count, bad = backend._write_merged_nuslist(
        tmp_path, [seg1, seg2], exp, logs
    )
    assert bad == []
    written = (tmp_path / "nuslist").read_text(encoding="utf-8").splitlines()
    # Deduplication at the same point across segments without repeated counting.
    assert len(written) == n
    assert count == n


def test_clean_source_nus_segments_drops_bad_and_dups(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.124: Multi-segment source cleaning -- Cross-border points and cross-segment duplicate
    points are deleted from each segment ser/nuslist."""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_segments

    seg1 = tmp_path / "s1"
    seg2 = tmp_path / "s2"
    shutil.copytree(bruker_dir / "nus_2d", seg1)
    shutil.copytree(bruker_dir / "nus_2d", seg2)
    nl1 = (seg1 / "nuslist").read_text(encoding="utf-8").splitlines()
    # Not in fixture, only appears once each in seg1/seg2 -> repeated across segments.
    dup_x = "7 3"
    (seg1 / "nuslist").write_text("\n".join(nl1) + "\n" + dup_x + "\n", encoding="utf-8")
    seg2_pts = [dup_x, "9 10", "11 12", "13 14", "15 16", "1000 1000"]
    (seg2 / "nuslist").write_text("\n".join(seg2_pts) + "\n", encoding="utf-8")
    n1, n2 = len(nl1) + 1, len(seg2_pts)
    row_bytes = 16384
    (seg1 / "ser").write_bytes(b"".join(bytes([i % 256]) * row_bytes for i in range(n1)))
    (seg2 / "ser").write_bytes(b"".join(bytes([j % 256]) * row_bytes for j in range(n2)))
    exp = read_segments([seg1, seg2])
    backend = NMRPipeBackend()
    logs: list[str] = []
    count, bad, removed = backend._clean_source_nus(exp, [seg1, seg2], logs)
    assert removed is True
    # Seg1 deletes 1 line (duplicate across segments dup_x); seg2 deletes 2 lines (dup_x + out of
    # bounds).
    assert (seg1 / "ser").stat().st_size == n1 * row_bytes - row_bytes
    assert (seg2 / "ser").stat().st_size == n2 * row_bytes - 2 * row_bytes
    assert (seg1 / "ser.bak").is_file() and (seg2 / "ser.bak").is_file()
    def _points(dir_path: Path) -> list[tuple[int, ...]]:
        return [
            tuple(int(v) for v in line.split())
            for line in (dir_path / "nuslist")
            .read_text(encoding="utf-8")
            .splitlines()
        ]

    merged = _points(seg1) + _points(seg2)
    assert len(merged) == count
    assert len(set(merged)) == len(merged)
    assert all(p != (1000, 1000) for p in merged)


class _FakeConvertRuntime:
    """Simulate bruker/fid.com: output a single file or sliced fid on request."""

    def __init__(self, slices: int = 0, single: bool = False) -> None:
        self.slices = slices
        self.single = single
        self.calls: list[tuple[list[str], str | None]] = []
        self.bruker_cwd: str | None = None
        self.acqu3s_td: int | None = None

    def run(self, argv, *, cwd=None, timeout=3600, on_line=None):
        from backend.runtime import CompletedProcess

        self.calls.append((list(argv), cwd))
        if argv[:2] == ["bruker", "-AUTO"]:
            from core.experiment.bruker_parser import parse_param_file

            base = Path(cwd)
            self.bruker_cwd = cwd
            if (base / "acqu3s").is_file():
                self.acqu3s_td = parse_param_file(base / "acqu3s")["TD"]
            (base / "fid.com").write_text(
                "bruk2pipe -in ./ser -out ./test.fid \\\n"
                " -xN 96 -yN 48 -zN 128\n",
                encoding="utf-8",
                newline="\n",
            )
            if self.slices:
                slice_dir = base / "fid"
                slice_dir.mkdir(exist_ok=True)
                for i in range(1, self.slices + 1):
                    (slice_dir / f"test{i:03d}.fid").write_bytes(b"x")
            elif self.single:
                (base / "test.fid").write_bytes(b"x")
            # Fid.com of nusExpand.tcl -mask output sampling mask (1= sampling point), test
            # cleaning.
            mask_dir = base / "mask"
            mask_dir.mkdir(exist_ok=True)
            for i in range(1, 5):
                (mask_dir / f"test{i:03d}.fid").write_bytes(b"\x00")
        return CompletedProcess("", "", "", 0)


def _fake_bruker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.nmrpipe_backend.find_tool",
        lambda name, nmrpipe_bin=None: (
            Path("/bin/bruker") if name == "bruker" else None
        ),
    )


def test_convert_dir_nus3d_single_file_no_stage(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch16: 3D NUS Convert in raw, single file output, no longer correct acqu3s."""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend

    raw = tmp_path / "raw"
    shutil.copytree(bruker_dir / "nus_3d", raw)
    work = tmp_path / "work"
    work.mkdir()
    exp = read_dataset(raw)
    fake = _FakeConvertRuntime(single=True)
    backend = NMRPipeBackend(nmrpipe_bin="")
    _fake_bruker(monkeypatch)
    logs: list[str] = []
    assert backend._convert_dir(fake, exp, raw, work, True, logs)
    assert Path(fake.bruker_cwd) == raw  # Convert directly in raw, no temporary storage.
    assert (work / f"{exp.dataset_id}.fid").is_file()  # Single file homing.
    assert not (work / "fid").exists()
    assert not (raw / "acqu3s.bak").exists()  # No more modifications/backup acqu3s.
    assert not (raw / "mask").exists()  # mask Intermediates are cleaned.


def test_convert_dir_nus2d_no_stage(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2D NUS is not affected by acqu3s correction: still converted in raw, single file returned."""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend

    raw = tmp_path / "raw2d"
    shutil.copytree(bruker_dir / "nus_2d", raw)
    work = tmp_path / "work"
    work.mkdir()
    exp = read_dataset(raw)
    fake = _FakeConvertRuntime(single=True)
    backend = NMRPipeBackend(nmrpipe_bin="")
    _fake_bruker(monkeypatch)
    logs: list[str] = []
    assert backend._convert_dir(fake, exp, raw, work, True, logs)
    bruker_cwd = next(
        cwd for argv, cwd in fake.calls if argv[:2] == ["bruker", "-AUTO"]
    )
    assert Path(bruker_cwd) == raw
    assert (work / f"{exp.dataset_id}.fid").is_file()
    assert not (work / "fid").exists()
    # Patch13: The mask/ output of fid.com in raw has been cleaned.
    assert not (raw / "mask").exists()


def test_converted_fid_path_single_first(tmp_path: Path) -> None:
    """0.2.199-patch16: The conversion product path single file takes precedence, and the old
    slicing method is only compatible."""
    from backend.nmrpipe_backend import NMRPipeBackend

    work = tmp_path / "work"
    work.mkdir()
    assert NMRPipeBackend._converted_fid_path(work, "exp") == work / "exp.fid"
    single = work / "exp.fid"
    single.write_bytes(b"x")
    slice_dir = work / "fid"
    slice_dir.mkdir()
    (slice_dir / "test001.fid").write_bytes(b"x")
    # When a single file and a slice exist at the same time, the single file takes precedence.
    assert NMRPipeBackend._converted_fid_path(work, "exp") == single
    single.unlink()
    assert NMRPipeBackend._converted_fid_path(work, "exp") == slice_dir

def _write_3d_stream_ft3(path: Path) -> None:
    """Write a single-stream 3D final spectrum header (FDSIZE=1H direct dimension, the size is
    consistent with sampleB.ft3 measured)."""
    import nmrglue as ng

    dic = {k: "0" for k in ng.fileio.pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 3
    dic["FDSIZE"] = 750.0
    dic["FDSPECNUM"] = 256.0
    dic["FDF3SIZE"] = 64.0
    dic["FDPIPEFLAG"] = 1.0
    dic["FDQUADFLAG"] = 1
    for pre, label, obs, car, orig, sw in (
        ("FDF1", "15N", 60.818, 117.986, 6115.307, 2189.142),
        ("FDF2", "1H", 600.133, 4.696, 3602.677, 3001.729),
        ("FDF3", "13C", 150.909, 38.996, 272.927, 11312.218),
    ):
        dic[pre + "LABEL"] = label
        dic[pre + "OBS"] = obs
        dic[pre + "CAR"] = car
        dic[pre + "ORIG"] = orig
        dic[pre + "SW"] = sw
        dic[pre + "QUADFLAG"] = 1
    data = np.zeros((64, 256, 750), dtype=np.float32)
    ng.pipe.write(str(path), dic, data, overwrite=True)


def _write_proj_ft2(path: Path, nrow: int, ncol: int) -> None:
    """Write projection output (the header is the wrong plane header 15N/1H of proj3D measured, to
    be rewritten)."""
    import nmrglue as ng

    dic = {k: "0" for k in ng.fileio.pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSPECNUM"] = float(nrow)
    dic["FDSIZE"] = float(ncol)
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    for pre in ("FDF1", "FDF2"):
        dic[pre + "LABEL"] = "15N" if pre == "FDF1" else "1H"
        dic[pre + "OBS"] = 60.818 if pre == "FDF1" else 600.133
        dic[pre + "CAR"] = 117.986 if pre == "FDF1" else 4.696
        dic[pre + "ORIG"] = 6115.307 if pre == "FDF1" else 3602.677
        dic[pre + "SW"] = 2189.142 if pre == "FDF1" else 3001.729
    ng.pipe.write(
        str(path), dic, np.zeros((nrow, ncol), dtype=np.float32), overwrite=True
    )


def test_project_3d_mapping(tmp_path: Path, monkeypatch) -> None:
    """Project_3d Directly feed the 3D spectrum to proj3D.tcl: automatically name *.dat without
    rewriting the header."""
    from backend.nmrpipe_backend import NMRPipeBackend

    fake_tool = tmp_path / "tool"
    fake_tool.write_text("#!/bin/sh", encoding="utf-8")

    def fake_find(name, _bin=None):
        return fake_tool

    monkeypatch.setattr("backend.nmrpipe_finder.find_tool", fake_find)

    calls: list[list[str]] = []

    class FakeRun:
        def __init__(self, rc):
            self.returncode = rc

    def fake_run(argv, *, cwd=None, timeout=3600, on_line=None):
        calls.append(list(argv))
        # 0.2.133:proj3D.tcl Automatically name and output {core A}.{core B}.dat (without pre-
        # splitting the plane).
        out_dir = Path(argv[argv.index("-outDir") + 1])
        _write_proj_ft2(out_dir / "13C.15N.dat", 128, 256)
        _write_proj_ft2(out_dir / "1H.13C.dat", 256, 600)
        _write_proj_ft2(out_dir / "1H.15N.dat", 128, 600)
        return FakeRun(0)

    monkeypatch.setattr(
        "backend.nmrpipe_backend.CshRuntime.run", staticmethod(fake_run)
    )
    backend = NMRPipeBackend()
    src = tmp_path / "final.ft3"
    _write_3d_stream_ft3(src)
    out = tmp_path / "out"
    out.mkdir()
    result = backend.project_3d(src, out, prefix="proj", labels=["15N", "1H", "13C"])
    # Key = file name two cores; labels = fixed axis core, nuclei = plane two cores (file name X.Y
    # order).
    assert result["labels"] == {
        "13C-15N": "1H",
        "1H-13C": "15N",
        "1H-15N": "13C",
    }
    assert result["nuclei"] == {
        "13C-15N": ["13C", "15N"],
        "1H-13C": ["1H", "13C"],
        "1H-15N": ["1H", "15N"],
    }
    assert len(result["paths"]) == 3
    for key, p in result["paths"].items():
        assert Path(p).is_file()
    # The header has not been rewritten: keep proj3D output as is (the slot is still 15N/1H written
    # by fake).
    import nmrglue as ng

    dic, _ = ng.pipe.read(result["paths"]["13C-15N"])
    assert str(dic["FDF1LABEL"]) == "15N"
    assert str(dic["FDF2LABEL"]) == "1H"
    # Only call the equivalent command of proj3D.tcl once (directly feed 3D spectrum, automatically
    # named, including -sum).
    assert len(calls) == 1
    argv = calls[0]
    joined = " ".join(str(a) for a in argv)
    assert "-in" in argv and argv[argv.index("-in") + 1].endswith("final.ft3")
    assert "-outDir" in argv
    assert "-sum" in argv
    assert "-xyOutName" not in joined
    assert "-xzOutName" not in joined


def test_finalize_nus_window_param_passthrough(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """Finalize_nus Transparently pass params.window to the final script (before indirect dimension
    FT)."""
    from backend.nmrpipe_backend import NMRPipeBackend
    from backend.runtime import CompletedProcess

    experiment = read_dataset(bruker_dir / "nus_2d")
    work = tmp_path / "win_work"
    (work / "nus2d").mkdir(parents=True)
    (work / "nus2d" / "recon.ft1").write_bytes(b"x")

    class _FakeCsh:
        def run(self, argv, *, cwd=None, timeout=3600, on_line=None):
            (Path(cwd) / f"{experiment.dataset_id}.ft2").write_bytes(b"x")
            return CompletedProcess("", "", "", 0)

    monkeypatch.setattr("backend.nmrpipe_backend.CshRuntime", _FakeCsh)
    monkeypatch.setattr(
        "backend.nmrpipe_backend.find_nmrpipe_bin", lambda explicit="": Path("/bin")
    )
    backend = NMRPipeBackend(nmrpipe_bin="")
    resp = backend.finalize_nus(
        experiment,
        work_dir=work,
        params={"window": {"F1": {"type": "gaussian", "g1": 3.0}}},
    )
    assert resp["success"] is True, resp
    script = (work / f"{experiment.dataset_id}_finalize.com").read_text(
        encoding="utf-8"
    )
    assert "| nmrPipe -fn GM -g1 3 -g2 15 \\" in script
    lines = script.splitlines()
    gm = next(i for i, line in enumerate(lines) if "GM -g1 3" in line)
    zf = next(
        i for i, line in enumerate(lines)
        if "| nmrPipe -fn ZF" in line and i > gm
    )
    ft = next(
        i for i, line in enumerate(lines)
        if "| nmrPipe -fn FT" in line and i > zf
    )
    assert gm < zf < ft


class _FakeMergeRuntime:
    """Simulate addNMR: connect the content of in1 to in2 and write it out (the merged product can
    be verified)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, argv, *, cwd=None, timeout=3600, on_line=None):
        from backend.runtime import CompletedProcess

        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == "addNMR":
            def _val(flag: str) -> str:
                return argv[argv.index(flag) + 1]

            base = Path(cwd)
            in1 = base / _val("-in1")
            in2 = base / _val("-in2")
            out = base / _val("-out")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(in1.read_bytes() + in2.read_bytes())
            return CompletedProcess("", "", "", 0)
        return CompletedProcess("", "", "", 0)


def test_merge_slices_combines_segments(tmp_path: Path) -> None:
    """0.2.199-patch29ct: Multiple slices are merged into merged/fid/test%03d.fid."""
    from backend.nmrpipe_backend import NMRPipeBackend

    work = tmp_path / "work"
    for seg, payload in (("seg_001", b"1"), ("seg_002", b"2")):
        d = work / seg / "fid"
        d.mkdir(parents=True)
        (d / "test001.fid").write_bytes(payload + b"a")
        (d / "test002.fid").write_bytes(payload + b"b")
    fake = _FakeMergeRuntime()
    backend = NMRPipeBackend(nmrpipe_bin="")
    logs: list[str] = []
    assert backend._merge_slices(fake, work, 2, logs)
    merged = work / "merged" / "fid"
    assert (merged / "test001.fid").read_bytes() == b"2a1a"  # in1(Segment 2)+in2(merge).
    assert (merged / "test002.fid").read_bytes() == b"2b1b"
    add_calls = [c for c in fake.calls if c[0] == "addNMR"]
    assert len(add_calls) == 2
    assert any("Multi-slice merging completed" in line for line in logs)


def test_merge_slices_slice_count_mismatch(tmp_path: Path) -> None:
    """The number of slices is inconsistent -> refuse to merge (0.2.199-patch29ct)."""
    from backend.nmrpipe_backend import NMRPipeBackend

    work = tmp_path / "work"
    d1 = work / "seg_001" / "fid"
    d2 = work / "seg_002" / "fid"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    (d1 / "test001.fid").write_bytes(b"1a")
    (d1 / "test002.fid").write_bytes(b"1b")
    (d2 / "test001.fid").write_bytes(b"2a")
    fake = _FakeMergeRuntime()
    backend = NMRPipeBackend(nmrpipe_bin="")
    logs: list[str] = []
    assert not backend._merge_slices(fake, work, 2, logs)
    assert any("slices, inconsistent with" in line for line in logs)


def test_segment_kind_info_repeat_uniform(monkeypatch) -> None:
    """0.2.199-patch29cv: uniform repeatedly superimposes log and indicates TopSpin time domain
    addition."""
    import core.data.bruker_reader as br
    from backend.nmrpipe_backend import _segment_kind_info

    monkeypatch.setattr(br, "classify_segment_kind", lambda paths: "repeat_uniform")
    kind, logs = _segment_kind_info([Path("a"), Path("b")])
    assert kind == "repeat_uniform"
    assert "TopSpin fidadd" in logs[0]
    assert "point-by-point summation in the time domain" in logs[0]
    assert "does not normalise" in logs[0]


def test_segment_kind_info_repeat_nus(monkeypatch) -> None:
    """Repeat_nus: Overlay with the same sampling point and the same grid."""
    import core.data.bruker_reader as br
    from backend.nmrpipe_backend import _segment_kind_info

    monkeypatch.setattr(br, "classify_segment_kind", lambda paths: "repeat_nus")
    kind, logs = _segment_kind_info(["a", "b"])
    assert kind == "repeat_nus"
    assert "summed on one grid and reconstructed once" in logs[0]
    assert "TopSpin fidadd" in logs[0]


def test_segment_kind_info_segmented_nus(monkeypatch) -> None:
    """Segmented_nus: Complementary sampling point completes the grid."""
    import core.data.bruker_reader as br
    from backend.nmrpipe_backend import _segment_kind_info

    monkeypatch.setattr(br, "classify_segment_kind", lambda paths: "segmented_nus")
    kind, logs = _segment_kind_info(["a", "b"])
    assert kind == "segmented_nus"
    assert "complete the grid" in logs[0]


def test_segment_kind_info_classify_failure_nonblocking(monkeypatch) -> None:
    """Classification failure downgrade: return None + warning, no blocking."""
    import core.data.bruker_reader as br
    from backend.nmrpipe_backend import _segment_kind_info

    def _boom(paths):
        raise ValueError("missing acqus")

    monkeypatch.setattr(br, "classify_segment_kind", _boom)
    kind, logs = _segment_kind_info(["a", "b"])
    assert kind is None
    assert "Multi-segment type recognition failed" in logs[0]


def test_convert_to_fid_segments_annotates_segment_kind(
    tmp_path: Path, bruker_dir: Path, monkeypatch
) -> None:
    """Convert_to_fid Multi-segment branch: segment_kind into log and effective_params."""
    import shutil

    import core.data.bruker_reader as br
    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_segments

    dst_a = tmp_path / "seg_a"
    dst_b = tmp_path / "seg_b"
    shutil.copytree(bruker_dir / "nus_3d", dst_a)
    shutil.copytree(bruker_dir / "nus_3d", dst_b)
    # 0.2.199-patch29dz: Pre-checking requirements acqus+ser (fixture simplified directory no ser,
    # real must have).
    (dst_a / "ser").touch()
    (dst_b / "ser").touch()
    exp = read_segments([dst_a, dst_b])
    backend = NMRPipeBackend(nmrpipe_bin="")
    monkeypatch.setattr(backend, "_bin_dir", lambda: Path("nmrpipe"))
    monkeypatch.setattr(
        backend,
        "_clean_source_nus",
        lambda experiment, raw_dirs, logs: (0, [], False),
    )
    monkeypatch.setattr(
        backend,
        "_convert_segments",
        lambda runtime, experiment, work, shifts, fid_com_overrides=None: (
            True,
            ["Conversion completed"],
        ),
    )
    monkeypatch.setattr(
        backend,
        "_write_merged_nuslist",
        lambda work, segment_dirs, experiment, logs: (0, []),
    )
    monkeypatch.setattr(
        backend,
        "_merged_fid_in",
        lambda work, dataset_id: f"merged/{dataset_id}.fid",
    )
    monkeypatch.setattr(br, "classify_segment_kind", lambda paths: "repeat_nus")
    result = backend.convert_to_fid(exp, tmp_path)
    assert result["success"] is True
    assert result["effective_params"]["segment_kind"] == "repeat_nus"
    joined = "|".join(result["logs"])
    assert "TopSpin fidadd" in joined
    assert "summed on one grid and reconstructed once" in joined


def test_convert_to_fid_segments_classify_failure_nonblocking(
    tmp_path: Path, bruker_dir: Path, monkeypatch
) -> None:
    """Classification failure does not block FID transformation (downgrade normal segment merge +
    warning)."""
    import shutil

    import core.data.bruker_reader as br
    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_segments

    dst_a = tmp_path / "seg_a"
    dst_b = tmp_path / "seg_b"
    shutil.copytree(bruker_dir / "nus_3d", dst_a)
    shutil.copytree(bruker_dir / "nus_3d", dst_b)
    # 0.2.199-patch29dz: Pre-checking requirements acqus+ser (fixture simplified directory no ser,
    # real must have).
    (dst_a / "ser").touch()
    (dst_b / "ser").touch()
    exp = read_segments([dst_a, dst_b])
    backend = NMRPipeBackend(nmrpipe_bin="")
    monkeypatch.setattr(backend, "_bin_dir", lambda: Path("nmrpipe"))
    monkeypatch.setattr(
        backend,
        "_clean_source_nus",
        lambda experiment, raw_dirs, logs: (0, [], False),
    )
    monkeypatch.setattr(
        backend,
        "_convert_segments",
        lambda runtime, experiment, work, shifts, fid_com_overrides=None: (
            True,
            [],
        ),
    )
    monkeypatch.setattr(
        backend,
        "_write_merged_nuslist",
        lambda work, segment_dirs, experiment, logs: (0, []),
    )
    monkeypatch.setattr(
        backend,
        "_merged_fid_in",
        lambda work, dataset_id: f"merged/{dataset_id}.fid",
    )

    def _boom(paths):
        raise ValueError("missing acqus")

    monkeypatch.setattr(br, "classify_segment_kind", _boom)
    result = backend.convert_to_fid(exp, tmp_path)
    assert result["success"] is True
    assert result["effective_params"].get("segment_kind") is None
    assert any(
        "Multi-segment type recognition failed" in line for line in result["logs"]
    )
