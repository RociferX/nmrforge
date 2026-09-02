"""峰挑选测试:合成谱检测 + Poky .list 落盘 + WorkflowRun 登记。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from core.peaks.peak_table import export_peaks_poky, import_peaks_poky
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
    assert peak_path.name == f"{exp_id}-{data_id}.list"
    assert peak_path.parent == manager.data_dir(exp_id, data_id, "peaks")

    rows = import_peaks_poky(peak_path)
    assert len(rows) >= 1
    assert "H_shift" in rows[0] and "N_shift" in rows[0]
    assert float(rows[0]["Intensity"]) > 0

    runs = [r for r in manager.project.workflow_runs if r.workflow_ref == "pick_peaks"]
    assert len(runs) == 1
    assert runs[0].status == "success"
    assert runs[0].inputs["spectrum_path"] == str(ft2)
    assert runs[0].outputs["peak_path"] == result["peak_path"]



def test_permutation_to_logical_maps_storage_to_logical() -> None:
    """0.2.199-补29df:storage→逻辑轴置换与 viewer 同源。"""
    from workflow.pick_peaks import _permutation_to_logical

    # storage (15N, 1H, 13C) 但逻辑 (1H, 15N, 13C):F1=存储轴1、F2=存储轴0
    perm = _permutation_to_logical(["15N", "1H", "13C"], ["1H", "15N", "13C"])
    assert perm == [1, 0, 2]
    logical_axes = [0, 0, 0]
    for spos, lpos in enumerate(perm):
        logical_axes[lpos] = spos
    assert logical_axes == [1, 0, 2]  # F1←轴1、F2←轴0、F3←轴2


def test_pick_peaks_subpixel_shift_interpolated(tmp_path: Path) -> None:
    """亚像素峰位:写 .list 的 ppm 为插值(非整数像素值,0.2.199-补29eo)。"""
    shape = (64, 128)
    yy, xx = np.mgrid[0:64, 0:128]
    rng = np.random.default_rng(4)
    real = np.exp(
        -(((yy - 20.4) ** 2) / (2 * 1.2 ** 2) + ((xx - 40.7) ** 2) / (2 * 1.2 ** 2))
    )
    real = real + rng.normal(0, 0.01, size=shape)
    ft2 = tmp_path / "sub.ft2"
    _write_ft2(ft2, real)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    result = pick_peaks(manager, exp_id, data_id)
    rows = import_peaks_poky(Path(result["peak_path"]))
    top = max(rows, key=lambda r: float(r["Intensity"]))
    h = float(top["H_shift"])
    n = float(top["N_shift"])
    # _write_ft2 头部 ORIG=1000/600,SW=6000:
    #   ppm_i = 1000/600 + (size-1-i)*6000/(size*600)
    exp_h = 1000 / 600 + (127 - 40.7) * 6000 / (128 * 600)
    exp_n = 1000 / 600 + (63 - 20.4) * 6000 / (64 * 600)
    assert abs(h - exp_h) < 0.02
    assert abs(n - exp_n) < 0.03
    # 确认不是四舍五入到最近整数像素(确实做了插值)
    int_h = 1000 / 600 + (127 - 41) * 6000 / (128 * 600)
    assert abs(h - int_h) > 0.01


def test_pick_peaks_missing_spectrum_fails(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")

    with pytest.raises(PickPeaksError, match="谱图缺失"):
        pick_peaks(manager, entry.id, data.id)

    runs = [r for r in manager.project.workflow_runs if r.workflow_ref == "pick_peaks"]
    assert len(runs) == 1
    assert runs[0].status == "failed"


def test_pick_peaks_writes_poky_list(tmp_path: Path) -> None:
    """0.2.199-补29ar:选峰输出 Poky .list(峰文件即 .list,无 CSV/可靠性列)。"""
    spec = np.zeros((64, 128))
    spec[20, 40] = 500.0
    spec = gaussian_filter(spec, sigma=1.5)
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)

    result = pick_peaks(manager, exp_id, data_id)
    path = Path(result["peak_path"])
    assert path.suffix == ".list"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "Assignment w1 w2 Data Height Volume"
    assert len(lines) >= 2
    assert "Reliability" not in "\n".join(lines)
    assert "阈值" in result["logs"][0]
    assert "15.0σ" in result["logs"][0]  # 默认 15σ(0.2.199-补29cm)


def _write_metadata(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    name: str,
    confidence: float = 1.0,
) -> None:
    """写数据 metadata(experiment_type.name+confidence),驱动峰符号模式。"""
    import json

    path = manager.data_metadata_path(exp_id, data_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 0.2.199-补29fa(修复):真实 metadata 结构为 dataset.experiment_type
    # (此前测试写顶层 experiment_type,掩盖了选峰读不到类型的 bug)
    path.write_text(
        json.dumps(
            {
                "dataset": {
                    "experiment_type": {
                        "name": name,
                        "confidence": confidence,
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _write_ft3_ordered(
    path: Path, data: np.ndarray, fddimorder: list[float]
) -> None:
    """写带 FDDIMORDER 的 3D 流文件(与 test_viewer3d 同构)。"""
    from nmrglue.fileio import pipe

    nz, ny, nx = data.shape
    dic = {k: "0" for k in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 3
    dic["FDPIPEFLAG"] = 1
    dic["FDSIZE"] = nx
    dic["FDSPECNUM"] = ny
    dic["FDF3SIZE"] = nz
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDF3QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDDIMORDER"] = [float(v) for v in fddimorder] + [4.0]
    for i, v in enumerate(fddimorder, start=1):
        dic[f"FDDIMORDER{i}"] = float(v)
    blocks = {
        1: ("15N", nz, 2189.0, 60.8, 118.0, 100.0 * 60.8),
        2: ("1H", nx, 3000.0, 600.0, 4.7, 6.0 * 600.0),
        3: ("13C", ny, 11300.0, 150.9, 45.0, 40.0 * 150.9),
    }
    for dim in (1, 2, 3):
        prefix = f"FDF{dim}"
        lab, size, sw, obs, car, orig = blocks[dim]
        dic[prefix + "T"] = size
        dic[prefix + "SW"] = sw
        dic[prefix + "OBS"] = obs
        dic[prefix + "CAR"] = car
        dic[prefix + "ORIG"] = orig
        dic[prefix + "LABEL"] = lab
    pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)


def _spectrum_with_peaks(
    shape: tuple[int, ...], peaks: list[tuple[tuple[int, ...], float]]
) -> np.ndarray:
    """峰值点叠加高斯核的谱(正值/负值峰均可);带 σ≈1 噪声底,使全局
    噪声估计走 robust MAD(阈值与峰强相对关系符合真实谱,0.2.199-补29cm)。"""
    rng = np.random.default_rng(20260829)
    spec = rng.normal(0, 1.0, shape)
    for pos, height in peaks:
        spec[pos] += height
    return gaussian_filter(spec, sigma=1.5)


def _read_rows(path: Path, nuclei=None) -> list[dict]:
    # 0.2.199-补29dk:3D .list 按外部约定 N,C,H 写列,读取需传每 F 轴核名
    return import_peaks_poky(path, nuclei=nuclei)



def test_experiment_type_name_reads_dataset_and_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补29fa:真实 metadata 的 dataset.experiment_type 优先,顶层旧结构兼容。"""
    import json

    from workflow.pick_peaks import _experiment_type_name

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")
    _write_metadata(manager, entry.id, data.id, "HNCACB")
    assert _experiment_type_name(manager, entry.id, data.id) == "HNCACB"

    path = manager.data_metadata_path(entry.id, data.id)
    path.write_text(
        json.dumps({"experiment_type": {"name": "HNCACB", "confidence": 1.0}}),
        encoding="utf-8",
    )
    assert _experiment_type_name(manager, entry.id, data.id) == "HNCACB"

    path.write_text(json.dumps({"dataset": {}}), encoding="utf-8")
    assert _experiment_type_name(manager, entry.id, data.id) == ""


