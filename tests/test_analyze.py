"""HSQC CSP 分析测试(0.2.199-补29er):匹配/Δδ/输出文件/异常。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from core.peaks.peak_table import export_peaks_poky
from core.project import ProjectManager
from workflow.analyze import AnalyzeError, analyze


def _write_ft2(path: Path, data: np.ndarray) -> None:
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


def _spec_with_peaks(shape: tuple[int, int], centers: list[tuple[float, float]]) -> np.ndarray:
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    arr = np.zeros(shape)
    for y, x in centers:
        arr += np.exp(-(((yy - y) ** 2) / (2 * 1.2**2) + ((xx - x) ** 2) / (2 * 1.2**2)))
    return gaussian_filter(arr, sigma=0.6)


def _setup_project(
    tmp_path: Path,
    cur_centers: list[tuple[float, float]],
    ref_centers: list[tuple[float, float]],
    labels: list[str] | None = None,
):
    shape = (128, 256)
    cur_spec = tmp_path / "cur.ft2"
    ref_spec = tmp_path / "ref.ft2"
    _write_ft2(cur_spec, _spec_with_peaks(shape, cur_centers))
    _write_ft2(ref_spec, _spec_with_peaks(shape, ref_centers))
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    cur = manager.import_data(entry.id, "/data/cur")
    ref = manager.import_data(entry.id, "/data/ref")
    manager.set_data_spectrum(entry.id, cur.id, str(cur_spec))
    manager.set_data_spectrum(entry.id, ref.id, str(ref_spec))
    # 峰表(N_shift/H_shift 按存储轴:axis0=N, axis1=H;ppm = ORIG/OBS 公式)
    def _ppm(axis_size: int, idx: float) -> float:
        return 1000 / 600 + (axis_size - 1 - idx) * 6000 / (axis_size * 600)

    def _peaks(centers: list[tuple[float, float]]) -> list[dict]:
        rows = []
        for i, (y, x) in enumerate(centers):
            row = {
                "N_shift": _ppm(shape[0], y),
                "H_shift": _ppm(shape[1], x),
                "Intensity": 1.0,
            }
            if labels is not None and i < len(labels):
                row["label"] = labels[i]
            rows.append(row)
        return rows

    cur_peaks = _peaks(cur_centers)
    ref_peaks = _peaks(ref_centers)
    cur_p = manager.data_dir(entry.id, cur.id, "peaks") / f"{entry.id}-{cur.id}.list"
    ref_p = manager.data_dir(entry.id, ref.id, "peaks") / f"{entry.id}-{ref.id}.list"
    export_peaks_poky(cur_p, cur_peaks)
    export_peaks_poky(ref_p, ref_peaks)
    return manager, entry.id, cur.id, ref.id


def test_csp_analysis_outputs(tmp_path: Path) -> None:
    """CSP 分析:输出数据文件 + 两张 SVG,Δδ 计算正确。"""
    cur_centers = [(40.0, 100.0), (60.0, 150.0)]
    ref_centers = [(40.0, 100.0), (60.0, 150.0)]
    # 扰动:peak0 的 H 移 1 点(0.0391 ppm)、N 移 1 点(0.0781 ppm),在匹配容差内
    cur_centers = [(41.0, 101.0), (60.0, 150.0)]
    manager, exp_id, cur_id, ref_id = _setup_project(
        tmp_path, cur_centers, ref_centers
    )
    result = analyze(
        manager, exp_id, cur_id, reference_data_id=ref_id
    )
    assert result["status"] == "success"
    out_dir = Path(result["analysis_dir"])
    csv_path = out_dir / "csp_data.csv"
    figures_dir = manager.data_dir(exp_id, cur_id, "figures")
    plot_path = figures_dir / "csp_plot.svg"
    overlay_path = figures_dir / "overlay_spectra.svg"
    assert csv_path.is_file()
    assert plot_path.is_file()
    assert overlay_path.is_file()
    # 0.2.199-补29es:参考数据对应位置有软链接(共享 CSP 产物;平台不支持
    # 软链接时跳过链接断言)
    ref_figures = manager.data_dir(exp_id, ref_id, "figures")
    ref_out = Path(result["analysis_dir"]).parent / ref_id
    for _link in (
        ref_figures / "csp_plot.svg",
        ref_figures / "overlay_spectra.svg",
        ref_out / "csp_data.csv",
    ):
        if _link.is_symlink():
            assert _link.resolve().is_file()
    assert plot_path.read_text(encoding="utf-8").lstrip().startswith("<?xml")
    assert overlay_path.read_text(encoding="utf-8").lstrip().startswith("<?xml")
    # Δδ 校验:peak0 dH = 1*6000/(256*600)=0.0390625,dN = 1*6000/(128*600)=0.078125
    import csv

    with csv_path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2
    peak0 = next(
        r for r in rows if abs(abs(float(r["dH"])) - 0.0391) < 0.005
    )
    expected = float(np.sqrt(0.0390625**2 + (0.2 * 0.078125) ** 2))
    assert abs(float(peak0["dCSP"]) - expected) < 0.005


def test_csp_requires_reference(tmp_path: Path) -> None:
    """未选参考谱 → pending(不阻断自动/批量路径)。"""
    manager, exp_id, cur_id, _ref = _setup_project(
        tmp_path, [(40.0, 100.0)], [(40.0, 100.0)]
    )
    result = analyze(manager, exp_id, cur_id)
    assert result.get("status") == "pending"


def test_csp_matches_by_assignment(tmp_path: Path) -> None:
    """带 Assignment 的峰按指认精确匹配(顺序不同也匹配)。"""
    labels = ["G1", "A45"]
    cur_centers = [(45.0, 110.0), (42.0, 103.0)]  # 顺序打乱 + 扰动
    ref_centers = [(40.0, 100.0), (50.0, 120.0)]
    manager, exp_id, cur_id, ref_id = _setup_project(
        tmp_path, cur_centers, ref_centers, labels=labels
    )
    result = analyze(manager, exp_id, cur_id, reference_data_id=ref_id)
    assert result["status"] == "success"
    import csv

    with Path(result["csp_data"]).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assignments = {r["Assignment"] for r in rows}
    assert "G1" in assignments and "A45" in assignments


def test_csp_fails_without_peaks(tmp_path: Path) -> None:
    """参考数据无峰表 → AnalyzeError。"""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    cur = manager.import_data(entry.id, "/data/cur")
    ref = manager.import_data(entry.id, "/data/ref")
    cur_spec = tmp_path / "cur.ft2"
    ref_spec = tmp_path / "ref.ft2"
    _write_ft2(cur_spec, _spec_with_peaks((64, 128), [(30, 60)]))
    _write_ft2(ref_spec, _spec_with_peaks((64, 128), [(30, 60)]))
    manager.set_data_spectrum(entry.id, cur.id, str(cur_spec))
    manager.set_data_spectrum(entry.id, ref.id, str(ref_spec))
    cur_p = manager.data_dir(entry.id, cur.id, "peaks") / f"{entry.id}-{cur.id}.list"
    export_peaks_poky(cur_p, [{"N_shift": 110.0, "H_shift": 8.0, "Intensity": 1.0}])
    with pytest.raises(AnalyzeError):
        analyze(manager, entry.id, cur.id, reference_data_id=ref.id)
