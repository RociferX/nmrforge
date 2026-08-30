"""峰表「可信度」列测试:来自 SMILE 优化逐峰可靠性,不进 .list。"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from core.peaks.peak_table import export_peaks_poky
from core.project import ProjectManager
from gui.spectrum_panel import SpectrumPanel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _manager_with_peaks(
    tmp_path: Path,
    list_text: str,
    reliability_peaks: list[dict] | None,
):
    """建项目 + 数据 + .list 峰文件 + 可选 smile_reliability JSON。"""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    exp_id, data_id = entry.id, data.id
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / f"{exp_id}-{data_id}.ft2").write_bytes(b"x")
    peaks = manager.data_dir(exp_id, data_id, "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / f"{exp_id}-{data_id}.list").write_text(list_text, encoding="utf-8")
    if reliability_peaks is not None:
        out = manager.data_dir(exp_id, data_id, "smile_optimized")
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{exp_id}-{data_id}_smile_reliability.json").write_text(
            json.dumps(
                {"schema": "smile_reliability_v3", "peaks": reliability_peaks},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    manager.save()
    return manager, exp_id, data_id


def test_peak_table_confidence_column_from_smile(
    tmp_path: Path, qapp: QApplication
) -> None:
    """匹配峰显示可信度,无匹配留空;列标题为「可信度」。"""
    rel = [
        {"shifts": {"H_shift": 8.2, "N_shift": 118.0}, "confidence": 88.5},
        {"shifts": {"H_shift": 7.5, "N_shift": 120.0}, "confidence": 55.0},
    ]
    manager, exp_id, data_id = _manager_with_peaks(
        tmp_path,
        "Assignment w1 w2 Data Height Volume\n"
        "G1 118.0 8.2 0 100.0 100.0\n"
        "A2 125.0 9.0 0 80.0 80.0\n",
        rel,
    )
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    panel._load_peaks(Path("fake.ft2"))
    headers = [
        panel.peak_table.horizontalHeaderItem(i).text()
        for i in range(panel.peak_table.columnCount())
    ]
    assert "可信度" in headers
    col = headers.index("可信度")
    values = [
        panel.peak_table.item(row, col).text()
        for row in range(panel.peak_table.rowCount())
    ]
    assert values[0] == "88.5"  # G1 与 SMILE 峰 8.2/118.0 匹配
    assert values[1] == ""      # A2 无匹配留空
    panel.close()


def test_confidence_not_written_to_list(
    tmp_path: Path, qapp: QApplication
) -> None:
    """可信度列不进入 .list(导出固定列)。"""
    rel = [{"shifts": {"H_shift": 8.2, "N_shift": 118.0}, "confidence": 88.5}]
    manager, exp_id, data_id = _manager_with_peaks(
        tmp_path,
        "Assignment w1 w2 Data Height Volume\n"
        "G1 118.0 8.2 0 100.0 100.0\n",
        rel,
    )
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    panel._load_peaks(Path("fake.ft2"))
    out = tmp_path / "out.list"
    export_peaks_poky(out, panel._table_peaks(), ndim=2)
    text = out.read_text(encoding="utf-8")
    assert "88.5" not in text
    assert "118.000" in text and "8.200" in text
    panel.close()


def test_no_smile_no_confidence_column(
    tmp_path: Path, qapp: QApplication
) -> None:
    """未做 SMILE 优化(无可靠性文件)时不显示可信度列。"""
    manager, exp_id, data_id = _manager_with_peaks(
        tmp_path,
        "Assignment w1 w2 Data Height Volume\n"
        "G1 118.0 8.2 0 100.0 100.0\n",
        None,
    )
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    panel._load_peaks(Path("fake.ft2"))
    headers = [
        panel.peak_table.horizontalHeaderItem(i).text()
        for i in range(panel.peak_table.columnCount())
    ]
    assert "可信度" not in headers
    panel.close()