def test_pick_peaks_uniform_type_keeps_dominant_sign_only(tmp_path: Path) -> None:
    """uniform(单符号)实验:只保留主符号峰,少数反号峰视为伪峰剔除。"""
    spec = _spectrum_with_peaks(
        (64, 128),
        [
            ((20, 40), 500.0),
            ((25, 90), 350.0),
            ((40, 60), 280.0),
            ((10, 100), -300.0),
        ],
    )
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    _write_metadata(manager, exp_id, data_id, "HSQC")  # peak_sign: uniform

    result = pick_peaks(manager, exp_id, data_id)
    rows = _read_rows(Path(result["peak_path"]))
    assert len(rows) == 3
    assert all(float(r["Intensity"]) > 0 for r in rows)
    assert "仅主符号峰" in result["logs"][0]


def test_pick_peaks_uniform_type_negative_dominant(tmp_path: Path) -> None:
    """uniform 实验主符号为负时,同样只选主符号(负峰)。"""
    spec = _spectrum_with_peaks(
        (64, 128),
        [
            ((20, 40), -500.0),
            ((25, 90), -350.0),
            ((40, 60), -280.0),
            ((10, 100), 300.0),
        ],
    )
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    _write_metadata(manager, exp_id, data_id, "HSQC")

    result = pick_peaks(manager, exp_id, data_id)
    rows = _read_rows(Path(result["peak_path"]))
    assert len(rows) == 3
    assert all(float(r["Intensity"]) < 0 for r in rows)



