"""Direct-dimension diagnostics: DC offset -> POLY -time, bad-point repair, render insertion.

The fid template comes from ``nmrpipe_fid_template`` in ``tests/conftest.py``: a 2048-byte
header + a real and an imaginary block per trace, matching real conversion output. This file
used to take a developer-machine absolute path as its template, so the whole file was skipped
on VM/CI (whether it ran depended on that path existing); the fixture is now generated with the
tests, so these diagnostic cases really run on every machine.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from workflow.direct_diagnostics import (
    DirectDiagnosticsResult,
    _read_fid_raw,
    run_direct_diagnostics,
)

ROOT_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "bruker"


@pytest.fixture
def template(nmrpipe_fid_template: Path) -> Path:
    """Diagnostic fid template (the shared fixture, layout matches real conversion output)."""
    return nmrpipe_fid_template


@pytest.fixture
def exp_fixture(bruker_dir: Path):
    from core.data.bruker_reader import read_dataset

    return read_dataset(bruker_dir / "nus_2d")


def _synth_like(template: Path) -> np.ndarray:
    """Synthetic complex fid at template size (exponential decay + noise, no DC or bad points)."""
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
    """Write a complex array back into the fid data region (header kept; re + im blocks)."""
    got = _read_fid_raw(dst)
    assert got is not None
    _data, fdsize, specnum, header = got
    assert array.shape == (specnum, fdsize), (array.shape, specnum, fdsize)
    flat = (
        np.frombuffer(dst.read_bytes(), dtype="<f4", count=specnum * fdsize * 2, offset=header)
        .astype(np.float32)
        .reshape(specnum, fdsize * 2)
    )
    flat[:, :fdsize] = array.real.reshape(specnum, fdsize)
    flat[:, fdsize:] = array.imag.reshape(specnum, fdsize)
    dst.write_bytes(dst.read_bytes()[:header] + flat.tobytes())


def _signal(template: Path, *, decay: float = 150.0, freq: float = 0.12) -> np.ndarray:
    """Clean single-frequency complex decay at template size (no noise, for edge cases)."""
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
    """Copy a fid from the shared template; optionally replace the array or inject a defect."""
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
    """The template is the real layout: 2048-byte header + re/im blocks, parser accepts it."""
    fid = _stage(template, tmp_path)
    got = _read_fid_raw(fid)
    assert got is not None
    data, fdsize, specnum, header = got
    assert fdsize == 1024
    assert specnum > 100
    assert header in (512, 1024, 2048)
    assert np.isfinite(data).all()


def test_dc_offset_enables_poly_time(template: Path, tmp_path: Path, exp_fixture) -> None:
    """Clear DC offset: enable POLY -time and report it (0.2.199-patch29cw, threshold 0.25)."""
    _stage(template, tmp_path, synthetic=True, dc_amp=1.0)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert isinstance(res, DirectDiagnosticsResult)
    assert res.apply_poly_time is True
    assert any("直流偏置" in r and "POLY -time" in r for r in res.reports)
    assert res.metrics["dc_ratio"] > 0.0


def test_dc_small_stays_off(template: Path, tmp_path: Path, exp_fixture) -> None:
    """DC-removed data: POLY -time stays off."""
    _stage(template, tmp_path, synthetic=True)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert res.apply_poly_time is False


def test_dc_small_offset_stays_off(template: Path, tmp_path: Path, exp_fixture) -> None:
    """Small FID mean (routine spectra) does not trigger POLY -time (0.2.199-patch29cw)."""
    _stage(template, tmp_path, synthetic=True, dc_amp=0.08)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert res.apply_poly_time is False


def test_first_point_ratio_reported(template: Path, tmp_path: Path, exp_fixture) -> None:
    """First point far above the second (group-delay issue): report and suggest checking acqus."""
    arr = _synth_like(template)
    arr[:, 0] *= 20.0
    _stage(template, tmp_path, array=arr)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert res.metrics["first_point_ratio"] > 1.6
    assert any("首点" in r for r in res.reports)


def test_broad_solvent_peak_reported(template: Path, tmp_path: Path, exp_fixture) -> None:
    """Broad hump (FWHM > 8% of sweep width): report solvent/exchange, suggest checks."""
    arr = _signal(template, decay=3.0, freq=0.05)
    _stage(template, tmp_path, array=arr)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert bool(res.metrics["broad_peak"]) is True  # metrics are all coerced to float
    assert res.metrics["fwhm_pts"] > 0.08 * res.metrics["n_direct"]
    assert any("宽带包峰" in r for r in res.reports)


def test_drift_reported(template: Path, tmp_path: Path, exp_fixture) -> None:
    """Drift over one linewidth between sampling periods: report and suggest re-acquiring."""
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
    line = next(r for r in res.reports if "场漂筛查" in r and "重新采谱" in r)

    expected = res.metrics["drift_pts"] / res.metrics["fwhm_pts"]
    assert f"{expected:.1f} 个线宽" in line
    assert f"{res.metrics['drift_pts'] * res.metrics['fwhm_pts']:.1f} 个线宽" not in line

    assert expected >= 10.0
    assert "两段谱峰基本不重叠" in line


def test_drift_screening_uses_trace_order_not_signal_strength() -> None:
    """Regression coverage: test drift screening uses trace order not signal strength."""
    from workflow.direct_diagnostics import _trace_metrics

    rows, points = 20, 256
    t = np.arange(points, dtype=float)
    traces = np.zeros((rows, points), dtype=np.complex64)
    for row in range(rows):
        frequency = 0.10 if row % 2 == 0 else 0.25
        amplitude = 4.0 if row % 2 == 0 else 1.0
        traces[row] = amplitude * np.exp(-t / 80.0) * np.exp(2j * np.pi * frequency * t)

    metrics = _trace_metrics(traces, points)
    assert metrics["drift_pts"] == pytest.approx(0.0)


def test_small_drift_is_not_reported(template: Path, tmp_path: Path, exp_fixture) -> None:
    """Regression coverage: test small drift is not reported."""
    from workflow.direct_diagnostics import DRIFT_LW_NEGLIGIBLE

    _stage(template, tmp_path, array=_signal(template))
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    multiple = res.metrics["drift_pts"] / res.metrics["fwhm_pts"]
    assert multiple < DRIFT_LW_NEGLIGIBLE
    assert res.clean is True

    assert not any("个线宽" in r for r in res.reports)


@pytest.mark.parametrize(
    ("drift_pts", "fwhm_pts", "tier"),
    [
        (3.0, 10.0, "negligible"),
        (18.0, 10.0, "moderate"),
        (59.0, 32.8, "moderate"),
        (35.0, 10.0, "large"),
        (150.0, 10.0, "severe"),
    ],
)
def test_drift_report_wording_is_tiered_by_linewidths(
    drift_pts: float, fwhm_pts: float, tier: str
) -> None:
    """Regression coverage: test drift report wording is tiered by linewidths."""
    from workflow.direct_diagnostics import (
        DRIFT_LW_LARGE,
        DRIFT_LW_NEGLIGIBLE,
        DRIFT_LW_SEVERE,
        format_drift_report_line,
    )

    line = format_drift_report_line(drift_pts, fwhm_pts)
    multiple = drift_pts / fwhm_pts

    assert f"{drift_pts:.1f} 点（{multiple:.1f} 个线宽）" in line
    assert "场漂筛查" in line and "表观峰位变化" in line
    assert f"{drift_pts * fwhm_pts:.1f} 个线宽" not in line
    if tier == "negligible":
        assert multiple < DRIFT_LW_NEGLIGIBLE
        assert "正常波动" in line and "无需处理" in line
        assert "建议" not in line
    elif tier == "moderate":
        assert DRIFT_LW_NEGLIGIBLE <= multiple < DRIFT_LW_LARGE
        assert "可接受但需留意" in line and "无需重新采谱" in line
        assert "建议" not in line
    elif tier == "large":
        assert DRIFT_LW_LARGE <= multiple < DRIFT_LW_SEVERE
        assert "数据处理无法完全消除" in line
        assert "建议" in line and "考虑重新采谱" in line
    else:
        assert multiple >= DRIFT_LW_SEVERE
        assert "两段谱峰基本不重叠" in line
        assert "建议" in line and "重新采谱" in line


def test_small_drift_line_has_no_reacquisition_advice() -> None:
    """Regression coverage: test small drift line has no reacquisition advice."""
    from workflow.direct_diagnostics import format_drift_report_line

    line = format_drift_report_line(59.0, 32.8)
    assert "59.0 点（1.8 个线宽）" in line

    assert "无需重新采谱" in line
    assert "考虑重新采谱" not in line
    assert "建议" not in line
    assert "温控" not in line and "锁场" not in line


def test_segmented_acceptable_drift_mentions_optional_inter_part_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import workflow.direct_diagnostics as diagnostics

    monkeypatch.setattr(
        diagnostics,
        "tr",
        lambda text, **values: text.format(**values),
    )
    single = diagnostics.format_drift_report_line(59.0, 32.8)
    segmented = diagnostics.format_drift_report_line(59.0, 32.8, segmented=True)
    assert "inter-part field-drift correction may be attempted" not in single
    assert "inter-part field-drift correction may be attempted" in segmented


def test_fid_report_passes_segment_count_into_drift_wording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import workflow.direct_diagnostics as diagnostics_module

    monkeypatch.setattr(
        diagnostics_module,
        "tr",
        lambda text, **values: text.format(**values),
    )
    diagnostics = diagnostics_module.DirectDiagnosticsResult(
        reports=["drift 1.8 linewidths"],
        metrics={"drift_pts": 59.0, "fwhm_pts": 32.8},
        clean=False,
    )
    single = "\n".join(diagnostics_module.format_fid_step_report(diagnostics=diagnostics, parts=1))
    segmented = "\n".join(
        diagnostics_module.format_fid_step_report(diagnostics=diagnostics, parts=2)
    )
    hint = "inter-part field-drift correction may be attempted"
    assert hint not in single
    assert hint in segmented


def test_fid_report_wording_matches_a_single_dataset() -> None:
    """A single-dataset (no merge) report must not mention "merge" or "inter-part drift" (user,
    2026-09-24).

    Real d_019 (12 drift points) is single-part, yet the report said "◆ conversion and merge
    stage" + "✓ source sampling points and inter-part drift: nothing to correct" -- that step
    neither merges nor has inter-part drift.
    """
    from workflow.direct_diagnostics import DirectDiagnosticsResult, format_fid_step_report

    diagnostics = DirectDiagnosticsResult(reports=[], clean=True)
    single = "\n".join(format_fid_step_report(diagnostics=diagnostics, parts=1))
    multi = "\n".join(format_fid_step_report(diagnostics=diagnostics, parts=3))

    def _headings(text: str) -> list[str]:
        return [line.strip() for line in text.splitlines() if line.strip().startswith(("==", "◆"))]

    assert _headings(single) == _headings(multi)
    assert "◆ 转换与合并阶段" in single
    assert "◆ FID 层检查" in single

    lowered = single.lower()
    assert "单数据集" not in single and "single dataset" not in lowered
    assert "组间场漂" not in single and "inter-part field drift" not in lowered
    assert "没有合并" not in single

    assert [ln for ln in single.splitlines() if "✓" in ln], single

    assert "没有检查记录" in multi
    assert "✓ 源头采样点与组间场漂:无需修正" not in multi

    unknown = "\n".join(format_fid_step_report(diagnostics=diagnostics))
    assert "◆ 转换与合并阶段" in unknown


def test_fid_report_recomputes_stale_linewidth_numbers() -> None:
    """Stale linewidth multiples in old records are recomputed in place (d_019: 12/10 = 1.2)."""
    from workflow.direct_diagnostics import DirectDiagnosticsResult, format_fid_step_report

    diagnostics = DirectDiagnosticsResult(
        reports=[
            "采样前/后周期直接维频率漂移约 12.0 点(120.0 个线宽量级),数据处理无法完全消除,"
            "建议检查温控/锁场并考虑重新采谱"
        ],
        metrics={"drift_pts": 12.0, "fwhm_pts": 10.0},
        clean=False,
    )
    text = "\n".join(format_fid_step_report(diagnostics=diagnostics, parts=1))
    assert "12.0 点（1.2 个线宽）" in text
    assert "场漂筛查" in text
    assert "120.0 个线宽" not in text

    assert "可接受但需留意" in text and "无需重新采谱" in text
    assert "考虑重新采谱" not in text
    assert "温控" not in text

    fresh = DirectDiagnosticsResult(
        reports=[
            "采样前/后周期直接维频率漂移约 12.0 点(1.2 个线宽量级),数据处理无法完全消除,"
            "建议检查温控/锁场并考虑重新采谱"
        ],
        metrics={"drift_pts": 12.0, "fwhm_pts": 10.0},
        clean=False,
    )
    again = "\n".join(format_fid_step_report(diagnostics=fresh, parts=1))
    assert "12.0 点（1.2 个线宽）" in again


def test_fid_step_quality_report_is_part_count_aware(tmp_path: Path) -> None:
    """The GUI read-only entry point must also follow the part count (single dataset ->
    conversion).

    2026-09-24 pitfall: only the sample call of `format_fid_step_report` was fixed, not the real
    call inside `fid_step_quality_report` -> the log was right while the GUI step report still
    said "conversion and merge stage".
    """
    import json as _json

    from workflow.direct_diagnostics import fid_step_quality_report

    def _stage(work, *, multi: bool):
        work.mkdir(parents=True, exist_ok=True)
        (work / "diagnostics.json").write_text(
            _json.dumps(
                {
                    "reports": [],
                    "metrics": {},
                    "notes": [],
                    "clean": True,
                    "auto_handled": 0,
                    "repaired_badpoints": 0,
                    "apply_poly_time": False,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        if multi:
            (work / "seg_001").mkdir(exist_ok=True)
            (work / "seg_002").mkdir(exist_ok=True)
            (work / "field_drift.json").write_text("{}", encoding="utf-8")
        return work

    raw = tmp_path / "raw"
    raw.mkdir()
    single = "\n".join(fid_step_quality_report(_stage(tmp_path / "one", multi=False), [raw]))
    multi = "\n".join(fid_step_quality_report(_stage(tmp_path / "many", multi=True), [raw]))

    def _headings(text: str) -> list[str]:
        return [line.strip() for line in text.splitlines() if line.strip().startswith(("==", "◆"))]

    assert (
        _headings(single)
        == _headings(multi)
        == [
            "== 数据质量报告(FID 生成) ==",
            "◆ 转换与合并阶段",
            "◆ FID 层检查(转换后直接维内存扫描)",
        ]
    )

    lowered = single.lower()
    assert "单数据集" not in single and "single dataset" not in lowered
    assert "组间场漂" not in single and "inter-part field drift" not in lowered

    checked = [line for line in single.splitlines() if "✓" in line]
    assert checked, single


def test_detect_part_count(tmp_path: Path) -> None:
    """Part count: multi dir/drift record -> multi; seg_001 only -> single; unknown -> None."""
    from workflow.direct_diagnostics import detect_part_count

    single = tmp_path / "single"
    (single / "seg_001").mkdir(parents=True)
    assert detect_part_count(single) == 1

    multi = tmp_path / "multi"
    (multi / "seg_001").mkdir(parents=True)
    (multi / "seg_002").mkdir()
    assert detect_part_count(multi) == 2

    drift = tmp_path / "drift"
    drift.mkdir()
    (drift / "field_drift.json").write_text("{}", encoding="utf-8")
    assert detect_part_count(drift) == 2

    # Empty dir + in-project raw/segments (5 parts) -> 5
    raw = tmp_path / "raw"
    for name in ("01", "02", "03", "04", "05"):
        d = raw / "segments" / name
        d.mkdir(parents=True)
        (d / "acqus").write_text("##TITLE= t\n", encoding="utf-8")
    assert detect_part_count(tmp_path / "empty", [raw]) == 5

    # Single-dataset raw dir (no segments subdir) -> 1
    plain = tmp_path / "plain_raw"
    plain.mkdir()
    assert detect_part_count(tmp_path / "empty", [plain]) == 1
    assert detect_part_count(tmp_path / "empty") is None


def test_run_fid_diagnostics_paths_file(template: Path, tmp_path: Path) -> None:
    """Standalone entry point: pass fid file paths directly to diagnose them
    (detect only by default, no repair, no backup).
    """
    from workflow.direct_diagnostics import run_fid_diagnostics_paths

    fid = _stage(template, tmp_path, synthetic=True)
    res = run_fid_diagnostics_paths([fid])
    assert isinstance(res, DirectDiagnosticsResult)
    assert res.reports
    assert res.apply_poly_time is False
    assert not (tmp_path / "fid_diag_bak").exists()


def test_run_fid_diagnostics_paths_folder(template: Path, tmp_path: Path) -> None:
    """Standalone entry point: a folder auto-collects *.fid / test*.fid."""
    from workflow.direct_diagnostics import run_fid_diagnostics_paths

    _stage(template, tmp_path, synthetic=True)
    res = run_fid_diagnostics_paths([tmp_path])
    assert isinstance(res, DirectDiagnosticsResult)
    assert res.reports


def test_standalone_slice_group_uses_all_slices_and_is_read_only(
    template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from workflow.direct_diagnostics import run_fid_diagnostics_paths

    slices = tmp_path / "fid"
    slices.mkdir()
    first = _stage(template, slices, synthetic=True)
    first = first.rename(slices / "test2.fid")
    shutil.copy2(first, slices / "test10.fid")
    shutil.copy2(first, slices / "test1.fid")
    before = {p.name: p.read_bytes() for p in slices.iterdir()}
    record = slices / "diagnostics.json"
    record.write_text("original pipeline record", encoding="utf-8")
    parsed = []
    real_read = _read_fid_raw
    from workflow.direct_diagnostics import _trace_metrics

    shapes = []

    def metrics(traces, n):
        shapes.append(traces.shape)
        return _trace_metrics(traces, n)

    def read(path):
        parsed.append(path.name)
        return real_read(path)

    monkeypatch.setattr("workflow.direct_diagnostics._read_fid_raw", read)
    monkeypatch.setattr("workflow.direct_diagnostics._trace_metrics", metrics)
    result = run_fid_diagnostics_paths([slices, first])
    assert parsed == ["test1.fid", "test2.fid", "test10.fid"]
    assert result.ran
    rows, width = real_read(first)[0].shape
    assert shapes == [(3 * rows, width)]
    assert result.auto_handled == 0
    assert {p.name: p.read_bytes() for p in slices.glob("*.fid")} == before
    assert record.read_text(encoding="utf-8") == "original pipeline record"
    assert not (slices / "fid_diag_bak").exists()


def test_standalone_slice_group_rejects_mixed_direct_lengths(tmp_path, monkeypatch) -> None:
    from workflow.direct_diagnostics import run_fid_diagnostics_paths

    paths = [tmp_path / "test1.fid", tmp_path / "test2.fid"]
    for path in paths:
        path.touch()

    monkeypatch.setattr(
        "workflow.direct_diagnostics._read_fid_raw",
        lambda p: (np.zeros((2, 8 if p == paths[0] else 16), dtype=np.complex64), 0, 0, 0),
    )
    result = run_fid_diagnostics_paths([tmp_path])
    assert not result.ran
    assert "test2.fid" in "\n".join(result.reports)
    assert not (tmp_path / "diagnostics.json").exists()


@pytest.mark.parametrize("folder", [False, True])
def test_standalone_reports_spikes_without_repair(template, tmp_path, folder):
    from workflow.direct_diagnostics import run_fid_diagnostics_paths

    fid = _stage(template, tmp_path, synthetic=True, spike=(-1, 128, 40.0))
    before = fid.read_bytes()
    result = run_fid_diagnostics_paths([tmp_path if folder else fid])
    assert result.metrics["detected_badpoints"] >= 1
    assert not result.clean
    assert result.repaired_badpoints == result.auto_handled == 0
    assert fid.read_bytes() == before
    assert not (tmp_path / "qc_audit.jsonl").exists()
    assert not (tmp_path / "diagnostics.json").exists()
    assert not (tmp_path / "fid_diag_bak").exists()


def test_run_fid_diagnostics_paths_missing(tmp_path: Path) -> None:
    """Standalone entry point: a missing path reports "fid not found" instead of crashing."""
    from workflow.direct_diagnostics import run_fid_diagnostics_paths

    res = run_fid_diagnostics_paths([tmp_path / "nope.fid"])
    assert any("未找到" in r for r in res.reports)


def test_dc_ratio_time_metric_unit() -> None:
    """Time-domain DC metric: constant offset ~ offset/peak; tiny for a clean decaying FID."""
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


def test_badpoint_repaired_with_backup(template: Path, tmp_path: Path, exp_fixture) -> None:
    """Isolated spike: auto-replaced, written back to disk, backup directory created."""
    fid = _stage(template, tmp_path, synthetic=True, spike=(-1, 128, 40.0))
    before = _read_fid_raw(fid)
    assert before is not None
    data_before, _, _, _ = before
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert res.repaired_badpoints >= 1
    assert any("坏点" in r and "自动替换" in r for r in res.reports)
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
    # The backup still keeps the spike
    orig = _read_fid_raw(backup / "nus_2d.fid")
    assert orig is not None
    assert np.isclose(np.asarray(orig[0])[row, 128], orig_val)


def test_nan_inf_reported_not_fixed(template: Path, tmp_path: Path, exp_fixture) -> None:
    """0.2.196: NaN/Inf values are reported, not auto-fixed."""
    fid = _stage(template, tmp_path, synthetic=True, nan_point=(-1, 64))
    got = _read_fid_raw(fid)
    assert got is not None  # NaN no longer breaks layout parsing
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert any("NaN/Inf" in r and "未自动处理" in r for r in res.reports)
    assert res.metrics.get("nan_inf_count", 0) >= 1


def test_uniform_zero_trace_reported(template: Path, tmp_path: Path, bruker_dir) -> None:
    """0.2.196: uniformly sampled all-zero traces are reported, not auto-fixed."""
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "hsqc_2d")
    fid = _stage(template, tmp_path, synthetic=True, zero_rows=[0])
    fid.rename(tmp_path / f"{exp.dataset_id}.fid")
    res = run_direct_diagnostics(tmp_path, exp)
    assert any("全零迹线" in r and "未自动处理" in r for r in res.reports)
    assert res.metrics.get("zero_traces", 0) >= 1


def test_high_energy_reported_not_fixed(template: Path, tmp_path: Path, exp_fixture) -> None:
    """0.2.196: persistently abnormal high-energy traces are reported, not auto-fixed."""
    _stage(template, tmp_path, synthetic=True, high_energy_row=1)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert any("能量异常偏高" in r and "未自动处理" in r for r in res.reports)
    assert res.metrics.get("high_energy_traces", 0) >= 1


def test_repair_false_leaves_data(template: Path, tmp_path: Path, exp_fixture) -> None:
    _stage(template, tmp_path, synthetic=True, spike=(-1, 200, 40.0))
    res = run_direct_diagnostics(tmp_path, exp_fixture, repair=False)
    assert res.repaired_badpoints == 0
    assert not (tmp_path / "fid_diag_bak").exists()


def test_no_fid_skips_gracefully(tmp_path: Path, exp_fixture) -> None:
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert res.reports
    assert "跳过" in res.reports[0]


def test_render_poly_time_inserted_before_sp(bruker_dir: Path) -> None:
    """With direct_poly_time=True, step1 inserts POLY -time before SP; off by default."""
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
    # 0.2.165: the full uniform 2D/3D production script also inserts POLY -time before the
    # direct-dimension SP (aligned with NUS step1); off by default (0.2.160 preview design).
    u2 = dict(in_file="e.fid", out_file="e.ft2")
    u3 = dict(in_file="e.fid", out_file="e.ft3")
    assert "POLY -time" not in generate_process_script(exp2, select_method(exp2), **u2)
    assert "POLY -time" not in generate_process_script(exp3, select_method(exp3), **u3)
    s2 = generate_process_script(exp2, select_method(exp2), direct_poly_time=True, **u2)
    assert s2.index("| nmrPipe -fn POLY -time") < s2.index("| nmrPipe -fn SP")
    s3 = generate_process_script(exp3, select_method(exp3), direct_poly_time=True, **u3)
    assert s3.index("| nmrPipe -fn POLY -time") < s3.index("| nmrPipe -fn SP")


# ---------------------------------------------------------------------------
# 2026-09-23 (user request): diagnostics moved to the end of "generate FID"
#   1) the merged multi-part single file (merged/{dataset_id}.fid) must be found, otherwise the
#      whole diagnostics block is silently skipped;
#   2) "generate spectrum" reads back the diagnostics.json from the FID step and reuses it
#      (re-run only when the artifacts changed);
#   3) the report states item by item what was found / how it was handled, and says ✓ when
#      nothing was found.
# ---------------------------------------------------------------------------


def _path_experiment(dataset_id: str, segments: list[str] | None = None):
    """Minimal stand-in object: locating a fid only needs dataset_id / segments."""
    from types import SimpleNamespace

    return SimpleNamespace(dataset_id=dataset_id, segments=list(segments or []))


def test_collect_fid_paths_finds_the_merged_single_file(tmp_path: Path, template: Path) -> None:
    """The merged single file lives at merged/{dataset_id}.fid and must not be missed."""
    from workflow.direct_diagnostics import collect_fid_paths

    work = tmp_path / "process"
    (work / "merged").mkdir(parents=True)
    merged = work / "merged" / "d_017.fid"
    merged.write_bytes(template.read_bytes())

    found = collect_fid_paths(work, _path_experiment("d_017", ["seg_001", "seg_002"]))
    assert found == [merged]
    # merged/*.fid fallback (when the merged name differs from dataset_id)
    other = work / "merged" / "anything.fid"
    other.write_bytes(template.read_bytes())
    merged.unlink()
    assert collect_fid_paths(work, _path_experiment("d_017", ["seg_001"])) == [other]
    # Slice streams win: while merged/fid/test*.fid exists, still take the slices
    (work / "merged" / "fid").mkdir()
    (work / "merged" / "fid" / "test001.fid").write_bytes(template.read_bytes())
    sliced = collect_fid_paths(work, _path_experiment("d_017", ["seg_001"]))
    assert [item.name for item in sliced] == ["test001.fid"]
    # 2026-09-24: with segment markers missing (old work dirs), the merged/fid slices must be
    # found too -- the manual path saying "fid not found" missed exactly this kind of location
    assert collect_fid_paths(work, _path_experiment("d_017")) == sliced


def test_load_or_run_reuses_the_fid_step_record(
    tmp_path: Path, template: Path, exp_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generate-spectrum reuses the FID-step record; re-runs only if the fid changed."""
    from workflow import direct_diagnostics as diag

    work = tmp_path / "process"
    work.mkdir()
    fid = work / f"{exp_fixture.dataset_id}.fid"
    fid.write_bytes(template.read_bytes())

    first = diag.run_direct_diagnostics(work, exp_fixture)
    assert (work / "diagnostics.json").is_file()

    calls: list[tuple] = []
    monkeypatch.setattr(
        diag,
        "run_direct_diagnostics",
        lambda *args, **kwargs: calls.append(args) or first,
    )

    again = diag.load_or_run_direct_diagnostics(work, exp_fixture)
    assert calls == []  # record hit -> no re-run
    assert again.reports == first.reports
    assert again.clean == first.clean
    assert again.apply_poly_time == first.apply_poly_time

    # fid changed (size/timestamp) -> the record is stale, fall back to a re-run
    fid.write_bytes(template.read_bytes() + b"\x00" * 8)
    diag.load_or_run_direct_diagnostics(work, exp_fixture)
    assert len(calls) == 1


