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
    # 0.2.155:精简——只保留可读参数报告,不再显示 ext_lo 等内部参数
    assert "ext_lo" not in text
    assert "填零: 2" in text
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


def test_step_detail_light_background(qapp: QApplication) -> None:
    """步骤详情面板显式浅色背景 + 深色文字(深色系统主题下仍可读)。"""
    from gui.pipeline_panel import PipelineStepRow

    row = PipelineStepRow("spectrum", "生成谱图", "desc")
    style = row.detail_frame.styleSheet()
    assert "background: #ffffff" in style
    assert "color: #222" in row.detail_label.styleSheet()
    row.close()



def test_step_detail_refreshes_on_data_switch(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.161:切换数据时,已展开的步骤详情立即刷新为新数据的报告。"""
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
    assert "填零: 2" in row.detail_label.text()
    # 切换到 d_002:已展开详情应立即刷新(无需重新点击展开)
    panel.set_selection("data", entry.id, d2.id)
    text2 = row.detail_label.text()
    assert "填零: 3" in text2
    assert "填零: 2" not in text2
    panel.close()