def test_pick_peaks_spectrum_evidence_backfill(tmp_path: Path) -> None:
    """0.2.199-补29fc:低置信类型但谱面正负数量+强度占比都高 → 按 mixed 正负都选。"""
    spec = _spectrum_with_peaks(
        (64, 128),
        [
            ((20, 40), 600.0),
            ((25, 90), 500.0),
            ((40, 60), 400.0),
            ((10, 100), 300.0),
            ((15, 50), -600.0),
            ((30, 70), -500.0),
            ((45, 20), -400.0),
        ],
    )
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    _write_metadata(manager, exp_id, data_id, "HSQC", confidence=0.3)  # 低置信

    result = pick_peaks(manager, exp_id, data_id)
    rows = _read_rows(Path(result["peak_path"]))
    signs = {float(r["Intensity"]) > 0 for r in rows}
    assert signs == {True, False}  # 正负都选
    assert any("谱面回补" in log for log in result["logs"])


def test_pick_peaks_spectrum_evidence_keeps_dominant_when_mostly_one_sign(
    tmp_path: Path,
) -> None:
    """0.2.199-补29fc:低置信但负峰只是零星伪峰 → 仍 dominant 只留主符号。"""
    spec = _spectrum_with_peaks(
        (64, 128),
        [
            ((20, 40), 600.0),
            ((25, 90), 500.0),
            ((40, 60), 400.0),
            ((10, 100), 300.0),
            ((15, 50), -200.0),  # 单个零星负峰
        ],
    )
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    _write_metadata(manager, exp_id, data_id, "HSQC", confidence=0.3)

    result = pick_peaks(manager, exp_id, data_id)
    rows = _read_rows(Path(result["peak_path"]))
    assert all(float(r["Intensity"]) > 0 for r in rows)


def test_pick_peaks_spectrum_evidence_respects_confident_uniform(
    tmp_path: Path,
) -> None:
    """0.2.199-补29fc:高置信 uniform 模板(HSQC 0.9+)即使谱面平衡也尊重模板 dominant。"""
    spec = _spectrum_with_peaks(
        (64, 128),
        [
            ((20, 40), 600.0),
            ((25, 90), 500.0),
            ((40, 60), 400.0),
            ((10, 100), 300.0),
            ((15, 50), -600.0),
            ((30, 70), -500.0),
            ((45, 20), -400.0),
        ],
    )
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    _write_metadata(manager, exp_id, data_id, "HSQC", confidence=0.95)

    result = pick_peaks(manager, exp_id, data_id)
    rows = _read_rows(Path(result["peak_path"]))
    assert all(float(r["Intensity"]) > 0 for r in rows)
    assert not any("谱面回补" in log for log in result["logs"])


def test_pick_peaks_spectrum_evidence_rejects_contamination(
    tmp_path: Path,
) -> None:
    """0.2.199-补29fc-修:少数符号被单个极强峰主导(疑似污染)不触发 mixed。"""
    spec = _spectrum_with_peaks(
        (64, 128),
        [
            ((20, 40), 500.0),
            ((25, 90), 450.0),
            ((40, 60), 400.0),
            ((10, 100), 350.0),
            ((15, 50), -1800.0),  # 单个极强负峰(污染)
            ((30, 70), -260.0),
            ((45, 20), -240.0),
        ],
    )
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    _write_metadata(manager, exp_id, data_id, "HSQC", confidence=0.3)

    result = pick_peaks(manager, exp_id, data_id)
    rows = _read_rows(Path(result["peak_path"]))
    assert all(float(r["Intensity"]) > 0 for r in rows)
    assert not any("谱面回补" in log for log in result["logs"])


