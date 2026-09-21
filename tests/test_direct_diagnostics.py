"""Direct dimension diagnostic gate test: DC offset -> POLY -time, bad point -> automatic
replacement, rendering insertion. fid template comes from ``tests/conftest.py``
``nmrpipe_fid_template``:2048 byte header + real part block per trace/imaginary block, the same
layout as the real conversion product. Previously, this file used the absolute path of the
development machine as the template, and the entire file was skipped on VM/CI (whether each
machine executes depends on whether that path exists), now the fixture As the tests are
generated, any machine will actually execute these diagnostic cases."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from workflow.direct_diagnostics import (
    DirectDiagnosticsResult,
    _read_fid_raw,
    run_direct_diagnostics,
)


@pytest.fixture
def template(nmrpipe_fid_template: Path) -> Path:
    """Fid template for diagnostics (= shared fixture, layout consistent with the real conversion
    product)."""
    return nmrpipe_fid_template


@pytest.fixture
def exp_fixture(bruker_dir: Path):
    from core.data.bruker_reader import read_dataset

    return read_dataset(bruker_dir / "nus_2d")


def _synth_like(template: Path) -> np.ndarray:
    """Synthetic replica fid of the same size as template (exponential decay + noise, no DC/bad
    point)."""
    got = _read_fid_raw(template)
    assert got is not None
    data, fdsize, specnum, _header = got
    rng = np.random.default_rng(7)
    t = np.arange(fdsize, dtype=float)
    sig = np.exp(-t / 150.0) * np.exp(2j * np.pi * 0.12 * t)
    arr = np.tile(sig, (specnum, 1))
    arr *= rng.uniform(0.5, 2.0, (specnum, 1))
    arr += rng.normal(0.0, 0.02, (specnum, fdsize))
    arr += 1j * rng.normal(0.0, 0.02, (specnum, fdsize))
    return arr.astype(np.complex64)


def _write_data_region(dst: Path, array: np.ndarray) -> None:
    """Write the complex array back to the fid data area (head still; real part block + imaginary
    part block per trace)."""
    got = _read_fid_raw(dst)
    assert got is not None
    _data, fdsize, specnum, header = got
    assert array.shape == (specnum, fdsize), (array.shape, specnum, fdsize)
    flat = (
        np.frombuffer(
            dst.read_bytes(), dtype="<f4", count=specnum * fdsize * 2, offset=header
        )
        .astype(np.float32)
        .reshape(specnum, fdsize * 2)
    )
    flat[:, :fdsize] = array.real.reshape(specnum, fdsize)
    flat[:, fdsize:] = array.imag.reshape(specnum, fdsize)
    dst.write_bytes(dst.read_bytes()[:header] + flat.tobytes())


def _signal(
    template: Path, *, decay: float = 150.0, freq: float = 0.12
) -> np.ndarray:
    """A clean single-frequency complex attenuated signal of template size (noiseless, easy to
    construct boundary conditions)."""
    got = _read_fid_raw(template)
    assert got is not None
    _data, fdsize, specnum, _header = got
    t = np.arange(fdsize, dtype=float)
    sig = np.exp(-t / decay) * np.exp(2j * np.pi * freq * t)
    return np.tile(sig, (specnum, 1)).astype(np.complex64)


def _stage(
    template: Path,
    tmp_path: Path,
    *,
    array: np.ndarray | None = None,
    synthetic: bool = False,
    dc_amp: float = 0.0,
    spike: tuple[int, int, float] | None = None,
    nan_point: tuple[int, int] | None = None,
    zero_rows: list[int] | None = None,
    high_energy_row: int | None = None,
) -> Path:
    """Copies a fid from the shared template; can replace the data area (array) or inject
    defects."""
    dst = tmp_path / "nus_2d.fid"
    dst.write_bytes(template.read_bytes())
    if array is not None:
        _write_data_region(dst, np.asarray(array, dtype=np.complex64))
    elif synthetic:
        arr = _synth_like(template)
        if dc_amp:
            arr = (arr.real + dc_amp).astype(np.float32) + 1j * arr.imag
        _write_data_region(dst, arr)
    if spike:
        row, col, mag = spike
        got = _read_fid_raw(dst)
        assert got is not None
        data, fdsize, specnum, header = got
        energies = np.sum(np.abs(data) ** 2, axis=1)
        target = row if row >= 0 else int(np.argmax(energies))
        val = data[target, col] * mag
        raw = bytearray(dst.read_bytes())
        re_off = header + target * fdsize * 8 + col * 4
        np.frombuffer(raw, dtype="<f4", offset=re_off, count=1)[0] = val.real
        np.frombuffer(raw, dtype="<f4", offset=re_off + fdsize * 4, count=1)[0] = val.imag
        dst.write_bytes(bytes(raw))
    if nan_point:
        row, col = nan_point
        got = _read_fid_raw(dst)
        assert got is not None
        data, fdsize, specnum, header = got
        target = row if row >= 0 else int(np.argmax(np.sum(np.abs(data) ** 2, axis=1)))
        raw = bytearray(dst.read_bytes())
        re_off = header + target * fdsize * 8 + col * 4
        np.frombuffer(raw, dtype="<f4", offset=re_off, count=1)[0] = np.nan
        np.frombuffer(raw, dtype="<f4", offset=re_off + fdsize * 4, count=1)[0] = np.nan
        dst.write_bytes(bytes(raw))
    if zero_rows:
        got = _read_fid_raw(dst)
        assert got is not None
        _data, fdsize, specnum, header = got
        raw = bytearray(dst.read_bytes())
        for row in zero_rows:
            re_off = header + row * fdsize * 8
            np.frombuffer(raw, dtype="<f4", offset=re_off, count=fdsize * 2)[:] = 0.0
        dst.write_bytes(bytes(raw))
    if high_energy_row is not None:
        got = _read_fid_raw(dst)
        assert got is not None
        _data, fdsize, specnum, header = got
        raw = bytearray(dst.read_bytes())
        re_off = header + high_energy_row * fdsize * 8
        block = np.frombuffer(raw, dtype="<f4", offset=re_off, count=fdsize * 2).copy()
        block *= 200.0
        np.frombuffer(raw, dtype="<f4", offset=re_off, count=fdsize * 2)[:] = block
        dst.write_bytes(bytes(raw))
    return dst


def test_parse_template_fid_layout(template: Path, tmp_path: Path, exp_fixture) -> None:
    """The template itself is the "real layout": 2048 byte header + Reality/virtual block, accepted
    by the parser."""
    fid = _stage(template, tmp_path)
    got = _read_fid_raw(fid)
    assert got is not None
    data, fdsize, specnum, header = got
    assert fdsize == 1024
    assert specnum > 100
    assert header in (512, 1024, 2048)
    assert np.isfinite(data).all()


def test_dc_offset_enables_poly_time(
    template: Path, tmp_path: Path, exp_fixture
) -> None:
    """Significant DC offset: Automatically enable POLY -time and report (0.2.199-patch29cw
    threshold 0.25)."""
    _stage(template, tmp_path, synthetic=True, dc_amp=1.0)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert isinstance(res, DirectDiagnosticsResult)
    assert res.apply_poly_time is True
    assert any("DC bias" in r and "POLY -time" in r for r in res.reports)
    assert res.metrics["dc_ratio"] > 0.0


def test_dc_small_stays_off(template: Path, tmp_path: Path, exp_fixture) -> None:
    """Data after DC removal: Not enabled POLY -time."""
    _stage(template, tmp_path, synthetic=True)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert res.apply_poly_time is False


def test_dc_small_offset_stays_off(
    template: Path, tmp_path: Path, exp_fixture
) -> None:
    """Small amplitude FID mean (common level of conventional spectrum) does not trigger POLY
    -time(0.2.199-patch29cw)."""
    _stage(template, tmp_path, synthetic=True, dc_amp=0.08)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert res.apply_poly_time is False


def test_first_point_ratio_reported(
    template: Path, tmp_path: Path, exp_fixture
) -> None:
    """The amplitude of the first point is much higher than that of the second point (group
    delay/First point reconstruction problem): report and give acqus check suggestions."""
    arr = _synth_like(template)
    arr[:, 0] *= 20.0
    _stage(template, tmp_path, array=arr)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert res.metrics["first_point_ratio"] > 1.6
    assert any("first sampling point" in r for r in res.reports)


def test_broad_solvent_peak_reported(
    template: Path, tmp_path: Path, exp_fixture
) -> None:
    """Broadband peak envelope (FWHM over spectral width 8%): Report solvent/Chemical exchange and
    recommendations to check pressing conditions."""
    arr = _signal(template, decay=3.0, freq=0.05)
    _stage(template, tmp_path, array=arr)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert bool(res.metrics["broad_peak"]) is True  # metrics Convert to float uniformly.
    assert res.metrics["fwhm_pts"] > 0.08 * res.metrics["n_direct"]
    assert any("broad envelope peak" in r for r in res.reports)


def test_drift_reported(template: Path, tmp_path: Path, exp_fixture) -> None:
    """Before sampling/Post-cycle frequency drift exceeds one line width: Report "Data processing
    cannot be eliminated + re-acquisition is recommended"."""
    got = _read_fid_raw(template)
    assert got is not None
    _data, fdsize, specnum, _header = got
    arr = np.zeros((specnum, fdsize), dtype=np.complex64)
    head_rows = max(int(specnum * 0.2), 4)
    t = np.arange(fdsize, dtype=float)
    early = 2.0 * np.exp(-t / 150.0) * np.exp(2j * np.pi * 0.10 * t)
    late = np.exp(-t / 150.0) * np.exp(2j * np.pi * 0.25 * t)
    arr[:head_rows] = early
    arr[head_rows:] = late
    _stage(template, tmp_path, array=arr)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert res.metrics["drift_pts"] > res.metrics["fwhm_pts"]
    assert any("frequency drift" in r and "re-acquiring" in r for r in res.reports)