def test_format_fid_step_report_lists_every_fix_and_fid_finding() -> None:
    """Report findings and auto-handling (source bad points, drift, FID-layer findings)."""
    from workflow.direct_diagnostics import (
        DirectDiagnosticsResult,
        format_fid_step_report,
    )

    diagnostics = DirectDiagnosticsResult(
        reports=[
            "直接维存在直流偏置(FID 均值约为最强幅度的 49%),已启用 POLY -time",
            "检出 3 个尖峰坏点并已自动替换(原 fid 备份在 fid_diag_bak/)",
        ],
        apply_poly_time=True,
        repaired_badpoints=3,
        clean=False,
    )
    drift = {
        "checked": True,
        "reference": 1,
        "hz_min": 1.5,
        "ppm_reference": 0.005,
        "rounds": [
            {
                "offsets_hz": [None, 2.1446, 2.8776],
                "offsets_ppm": [None, 0.00268, 0.0036],
                "shifted_parts_hz": {"2": 2.1446, "3": 2.8776},
            },
            {"offsets_hz": [None, 0.0, 0.0], "offsets_ppm": [None, 0.0, 0.0]},
        ],
        "corrected_parts": [2, 3],
        "within_threshold_after": True,
    }
    lines = format_fid_step_report(
        diagnostics=diagnostics,
        source_bad_points=[(27, 2350)],
        source_removed=True,
        field_drift=drift,
    )
    text = "\n".join(lines)
    assert text.startswith("== 数据质量报告(FID 生成) ==")
    assert "◆ 转换与合并阶段" in text
    # Source bad points: state what was removed and where the backup is
    assert "源头采样坏点 (27, 2350)" in text
    assert "已从 raw 的 ser + nuslist 删除" in text
    assert ".bak" in text
    # Field drift: which parts, how many Hz, what changed, and the re-check result
    assert "组间场漂" in text
    assert "+2.14 Hz" in text and "+2.88 Hz" in text
    assert "PS -rs" in text
    assert "最大残差 0.00 Hz" in text
    # FID layer: findings + auto-handled count + each item's original text
    assert "◆ FID 层检查(转换后直接维内存扫描)" in text
    assert "检出 2 项问题" in text
    assert "已自动处理 2 项" in text
    assert "POLY -time" in text
    assert "尖峰坏点" in text


