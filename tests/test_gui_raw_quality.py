"""Raw data quality check test (automatically executed after import, GUI side)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.project import ProjectManager
from gui.raw_quality import check_raw_quality, format_quality_report


def test_raw_quality_reports_missing_fid(tmp_path: Path) -> None:
    """0.2.86: A warning is given when ser/fid is missing; dimension /nuclear/temperature parameter
    is collected normally."""
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
    assert report["info"]["Dimensions"] == "2D"
    assert report["info"]["nuclear"] == "1H-15N"
    assert report["info"]["temperature"] == "298.0 K"  # TE=2980 → 298.0 K(Kelvin).
    text = format_quality_report(report)
    assert "warn" in text


def test_raw_quality_segmented_checks_first_segment(tmp_path: Path) -> None:
    """0.2.199: Segmented collection is evaluated based on the first segment, and there will be no
    false positives due to the lack of acqus/ser in the container root directory."""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("cc")
    data = manager.import_data(entry.id, "/fake/cc")
    raw = manager.data_dir(entry.id, data.id, "raw")
    seg0 = raw / "segments" / "01"
    seg0.mkdir(parents=True)
    (seg0 / "acqus").write_text(
        "##$NUC1= 1H\n##$TD= 908\n", encoding="latin-1"
    )
    (seg0 / "ser").write_bytes(b"x")
    data.segments = [str(seg0)]
    manager.save()
    report = check_raw_quality(manager, entry.id, data.id)
    assert not any("acqus" in issue for issue in report["issues"])
    assert report["info"].get("segmented")
    assert report["info"]["Dimensions"] == "1D"
