"""报告页测试:ReportPanel 产物扫描/内嵌预览 + Pipeline analysis 状态由报告驱动(offscreen)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from gui.center_panel import CenterPanel
from gui.main_window import MainWindow
from gui.pipeline_panel import PipelinePanel, compute_step_statuses
from gui.report_panel import ReportPanel, report_products


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch | None = None
) -> ProjectManager:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    manager = ProjectManager.create_project(ws / "proj", "demo")
    manager.add_experiment("/sampleD", title="HSQC")
    manager.save()
    if monkeypatch is not None:
        monkeypatch.setattr(
            "gui.main_window.WorkspaceManager", lambda: _TempWorkspace(ws)
        )
        monkeypatch.setattr(
            "core.workspace.WorkspaceManager", lambda *a, **k: _TempWorkspace(ws)
        )
    return manager


class _TempWorkspace:
    def __init__(self, root) -> None:
        self.root = Path(root)

    def list_projects(self):
        return sorted(
            p
            for p in self.root.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
        )

    def ensure(self):
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root


def test_report_products_scans_dir(tmp_path: Path) -> None:
    """report_products 只返回 html/pdf/json 产物。"""
    manager = _manager(tmp_path)
    report_dir = manager.data_dir("exp_001", "d_001", "report")
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.html").write_text("<html><body>ok</body></html>", encoding="utf-8")
    (report_dir / "report.pdf").write_bytes(b"%PDF")
    (report_dir / "report.json").write_text("{}", encoding="utf-8")
    (report_dir / "notes.txt").write_text("x", encoding="utf-8")
    products = report_products(manager, "exp_001", "d_001")
    assert [p.name for p in products] == ["report.html", "report.json", "report.pdf"]


def test_report_products_empty_without_dir(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    assert report_products(manager, "exp_001", "d_001") == []


def test_report_panel_missing_hint(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager(tmp_path)
    panel = ReportPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert "未生成报告" in panel.hint_label.text()
    assert panel.file_list.count() == 0
    panel.close()


def test_report_panel_lists_and_previews_html(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = _manager(tmp_path)
    report_dir = manager.data_dir("exp_001", "d_001", "report")
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.html").write_text(
        "<html><body>报告内容</body></html>", encoding="utf-8"
    )
    panel = ReportPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert panel.file_list.count() == 1
    assert "报告内容" in panel.preview.toHtml()
    assert panel.open_button.isEnabled()
    assert panel.current_report_path() == report_dir / "report.html"
    panel.close()


def test_analysis_status_driven_by_report_products(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Pipeline analysis 状态由 report 产物驱动(产物存在 → SUCCESS)。"""
    manager = _manager(tmp_path)
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["analysis"] == "LOCKED"  # 无报告产物

    report_dir = manager.data_dir("exp_001", "d_001", "report")
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.json").write_text('{"qc": "ok"}', encoding="utf-8")
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["analysis"] == "SUCCESS"


def test_pipeline_report_button_on_analysis_success(
    tmp_path: Path, qapp: QApplication
) -> None:
    """分析成功(报告存在)时 Pipeline 显示「报告」按钮。"""
    manager = _manager(tmp_path)
    report_dir = manager.data_dir("exp_001", "d_001", "report")
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.json").write_text("{}", encoding="utf-8")
    panel = PipelinePanel(manager)
    panel.set_selection("data", "exp_001", "d_001")
    assert not panel._rows["analysis"].report_button.isHidden()
    panel.close()


def test_center_panel_report_page(tmp_path: Path, qapp: QApplication) -> None:
    """中间面板报告页:show_report 切到 index 4 并刷新上下文。"""
    manager = _manager(tmp_path)
    panel = CenterPanel(manager)
    panel.set_selection("data", "exp_001", "d_001")
    panel.show_report(exp_id="exp_001", data_id="d_001")
    assert panel.stack.currentIndex() == 4
    assert panel.report_page._exp_id == "exp_001"
    assert panel.report_page._data_id == "d_001"
    panel.close()


def test_main_window_report_menu(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """查看 → 报告:中间面板切到报告页。"""
    manager = _manager(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window.project_tree.select_experiment("exp_001")
    window._show_report()
    assert window.center_panel.stack.currentIndex() == 4
    window.close()