def test_format_fid_step_report_states_an_all_clear() -> None:
    """With nothing found, say ✓; "nothing found" is not 1 issue in the count."""
    from workflow.direct_diagnostics import (
        DirectDiagnosticsResult,
        format_fid_step_report,
    )

    diagnostics = DirectDiagnosticsResult(
        reports=["数据质量诊断:未检出直流偏置、尖峰坏点、首点异常、宽带峰或漂移"],
        clean=True,
    )
    lines = format_fid_step_report(
        diagnostics=diagnostics,
        field_drift={
            "checked": True,
            "reference": 1,
            "hz_min": 1.5,
            "rounds": [{"offsets_hz": [None, 0.0], "offsets_ppm": [None, 0.0]}],
            "corrected_parts": [],
        },
    )
    text = "\n".join(lines)
    assert "✓ 未检出直流偏置" in text
    assert "判据以内" in text
    assert "项问题" not in text
    # Say so when diagnostics did not run (do not pretend "no problem")
    missing = "\n".join(format_fid_step_report(diagnostics=None))
    assert "本步未执行 FID 层检查" in missing


def test_fid_report_states_subpoint_drift_cannot_be_resolved() -> None:
    """d_018 wording: an offset below one FFT point says "cannot resolve" -- no ✓, and not
    "within criterion" either.

    At 66.9 Hz/point, +0.98 / +4.0 Hz is only 0.01-0.06 points; the old implementation wrote
    "max offset 4.00 Hz, within criterion". Now the real reason is stated and that part is not
    counted as "nothing to correct".
    """
    from workflow.direct_diagnostics import format_fid_step_report

    record = {
        "checked": True,
        "reference": 1,
        "parts": 5,
        "hz_min": 1.5,
        "points_min": 1.0,
        "rounds": [
            {
                "offsets_hz": [None, 0.98, 2.0, 83.9, 33.4],
                "offsets_ppm": [None, 0.0016, 0.0033, 0.1398, 0.0556],
                "below_resolution_parts": [2, 3],
                "point_hz": 66.9,
                "criterion_hz": 66.9,
                "quality": [None, 1.4, 1.4, 1.4, 1.4],
                "uncertainty_hz": [None, 113.2, 39.5, 151.0, 130.0],
                "skipped": [
                    "第 4 段:两段看起来不是同一次实验(相关峰是底线的 3.4 倍,逐迹分歧 33.4 Hz)"
                ],
            }
        ],
        "corrected_parts": [],
        "merge": {"parts": 5, "corrected_parts": [], "uncorrected_parts": []},
        "segment_consistency": {
            "available": 5,
            "warnings": [{"key": "NS", "parameter": "acqus.NS", "values": [32, 16, 32, 32, 16]}],
            "blocking": [],
        },
    }
    text = "\n".join(format_fid_step_report(field_drift=record))
    assert "无法分辨" in text
    assert "66.9 Hz/点" in text
    assert "已合并 5 段" in text
    assert "NS 不同" in text and "32/16/32/32/16" in text
    assert "✓ 源头采样点与组间场漂:无需修正" not in text
    # Old records' "not the same experiment" wording gets the measurement wording (user,
    # 2026-09-24: the sentence read as self-contradictory)
    assert "看起来不是同一次实验" not in text
    assert "第 4 段:逐迹信噪比不足,量不出可信漂移" in text
    assert "不代表各段不是同一次实验" in text


