"""GUI 骨架测试:主窗口建立在 ProjectManager/WorkspaceManager 之上(offscreen)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QFileDialog

from core.project import ProjectManager
from gui.dialogs import ConfirmDialog, ImportExperimentDialog
from gui.main_window import MainWindow
from workflow.import_workflow import ImportResult


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _build_manager(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    manager.add_experiment("/sampleD", title="HSQC")
    manager.add_experiment("/sampleE", title="HNCACB")
    manager.save()
    return manager


def test_window_shows_experiments_from_project(tmp_path: Path, qapp: QApplication) -> None:
    manager = _build_manager(tmp_path)
    window = MainWindow(manager=manager)
    assert window.experiment_tree.topLevelItemCount() == 2
    first = window.experiment_tree.topLevelItem(0)
    assert first.text(0) == "exp_001"
    assert first.text(1) == "HSQC"
    assert "demo" in window.windowTitle()
    window.close()


def test_window_empty_state(qapp: QApplication) -> None:
    window = MainWindow()
    assert window.experiment_tree.topLevelItemCount() == 0
    assert "欢迎" in window.windowTitle()
    assert window.welcome_page is not None
    window.close()


def test_new_project_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "gui.main_window.QInputDialog.getText",
        staticmethod(lambda *args, **kwargs: ("demo", True)),
    )
    # 新建项目落在默认工作区(避免污染真实 ~/NMRForgeWorkspace)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setattr(
        "gui.main_window.WorkspaceManager",
        lambda: _WorkspaceStub(workspace),
    )
    window = MainWindow()
    window.new_project()
    assert window.manager.project is not None
    assert window.manager.project.name == "demo"
    assert window.experiment_tree.topLevelItemCount() == 0
    window.close()


class _WorkspaceStub:
    """测试用工作区桩:在工作区目录下创建项目。"""

    def __init__(self, root: Path) -> None:
        self.root = root

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def create_project(self, name: str, **kwargs):
        return ProjectManager.create_project(self.root / name, name, **kwargs)

    def list_projects(self) -> list[Path]:
        return sorted(
            p
            for p in self.root.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
        )


def test_open_project_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    ProjectManager.create_project(root, "demo")
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *args, **kwargs: str(root)),
    )
    window = MainWindow()
    window.open_project()
    assert window.manager.project is not None
    assert window.manager.project.name == "demo"
    window.close()


def test_add_experiment_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _build_manager(tmp_path)
    ran: list[bool] = []

    class SyncThread:
        def __init__(self, target=None, daemon=None) -> None:
            self._target = target

        def start(self) -> None:
            self._target()
            ran.append(True)

    monkeypatch.setattr("threading.Thread", SyncThread)

    def fake_import(mgr, exp_id, source, *, segments=None, copy=True) -> ImportResult:
        data = mgr.import_data(exp_id, source)
        return ImportResult(
            experiment_id=exp_id,
            data_id=data.id,
            run_id="R-20260812-001",
            source=Path(source),
            raw_dir=None,
            metadata_path=mgr.data_metadata_path(exp_id, data.id),
            checksums={},
            file_count=0,
            total_bytes=0,
        )

    monkeypatch.setattr("workflow.import_workflow.import_data", fake_import)

    class FakeImportDialog(ImportExperimentDialog):
        def exec(self) -> int:
            self.source_edit.setText("/sampleF")
            self.title_edit.setText("3D HNCACB")
            return int(ImportExperimentDialog.DialogCode.Accepted)

    monkeypatch.setattr("gui.main_window.ImportExperimentDialog", FakeImportDialog)

    window = MainWindow(manager=manager)
    window.add_experiment()
    assert ran == [True]  # 后台导入已同步执行
    assert window.experiment_tree.topLevelItemCount() == 3
    last = window.experiment_tree.topLevelItem(2)
    assert last.text(0) == "exp_003"
    assert last.text(1) == "3D HNCACB"
    window.close()


def test_delete_experiment_action_keeps_audit(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _build_manager(tmp_path)
    manager.start_run("exp_001", workflow_ref="hsqc_standard")
    monkeypatch.setattr(
        ConfirmDialog,
        "confirm",
        staticmethod(lambda *args, **kwargs: True),
    )
    window = MainWindow(manager=manager)
    window.project_tree.select_experiment("exp_001")
    window.delete_experiment()
    assert window.experiment_tree.topLevelItemCount() == 1
    assert manager.project is not None
    assert len(manager.project.workflow_runs) == 1  # 审计保留
    window.close()


def test_recent_menu_persists(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "proj"
    ProjectManager.create_project(root, "demo")
    recent = __import__(
        "core.project.recent", fromlist=["JsonRecentProjectsStore"]
    ).JsonRecentProjectsStore(tmp_path / "recent.json")
    window = MainWindow(recent=recent)
    window._open_root(root)
    assert recent.list() == [str(root.resolve())]
    assert len(window.recent_menu.actions()) == 1
    window.close()


def test_import_workflow_e2e(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端:真实 Bruker fixture 目录经 workflow.import_workflow.import_data 导入。"""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "acqus").write_text(
        "##SIMPLE 1\n##NUC1 1H\n##NUC2 15N\n##TD 1024\n",
        encoding="utf-8",
    )

    class SyncThread:
        def __init__(self, target=None, daemon=None) -> None:
            self._target = target

        def start(self) -> None:
            self._target()

    monkeypatch.setattr("threading.Thread", SyncThread)
    window = MainWindow(manager=manager)
    window.add_experiment_via_import(str(dataset), title="HSQC")
    assert manager.project is not None
    entry = manager.project.experiment("exp_001")
    assert entry is not None and entry.title == "HSQC"
    assert entry.data and entry.data[0].id == "d_001"
    assert manager.data_metadata_path("exp_001", "d_001").is_file()
    assert manager.data_dir("exp_001", "d_001", "raw").is_dir()
    assert any(r.workflow_ref == "import" for r in manager.project.workflow_runs)
    assert window.project_tree.current_experiment_id() == "exp_001"
    window.close()
