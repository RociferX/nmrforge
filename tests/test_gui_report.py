"""报告页测试:ReportPanel 产物扫描/内嵌预览(offscreen)。
analysis 步骤已从 GUI 隐藏(2026-09-03),相关状态断言移除。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from gui.center_panel import CenterPanel
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