def test_fid_report_neutralizes_legacy_identity_claim() -> None:
    """The "the two parts look like different experiments" wording in old records (real d_018)
    must be replaced by the measurement wording.

    User, 2026-09-24: "the two parts look like different experiments, why is it still shown" --
    the same report's segment consistency says "only NS differs", so that sentence both
    contradicts the conclusion and turns "cannot measure" into "data from different sources".
    """
    from workflow.direct_diagnostics import format_fid_step_report

    record = {
        "checked": True,
        "reference": 1,
        "parts": 5,
        "hz_min": 1.5,
        "point_hz": 66.9,
        "rounds": [
            {
                "offsets_hz": [None, -19.606, -3.639, -23.611, -24.256],
                "offsets_ppm": [None, -0.0327, -0.0061, -0.0393, -0.0404],
                "quality": [None, 1.38, 1.43, 1.35, 1.54],
                "uncertainty_hz": [None, 113.25, 39.5, 151.0, 130.0],
                "below_resolution_parts": [],
                "trusted_parts": [],
                "point_hz": 66.9,
                "criterion_hz": 66.9,
                "skipped": [
                    "第 2 段:两段看起来不是同一次实验(相关峰是底线的 1.4 倍,逐迹分歧 113.2 Hz,"
                    "1.69 个 FFT 点);未做场漂校正",
                    "第 5 段:两段看起来不是同一次实验(相关峰是底线的 1.5 倍,逐迹分歧 130.0 Hz,"
                    "1.94 个 FFT 点);未做场漂校正",
                ],
            }
        ],
        "corrected_parts": [],
        "segment_consistency": {
            "available": 5,
            "warnings": [{"key": "NS", "parameter": "acqus.NS", "values": [32, 16, 32, 32, 16]}],
            "blocking": [],
        },
        "merge": {"parts": 5, "corrected_parts": [], "uncorrected_parts": []},
    }
    text = "\n".join(format_fid_step_report(field_drift=record))
    assert "看起来不是同一次实验" not in text
    assert "第 2 段:逐迹信噪比不足,量不出可信漂移" in text
    assert "113.2 Hz" in text and "1.69 个 FFT 点" in text  # numbers still come from the record
    assert "不代表各段不是同一次实验" in text
    assert "相对第 1 段没有任何一段给出可信测量" in text