def test_run_fid_diagnostics_paths_file(template: Path, tmp_path: Path) -> None:
    """Independent entrance: directly provide the path to the fid file to diagnose (default only
    detects but does not repair, and does not generate backups."""
    from workflow.direct_diagnostics import run_fid_diagnostics_paths

    fid = _stage(template, tmp_path, synthetic=True)
    res = run_fid_diagnostics_paths([fid])
    assert isinstance(res, DirectDiagnosticsResult)
    assert res.reports
    assert res.apply_poly_time is False
    assert not (tmp_path / "fid_diag_bak").exists()


def test_run_fid_diagnostics_paths_folder(template: Path, tmp_path: Path) -> None:
    """Independent entrance: file folder automatically collects *.fid / test*.fid."""
    from workflow.direct_diagnostics import run_fid_diagnostics_paths

    _stage(template, tmp_path, synthetic=True)
    res = run_fid_diagnostics_paths([tmp_path])
    assert isinstance(res, DirectDiagnosticsResult)
    assert res.reports


def test_run_fid_diagnostics_paths_missing(tmp_path: Path) -> None:
    """Independent entrance: when the path does not exist, it will prompt that fid is not found and
    will not crash."""
    from workflow.direct_diagnostics import run_fid_diagnostics_paths

    res = run_fid_diagnostics_paths([tmp_path / "nope.fid"])
    assert any("not found" in r for r in res.reports)


