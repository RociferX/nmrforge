"""阶段 A 测试:全局上下文条 + 谱图联动(步骤/参数摘要、定位、3D 记忆)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from gui.main_window import MainWindow
from gui.spectrum_panel import SpectrumPanel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


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


def _manager_with_ws(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    manager = ProjectManager.create_project(ws / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    manager.save()
    return manager, ws, entry.id, data.id


def _write_ft2(path: Path, shape=(16, 32)) -> None:
    from nmrglue.fileio import pipe

    data = np.zeros(shape, dtype=np.float32)
    data[8, 16] = 10.0
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = shape[1]
    dic["FDSPECNUM"] = shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDF1T"] = shape[0]
    dic["FDF1SW"] = 6000.0
    dic["FDF1OBS"] = 600.0
    dic["FDF1CAR"] = 118.0
    dic["FDF1ORIG"] = 118.0 * 600.0
    dic["FDF2T"] = shape[1]
    dic["FDF2SW"] = 6000.0
    dic["FDF2OBS"] = 600.0
    dic["FDF2CAR"] = 4.7
    dic["FDF2ORIG"] = 4.7 * 600.0
    pipe.write(str(path), dic, data, overwrite=True)


def _write_ft3(path: Path, shape=(2, 3, 8)) -> None:
    from nmrglue.fileio import pipe

    data = np.zeros(shape, dtype=np.float32)
    data[0, 1, 2] = 100.0
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 3
    dic["FDPIPEFLAG"] = 1
    dic["FDSIZE"] = shape[2]
    dic["FDSPECNUM"] = shape[1]
    dic["FDF3SIZE"] = shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDF3QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    for index, prefix in enumerate(("FDF1", "FDF2", "FDF3")):
        dic[prefix + "T"] = shape[index]
        dic[prefix + "SW"] = 6000.0
        dic[prefix + "OBS"] = 600.0
        dic[prefix + "CAR"] = 4.7
        dic[prefix + "ORIG"] = 4.7 * 600.0
    pipe.write(str(path), dic, data, overwrite=True)


def test_context_bar_follows_selection(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, ws, exp_id, data_id = _manager_with_ws(tmp_path)
    monkeypatch.setattr(
        "gui.main_window.WorkspaceManager", lambda: _TempWorkspace(ws)
    )
    monkeypatch.setattr(
        "core.workspace.WorkspaceManager",
        lambda *a, **k: _TempWorkspace(ws),
    )
    window = MainWindow(manager=manager)
    window.project_tree.select_data(exp_id, data_id)
    text = window.context_bar.text()
    assert "demo" in text and "HSQC" in text
    assert data_id in text and "已导入" in text
    window.close()


def test_pipeline_buttons_gated_by_prerequisites(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.163-补14:前置步骤未完成(LOCKED)时,后续步骤(生成谱图/
    峰挑选)不提供运行/人工按钮,程序化运行入口也被拒绝。"""
    from gui.main_window import MainWindow
    from gui.pipeline_state import record_step_success

    manager, ws, exp_id, data_id = _manager_with_ws(tmp_path)
    monkeypatch.setattr(
        "gui.main_window.WorkspaceManager", lambda: _TempWorkspace(ws)
    )
    monkeypatch.setattr(
        "core.workspace.WorkspaceManager",
        lambda *a, **k: _TempWorkspace(ws),
    )
    window = MainWindow(manager=manager)
    pipeline = window.center_panel.pipeline
    pipeline.set_selection("data", exp_id, data_id)
    rows = pipeline._rows
    # 窗口未 show,用 isHidden 反映 setVisible 的显隐状态
    # fid 未生成:spectrum/peaks 全部 LOCKED → 无运行/人工按钮
    for sid in ("spectrum", "peaks"):
        assert rows[sid].manual_button.isHidden(), sid
        assert rows[sid].run_button.isHidden(), sid
    # 0.2.199-补29dm:fid 未自动处理(READY)时人工按钮隐藏
    assert rows["fid"].manual_button.isHidden()

    # 程序化运行入口同样被前置守卫拒绝(不进入 RUNNING/后端)
    messages: list[str] = []
    pipeline.log_message.connect(messages.append)
    pipeline._on_run_requested("peaks")
    assert any("前置步骤未完成" in m for m in messages)
    assert "RUNNING" not in rows["peaks"].status_label.text()

    def _ready(sid: str, product: Path) -> None:
        product.parent.mkdir(parents=True, exist_ok=True)
        product.write_bytes(b"x")
        manager.save()
        pipeline.refresh()
        # 0.2.199-补29dl(用户):峰挑选无人工脚本,人工按钮始终隐藏
        if sid == "peaks":
            assert rows[sid].manual_button.isHidden(), sid
        else:
            assert not rows[sid].manual_button.isHidden(), sid
        assert not rows[sid].run_button.isHidden(), sid

    # 生成 FID → spectrum READY;peaks 仍 LOCKED
    fid = manager.data_dir(exp_id, data_id, "process") / f"{data_id}.fid"
    fid.parent.mkdir(parents=True, exist_ok=True)
    fid.write_bytes(b"fid")
    manager.set_data_fid(exp_id, data_id, fid)
    record_step_success(manager, exp_id, data_id, "fid")
    manager.save()
    pipeline.refresh()
    # 0.2.199-补29dm:fid 自动处理成功后人工按钮出现(直接读 fid.com)
    assert not rows["fid"].manual_button.isHidden()
    assert not rows["spectrum"].manual_button.isHidden()
    assert not rows["spectrum"].run_button.isHidden()
    assert rows["peaks"].manual_button.isHidden()

    # 生成谱图 → peaks READY
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    spec = spectra / f"{data_id}.ft2"
    spec.write_bytes(b"ft2")
    manager.set_data_spectrum(exp_id, data_id, spec)
    record_step_success(manager, exp_id, data_id, "spectrum")
    _ready("peaks", spec)

    # 峰表 → peaks 完成(分析步骤已删除,流程止于峰挑选)
    peaks = manager.data_dir(exp_id, data_id, "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    peaks_list = peaks / f"{exp_id}-{data_id}.list"
    peaks_list.write_text("", encoding="utf-8")
    record_step_success(manager, exp_id, data_id, "peaks")
    pipeline.refresh()
    assert "全部步骤已完成" in pipeline.next_label.text()
    window.close()


def test_spectrum_panel_no_locator_bar(tmp_path: Path, qapp: QApplication) -> None:
    """谱图面板不再显示「在 Pipeline 中定位 / 数据摘要」条(用户反馈无用)。"""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    spectra = manager.data_dir(entry.id, data.id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / f"{entry.id}-{data.id}.ft2")
    manager.save()
    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data.id)
    assert not hasattr(panel, "context_summary")
    assert not hasattr(panel, "locate_button")
    panel.close()


def test_3d_viewer_state_memory(tmp_path: Path, qapp: QApplication) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("3D")
    data1 = manager.import_data(entry.id, "/fake/1")
    data2 = manager.import_data(entry.id, "/fake/2")
    spectra = manager.data_dir(entry.id, data1.id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft3(spectra / f"{entry.id}-{data1.id}.ft3")
    manager.save()
    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data1.id)
    panel._spectrum3d_panel.plane_combo.setCurrentIndex(2)
    panel._render_3d_view()
    assert panel._viewer3d_state.get((entry.id, data1.id)) == 2
    # 切走再切回 → 平面记忆恢复(0.2.133 仅 slice,无模式记忆)
    panel.set_context(entry.id, data2.id)
    panel.set_context(entry.id, data1.id)
    assert panel._spectrum3d_panel.plane_combo.currentIndex() == 2
    assert panel._spectrum3d_panel._mode == "slice"
    panel.close()
