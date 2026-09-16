"""阶段 C 测试:批量进度/汇总、拖拽导入、设置对话框。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager
from gui.main_window import MainWindow
from gui.pipeline_panel import PipelinePanel


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
        self.group_calls: list[tuple] = []

    def set_manager(self, manager) -> None:
        pass

    def generate_spectrum(self, data, exp_id=None, data_id=None, progress=None):
        self.calls.append(data_id or "")
        if progress:
            progress("正在重构")
        if data_id == "d_002":
            raise RuntimeError("模拟失败")
        return "/tmp/x.ft2"

    def run_group_batch(
        self,
        exp_id,
        group_id,
        steps,
        reference_data_id="",
        progress=None,
        params=None,
    ) -> dict:
        """新引擎入口假实现:末位成员失败,汇总 1/2 成功。"""
        self.group_calls.append((group_id, list(steps)))
        if progress:
            progress(f"{group_id}: 1/2 完成")
        ids = list(self.member_ids)
        results = {
            d: {"data_id": d, "status": "success", "steps": {}, "error": ""}
            for d in ids
        }
        results[ids[-1]] = {
            "data_id": ids[-1],
            "status": "failed",
            "steps": {},
            "error": "模拟失败",
        }
        return {
            "batch_id": group_id,
            "data_ids": ids,
            "steps": list(steps),
            "results": results,
            "failed": [ids[-1]],
            "summary": {"total": len(ids), "success": len(ids) - 1, "failed": 1},
        }


def test_batch_progress_and_summary(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """数据组整组执行:统一委托新引擎,进度与汇总经面板日志/信号输出。"""
    monkeypatch.setattr("threading.Thread", _SyncThread)
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("batch")
    data1 = manager.import_data(entry.id, "/fake/1")
    data2 = manager.import_data(entry.id, "/fake/2")
    manager.create_data_group(entry.id, data_ids=[data1.id, data2.id])
    manager.save()
    # 0.2.163-补14:前置未完成不运行下一步——先让两组 fid 就绪
    from gui.pipeline_state import record_step_success

    for data in (data1, data2):
        fid = manager.data_dir(entry.id, data.id, "process") / f"{data.id}.fid"
        fid.parent.mkdir(parents=True, exist_ok=True)
        fid.write_bytes(b"fid")
        manager.set_data_fid(entry.id, data.id, fid)
        record_step_success(manager, entry.id, data.id, "fid")
    manager.save()
    controller = _BatchController()
    controller.member_ids = [data1.id, data2.id]
    panel = PipelinePanel(manager, controller)
    panel.set_selection("data", entry.id, data1.id)
    summaries: list[dict] = []
    logs: list[str] = []
    panel.batch_summary_requested.connect(summaries.append)
    panel.log_message.connect(logs.append)
    panel.log_scoped.connect(lambda msg, _scope: logs.append(msg))  # 0.2.199-补29d
    panel._on_run_requested("spectrum")
    # 0.2.199-补29gv:组内单个数据在面板里独立运行(不自动转整组)
    assert controller.group_calls == []
    assert controller.calls == [data1.id]
    assert any("正在重构" in msg for msg in logs)
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
    assert any(
        "缺少 acqus" in text or "既不是 Bruker 数据集" in text
        for text in shown
    )
    window.close()


def test_settings_defaults_and_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gui import settings as settings_module

    cfg = tmp_path / "nmrforge.local.yaml"
    monkeypatch.setattr(settings_module, "_settings_path", lambda: cfg)
    loaded = settings_module.load_settings()
    assert "points_per_line" not in loaded
    assert "smile_thread_cap" not in loaded
    assert loaded["linewidth_hz"]["1H"] == 8
    # 0.2.199-补29gg:数据总目录默认空(导入浏览回退用户主目录)
    assert loaded["data_root"] == ""
    # 0.2.199-补29fx:对齐容差默认 = Poky kr
    assert loaded["alignment_tolerance_ppm"] == {
        "1H": 0.02,
        "15N": 0.2,
        "13C": 0.2,
    }
    settings_module.save_settings(
        {"linewidth_hz": {"1H": 10}}
    )
    loaded2 = settings_module.load_settings()
    assert loaded2["linewidth_hz"]["1H"] == 10
    assert loaded2["linewidth_hz"]["15N"] == 15
    assert loaded2["linewidth_hz"]["13C"] == 20
    # 只覆盖 1H 容差时,15N/13C 保持默认
    settings_module.save_settings(
        {"alignment_tolerance_ppm": {"1H": 0.05}}
    )
    loaded3 = settings_module.load_settings()
    assert loaded3["alignment_tolerance_ppm"]["1H"] == 0.05
    assert loaded3["alignment_tolerance_ppm"]["15N"] == 0.2
    # 0.2.199-补29gg:数据总目录保存/读取/路径回退
    settings_module.save_settings({"data_root": str(tmp_path)})
    loaded4 = settings_module.load_settings()
    assert loaded4["data_root"] == str(tmp_path)
    assert settings_module.data_root_path() == tmp_path


def test_settings_dialog_defaults(qapp: QApplication) -> None:
    from gui.dialogs import SettingsDialog

    dialog = SettingsDialog()
    # 0.2.199-补29gh:窗口高度足够,新增行不被裁剪
    assert dialog.height() >= 460
    assert dialog.linewidth_spins["1H"].value() == 8
    assert dialog.linewidth_spins["15N"].value() == 15
    assert dialog.linewidth_spins["13C"].value() == 20
    assert hasattr(dialog, "data_root_edit")  # 0.2.199-补29gg
    assert dialog.tolerance_spins["1H"].value() == 0.02
    assert dialog.tolerance_spins["15N"].value() == 0.2
    assert dialog.tolerance_spins["13C"].value() == 0.2
    assert not hasattr(dialog, "ppl_spin")
    assert not hasattr(dialog, "smile_spin")
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


def test_settings_path_appimage_uses_user_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0.2.199-补29gb:AppImage 运行时设置保存到 ~/.config/NMRForge/。"""
    from pathlib import Path

    from gui import settings as settings_module

    monkeypatch.setattr(settings_module, "is_appimage", lambda: True)
    path = settings_module._settings_path()
    assert path == (
        Path.home()
        / ".config"
        / "NMRForge"
        / settings_module.SETTINGS_FILENAME
    )


def test_settings_dialog_hides_simple_mode_in_appimage(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补29gb:AppImage 打包运行时隐藏「简单模式」。"""
    from gui import settings as settings_module
    from gui.dialogs import SettingsDialog

    monkeypatch.setattr(settings_module, "is_appimage", lambda: True)
    monkeypatch.setattr(
        settings_module, "load_settings", lambda: dict(settings_module.DEFAULTS)
    )
    dialog = SettingsDialog()
    assert not dialog.simple_mode_check.isVisible()
    dialog.close()


def test_data_root_path_fallback_to_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补29gg:data_root 无效/为空时回退用户主目录。"""
    from pathlib import Path as _Path

    from gui import settings as settings_module

    monkeypatch.setattr(
        settings_module,
        "load_settings",
        lambda: {"data_root": str(tmp_path / "missing")},
    )
    assert settings_module.data_root_path() == _Path.home()
    monkeypatch.setattr(
        settings_module,
        "load_settings",
        lambda: {"data_root": str(tmp_path)},
    )
    assert settings_module.data_root_path() == tmp_path
