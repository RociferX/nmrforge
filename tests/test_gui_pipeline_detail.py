"""Phase B tests: Pipeline step details / parameter trace-back / reasons shown
inline / FAILED retry."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager
from gui.pipeline_panel import PipelinePanel, compute_data_step_statuses


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeController:
    def set_manager(self, manager) -> None:
        pass


def _manager_with_spectrum(tmp_path: Path, failed: bool = False):
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    exp_id, data_id = entry.id, data.id
    process = manager.data_dir(exp_id, data_id, "process")
    process.mkdir(parents=True, exist_ok=True)
    fid = process / f"{exp_id}-{data_id}.fid"
    fid.write_bytes(b"fid")
    manager.set_data_fid(exp_id, data_id, fid)
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    ft2 = spectra / f"{exp_id}-{data_id}.ft2"
    ft2.write_bytes(b"ft2")
    manager.set_data_spectrum(exp_id, data_id, ft2)
    run = manager.start_run(
        exp_id,
        workflow_ref="process",
        inputs={"data_id": data_id},
        params={"ext_lo": "11.0", "ext_hi": "6.0", "zero_fill": 2},
    )
    if failed:
        manager.finish_run(run.run_id, "failed", message="SMILE 重构失败: boom")
    else:
        manager.finish_run(
            run.run_id,
            "success",
            outputs={"spectrum_path": str(ft2)},
            message="生成谱图",
        )
        manager.snapshot_run(run.run_id, {"process.com": "nmrPipe ..."})
    manager.save()
    return manager, exp_id, data_id


def _record_spectrum_report(spectrum_path: Path, params: dict, text: str) -> None:
    """Write {spectrum}.quality.json with the same fingerprint as GUI
    _cached_spectrum_report."""
    import hashlib
    import json

    st = spectrum_path.stat()
    fp = f"{st.st_mtime_ns}|{st.st_size}"
    params_fp = hashlib.sha256(
        json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    Path(f"{spectrum_path}.quality.json").write_text(
        json.dumps({"fp": fp, "params_fp": params_fp, "text": text}),
        encoding="utf-8",
    )


def test_step_detail_uses_quality_record_not_recompute(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29d: expanding the details reads the {spectrum}.quality.json
    record instead of recomputing the report (no spectrum read)."""
    from workflow.optimization_report import (
        report_text_from_logs,
        write_quality_record,
    )

    logs = ["处理完成", "== 谱图质量报告 ==", "◆ 最终谱图质量: 良好", "处理参数: 填零 2"]
    text = report_text_from_logs(logs)
    assert text is not None and "良好" in text
    assert report_text_from_logs(["无报告"]) is None

    manager, exp_id, data_id = _manager_with_spectrum(tmp_path)
    runs = [
        r
        for r in manager.project.workflow_runs
        if r.experiment_id == exp_id and r.workflow_ref == "process"
    ]
    run = runs[-1]
    ft2 = Path(run.outputs["spectrum_path"])
    write_quality_record(str(ft2), run.params, text)

    called = []
    monkeypatch.setattr(
        "gui.pipeline_panel._spectrum_param_report",
        lambda *a, **k: called.append(1) or "不应重算",
    )
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    panel._toggle_step_detail("spectrum")
    assert "良好" in panel._rows["spectrum"].detail_label.text()
    assert not called, "命中记录时不应重算报告"
    panel.close()


@pytest.mark.parametrize("step", ["fid", "spectrum"])
@pytest.mark.parametrize("failed", [False, True])
def test_step_detail_reports_manual_script_changes_even_without_quality(
    tmp_path: Path, qapp: QApplication, step: str, failed: bool
) -> None:
    from workflow.script_audit import script_changes

    manager, exp_id, data_id = _manager_with_spectrum(tmp_path)
    run = manager.start_run(
        exp_id,
        workflow_ref="manual_fid" if step == "fid" else "manual_process",
        inputs={"data_id": data_id},
        params={
            "mode": "manual",
            "manual_script_changes": [
                script_changes(
                    "process.com", "nmrPipe -fn PS -p0 30", "nmrPipe -fn PS -p0 20", "old"
                )
            ],
        },
    )
    manager.finish_run(run.run_id, "failed" if failed else "success")
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    detail, _, is_failed = panel._step_detail(step)
    assert "-p0" in detail and "20 → 30" in detail
    assert is_failed == failed
    panel.close()