def test_pick_peaks_mixed_type_picks_both_signs(tmp_path: Path) -> None:
    """mixed 实验(如 HNCACB 13Cα/13Cβ 反相):正负峰都选。"""
    spec = _spectrum_with_peaks(
        (64, 128),
        [
            ((20, 40), 500.0),
            ((25, 90), 350.0),
            ((10, 100), -300.0),
            ((45, 20), -280.0),
        ],
    )
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    _write_metadata(manager, exp_id, data_id, "HNCACB")  # peak_sign: mixed

    result = pick_peaks(manager, exp_id, data_id)
    rows = _read_rows(Path(result["peak_path"]))
    assert len(rows) == 4
    signs = {float(r["Intensity"]) > 0 for r in rows}
    assert signs == {True, False}
    assert "正负峰都选" in result["logs"][0]


def test_pick_peaks_ft3_shifts_follow_logical_axes(tmp_path: Path) -> None:
    """3D ORDER 2 3 1:F1/F2/F3_shift 按逻辑维取对应数据轴 ppm(0.2.199-补29ap 修)。"""
    data = np.zeros((16, 16, 16))  # (FDF3SIZE=15N, FDSPECNUM=13C, FDSIZE=1H)
    data[8, 5, 8] = 500.0  # F1=8 避开上下边缘(补29bf 轴峰排除 5 点)
    data = gaussian_filter(data, sigma=1.0)
    ft3 = tmp_path / "out.ft3"
    _write_ft3_ordered(ft3, data, [2.0, 3.0, 1.0])
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft3)

    result = pick_peaks(manager, exp_id, data_id)
    rows = _read_rows(Path(result["peak_path"]), nuclei=["15N", "1H", "13C"])
    assert len(rows) >= 1
    row = rows[0]
    # 逻辑维:F1=15N(FDF1)、F2=1H(FDF2)、F3=13C(FDF3)
    f1 = 100.0 + (16 - 1 - 8) * 2189.0 / (16 * 60.8)
    f2 = 6.0 + (16 - 1 - 8) * 3000.0 / (16 * 600.0)
    f3 = 40.0 + (16 - 1 - 5) * 11300.0 / (16 * 150.9)
    assert abs(float(row["F1_shift"]) - f1) < 0.05
    assert abs(float(row["F2_shift"]) - f2) < 0.05
    assert abs(float(row["F3_shift"]) - f3) < 0.05


def test_pick_peaks_flat_plateau_not_picked(tmp_path: Path) -> None:
    """平坦基线不作为峰(严格局部极大 + 选峰 6σ,0.2.199-补29aq/ar 修)。"""
    spec = np.full((64, 128), 100.0)
    spec[20, 40] = 500.0
    spec[25, 90] = 500.0
    spec = gaussian_filter(spec, sigma=1.0)
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)

    result = pick_peaks(manager, exp_id, data_id)
    rows = _read_rows(Path(result["peak_path"]))
    assert len(rows) == 2


def test_pick_peaks_sigma_multiplier_param(tmp_path: Path) -> None:
    """sigma_multiplier 参数可调阈值:更高阈值选出更少峰(0.2.199-补29ar)。"""
    rng = np.random.default_rng(3)
    spec = rng.normal(0, 1.0, (64, 128))
    spec[20, 40] += 30.0
    spec[25, 90] += 12.0
    spec[45, 60] += 6.0
    spec = gaussian_filter(spec, sigma=1.0)
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)

    low = pick_peaks(manager, exp_id, data_id, sigma_multiplier=4.0)
    high = pick_peaks(manager, exp_id, data_id, sigma_multiplier=8.0)
    assert high["peak_count"] <= low["peak_count"]
    assert "4.0σ" in low["logs"][0]
    assert "8.0σ" in high["logs"][0]