def test_fid_report_never_calls_untrusted_offsets_within_criterion() -> None:
    """When every part is blocked by the confidence gate, the fid report must not say "max offset
    X Hz, within criterion" (real d_018).

    Field case (2026-09-24, d_018 re-converted in five parts): four parts were judged "not the
    same experiment", yet the old implementation wrote their noise-level estimates (max 24.26
    Hz)
    as "within the 1.5 Hz criterion, no shift applied".
    """
    from workflow.direct_diagnostics import format_fid_step_report

    record = {
        "checked": True,
        "reference": 1,
        "parts": 5,
        "hz_min": 1.5,
        "rounds": [
            {
                "offsets_hz": [None, -24.26, 39.5, 151.0, 130.0],
                "offsets_ppm": [None, -0.04, 0.066, 0.25, 0.22],
                "below_resolution_parts": [],
                "trusted_parts": [],
                "point_hz": 66.9,
                "criterion_hz": 66.9,
                "skipped": ["第 2 段:两段看起来不是同一次实验"],
            }
        ],
        "corrected_parts": [],
        "merge": {"parts": 5, "corrected_parts": [], "uncorrected_parts": []},
    }
    text = "\n".join(format_fid_step_report(field_drift=record))
    assert "判据以内" not in text
    assert "没有任何一段给出可信测量" in text
    assert "✓ 源头采样点与组间场漂:无需修正" not in text


def test_fid_report_does_not_call_a_rolled_back_offset_within_criterion() -> None:
    """On a failed correction or rollback the report must not say "within criterion", and must not
    give a ✓ (2026-09-24 review).

    Field case: rewriting fid.com failed -> all-or-nothing rollback -> `corrected_parts` empty,
    but the part in `trusted_parts` **really does exceed the criterion**. The old renderer only
    checked "is there a trusted measurement", so one report showed both "✓ nothing to correct"
    and "max offset 60.00 Hz ... within the 7.81 Hz criterion" -- self-contradictory.
    """
    from workflow.direct_diagnostics import format_fid_step_report

    record = {
        "checked": True,
        "reference": 1,
        "parts": 2,
        "rounds": [
            {
                "offsets_hz": [None, 60.0],
                "offsets_ppm": [None, 0.075],
                "trusted_parts": [2],
                "point_hz": 7.8125,
                "linewidth_hz": 39.06,
                "criterion_basis": "linewidth",
                "criterion_hz": 7.81,
                "quality": [None, 300.0],
                "uncertainty_hz": [None, 0.2],
                "skipped": [],
            }
        ],
        "corrected_parts": [],
        "rolled_back": True,
        "merge": {"parts": 2, "corrected_parts": [], "uncorrected_parts": [2]},
    }
    text = "\n".join(format_fid_step_report(field_drift=record, parts=2))
    assert "判据以内" not in text
    assert "✓ 源头采样点与组间场漂:无需修正" not in text
    assert "+60.00 Hz" in text and "没有做成" in text


def test_fid_report_adds_the_measurement_note_for_a_not_reproducible_part() -> None:
    """When some parts are "not reproducible", the report still needs the closing note that
    "cannot measure != different origin" (2026-09-24 review).

    That note is decided by `_measurement_note_needed`; it used to accept only the old wording
    ("cannot measure a trusted drift" / "too low"), so the new wording ("not reproducible" /
    "too few comparable traces") dropped the note in **mixed cases** (some parts trusted, some
    not) -- the log (written at run time) had it, the step report (GUI re-render) did not.
    """
    from workflow.direct_diagnostics import format_fid_step_report

    record = {
        "checked": True,
        "reference": 1,
        "parts": 3,
        "rounds": [
            {
                "offsets_hz": [None, 3.0, None],
                "offsets_ppm": [None, 0.0037, None],
                "trusted_parts": [2],
                "point_hz": 66.9,
                "linewidth_hz": 117.0,
                "criterion_basis": "linewidth",
                "criterion_hz": 23.41,
                "skipped": [
                    "第 3 段:随机半份重采样的散布 ±56.1 Hz,超过 23.41 Hz 判据 —— "
                    "这个信噪比下估计不可复现,未做校正"
                ],
            }
        ],
        "corrected_parts": [],
        "merge": {"parts": 3, "corrected_parts": [], "uncorrected_parts": []},
    }
    text = "\n".join(format_fid_step_report(field_drift=record))
    assert "判据以内" in text  # part 2 really is within criterion
    assert "不代表各段不是同一次实验" in text  # the wording note must be present