def test_spectrum_report_title_comes_from_the_shared_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-09-23 fix guard: the report title is defined in exactly one place, so
    the producer and the extractor must agree with each other.

    Real bug seen: the producer renamed the title to "== spectrum quality report =="
    while the extractor (report_text_from_logs) still compared against the old
    title -> the report is still announced in the log, but {spectrum}.quality.json
    is never refreshed, so the GUI "generate spectrum" step detail keeps showing
    "no report record".
    """
    from workflow.optimization_report import (
        report_text_from_logs,
        spectrum_report_title,
    )
    from workflow.phase_routes import _append_final_summary

    monkeypatch.setattr(
        "workflow.optimization_report.spectrum_quality_report_lines",
        lambda *a, **k: ["◆ 最终谱图质量: 良好"],
    )
    logs: list[str] = ["处理完成"]
    _append_final_summary(logs, str(tmp_path / "x.ft2"))
    assert logs[1] == spectrum_report_title()
    extracted = report_text_from_logs(logs)
    assert extracted is not None and "良好" in extracted
    # The old title (before 2026-09-23) is still tolerated, so legacy records
    # are not lost
    legacy = ["处理完成", "== 谱图质量与数据质量报告 ==", "◆ 数据质量: 已清理坏点"]
    assert "已清理坏点" in (report_text_from_logs(legacy) or "")


def test_quality_record_matches_on_the_spectrum_file_fingerprint(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Record reuse is judged by the **spectrum file fingerprint**: a changed
    parameter representation does not invalidate it, a changed spectrum does."""
    import json
    from pathlib import Path as _Path

    from gui.pipeline_panel import PipelinePanel
    from workflow.optimization_report import write_quality_record

    manager, exp_id, data_id = _manager_with_spectrum(tmp_path)
    runs = [
        r
        for r in manager.project.workflow_runs
        if r.experiment_id == exp_id and r.workflow_ref == "process"
    ]
    ft2 = _Path(runs[-1].outputs["spectrum_path"])
    write_quality_record(str(ft2), runs[-1].params, "报告A")

    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    assert "报告A" in panel._cached_spectrum_report(runs[-1].params, str(ft2))

    # Parameter fingerprint mismatches but the spectrum is unchanged -> the
    # record is still valid (the spectrum file fingerprint is authoritative)
    record = json.loads(_Path(f"{ft2}.quality.json").read_text(encoding="utf-8"))
    record["params_fp"] = "0" * 16
    _Path(f"{ft2}.quality.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )
    assert "报告A" in panel._cached_spectrum_report(runs[-1].params, str(ft2))

    # Spectrum changed (regenerated elsewhere) -> the old record is void, prompt
    # to re-run
    ft2.write_bytes(b"ft2-regenerated")
    assert "无报告记录" in panel._cached_spectrum_report(runs[-1].params, str(ft2))
    panel.close()


def test_spectrum_report_without_record_shows_note(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29e: with no quality record no report is generated on the
    spot (no spectrum read), and the user is prompted to re-run."""
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path)
    called = []
    monkeypatch.setattr(
        "gui.pipeline_panel._spectrum_param_report",
        lambda *a, **k: called.append(1) or "不应现场生成",
    )
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    panel._toggle_step_detail("spectrum")
    text = panel._rows["spectrum"].detail_label.text()
    assert "无报告记录" in text
    assert not called, "无记录时不应现场生成报告"
    panel.close()


def test_step_detail_expands_with_params(tmp_path: Path, qapp: QApplication) -> None:
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path)
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    row = panel._rows["spectrum"]
    assert row.detail_frame.isHidden()
    panel._toggle_step_detail("spectrum")
    assert not row.detail_frame.isHidden()
    text = row.detail_label.text()
    assert "结果文件" in text or "Result file" in text

    assert "ext_lo" not in text
    # 0.2.199-patch29e: with no quality record nothing is generated on the spot;
    # prompt to re-run
    assert "无报告记录" in text
    assert not hasattr(row, "manual_with_params_button")
    panel._toggle_step_detail("spectrum")
    assert row.detail_frame.isHidden()
    panel.close()


def test_step_detail_hides_internal_run_metadata(tmp_path: Path, qapp: QApplication) -> None:
    "Regression coverage: test step detail hides internal run metadata."
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path)
    run = manager.last_run_for_data(exp_id, data_id, ("process",))
    assert run is not None
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    text, _params, _failed = panel._step_detail("spectrum")
    assert run.run_id not in text
    assert run.workflow_ref not in text
    assert "snapshot" not in text.lower()
    panel.close()


def test_locked_reason_inline(tmp_path: Path, qapp: QApplication) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    manager.import_data(entry.id, "/fake/1")
    manager.save()
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", entry.id, "d_001")
    row = panel._rows["spectrum"]
    assert not row.reason_label.isHidden()
    assert "生成 FID" in row.reason_label.text()
    panel.close()


def test_failed_status_and_retry(tmp_path: Path, qapp: QApplication) -> None:
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, failed=True)
    statuses = compute_data_step_statuses(manager, exp_id, data_id)
    assert statuses["spectrum"] == "FAILED"
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    row = panel._rows["spectrum"]
    assert row.status_label.text().startswith("×")
    assert not row.reason_label.isHidden()
    assert "boom" in row.reason_label.text()
    panel._toggle_step_detail("spectrum")
    assert not row.retry_button.isHidden()
    assert not row.view_log_button.isHidden()
    panel.close()


def test_view_log_signal(tmp_path: Path, qapp: QApplication) -> None:
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, failed=True)
    panel = PipelinePanel(manager, _FakeController())
    seen: list[str] = []
    panel.view_log_requested.connect(seen.append)
    panel._rows["spectrum"].view_log_button.click()
    assert seen == ["spectrum"]
    panel.close()


def test_step_detail_uses_the_application_dark_theme(qapp: QApplication) -> None:
    "Regression coverage: test step detail uses the application dark theme."
    from gui.pipeline_panel import PipelineStepRow
    from ui_support.theme import SURFACE_ALT, TEXT_PRIMARY

    row = PipelineStepRow("spectrum", "生成谱图", "desc")
    style = row.detail_frame.styleSheet()
    assert f"background: {SURFACE_ALT}" in style
    assert f"color: {TEXT_PRIMARY}" in row.detail_label.styleSheet()
    assert row.detail_label.textInteractionFlags()
    row.close()


def test_step_detail_refreshes_on_data_switch(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.161: when switching data, an already expanded step detail refreshes
    immediately to the new data's report."""
    manager = ProjectManager.create_project(tmp_path / "proj2", "demo")
    entry = manager.create_experiment("HSQC")
    d1 = manager.import_data(entry.id, "/fake/1")
    d2 = manager.import_data(entry.id, "/fake/2")
    for data, zf in ((d1, 2), (d2, 3)):
        run = manager.start_run(
            entry.id,
            workflow_ref="process",
            inputs={"data_id": data.id},
            params={"zero_fill": zf},
        )
        manager.finish_run(run.run_id, "success", outputs={}, message="生成谱图")
    manager.save()

    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", entry.id, d1.id)
    panel._toggle_step_detail("spectrum")
    row = panel._rows["spectrum"]
    assert not row.detail_frame.isHidden()
    first_text = row.detail_label.text()
    assert "无报告记录" in first_text  # 0.2.199-patch29e: no record, no on-the-spot
    # generation
    # Switch to d_002: the expanded detail must refresh immediately (no re-click)
    panel.set_selection("data", entry.id, d2.id)
    text2 = row.detail_label.text()
    assert text2 != first_text  # refreshed with the new data's run record
    assert "无报告记录" in text2
    panel.close()


# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
def test_format_params_prefers_the_readable_pick_report() -> None:
    "Regression coverage: test format params prefers the readable pick report."
    from gui.pipeline_panel import _format_params

    params = {
        "sigma": 4.0,
        "localization": {"method": "parabolic", "n_boundary_hit": 2},
        "peak_pick_report": "选峰汇总: 候选 120 / 接受 37\n定位方法: 三点抛物线",
    }
    text = _format_params(params)

    assert "选峰汇总" in text
    assert "三点抛物线" in text

    assert "localization=" not in text
    assert "'method'" not in text

    assert "sigma=4.0" not in text


def test_format_params_without_a_report_falls_back_to_flat_rendering() -> None:
    "Regression coverage: test format params without a report falls back to flat rendering."
    from gui.pipeline_panel import _format_params

    text = _format_params({"sigma": 4.0, "localization": {"method": "parabolic"}})
    assert "sigma=4.0" in text
    assert "localization=" in text
    assert _format_params({}) == ""


def test_format_params_accepts_a_line_list_report() -> None:
    "Regression coverage: test format params accepts a line list report."
    from gui.pipeline_panel import _format_params

    text = _format_params({"peak_pick_report": ["第一行", "", "第二行"], "localization": {"x": 1}})
    assert "第一行" in text and "第二行" in text
    assert "localization=" not in text


def test_smile_report_does_not_dump_the_ranking_repr() -> None:
    "Regression coverage: test smile report does not dump the ranking repr."
    from gui.pipeline_panel import _smile_step_report

    lines = _smile_step_report(
        {
            "n_combos": 16,
            "ranking": [{"rank": 1, "nsigma": 5.0, "thresh": 0.25, "stable_count": 42}],
        },
        {"csv": "ranking.csv"},
    )
    text = "\n".join(lines)
    assert "16" in text and "1" in text and "42" in text
    assert "{'rank'" not in text and "[{" not in text