def test_pick_peaks_excludes_axial_edges(tmp_path: Path) -> None:
    # 0.2.199-补29at:上下边缘轴峰(横条)不选,谱内峰保留
    # (带 σ≈1 噪声底,15σ 默认阈值下测试意图不变,0.2.199-补29cm)
    rng = np.random.default_rng(20260829)
    spec = rng.normal(0, 1.0, (64, 128))
    spec[0, 60] += 800.0  # 顶部轴峰(横条)
    spec[63, 60] += 700.0  # 底部轴峰(横条)
    spec[20, 40] += 500.0
    spec[40, 90] += 450.0
    spec = gaussian_filter(spec, sigma=1.0)
    ft2 = tmp_path / 'out.ft2'
    _write_ft2(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)

    result = pick_peaks(manager, exp_id, data_id)
    rows = _read_rows(Path(result['peak_path']))
    assert len(rows) == 2  # 两个谱内峰,轴峰被排除


def test_infer_nucleus_obs_covers_common_spectrometers() -> None:
    """0.2.199-补29dh:OBS 推断支持各场强与 15N/13C(旧实现除反+只认 600 MHz)。"""
    from workflow.pick_peaks import _infer_nucleus_obs as infer

    assert infer(500.13) == "1H"
    assert infer(700.13) == "1H"
    assert infer(800.3) == "1H"
    assert infer(1200.57) == "1H"
    assert infer(50.68) == "15N"  # 500 MHz 15N
    assert infer(81.1) == "15N"  # 800 MHz 15N
    assert infer(125.76) == "13C"  # 500 MHz 13C
    assert infer(201.2) == "13C"  # 800 MHz 13C
    assert infer(0.0) == ""
    assert infer(-1.0) == ""


def test_parse_nmrpipe_label_hn_alias() -> None:
    """0.2.199-补29dh:真实 NMRPipe LABEL 'HN' 解析为 1H(30.ft3/d_011.ft3 实测)。"""
    from workflow.pick_peaks import _parse_nmrpipe_label as parse

    assert parse("HN") == "1H"
    assert parse("15N") == "15N"
    assert parse("13C") == "13C"
    assert parse("N15") == "15N"
    assert parse("H1") == "1H"
    assert parse("C13") == "13C"
    assert parse("15Nx") == "15N"
    assert parse("") == ""


def _write_metadata_dims(
    manager: ProjectManager, exp_id: str, data_id: str, dims: list[dict]
) -> None:
    """写带 dataset.dimensions 的 metadata(驱动选峰 metadata 核)。"""
    import json

    path = manager.data_metadata_path(exp_id, data_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"experiment_type": {"name": "HNCA"},
                    "dataset": {"dimensions": dims}}),
        encoding="utf-8",
    )


def test_pick_peaks_ft3_header_order_wins_over_metadata(
    tmp_path: Path,
) -> None:
    """0.2.199-补29dh:文件头(FDDIMORDER+LABEL)与 metadata 冲突时,文件头优先。

    复现真实 HNCA(Bruker 采集序 metadata F1=13C/F2=15N/F3=1H=CNH vs
    NMRPipe .ft3 头部 F1=15N/F2=1H/F3=13C=NHC):峰表必须按 NHC 写值。
    """
    data = np.zeros((16, 16, 16))
    data[8, 5, 8] = 500.0  # 逻辑 F1(N)=8、F2(H)=8、F3(C)=5
    data = gaussian_filter(data, sigma=1.0)
    ft3 = tmp_path / "out.ft3"
    _write_ft3_ordered(ft3, data, [2.0, 3.0, 1.0])
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft3)
    _write_metadata_dims(
        manager,
        exp_id,
        data_id,
        [
            {"logical_axis": "F1", "sf": 150.9, "nucleus": "13C"},
            {"logical_axis": "F2", "sf": 60.8, "nucleus": "15N"},
            {"logical_axis": "F3", "sf": 600.13, "nucleus": "1H"},
        ],
    )

    result = pick_peaks(manager, exp_id, data_id)
    row = _read_rows(Path(result["peak_path"]), nuclei=["15N", "1H", "13C"])[0]
    n_ppm = 100.0 + (16 - 1 - 8) * 2189.0 / (16 * 60.8)
    h_ppm = 6.0 + (16 - 1 - 8) * 3000.0 / (16 * 600.0)
    c_ppm = 40.0 + (16 - 1 - 5) * 11300.0 / (16 * 150.9)
    assert abs(float(row["F1_shift"]) - n_ppm) < 0.05
    assert abs(float(row["F2_shift"]) - h_ppm) < 0.05
    assert abs(float(row["F3_shift"]) - c_ppm) < 0.05