def test_dc_ratio_time_metric_unit() -> None:
    """Time domain DC indicator: constant offset ≈ offset/peak; clean attenuation FID is very
    small."""
    from workflow.direct_diagnostics import _dc_ratio_time

    rng = np.random.default_rng(3)
    t = np.arange(1024, dtype=float)
    sig = np.exp(-t / 200.0) * np.exp(2j * np.pi * 0.1 * t)
    base = np.tile(sig, (20, 1)) * rng.uniform(0.5, 2.0, (20, 1))
    base += 0.02 * (rng.normal(size=(20, 1024)) + 1j * rng.normal(size=(20, 1024)))
    r_clean = _dc_ratio_time(base)
    r_dc = _dc_ratio_time(base + 1.0)
    assert r_clean < 0.20
    assert r_dc > 0.30


def test_badpoint_repaired_with_backup(
    template: Path, tmp_path: Path, exp_fixture
) -> None:
    """Orphaned spikes: automatic replacement, write back to disk, backup directory generation."""
    fid = _stage(template, tmp_path, synthetic=True, spike=(-1, 128, 40.0))
    before = _read_fid_raw(fid)
    assert before is not None
    data_before, _, _, _ = before
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert res.repaired_badpoints >= 1
    assert any("bad point" in r and "replaced automatically" in r for r in res.reports)
    backup = tmp_path / "fid_diag_bak"
    assert backup.is_dir()
    assert (backup / "nus_2d.fid").is_file()
    after = _read_fid_raw(fid)
    assert after is not None
    data_after, _, _, _ = after
    row = int(np.argmax(np.sum(np.abs(data_before) ** 2, axis=1)))
    orig_val = data_before[row, 128]
    expect = 0.5 * (data_after[row, 127] + data_after[row, 129])
    assert abs(data_after[row, 128] - expect) < 1.0
    # Backup still retains spikes.
    orig = _read_fid_raw(backup / "nus_2d.fid")
    assert orig is not None
    assert np.isclose(np.asarray(orig[0])[row, 128], orig_val)


