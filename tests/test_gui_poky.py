"""Poky 峰表关联替换测试(导入替换关联,保存写 .list)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from gui.spectrum_panel import SpectrumPanel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _manager_with_peaks(tmp_path: Path):
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    exp_id, data_id = entry.id, data.id
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / f"{exp_id}-{data_id}.ft2").write_bytes(b"x")
    peaks = manager.data_dir(exp_id, data_id, "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / f"{exp_id}-{data_id}.csv").write_text(
        "Peak_ID,H_shift,N_shift,Intensity,SN,label\n"
        "1,8.0,115.0,100,20,OLD\n",
        encoding="utf-8",
    )
    manager.save()
    return manager, exp_id, data_id


def test_import_poky_replaces_association_then_save_writes_list(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Poky 导入只替换峰表关联(不覆盖文件);保存时写 .list 峰文件。"""
    manager, exp_id, data_id = _manager_with_peaks(tmp_path)
    list_file = tmp_path / "new.list"
    list_file.write_text(
        "Assignment w1 w2 Data Height Volume\n"
        "G1 118.0 8.2 0 100.0 100.0\n"
        "A2 120.0 7.5 0 80.0 80.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "PyQt6.QtWidgets.QFileDialog.getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(list_file), "")),
    )
    shown: list[str] = []
    monkeypatch.setattr(
        "gui.spectrum_panel.InfoDialog.show_info",
        staticmethod(lambda parent, title, text: shown.append(text)),
    )
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)

    # 导入:替换关联,不覆盖旧 CSV
    panel._on_import_poky()
    old_csv = (
        manager.data_dir(exp_id, data_id, "peaks") / f"{exp_id}-{data_id}.csv"
    )
    assert "OLD" in old_csv.read_text(encoding="utf-8")
    assert any("替换当前峰表关联" in text for text in shown)
    assert panel.peak_table.rowCount() == 2

    # 保存:写 Poky .list 峰文件并登记 manual_peaks 运行
    panel._on_save_peaks()
    list_path = (
        manager.data_dir(exp_id, data_id, "peaks") / f"{exp_id}-{data_id}.list"
    )
    assert list_path.is_file()
    content = list_path.read_text(encoding="utf-8")
    assert "G1" in content and "A2" in content and "OLD" not in content
    runs = [
        r
        for r in manager.project.workflow_runs
        if r.workflow_ref == "manual_peaks"
    ]
    assert len(runs) == 1 and runs[0].status == "success"
    assert runs[0].outputs["peaks"] == str(list_path)
    panel.close()