def test_pick_peaks_2d_reversed_storage_maps_by_nucleus(
    tmp_path: Path,
) -> None:
    """0.2.199-补29dh:2D 存储序 (1H,15N) 时,N/H 列按核匹配,不再写反。"""
    from nmrglue.fileio import pipe

    spec = np.zeros((64, 32))
    spec[20, 10] = 500.0  # 存储 axis0=1H idx20、axis1=15N idx10
    spec = gaussian_filter(spec, sigma=1.2)
    ft2 = tmp_path / "hn.ft2"
    dic = {k: "0" for k in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = spec.shape[1]
    dic["FDSPECNUM"] = spec.shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    blocks = {
        1: ("1H", 64, 3000.0, 600.0, 4.7, 6.0 * 600.0),
        2: ("15N", 32, 2189.0, 60.8, 118.0, 100.0 * 60.8),
    }
    for dim, (lab, size, sw, obs, car, orig) in blocks.items():
        prefix = f"FDF{dim}"
        dic[prefix + "T"] = size
        dic[prefix + "SW"] = sw
        dic[prefix + "OBS"] = obs
        dic[prefix + "CAR"] = car
        dic[prefix + "ORIG"] = orig
        dic[prefix + "LABEL"] = lab
    pipe.write(str(ft2), dic, spec.astype(np.float32), overwrite=True)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)

    result = pick_peaks(manager, exp_id, data_id)
    row = _read_rows(Path(result["peak_path"]))[0]
    n_ppm = 100.0 + (32 - 1 - 10) * 2189.0 / (32 * 60.8)
    h_ppm = 6.0 + (64 - 1 - 20) * 3000.0 / (64 * 600.0)
    assert abs(float(row["N_shift"]) - n_ppm) < 0.05
    assert abs(float(row["H_shift"]) - h_ppm) < 0.05

def _write_ft2_nh(path: Path, data: np.ndarray) -> None:
    """真实 N-H 二维谱夹具(0.2.199-补29dl 参考约束测试用)。"""
    from nmrglue.fileio import pipe

    dic = {k: "0" for k in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = data.shape[1]
    dic["FDSPECNUM"] = data.shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    blocks = {
        1: ("15N", data.shape[0], 2189.0, 60.8, 118.0, 100.0 * 60.8),
        2: ("1H", data.shape[1], 3000.0, 600.0, 4.7, 6.0 * 600.0),
    }
    for dim, (lab, size, sw, obs, car, orig) in blocks.items():
        prefix = f"FDF{dim}"
        dic[prefix + "T"] = size
        dic[prefix + "SW"] = sw
        dic[prefix + "OBS"] = obs
        dic[prefix + "CAR"] = car
        dic[prefix + "ORIG"] = orig
        dic[prefix + "LABEL"] = lab
    pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)


def _nh_ppm(n_idx: int, h_idx: int) -> tuple[float, float]:
    """合成 N-H 谱的 (N,H) ppm(与 _write_ft2_nh 头部一致)。"""
    n_ppm = 100.0 + (64 - 1 - n_idx) * 2189.0 / (64 * 60.8)
    h_ppm = 6.0 + (128 - 1 - h_idx) * 3000.0 / (128 * 600.0)
    return n_ppm, h_ppm


