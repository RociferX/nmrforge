"""原始数据质量检查测试(导入后自动执行,GUI 侧)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.project import ProjectManager
from gui.raw_quality import check_raw_quality, format_quality_report


def test_raw_quality_reports_missing_fid(tmp_path: Path) -> None:
    """0.2.86:缺 ser/fid 时给出警告;维度/核/温度参数正常收集。"""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    raw = manager.data_dir(entry.id, data.id, "raw")
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "acqus").write_text(
        "##$NUC1= 1H\n##$TD= 1024\n##$TE= 2980\n",
        encoding="latin-1",
    )
    (raw / "acqu2s").write_text("##$NUC2= 15N\n", encoding="latin-1")
    report = check_raw_quality(manager, entry.id, data.id)
    assert not report["ok"]
    assert any("ser/fid" in issue for issue in report["issues"])
    assert report["info"]["维度"] == "2D"
    assert report["info"]["核"] == "1H-15N"
    assert "温度" in report["info"]
    text = format_quality_report(report)
    assert "警告" in text
