"""Poky 导入替换峰文件测试(峰表展示/隐藏 + 替换落盘)。"""

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


def test_import_poky_replaces_peak_file(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Poky .list 导入直接替换峰 CSV 文件并登记 manual_peaks 运行。"""
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
    panel._on_import_poky()

    csv_path = (
        manager.data_dir(exp_id, data_id, "peaks") / f"{exp_id}-{data_id}.csv"
    )
    content = csv_path.read_text(encoding="utf-8")
    assert "OLD" not in content
    assert "G1" in content and "A2" in content
    assert any("替换峰文件" in text for text in shown)
    runs = [
        r
        for r in manager.project.workflow_runs
        if r.workflow_ref == "manual_peaks"
    ]
    assert len(runs) == 1 and runs[0].status == "success"
    panel.close()
