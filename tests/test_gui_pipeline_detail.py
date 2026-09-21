"""Phase B Test: Pipeline step details/parameter trace back/The reason is obvious/FAILED Retry."""

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
        manager.finish_run(run.run_id, "failed", message="SMILE Refactoring failed: boom")
    else:
        manager.finish_run(
            run.run_id,
            "success",
            outputs={"spectrum_path": str(ft2)},
            message="generate spectrum",
        )
        manager.snapshot_run(run.run_id, {"process.com": "nmrPipe ..."})
    manager.save()
    return manager, exp_id, data_id


def _record_spectrum_report(spectrum_path: Path, params: dict, text: str) -> None:
    """Press GUI _cached_spectrum_report and write {notation}.quality.json with the same
    fingerprint."""
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
    """0.2.199-patch29d: Expand details and read {spectrum}. quality.json Record, do not
    recalculate the report (do not read the spectrum)."""
    from workflow.optimization_report import (
        report_text_from_logs,
        write_quality_record,
    )

    logs = [
        "Processing completed",
        "== spectrum quality and data quality report ==",
        "◆ Final spectrum image quality: good",
        "Processing parameter: zero filling 2",
    ]
    text = report_text_from_logs(logs)
    assert text is not None and "good" in text
    assert report_text_from_logs(["No report"]) is None

    manager, exp_id, data_id = _manager_with_spectrum(tmp_path)
    runs = [
        r for r in manager.project.workflow_runs
        if r.experiment_id == exp_id and r.workflow_ref == "process"
    ]
    run = runs[-1]
    ft2 = Path(run.outputs["spectrum_path"])
    write_quality_record(str(ft2), run.params, text)

    called = []
    monkeypatch.setattr(
        "gui.pipeline_panel._spectrum_param_report",
        lambda *a, **k: called.append(1) or "should not be recalculated",
    )
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    panel._toggle_step_detail("spectrum")
    assert "good" in panel._rows["spectrum"].detail_label.text()
    assert not called, "Reports should not be recalculated when records are hit"
    panel.close()


def test_spectrum_report_without_record_shows_note(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29e: When there is no quality record, no report will be generated on site (no
    spectrum will be read), and a re-run will be prompted."""
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path)
    called = []
    monkeypatch.setattr(
        "gui.pipeline_panel._spectrum_param_report",
        lambda *a, **k: called.append(1) or "Should not be generated on-site",
    )
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    panel._toggle_step_detail("spectrum")
    text = panel._rows["spectrum"].detail_label.text()
    assert "No report record" in text
    assert not called, "Reports should not be generated on-site when there are no records"
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
    assert "product" in text and "parameter" in text
    # 0.2.155: Simplified -- Only readable parameter reports are retained, and internal parameters
    # such as ext_lo are no longer displayed.
    assert "ext_lo" not in text
    # 0.2.199-patch29e: When there is no quality record, it will not be generated on-site, and it
    # will prompt to re-run.
    assert "No report record" in text
    assert not hasattr(row, "manual_with_params_button")
    panel._toggle_step_detail("spectrum")
    assert row.detail_frame.isHidden()
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
    assert "Generate FID" in row.reason_label.text()
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


def test_step_detail_light_background(qapp: QApplication) -> None:
    """The step details panel has an explicit light background + dark text (still readable under
    dark system themes)."""
    from gui.pipeline_panel import PipelineStepRow

    row = PipelineStepRow("spectrum", "generate spectrum", "desc")
    style = row.detail_frame.styleSheet()
    assert "background: #ffffff" in style
    assert "color: #222" in row.detail_label.styleSheet()
    row.close()



def test_step_detail_refreshes_on_data_switch(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.161: When switching data, the expanded step details are immediately refreshed as reports
    of new data."""
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
        manager.finish_run(run.run_id, "success", outputs={}, message="generate spectrum")
    manager.save()

    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", entry.id, d1.id)
    panel._toggle_step_detail("spectrum")
    row = panel._rows["spectrum"]
    assert not row.detail_frame.isHidden()
    first_text = row.detail_label.text()
    assert "No report record" in first_text  # 0.2.199-Patch29e: no record, no on-site generation.
    # Switch to d_002: Expanded details should refresh immediately (no need to re-click to expand).
    panel.set_selection("data", entry.id, d2.id)
    text2 = row.detail_label.text()
    assert text2 != first_text  # Running records that have been refreshed with new data.
    assert "No report record" in text2
    panel.close()