# ---------------------------------------------------------------------------
# 2026-09-23 (round 2, user request)
#   1) data whose bad points were already removed must still be reported (cleanup history);
#   2) wording fixed in review: the audit summary does not count toward "N issues found", drift
#      "not checked / not measurable" no longer says "within criterion", bool metrics are not
#      written as 1.0;
#   3) the GUI "generate FID" step report only reads records (no diagnostics re-run, no fid fix).
# ---------------------------------------------------------------------------


def _history_work(tmp_path: Path) -> tuple[Path, Path]:
    """Build a work dir with a past source-bad-point cleanup (qc_audit.jsonl + .bak)."""
    from core.audit.qc_audit import QcAction, QcAuditLog

    work = tmp_path / "process"
    work.mkdir(parents=True, exist_ok=True)
    raw = tmp_path / "raw"
    (raw / "segments" / "01").mkdir(parents=True, exist_ok=True)
    (raw / "ser.bak").write_bytes(b"x")
    (raw / "segments" / "01" / "nuslist.bak").write_text("1 2\n", encoding="utf-8")
    QcAuditLog(work).record(
        QcAction(
            issue_detected="bad point in the NUS sampling table",
            location="raw/ser + nuslist",
            detection_rule="_validate_nus_points",
            action_taken="removed_from_source_ser_and_nuslist",
            before_state={"sampling_points": 12, "bad_points": 2},
            after_state={"sampling_points": 10},
            extra={"bad_points_sample": [[27, 2350], [28, 2351]], "source_removed": True},
        )
    )
    return work, raw


def test_source_cleanup_history_is_reported_for_already_cleaned_data(tmp_path: Path) -> None:
    """No cleanup this step != never broken: cleanup history belongs in the report (user)."""
    from workflow.direct_diagnostics import (
        DirectDiagnosticsResult,
        format_fid_step_report,
        read_source_cleanup_history,
    )

    work, raw = _history_work(tmp_path)
    history = read_source_cleanup_history(work, [raw])
    assert history["events"] == 1 and history["points"] == 2
    assert history["sample"] == [[27, 2350], [28, 2351]]
    assert "raw/ser.bak" in history["backup_files"]
    assert "raw/segments/01/nuslist.bak" in history["backup_files"]

    text = "\n".join(
        format_fid_step_report(
            diagnostics=DirectDiagnosticsResult(
                reports=["数据质量诊断:未检出直流偏置、尖峰坏点、首点异常、宽带峰或漂移"],
                clean=True,
            ),
            source_history=history,
        )
    )
    assert "2 处已在更早的运行里从 raw 的 ser + nuslist 删除" in text
    assert "(27, 2350)" in text and "ser.bak" in text
    # Must not say "source sampling points ... nothing to correct" (the misleading line)
    assert "无需修正" not in text
    # Keep the current behavior when there is no history (do not invent history)
    assert read_source_cleanup_history(tmp_path / "nowhere", [tmp_path / "noraw"]) == {}


def test_source_cleanup_history_finds_nested_segment_backups_without_audit(
    tmp_path: Path,
) -> None:
    """Regression coverage: test source cleanup history finds nested segment backups without audit.

    """
    from workflow.direct_diagnostics import (
        fid_step_quality_report,
        read_source_cleanup_history,
    )

    work = tmp_path / "process"
    work.mkdir()
    raw = tmp_path / "raw"
    nested = raw / "segments" / "02"
    nested.mkdir(parents=True)
    (nested / "ser.bak").write_bytes(b"backup")

    history = read_source_cleanup_history(work, [raw])
    assert history["events"] == 0
    assert history["backup_files"] == ["raw/segments/02/ser.bak"]
    text = "\n".join(fid_step_quality_report(work, [raw]))
    assert "ser + nuslist 在更早的运行里已清理" in text
    assert "raw/segments/02/ser.bak" in text


def test_diagnostics_metrics_keep_boolean_flags(
    tmp_path: Path, template: Path, exp_fixture
) -> None:
    """Flags like broad_peak must persist as bool (isinstance(True, int) used to write 1.0)."""
    import json

    from workflow.direct_diagnostics import run_direct_diagnostics

    work = tmp_path / "process"
    work.mkdir()
    (work / f"{exp_fixture.dataset_id}.fid").write_bytes(template.read_bytes())
    run_direct_diagnostics(work, exp_fixture)
    data = json.loads((work / "diagnostics.json").read_text(encoding="utf-8"))
    assert isinstance(data["metrics"]["broad_peak"], bool)


def test_fid_step_report_counts_issues_not_notes() -> None:
    """Only reports count toward "N issues found"; notes such as the audit summary do not."""
    from workflow.direct_diagnostics import DirectDiagnosticsResult, format_fid_step_report

    result = DirectDiagnosticsResult(
        reports=["直接维存在直流偏置(...)", "检出 3 个尖峰坏点并已自动替换(...)"],
        notes=["QC 审计记录: 4 条(...)"],
        apply_poly_time=True,
        repaired_badpoints=3,
        auto_handled=2,
        clean=False,
    )
    text = "\n".join(format_fid_step_report(diagnostics=result))
    assert "检出 2 项问题" in text and "已自动处理 2 项" in text
    assert "· QC 审计记录" in text


def test_drift_report_never_claims_clean_when_not_measured() -> None:
    """When drift was not checked or nothing is measurable, state it and give no ✓."""
    from workflow.direct_diagnostics import DirectDiagnosticsResult, format_fid_step_report

    clean = DirectDiagnosticsResult(reports=["x"], clean=True)
    unchecked = "\n".join(
        format_fid_step_report(
            diagnostics=clean,
            field_drift={"checked": False, "reason": "direct-axis-unknown"},
        )
    )
    assert "未检查(直接维 SW/OBS 未知)" in unchecked
    assert "无需修正" not in unchecked
    unmeasurable = "\n".join(
        format_fid_step_report(
            diagnostics=clean,
            field_drift={
                "checked": True,
                "reference": 1,
                "hz_min": 1.5,
                "rounds": [{"offsets_hz": [None, None], "offsets_ppm": [None, None]}],
                "corrected_parts": [],
            },
        )
    )
    assert "一段都测不出偏移" in unmeasurable
    assert "判据以内" not in unmeasurable


