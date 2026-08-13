"""阶段 B 测试:Pipeline 步骤详情/参数追溯/原因直显/FAILED 重试。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

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


def test_step_detail_expands_with_params(tmp_path: Path, qapp: QApplication) -> None:
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path)
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    row = panel._rows["spectrum"]
    assert row.detail_frame.isHidden()
    panel._toggle_step_detail("spectrum")
    assert not row.detail_frame.isHidden()
    text = row.detail_label.text()
    assert "产物" in text and "参数" in text
    assert "ext_lo=11.0" in text and "zero_fill=2" in text
    assert not row.manual_with_params_button.isHidden()
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


def test_manual_with_params_signal(tmp_path: Path, qapp: QApplication) -> None:
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path)
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    seen: list[tuple[str, dict]] = []
    panel.manual_with_params_requested.connect(
        lambda sid, p: seen.append((sid, p))
    )
    panel._rows["spectrum"].manual_with_params_button.click()
    assert seen and seen[0][0] == "spectrum"
    assert seen[0][1].get("zero_fill") == 2
    panel.close()
