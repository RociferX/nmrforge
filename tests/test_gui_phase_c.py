"""阶段 C 测试:批量进度/汇总、拖拽导入、设置对话框。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from gui.main_window import MainWindow
from gui.pipeline_panel import PipelinePanel
from gui.pipeline_state import set_batch_id


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


class _SyncThread:
    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


class _TempWorkspace:
    def __init__(self, root) -> None:
        self.root = Path(root)

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def list_projects(self):
        return sorted(
            p
            for p in self.root.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
        )

    def create_project(self, name: str, **kwargs):
        return ProjectManager.create_project(self.root / name, name, **kwargs)


class _BatchController:
    """记录调用;d_002 失败(验证批量汇总成功/失败计数)。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def set_manager(self, manager) -> None:
        pass

    def generate_spectrum(self, data, exp_id=None, data_id=None, progress=None):
        self.calls.append(data_id or "")
        if progress:
            progress("正在重构")
        if data_id == "d_002":
            raise RuntimeError("模拟失败")
        return "/tmp/x.ft2"


def test_batch_progress_and_summary(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("threading.Thread", _SyncThread)
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("batch")
    data1 = manager.import_data(entry.id, "/fake/1")
    data2 = manager.import_data(entry.id, "/fake/2")
    manager.save()
    set_batch_id(manager, entry.id, data1.id, "B1")
    set_batch_id(manager, entry.id, data2.id, "B1")
    controller = _BatchController()
    panel = PipelinePanel(manager, controller)
    panel.set_selection("data", entry.id, data1.id)
    summaries: list[dict] = []
    progress_msgs: list[str] = []
    panel.batch_summary_requested.connect(summaries.append)
    panel.progress_updated.connect(progress_msgs.append)
    panel._on_run_requested("spectrum")
    assert controller.calls == [data1.id, data2.id]
    assert summaries and summaries[0]["info"].startswith("批量组 B1: 1/2 成功")
    assert any("1/2 完成" in msg for msg in progress_msgs)
    assert any("当前:" in msg for msg in progress_msgs)
    panel.close()


def test_drag_drop_import(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    bruker_dir: Path,
) -> None:
    monkeypatch.setattr("threading.Thread", _SyncThread)
    ws = tmp_path / "ws"
    ws.mkdir()
    manager = ProjectManager.create_project(ws / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    manager.save()
    monkeypatch.setattr(
        "gui.main_window.WorkspaceManager", lambda: _TempWorkspace(ws)
    )
    monkeypatch.setattr(
        "core.workspace.WorkspaceManager",
        lambda *a, **k: _TempWorkspace(ws),
    )
    shown: list[str] = []
    monkeypatch.setattr(
        "gui.main_window.InfoDialog.show_info",
        staticmethod(lambda parent, title, text: shown.append(text)),
    )
    window = MainWindow(manager=manager)
    window.project_tree.select_experiment(entry.id)
    bad = tmp_path / "notdata"
    bad.mkdir()
    window._handle_dropped_import_paths([bruker_dir / "hsqc_2d", bad])
    entry_now = manager.project.experiment(entry.id)
    assert len(entry_now.data) == 1
    assert any("缺少 acqus" in text for text in shown)
    window.close()


def test_settings_defaults_and_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gui import settings as settings_module

    cfg = tmp_path / "nmrforge.local.yaml"
    monkeypatch.setattr(settings_module, "_settings_path", lambda: cfg)
    loaded = settings_module.load_settings()
    assert loaded["points_per_line"] == 2
    assert loaded["smile_thread_cap"] == 2
    assert loaded["linewidth_hz"]["1H"] == 8
    settings_module.save_settings(
        {"points_per_line": 4, "linewidth_hz": {"1H": 10}}
    )
    loaded2 = settings_module.load_settings()
    assert loaded2["points_per_line"] == 4
    assert loaded2["linewidth_hz"]["1H"] == 10
    assert loaded2["linewidth_hz"]["15N"] == 15
    assert loaded2["smile_thread_cap"] == 2


def test_settings_dialog_defaults(qapp: QApplication) -> None:
    from gui.dialogs import SettingsDialog

    dialog = SettingsDialog()
    assert dialog.ppl_spin.value() == 2
    assert dialog.smile_spin.value() == 2
    dialog.close()


def test_batch_summary_dialog(qapp: QApplication) -> None:
    from gui.dialogs import BatchSummaryDialog

    summary = {
        "info": "批量组 B1: 1/2 成功",
        "items": [
            {"data_id": "d_001", "step": "生成谱图", "ok": True},
            {
                "data_id": "d_002",
                "step": "生成谱图",
                "ok": False,
                "error": "RuntimeError: 模拟失败",
            },
        ],
    }
    dialog = BatchSummaryDialog(None, summary, "demo")
    assert dialog.list_widget.count() == 2
    dialog.close()