def test_nan_inf_reported_not_fixed(
    template: Path, tmp_path: Path, exp_fixture
) -> None:
    """0.2.196:NaN/Inf values are only reported and not automatically processed."""
    fid = _stage(template, tmp_path, synthetic=True, nan_point=(-1, 64))
    got = _read_fid_raw(fid)
    assert got is not None  # NaN No longer causes layout parsing to fail.
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert any("NaN/Inf" in r and "left untouched" in r for r in res.reports)
    assert res.metrics.get("nan_inf_count", 0) >= 1


def test_uniform_zero_trace_reported(
    template: Path, tmp_path: Path, bruker_dir
) -> None:
    """0.2.196: Uniformly sampled all-zero trace is only reported and not processed
    automatically."""
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "hsqc_2d")
    fid = _stage(template, tmp_path, synthetic=True, zero_rows=[0])
    fid.rename(tmp_path / f"{exp.dataset_id}.fid")
    res = run_direct_diagnostics(tmp_path, exp)
    assert any("all-zero trace" in r and "left untouched" in r for r in res.reports)
    assert res.metrics.get("zero_traces", 0) >= 1


def test_high_energy_reported_not_fixed(
    template: Path, tmp_path: Path, exp_fixture
) -> None:
    """0.2.196: Sustained abnormally high energy trace is only reported and not processed
    automatically."""
    _stage(template, tmp_path, synthetic=True, high_energy_row=1)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert any(
        "abnormally high energy" in r and "left untouched" in r for r in res.reports
    )
    assert res.metrics.get("high_energy_traces", 0) >= 1


def test_repair_false_leaves_data(
    template: Path, tmp_path: Path, exp_fixture
) -> None:
    _stage(template, tmp_path, synthetic=True, spike=(-1, 200, 40.0))
    res = run_direct_diagnostics(tmp_path, exp_fixture, repair=False)
    assert res.repaired_badpoints == 0
    assert not (tmp_path / "fid_diag_bak").exists()


def test_no_fid_skips_gracefully(tmp_path: Path, exp_fixture) -> None:
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert res.reports
    assert "skipped" in res.reports[0]


def test_render_poly_time_inserted_before_sp(bruker_dir: Path) -> None:
    """When direct_poly_time=True, step1 inserts POLY -time before SP; it is not inserted by
    default."""
    from backend.script_generator import (
        generate_2d_nus_script,
        generate_3d_nus_script,
        generate_process_script,
    )
    from core.data.bruker_reader import read_dataset
    from core.planning.method_selector import select_method

    exp2 = read_dataset(bruker_dir / "nus_2d")
    exp3 = read_dataset(bruker_dir / "nus_3d")
    b2 = dict(in_file="e.fid", nuslist="nuslist", out_file="e.ft2", nuslist_count=5)
    b3 = dict(in_file="e.fid", nuslist="nuslist", out_file="e.ft3", nuslist_count=4)
    assert "POLY -time" not in generate_2d_nus_script(exp2, **b2)
    assert "POLY -time" not in generate_3d_nus_script(exp3, **b3)
    s = generate_2d_nus_script(exp2, direct_poly_time=True, **b2)
    assert s.index("| nmrPipe -fn POLY -time") < s.index("| nmrPipe -fn SP")
    s = generate_3d_nus_script(exp3, direct_poly_time=True, **b3)
    assert s.index("| nmrPipe -fn POLY -time") < s.index("| nmrPipe -fn SP")
    # 0.2.165:uniform 2D/3D When running the complete script, insert POLY -time before direct
    # dimension SP (aligned with NUS step1); default is not inserted (first pass preview, 0.2.160
    # design).
    u2 = dict(in_file="e.fid", out_file="e.ft2")
    u3 = dict(in_file="e.fid", out_file="e.ft3")
    assert "POLY -time" not in generate_process_script(
        exp2, select_method(exp2), **u2
    )
    assert "POLY -time" not in generate_process_script(
        exp3, select_method(exp3), **u3
    )
    s2 = generate_process_script(
        exp2, select_method(exp2), direct_poly_time=True, **u2
    )
    assert s2.index("| nmrPipe -fn POLY -time") < s2.index("| nmrPipe -fn SP")
    s3 = generate_process_script(
        exp3, select_method(exp3), direct_poly_time=True, **u3
    )
    assert s3.index("| nmrPipe -fn POLY -time") < s3.index("| nmrPipe -fn SP")
