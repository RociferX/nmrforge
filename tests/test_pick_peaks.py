"""峰挑选测试:合成 2D 谱检测 ≥1 峰 + CSV 落盘 + WorkflowRun 登记。"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from core.project import ProjectManager
from workflow.pick_peaks import PickPeaksError, pick_peaks


def _write_ft2(path: Path, data: np.ndarray) -> None:
    """用 nmrglue 写合成 NMRPipe 2D 谱(带 ORIG/SW/OBS/CAR 头部)。"""
    from nmrglue.fileio import pipe

    dic = {k: "0" for k in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = data.shape[1]
    dic["FDSPECNUM"] = data.shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    for prefix in ("FDF1", "FDF2"):
        dic[prefix + "SW"] = "6000.0"
        dic[prefix + "OBS"] = "600.0"
        dic[prefix + "CAR"] = "4.7"
        dic[prefix + "ORIG"] = "1000.0"
    pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)


def _manager_with_spectrum(
    tmp_path: Path, spec_path: Path
) -> tuple[ProjectManager, str, str]:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")
    manager.set_data_spectrum(entry.id, data.id, str(spec_path))
    return manager, entry.id, data.id


def test_pick_peaks_detects_and_writes(tmp_path: Path) -> None:
    spec = np.zeros((64, 128))
    spec[20, 40] = 500.0
    spec[25, 90] = 350.0
    spec = gaussian_filter(spec, sigma=1.5)
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)

    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    result = pick_peaks(manager, exp_id, data_id)

    assert result["status"] == "success"
    assert result["peak_count"] >= 1
    peak_path = Path(result["peak_path"])
    assert peak_path.is_file()
    assert peak_path.name == f"{exp_id}-{data_id}.csv"
    assert peak_path.parent == manager.data_dir(exp_id, data_id, "peaks")

    rows = list(csv.DictReader(peak_path.open(encoding="utf-8")))
    assert rows[0]["Peak_ID"] == "1"  # 数字 Peak_ID(G2B-005)
    assert "H_shift" in rows[0] and "N_shift" in rows[0]
    assert float(rows[0]["SN"]) >= 3.0

    runs = [r for r in manager.project.workflow_runs if r.workflow_ref == "pick_peaks"]
    assert len(runs) == 1
    assert runs[0].status == "success"
    assert runs[0].inputs["spectrum_path"] == str(ft2)
    assert runs[0].outputs["peak_path"] == result["peak_path"]


def test_pick_peaks_missing_spectrum_fails(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")

    with pytest.raises(PickPeaksError, match="谱图缺失"):
        pick_peaks(manager, entry.id, data.id)

    runs = [r for r in manager.project.workflow_runs if r.workflow_ref == "pick_peaks"]
    assert len(runs) == 1
    assert runs[0].status == "failed"


def test_pick_peaks_annotates_reliability(tmp_path: Path) -> None:
    """0.2.162-补4:检测到 smile_reliability 文件时峰列表自动注释可靠性列。"""
    import json

    from nmrglue.fileio import pipe

    from workflow.pick_peaks import _axes_ppm

    spec = np.zeros((64, 128))
    spec[20, 40] = 500.0
    spec = gaussian_filter(spec, sigma=1.5)
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)

    _dic, data = pipe.read(str(ft2))
    axes = _axes_ppm(dict(_dic), np.asarray(data))
    rel_dir = manager.data_dir(exp_id, data_id, "smile_optimized")
    rel_dir.mkdir(parents=True, exist_ok=True)
    (rel_dir / f"{exp_id}-{data_id}_smile_reliability.json").write_text(
        json.dumps(
            {
                "schema": "smile_reliability_v1",
                "n_combos": 25,
                "peaks": [
                    {
                        "position_pts": [20.0, 40.0],
                        "shifts": {
                            "N_shift": float(axes[0][20]),
                            "H_shift": float(axes[1][40]),
                        },
                        "support": 25,
                        "n_combos": 25,
                        "confidence": 100.0,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = pick_peaks(manager, exp_id, data_id)
    rows = list(csv.DictReader(Path(result["peak_path"]).open(encoding="utf-8")))
    assert rows and rows[0]["Reliability(%)"] == "100.0"
    assert any("可靠性注释" in line for line in result["logs"])
    runs = [
        r
        for r in manager.project.workflow_runs
        if r.workflow_ref == "pick_peaks"
    ]
    assert "可靠性注释" in runs[-1].message