def test_pick_peaks_reference_constraint_2d(tmp_path: Path) -> None:
    """0.2.199-补29dl:参考峰表约束——2D 只保留与参考(N,H)匹配的峰。"""
    rng = np.random.default_rng(20260831)
    spec = rng.normal(0, 0.3, (64, 128))
    spec[20, 40] += 1500.0
    spec[25, 90] += 1200.0
    spec[40, 60] += 1000.0
    spec = gaussian_filter(spec, sigma=1.0)
    ft2 = tmp_path / "out.ft2"
    _write_ft2_nh(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    ref_path = tmp_path / "ref.list"
    export_peaks_poky(
        ref_path,
        [
            {"N_shift": _nh_ppm(20, 40)[0], "H_shift": _nh_ppm(20, 40)[1],
             "Intensity": 1, "label": ""},
            {"N_shift": _nh_ppm(25, 90)[0], "H_shift": _nh_ppm(25, 90)[1],
             "Intensity": 1, "label": ""},
        ],
        ndim=2,
    )
    ref_peaks = import_peaks_poky(ref_path)
    result = pick_peaks(
        manager, exp_id, data_id,
        ref_peaks=ref_peaks, ref_nuclei=["15N", "1H"],
    )
    rows = _read_rows(Path(result["peak_path"]))
    assert len(rows) == 2
    assert "参考峰表约束" in "".join(result["logs"])


def test_pick_peaks_reference_constraint_3d_with_2d_ref(
    tmp_path: Path,
) -> None:
    """0.2.199-补29dl:3D 选峰按 2D 参考(N,H)约束——第三维自由,一个参考峰
    可保留多个峰(如 HNCA 的 CA/CB)。"""
    rng = np.random.default_rng(20260831)
    data = rng.normal(0, 0.3, (16, 16, 16))
    # 逻辑 F1(N)=8、F2(H)=8、F3(C)=5 和 F3(C)=10;另一 (N,H)=(10,10)
    data[8, 5, 8] += 1500.0
    data[8, 10, 8] += 1200.0
    data[10, 5, 10] += 1000.0
    data = gaussian_filter(data, sigma=1.0)
    ft3 = tmp_path / "out.ft3"
    _write_ft3_ordered(ft3, data, [2.0, 3.0, 1.0])
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft3)
    n_ppm = 100.0 + (16 - 1 - 8) * 2189.0 / (16 * 60.8)
    h_ppm = 6.0 + (16 - 1 - 8) * 3000.0 / (16 * 600.0)
    ref_path = tmp_path / "ref.list"
    export_peaks_poky(
        ref_path,
        [{"N_shift": n_ppm, "H_shift": h_ppm, "Intensity": 1, "label": ""}],
        ndim=2,
    )
    ref_peaks = import_peaks_poky(ref_path)
    result = pick_peaks(
        manager, exp_id, data_id,
        ref_peaks=ref_peaks, ref_nuclei=["15N", "1H"],
        tolerance_ppm={"15N": 2.0, "1H": 0.5, "13C": 20.0},
    )
    rows = _read_rows(Path(result["peak_path"]), nuclei=["15N", "1H", "13C"])
    assert len(rows) == 2  # (N=8,H=8) 的两个 C 值保留,另一个 (N,H) 剔除
    assert "参考峰表约束" in "".join(result["logs"])


def test_pick_peaks_reference_tolerance(tmp_path: Path) -> None:
    """0.2.199-补29dl:参考容差——默认 4 点×ppm/点外剔除,放宽后保留。"""
    rng = np.random.default_rng(20260831)
    spec = rng.normal(0, 0.3, (64, 128))
    spec[20, 40] += 1500.0
    spec = gaussian_filter(spec, sigma=1.0)
    ft2 = tmp_path / "out.ft2"
    _write_ft2_nh(ft2, spec)
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    n20, h40 = _nh_ppm(20, 40)
    ref_path = tmp_path / "ref.list"
    # 参考 N 偏移 3 ppm:默认 15N 容差(4 点 ≈ 2.25 ppm)外 → 剔除
    export_peaks_poky(
        ref_path,
        [{"N_shift": n20 + 3.0, "H_shift": h40, "Intensity": 1, "label": ""}],
        ndim=2,
    )
    ref_peaks = import_peaks_poky(ref_path)
    result = pick_peaks(
        manager, exp_id, data_id,
        ref_peaks=ref_peaks, ref_nuclei=["15N", "1H"],
    )
    assert _read_rows(Path(result["peak_path"])) == []
    # tolerance_ppm 放宽到 5 ppm → 保留
    result2 = pick_peaks(
        manager, exp_id, data_id,
        ref_peaks=ref_peaks, ref_nuclei=["15N", "1H"],
        tolerance_ppm={"15N": 5.0, "1H": 1.0},
    )
    assert len(_read_rows(Path(result2["peak_path"]))) == 1