def test_fid_step_quality_report_only_reads_records(
    tmp_path: Path, template: Path, exp_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GUI "generate FID" step report: read records only, no diagnostics re-run, no fid fix."""
    from workflow import direct_diagnostics as diag

    work = tmp_path / "process"
    work.mkdir()
    (work / f"{exp_fixture.dataset_id}.fid").write_bytes(template.read_bytes())
    diag.run_direct_diagnostics(work, exp_fixture)

    calls: list[tuple] = []
    monkeypatch.setattr(diag, "run_direct_diagnostics", lambda *a, **k: calls.append(a))
    lines = diag.fid_step_quality_report(work, [tmp_path / "raw"])
    assert calls == []
    assert lines[0] == "== 数据质量报告(FID 生成) =="
    assert any("FID 层检查" in line for line in lines)

    # State it when the record is missing; do not pretend "no problem"
    missing = diag.fid_step_quality_report(tmp_path / "empty", None)
    assert "本步未执行 FID 层检查" in "\n".join(missing)


#: Real-data (OR8C_600/BMRB) sweep-width note: both step tests use it so log and report match
_SWEEP_WIDTH_NOTE = (
    "F1: sweep width: SW_h=2000 Hz and SW=30 ppm x SFO1=60.8178 MHz = 1824.53 Hz "
    "differ by 9.6%; the ppm convention 1824.53 Hz is used (SW_h looks stale)"
)


def test_fid_report_lists_the_sweep_width_decision() -> None:
    """A revised sweep-width convention goes into the conversion-stage correction list, not into
    "nothing to correct".

    2026-09-24: the BMRB deposited data writes ``SW_h=2000 Hz`` in ``acqu2s`` (a constant
    carried
    over), while the ppm convention gives 1824.5 Hz; that step really changed ``-ySW`` in
    fid.com, so the report gives no ✓.
    """
    from workflow.direct_diagnostics import format_fid_step_report

    lines = format_fid_step_report(
        field_drift=None,
        sweep_width=[{"axis": "F1", "note": _SWEEP_WIDTH_NOTE}],
        parts=1,
    )
    text = "\n".join(lines)
    assert "1824.53" in text
    assert "✓" not in text


def test_conversion_record_sweep_width_reaches_the_report(tmp_path: Path) -> None:
    """Sweep-width records in ``*.fid.conversion.json`` must appear in the step report."""
    import json

    from workflow.direct_diagnostics import fid_step_quality_report, read_sweep_width_audit

    work = tmp_path / "process"
    work.mkdir()
    entry = {"axis": "F1", "nucleus": "15N", "sw_hz_raw": 2000.0, "note": _SWEEP_WIDTH_NOTE}
    (work / "d_001.fid.conversion.json").write_text(
        json.dumps({"raw_fingerprint": {}, "sweep_width": [entry]}),
        encoding="utf-8",
    )
    assert read_sweep_width_audit(work) == [entry]
    text = "\n".join(fid_step_quality_report(work, [tmp_path / "raw"], parts=1))
    assert "1824.53" in text
    assert "✓" not in text
    # A missing record does not affect existing rendering
    empty = tmp_path / "process_empty"
    empty.mkdir()
    assert read_sweep_width_audit(empty) == []


#: The carrier-convention note measured on d_018 (report and log share the text)
_CARRIER_SUMMARY = (
    "CAR switched to the acqus O1/BF1 convention (x=8.49, y=117.5, z=53): the conversion "
    "script assumes the 1H carrier sits on the water peak, but this dataset's 1H carrier is "
    "at 8.49 ppm while the water peak should be at 5.3554 ppm (3.1346 ppm apart), so that "
    "convention cannot be used"
)
_CARRIER_FIX = "yCAR: fid.com=114.375 -> acqus O1/BF1=117.5 (corrected)"

_CARRIER_OLD_BLOCK = (
    "15N acquisition center : 117.50 ppm\n"
    "Configured target CAR  : 114.38 ppm\n"
    "Δ                       : -3.13 ppm\n"
    "status: REFERENCE_OVERRIDE"
)


def test_advisory_lines_are_not_listed_as_corrections() -> None:
    """Regression coverage: test advisory lines are not listed as corrections."""
    from workflow.direct_diagnostics import is_advisory_line

    assert not is_advisory_line("xLAB: fid.com=HN → acqus=1H（已修正）")
    assert not is_advisory_line("ySW: fid.com=2000.000 -> acqus=1824.535 (corrected)")
    assert not is_advisory_line("out: ./test.fid → d_001.fid（已修正）")

    assert is_advisory_line(
        "x, y, z 的 CAR 与谱中心不一致(谱中心 x=8.49;差值 x: -3.13 ppm);已保留脚本原值"
    )
    assert is_advisory_line("CAR 参考 = 各维计算出来的采集中心(acqus O1/BF1)(x=8.49)")

    assert not is_advisory_line("")
    assert not is_advisory_line("   ")


def test_lines_that_say_nothing_changed_are_not_corrections() -> None:
    """Regression coverage: test lines that say nothing changed are not corrections."""
    from workflow.direct_diagnostics import is_advisory_line

    assert is_advisory_line(
        "xN:保留原值 2048;NUS 的行长由 nusExpand(serPadSize)决定,故未做几何核对"
    )
    assert is_advisory_line(
        "yMODE: fid.com=Complex -> States-TPPI(bruk2pipe 模式号相同,但具体关键字才记下"
        "采集模式;符号调整由处理期 FT 标志施加,转换期不做)"
    )

    assert is_advisory_line(
        "xN: 2048 kept as-is; for NUS the row length is set by nusExpand "
        "(serPadSize), so no geometry check was done"
    )
    assert is_advisory_line(
        "yMODE: fid.com=Complex -> States-TPPI (same bruk2pipe mode code, but the specific "
        "keyword records the acquisition mode; the sign adjustment is applied by "
        "the FT flags, not at conversion)"
    )
    assert is_advisory_line("yMODE: fid.com=Complex kept as written (canonical -N mode)")

    assert not is_advisory_line("xLAB: fid.com=HN → acqus=1H（已修正）")
    assert not is_advisory_line("sampleCount: fid.com=1 → nuslist=700（已修正）")
    assert not is_advisory_line(
        "yMODE: fid.com=Complex -> acqus=Echo-AntiEcho (corrected: acquNs FnMODE=6)"
    )


def test_report_leads_with_a_conclusion_summary() -> None:
    """Regression coverage: test report leads with a conclusion summary."""
    from workflow.direct_diagnostics import DirectDiagnosticsResult, format_fid_step_report

    clean = DirectDiagnosticsResult(reports=[], clean=True)
    lines = format_fid_step_report(diagnostics=clean, parts=1)
    assert lines[0].startswith("==")
    summary = lines[1]
    assert "0" in summary

    assert summary.index("0") >= 0
    assert any(line.lstrip().startswith("◆") for line in lines[2:])

    dirty = DirectDiagnosticsResult(reports=["x", "y", "z"], clean=False, auto_handled=2)
    lines = format_fid_step_report(diagnostics=dirty, parts=1)
    assert "3" in lines[1]
    assert "2" in lines[1]
    assert "1" in lines[1]

    skipped = DirectDiagnosticsResult(reports=["layout unavailable"], ran=False)
    lines = format_fid_step_report(diagnostics=skipped, parts=1)
    assert "1" in lines[1]

    mixed = format_fid_step_report(
        diagnostics=clean,
        corrections=[
            "xLAB: fid.com=HN → acqus=1H（已修正）",
            "sampleCount: fid.com=1 → nuslist=700（已修正）",
            "xN:保留原值 2048;NUS 的行长由 nusExpand(serPadSize)决定,故未做几何核对",
        ],
        parts=1,
    )
    assert "2" in mixed[1], "advisory line was counted as a rewrite"


def test_neg_applied_notice_stays_a_single_short_line() -> None:
    """Regression coverage: test neg applied notice stays a single short line."""
    from core.experiment.pulse_pathways import neg_applied_line

    decision = SimpleNamespace(axis="F2")
    line = neg_applied_line(decision, 3)
    assert "\n" not in line
    assert line.startswith("yMODE (F2):")

    assert len(line) < 200, f"the -neg notice grew back to {len(line)} chars"

    for jargon in ("共轭", "conjugat", "相位", "phase"):
        assert jargon not in line


def test_neg_notice_is_recognised_by_its_source_text() -> None:
    """Regression coverage: test neg notice is recognised by its source text."""
    from core.experiment.pulse_pathways import is_neg_notice

    assert is_neg_notice("yMODE (F2):已自动施加 FT -neg(它对本维时域数据取共轭…)")
    assert is_neg_notice("zMODE (F1):no FT -neg was applied automatically (…); first look…")
    assert is_neg_notice("  xMODE (F2): something")

    assert not is_neg_notice("yMODE: fid.com=Complex -> States-TPPI(bruk2pipe 模式号相同…)")
    assert not is_neg_notice("CAR 参考 = 各维计算出来的采集中心")
    assert not is_neg_notice("")
    assert not is_neg_notice("组间场漂:已合并 5 段")


def test_fid_report_lists_the_carrier_decision() -> None:
    """A rewritten carrier convention goes in the conversion list; AUTO keeps only a · note."""
    from workflow.direct_diagnostics import format_fid_step_report

    overridden = format_fid_step_report(
        field_drift=None,
        carrier={"fix_lines": [_CARRIER_FIX], "summary": _CARRIER_SUMMARY},
        parts=1,
    )
    assert any("117.5" in line for line in overridden)
    assert any("3.1346" in line for line in overridden)

    assert _CARRIER_FIX not in "\n".join(overridden)

    kept = format_fid_step_report(
        field_drift=None,
        carrier={
            "fix_lines": [],
            "summary": (
                "CAR keeps the conversion script (AUTO) convention: the 1H carrier is taken "
                "as the water peak ... (x=4.754, y=118.05)"
            ),
        },
        parts=1,
    )
    assert any("AUTO" in line and "118.05" in line for line in kept)

    assert not any("单数据集" in line for line in kept)
    assert not any("组间场漂" in line for line in kept)
    assert any("✓" in line and "组间场漂" not in line for line in kept)

    assert not any(line.strip().startswith(("1.", "2.")) for line in kept)


def test_conversion_record_carrier_reaches_the_report(tmp_path: Path) -> None:
    """Carrier records in ``*.fid.conversion.json`` must appear in the step report."""
    import json

    from workflow.direct_diagnostics import fid_step_quality_report, read_carrier_audit

    work = tmp_path / "process"
    work.mkdir()

    carrier = {
        "convention": "o1bf1",
        "summary": _CARRIER_SUMMARY,
        "fix_lines": [_CARRIER_FIX],
        "blocks": [_CARRIER_OLD_BLOCK],
        "dims": [
            {
                "axis": "y",
                "logical_axis": "F1",
                "nucleus": "15N",
                "auto": 114.375,
                "o1bf1": 117.5,
                "expected": 117.5,
                "decision": "o1bf1",
                "delta_ppm": -3.125,
            },
        ],
    }
    (work / "d_001.fid.conversion.json").write_text(
        json.dumps({"raw_fingerprint": {}, "carrier": carrier}), encoding="utf-8"
    )
    read_back = read_carrier_audit(work)

    assert read_back == {**carrier, "fix_lines": []}

    on_disk = json.loads((work / "d_001.fid.conversion.json").read_text(encoding="utf-8"))
    assert on_disk["carrier"]["fix_lines"] == [_CARRIER_FIX]
    lines = fid_step_quality_report(work, [tmp_path / "raw"], parts=1)
    assert any("3.1346" in line for line in lines)
    assert any("117.5" in line for line in lines)

    assert not any(_CARRIER_FIX in line for line in lines)

    assert not any("Configured target CAR" in line for line in lines)
    assert not any("REFERENCE_OVERRIDE" in line for line in lines)
    assert not any("Δ" in line for line in lines)
    empty = tmp_path / "process_empty"
    empty.mkdir()
    assert read_carrier_audit(empty) == {}


#: fid.com parameters changed during conversion (report and log share the text)
_FID_COM_FIX = "yMODE: fid.com=Complex -> acqus=Echo-AntiEcho (corrected)"


def test_fid_report_lists_fid_com_parameter_corrections() -> None:
    """Per-line "parameter corrections" printed in the log also go into the conversion list."""
    from workflow.direct_diagnostics import format_fid_step_report

    lines = format_fid_step_report(field_drift=None, corrections=[_FID_COM_FIX], parts=1)
    assert any(_FID_COM_FIX in line for line in lines)
    assert not any("nothing needed correcting" in line for line in lines)


def test_conversion_record_fid_com_corrections_reach_the_report(tmp_path: Path) -> None:
    """fid.com correction records in the conversion record must appear in the step report."""
    import json

    from workflow.direct_diagnostics import fid_step_quality_report, read_fid_com_corrections

    work = tmp_path / "process"
    work.mkdir()
    (work / "d_001.fid.conversion.json").write_text(
        json.dumps({"raw_fingerprint": {}, "fid_com_corrections": [_FID_COM_FIX]}),
        encoding="utf-8",
    )
    assert read_fid_com_corrections(work) == [_FID_COM_FIX]
    lines = fid_step_quality_report(work, [tmp_path / "raw"], parts=1)
    assert any(_FID_COM_FIX in line for line in lines)
    empty = tmp_path / "process_empty"
    empty.mkdir()
    assert read_fid_com_corrections(empty) == []


# ------------------------------------------------- 2026-09-24 review batch B guards


def test_drift_residual_counts_only_trusted_parts() -> None:
    """B4: the re-check max residual counts **trusted parts** only -- otherwise "residual 151 Hz"
    sits right next to "that estimate is not reproducible".
    """
    from workflow.direct_diagnostics import _drift_report_lines

    record = {
        "checked": True,
        "reference": 1,
        "parts": 3,
        "corrected_parts": [2],
        "rounds": [
            {
                "offsets_hz": [None, 1.0, 151.0],
                "offsets_ppm": [None, 0.002, 0.3],
                "criterion_hz": 2.0,
                "linewidth_hz": 10.0,
                "criterion_basis": "linewidth",
                "trusted_parts": [2],
                "skipped": ["part 3: 不可复现"],
            }
        ],
    }
    lines, corrected, _all_clear = _drift_report_lines(record)
    joined = "\n".join(lines)
    assert corrected is True
    assert "1.00 Hz" in joined  # residual of the trusted part
    assert "151.00 Hz" not in joined  # skipped parts do not enter the residual


def test_drift_report_does_not_claim_a_merge_when_it_was_refused() -> None:
    """B5: on a rollback mismatch (blocked) the report must say "merge refused", not "merged N"."""
    from workflow.direct_diagnostics import _drift_report_lines

    record = {
        "checked": True,
        "reference": 1,
        "parts": 3,
        "blocked": True,
        "corrected_parts": [2, 3],
        "rounds": [
            {
                "offsets_hz": [None, 3.0, 4.0],
                "offsets_ppm": [None, 0.01, 0.01],
                "criterion_hz": 2.0,
                "linewidth_hz": 10.0,
                "criterion_basis": "linewidth",
                "trusted_parts": [2, 3],
                "skipped": [],
            }
        ],
        "merge": {"parts": 3, "blocked": True, "corrected_parts": [2, 3]},
    }
    lines, _corrected, _all_clear = _drift_report_lines(record)
    joined = "\n".join(lines)
    assert "拒绝合并" in joined
    assert "已合并" not in joined


def test_fid_inspection_that_did_not_run_is_not_reported_as_an_issue() -> None:
    """B13: "fid not found / layout unparsable" means it did not run, not "1 issue found"."""
    from workflow.direct_diagnostics import DirectDiagnosticsResult, format_fid_step_report

    skipped = DirectDiagnosticsResult(
        reports=["Data quality diagnosis: converted fid not found, skipped"],
        clean=False,
        ran=False,
    )
    lines = "\n".join(format_fid_step_report(diagnostics=skipped, parts=1))
    assert "未执行" in lines
    assert "检出" not in lines

    issues = DirectDiagnosticsResult(reports=["dc offset 0.4"], clean=False, ran=True)
    lines2 = "\n".join(format_fid_step_report(diagnostics=issues, parts=1))
    assert "检出" in lines2


def test_collect_fid_paths_finds_the_new_style_slice_names(tmp_path: Path) -> None:
    """B11: slice streams use two naming generations (`{dataset_id}NNN.fid`, `testNNN.fid`)."""
    from core.data.bruker_reader import read_dataset
    from workflow.direct_diagnostics import collect_fid_paths

    work = tmp_path / "process"
    (work / "fid").mkdir(parents=True)
    first = work / "fid" / "d_001001.fid"
    second = work / "fid" / "d_001002.fid"
    for path in (first, second):
        path.write_bytes(b"\x00" * 4096)
    exp = read_dataset(ROOT_FIXTURES / "hsqc_2d")
    exp.dataset_id = "d_001"
    assert collect_fid_paths(work, exp) == [first, second]
