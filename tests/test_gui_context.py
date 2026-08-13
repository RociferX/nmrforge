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


def test_spectrum_panel_params_summary(tmp_path: Path, qapp: QApplication) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    exp_id, data_id = entry.id, data.id
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    ft2 = spectra / f"{exp_id}-{data_id}.ft2"
    _write_ft2(ft2)
    run = manager.start_run(
        exp_id,
        workflow_ref="process",
        inputs={"data_id": data_id},
        params={"extract": True, "ext_lo": "11.0", "ext_hi": "6.0", "zero_fill": 2},
    )
    manager.finish_run(
        run.run_id, "success", outputs={"spectrum_path": str(ft2)}
    )
    manager.save()
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    assert "生成谱图" in panel.context_summary.text()
    assert "EXT" in panel.context_summary.text()
    assert "ZF 2" in panel.context_summary.text()
    assert not panel.locate_button.isHidden()
    panel.close()


def test_spectrum_panel_summary_historical(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    spectra = manager.data_dir(entry.id, data.id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / f"{entry.id}-{data.id}.ft2")
    manager.save()
    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data.id)
    assert "历史数据" in panel.context_summary.text()
    panel.close()


def test_locate_button_selects_data_in_pipeline(
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
    window.spectrum_panel._current_exp_id = exp_id
    window.spectrum_panel._current_data_id = data_id
    window.spectrum_panel.locate_button.click()
    assert (
        window.project_tree._data_id_of(window.project_tree.tree.currentItem())
        == data_id
    )
    assert window.center_panel.stack.currentIndex() == 3  # Pipeline 页
    window.close()


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
    panel._spectrum3d_panel.mode_combo.setCurrentIndex(1)
    panel._render_3d_view()
    assert panel._viewer3d_state.get(data1.id) == (2, 1)
    # 切走再切回 → 平面/投影记忆恢复
    panel.set_context(entry.id, data2.id)
    panel.set_context(entry.id, data1.id)
    assert panel._spectrum3d_panel.plane_combo.currentIndex() == 2
    assert panel._spectrum3d_panel.mode_combo.currentIndex() == 1
    panel.close()
