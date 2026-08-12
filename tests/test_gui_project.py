"""GUI 骨架测试:主窗口建立在 ProjectManager 之上(offscreen)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QFileDialog, QInputDialog

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
    # 状态由产物文件推断:未导入数据时显示 registered
    assert first.text(2) == "registered"
    (manager.dir_path("metadata") / "exp_001.json").write_text("{}", encoding="utf-8")
    window.refresh()
    assert window.experiment_tree.topLevelItem(0).text(2) == "imported"
    assert "demo" in window.windowTitle()
    window.close()


def test_window_empty_state(qapp: QApplication) -> None:
    window = MainWindow()
    assert window.experiment_tree.topLevelItemCount() == 0
    assert "未打开项目" in window.windowTitle()
    window.close()


def test_new_project_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "newproj"
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *args, **kwargs: str(root)),
    )
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *args, **kwargs: ("demo", True))
    )
    window = MainWindow()
    window.new_project()
    assert window.manager.project is not None
    assert window.manager.project.name == "demo"
    assert window.experiment_tree.topLevelItemCount() == 0
    assert (root / "project.json").is_file()
    window.close()


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
        """把后台导入线程变为同步执行,测试不依赖线程时序。"""

        def __init__(self, target=None, daemon=None) -> None:
            self._target = target

        def start(self) -> None:
            self._target()
            ran.append(True)

    monkeypatch.setattr("threading.Thread", SyncThread)

    def fake_import(mgr, source, *, title="", sample_id="", copy=True) -> ImportResult:
        entry = mgr.add_experiment(source, title=title, sample_id=sample_id)
        return ImportResult(
            experiment_id=entry.id,
            run_id="R-20260812-001",
            source=Path(source),
            raw_dir=None,
            metadata_path=mgr.dir_path("metadata") / f"{entry.id}.json",
            checksums={},
            file_count=0,
            total_bytes=0,
        )

    monkeypatch.setattr("gui.main_window.import_bruker_dataset", fake_import)

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
    """B2G-001 端到端:真实 Bruker fixture 目录经 import_bruker_dataset 导入。"""
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
    assert manager.dir_path("metadata").joinpath("exp_001-d_001.json").is_file()
    assert manager.dir_path("raw").joinpath("exp_001", "d_001").is_dir()
    assert any(r.workflow_ref == "import" for r in manager.project.workflow_runs)
    assert window.project_tree.current_experiment_id() == "exp_001"
    window.close()
