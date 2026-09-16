"""nmrforge_api(v0.2,2026-09-13 规范)回归。

覆盖:参考工作流(1 脚本 + 2 峰表)、workflow_id、三层参数留档、组合模式按
外部选择输出定位峰表、状态三值、完整日志与版本、多条件独立参考基底、自动参数
实际值、软件边界(不做 CSP/统计)、CLI 与「不依赖 Qt」。

规范符合性台账:``docs/reviews/2026-09-13-api-spec-compliance.md``。
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from core.peaks.peak_table import export_peaks_poky
from nmrforge_api import (
    API_VERSION,
    PEAK_TABLE_COLUMNS,
    STATUS_SUCCESS,
    STATUS_WARNING,
    DatasetError,
    DatasetRef,
    MeasurementError,
    ReferenceError,
    SweepError,
    SweepPlan,
    add_dataset,
    build_reference,
    condition_token,
    detect_and_localize,
    ensure_reference_peaks,
    expand_grid,
    load_combo_table,
    load_plan,
    load_runs,
    load_workflows,
    measure_peak_positions,
    merge_overrides,
    open_study,
    plan_sweep,
    read_peak_table,
    run_combination_study,
    run_parameter_study,
    run_reference_study,
    run_sweep,
    window_points_by_axis,
)
from nmrforge_api.cli import main as cli_main
from nmrforge_api.peaks import (
    PeakMeasurement,
    peak_coordinates,
    read_reference_peaks,
    reference_peak_id,
)
from nmrforge_api.reference import (
    ReferenceSpectrum,
    load_reference,
    reference_phase,
    sanitize_sweep_params,
)

# 合成谱几何:数据轴 0 = 间接(15N,64 点),轴 1 = 直接(1H,128 点);
# 头部用 CAR(ORIG=0),ppm[i] = CAR + (size/2 - i) * SW/(size*OBS)
_N15_OBS, _N15_SW, _N15_CAR, _N15_SIZE = 60.8, 2000.0, 118.0, 64
_H1_OBS, _H1_SW, _H1_CAR, _H1_SIZE = 600.0, 6000.0, 4.7, 128
_PEAK_A = (30, 60)
_PEAK_B = (45, 90)


def _n15_step() -> float:
    return _N15_SW / (_N15_SIZE * _N15_OBS)


def _h1_step() -> float:
    return _H1_SW / (_H1_SIZE * _H1_OBS)


def _n15_ppm(index: float) -> float:
    return _N15_CAR + (_N15_SIZE / 2 - index) * _n15_step()


def _h1_ppm(index: float) -> float:
    return _H1_CAR + (_H1_SIZE / 2 - index) * _h1_step()


def _write_ft2(path: Path, *, shift_y: float = 0.0, shift_x: float = 0.0) -> Path:
    """写一张可被 nmrglue 读取的 2D 谱,峰位按 shift_y/shift_x 平移(点)。"""
    return _write_ft2_grid(path, 1, shift_y=shift_y, shift_x=shift_x)


def _write_ft2_grid(
    path: Path, factor: int, *, shift_y: float = 0.0, shift_x: float = 0.0
) -> Path:
    """同一张谱的 ``factor`` 倍网格版本(填零:点距 1/factor,物理峰位不变)。

    ``shift_y``/``shift_x`` 以 **1× 网格**点数计,内部乘 ``factor`` 换算;
    头部 SW/OBS/CAR/ORIG 不变,因此 ppm 轴按点数自动变密。
    """
    from nmrglue.fileio import pipe

    factor = max(1, int(factor))
    shape = (_N15_SIZE * factor, _H1_SIZE * factor)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    arr = np.zeros(shape, dtype=float)
    for index, (y, x) in enumerate((_PEAK_A, _PEAK_B), start=1):
        cy, cx = (y + shift_y) * factor, (x + shift_x) * factor
        arr += (120.0 - 20.0 * (index - 1)) * np.exp(
            -(
                ((yy - cy) ** 2) / (2 * (1.2 * factor) ** 2)
                + ((xx - cx) ** 2) / (2 * (1.4 * factor) ** 2)
            )
        )
    rng = np.random.default_rng(20260912)
    arr = gaussian_filter(arr, sigma=0.5) + rng.normal(0.0, 0.5, shape)
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = shape[1]
    dic["FDSPECNUM"] = shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDDIMORDER"] = [2, 1]
    dic["FDF1LABEL"] = "N15"
    dic["FDF2LABEL"] = "H1"
    dic["FDF1SW"] = str(_N15_SW)
    dic["FDF1OBS"] = str(_N15_OBS)
    dic["FDF1CAR"] = str(_N15_CAR)
    dic["FDF1ORIG"] = "0"
    dic["FDF2SW"] = str(_H1_SW)
    dic["FDF2OBS"] = str(_H1_OBS)
    dic["FDF2CAR"] = str(_H1_CAR)
    dic["FDF2ORIG"] = "0"
    path.parent.mkdir(parents=True, exist_ok=True)
    pipe.write(str(path), dic, arr.astype(np.float32), overwrite=True)
    return path


def _write_peak_table(path: Path) -> Path:
    """按「参考谱」(shift=0)写峰表;位置取自上面同一套 ppm 公式。"""
    rows = []
    for index, (y, x) in enumerate((_PEAK_A, _PEAK_B), start=1):
        rows.append(
            {
                "N_shift": _n15_ppm(y),
                "H_shift": _h1_ppm(x),
                "Intensity": 120.0 - 20.0 * (index - 1),
                "label": f"G{index}",
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    export_peaks_poky(path, rows)
    return path


class _FakeSweepBackend:
    """模拟 NMRPipe 后端:按 ``window.F1.off`` 线性平移峰位,写真实 ft2。"""

    def __init__(self) -> None:
        self.work_dir = ""
        self.process_calls: list[dict] = []
        self.reconstruct_calls: list[dict] = []
        self.convert_calls = 0

    def _work(self) -> Path:
        path = Path(self.work_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def convert_to_fid(self, experiment, data_dir, progress=None, **_kwargs) -> dict:
        self.convert_calls += 1
        work = self._work()
        (work / "fid.com").write_text("#!/bin/csh\n", encoding="utf-8")
        fid = work / f"{experiment.dataset_id}.fid"
        fid.write_bytes(b"fid-bytes")
        return {
            "success": True,
            "fid_path": str(fid),
            "message": "ok",
            "logs": [],
            "effective_params": {},
        }

    def reconstruct_nus(
        self,
        experiment,
        params=None,
        progress=None,
        script_only=False,
        out_file=None,
        script_name=None,
    ) -> dict:
        """模拟 NUS 重构:候选输出走 _intermediate,并按 nSigma 平移峰位。"""
        params = dict(params or {})
        self.reconstruct_calls.append(
            {"params": params, "out_file": out_file, "script_name": script_name}
        )
        work = self._work()
        script = work / (script_name or f"{experiment.dataset_id}_nus.com")
        nsigma = float(params.get("nsigma", params.get("nSigma", 5.0)))
        script.write_text(f"#!/bin/csh\n# nSigma={nsigma}\n", encoding="utf-8")
        if script_only:
            return {
                "success": True,
                "message": "script only",
                "logs": [],
                "script": script.read_text(encoding="utf-8"),
                "script_path": str(script),
            }
        shift = (5.0 - nsigma) * 0.5
        target = work / (out_file or f"{experiment.dataset_id}.ft2")
        if out_file:
            target = work / "_intermediate" / out_file
        _write_ft2(target, shift_y=shift, shift_x=shift / 2.0)
        return {
            "success": True,
            "message": "ok",
            "spectrum_path": str(target),
            "logs": ["fake nus"],
            "effective_params": {
                "nSigma": nsigma,
                "thresh": float(params.get("thresh", 0.95)),
                "direct_phase": [0.0, 0.0],
                "phases": {"F1": [0.0, 0.0]},
            },
        }

    def process(
        self,
        experiment,
        plan,
        *,
        params=None,
        direct_phase_override=None,
        script_name=None,
        out_file=None,
        progress=None,
        **_kwargs,
    ) -> dict:
        params = dict(params or {})
        self.process_calls.append({"params": params, "phase": direct_phase_override})
        work = self._work()
        script = work / (script_name or f"{experiment.dataset_id}_process.com")
        off = float((params.get("window") or {}).get("F1", {}).get("off", 0.40))
        script.write_text(
            f"#!/bin/csh\n# window.F1.off={off}\n", encoding="utf-8"
        )
        # 0.40 为「参考」;偏离 0.01 平移 0.25 点(非整数 → 必须亚像素才能还原)
        shift = (0.40 - off) * 25.0
        # 模拟填零(方案 A 的动机):zero_fill=k → k 倍网格、点距 1/k,
        # 峰的**物理位置(ppm)不变**;真后端同样只改数字分辨率。
        try:
            factor = max(1, int(params.get("zero_fill") or 1))
        except (TypeError, ValueError):
            factor = 1
        target = work / (out_file or f"{experiment.dataset_id}.ft2")
        if out_file:
            target = work / "_intermediate" / out_file
        _write_ft2_grid(target, factor, shift_y=shift, shift_x=shift / 2.0)
        return {
            "success": True,
            "message": "ok",
            "spectrum_path": str(target),
            "logs": ["fake process"],
            "effective_params": {
                "window": params.get("window"),
                "zero_fill": params.get("zero_fill"),
                "direct_phase": {"F2": [0.0, 0.0]},
            },
        }




# ------------------------------------------------------------------ 单元层
def test_expand_grid_and_merge_overrides() -> None:
    combos = expand_grid({"zero_fill": [1, 2], "window.F1.off": [0.35, 0.45]})
    assert len(combos) == 4
    assert {"zero_fill": 1, "window.F1.off": 0.35} in combos
    merged = merge_overrides(
        {"window": {"F1": {"off": 0.4, "end": 0.98}}, "zero_fill": 2},
        {"window.F1.off": 0.45, "points_per_line": 4.0},
    )
    assert merged["window"]["F1"] == {"off": 0.45, "end": 0.98}
    assert merged["points_per_line"] == 4.0
    assert merged["zero_fill"] == 2
    assert expand_grid({}) == [{}]
    with pytest.raises(SweepError):
        expand_grid({"zero_fill": []})


def test_reference_peak_id_is_stable_and_prefixed() -> None:
    assert reference_peak_id(1) == "R0001"
    assert reference_peak_id(37) == "R0037"


def test_measure_peak_positions_recovers_subpoint_shift(tmp_path: Path) -> None:
    from core.peaks.peak_table import load_peaks

    peaks = _write_peak_table(tmp_path / "ref.list")
    rows = load_peaks(peaks)
    # 平移 +1.25 点(15N)/ +0.625 点(1H)后,测量值应还原到 1/5 点以内
    spectrum = _write_ft2(tmp_path / "shift.ft2", shift_y=1.25, shift_x=0.625)
    measured = measure_peak_positions(spectrum, rows, window_pts=3)
    assert len(measured) == 2
    for index, (y, x) in enumerate((_PEAK_A, _PEAK_B)):
        item = measured[index]
        assert item.found
        assert item.reference_peak_id == f"R{index + 1:04d}"
        assert item.snr > 0  # SNR = |intensity| / 谱噪声 σ
        expected_n = _n15_ppm(y + 1.25)
        expected_h = _h1_ppm(x + 0.625)
        assert abs(item.positions["15N"] - expected_n) < 0.2 * _n15_step()
        assert abs(item.positions["1H"] - expected_h) < 0.2 * _h1_step()
        assert not item.window_edge
        assert not item.boundary
        assert not item.out_of_range
    # 整数截断会差整整 1 个点;这里必须明显好于它
    assert abs(measured[0].positions["15N"] - _n15_ppm(_PEAK_A[0])) > 0.5 * _n15_step()


def test_physical_window_is_scale_invariant_across_zero_fill(
    tmp_path: Path,
) -> None:
    """方案 A:窗口/边距按物理宽度定义 → 填零只改点数,不改覆盖 ppm。

    结构性点数(3 点邻域/抛物线 ±1 点)不变;**物理宽度**点数必须随点距换算。
    """
    from core.peaks.peak_table import load_peaks
    from workflow.pick_peaks import read_spectrum_axes

    _write_ft2(tmp_path / "zf1.ft2")
    _write_ft2_grid(tmp_path / "zf4.ft2", 4)
    axes_one = read_spectrum_axes(tmp_path / "zf1.ft2")
    axes_four = read_spectrum_axes(tmp_path / "zf4.ft2")

    auto_one = window_points_by_axis(axes_one)
    auto_four = window_points_by_axis(axes_four)
    # 默认物理半径 = 1.5×该轴核素线宽折算 ppm(15N 15 Hz / 60.8 MHz)
    assert auto_one[0]["ppm"] == pytest.approx(1.5 * 15.0 / _N15_OBS, rel=0.02)
    assert auto_one[1]["ppm"] == pytest.approx(1.5 * 8.0 / _H1_OBS, rel=0.02)
    for axis in (0, 1):
        assert auto_four[axis]["ppm"] == pytest.approx(
            auto_one[axis]["ppm"], rel=0.02
        )
        assert auto_four[axis]["nucleus"] == auto_one[axis]["nucleus"]
        # 点距随填零变密 → 点数按点距换算
        assert auto_four[axis]["ppm_per_point"] == pytest.approx(
            auto_one[axis]["ppm_per_point"] / 4.0, rel=0.05
        )

    # 显式 1 ppm 窗口:1× 与 4× 的**点数**不同、覆盖宽度相同
    win_one = window_points_by_axis(axes_one, window_ppm=1.0)
    win_four = window_points_by_axis(axes_four, window_ppm=1.0)
    for axis in (0, 1):
        assert win_one[axis]["source"] == "ppm(显式)"
        assert win_four[axis]["points"] == pytest.approx(
            4 * win_one[axis]["points"], rel=0.15
        )
        assert win_four[axis]["effective_ppm"] == pytest.approx(
            win_one[axis]["effective_ppm"], rel=0.15
        )
        assert win_one[axis]["effective_ppm"] == pytest.approx(1.0, rel=0.1)

    # 峰位:同一物理窗口 → 两个分辨率给出同一 ppm
    rows = load_peaks(_write_peak_table(tmp_path / "reference.list"))
    measured_one = measure_peak_positions(
        tmp_path / "zf1.ft2", rows, window_ppm=1.0
    )
    measured_four = measure_peak_positions(
        tmp_path / "zf4.ft2", rows, window_ppm=1.0
    )
    assert len(measured_one) == len(measured_four) == 2
    for thin, fine in zip(measured_one, measured_four):
        assert abs(thin.positions["15N"] - fine.positions["15N"]) < 0.2 * _n15_step()
        assert abs(thin.positions["1H"] - fine.positions["1H"]) < 0.2 * _h1_step()

    # 点数口径(逃生口)仍然是「点数即点数」,不随填零换算
    pts_one = window_points_by_axis(axes_one, window_pts=3)
    pts_four = window_points_by_axis(axes_four, window_pts=3)
    for axis in (0, 1):
        assert pts_one[axis]["points"] == pts_four[axis]["points"] == 3
        assert pts_four[axis]["effective_ppm"] == pytest.approx(
            pts_one[axis]["effective_ppm"] / 4.0, rel=0.05
        )


def test_read_reference_peaks_accepts_research_csv(tmp_path: Path) -> None:
    """下游研究项目导出的 peak_id,H_ppm,N_ppm 峰表可直接使用。"""
    path = tmp_path / "reference_peaks.csv"
    path.write_text(
        "peak_id,H_ppm,N_ppm,height,linewidth,volume\n"
        "1:LEU10,8.211,122.733,0.0,0.0,0.0\n"
        "2:GLY101,8.266,109.496,0.0,0.0,0.0\n",
        encoding="utf-8-sig",
    )
    rows = read_reference_peaks(path)
    assert len(rows) == 2
    assert rows[0]["label"] == "LEU10"
    assert rows[0]["reference_peak_id"] == "R0001"
    assert peak_coordinates(rows[0], None) == {"1H": 8.211, "15N": 122.733}


def test_measure_peak_positions_flags_out_of_range(tmp_path: Path) -> None:
    spectrum = _write_ft2(tmp_path / "ref.ft2")
    rows = [{"N_shift": 999.0, "H_shift": 5.0, "label": "X"}]
    measured = measure_peak_positions(spectrum, rows, window_pts=2)
    assert measured[0].out_of_range


def test_sanitize_sweep_params_drops_runtime_keys() -> None:
    cleaned = sanitize_sweep_params(
        {
            "window": {"F1": {"off": 0.4}},
            "phase_route": "unified",
            "preview_axis": "F1",
            "diagnostics": {"x": 1},
            "fill": {"F1": 64},
            "nus": {"nthread": 2},
            "points_per_line": 4.0,
        }
    )
    assert set(cleaned) == {"window", "points_per_line"}


def test_reference_phase_uses_all_axes_when_direct_missing() -> None:
    """统一路线把相位写在 phases(各轴 PS),锁定时必须全部继承。"""
    effective = {"phases": {"F1": [172.5, 0.0], "F2": [27.5, 0.0]}}
    locked = reference_phase(effective)
    assert locked == {"F1": [172.5, 0.0], "F2": [27.5, 0.0]}
    ref = ReferenceSpectrum(
        dataset_key="exp_001/d_001",
        exp_id="exp_001",
        data_id="d_001",
        direct_phase=locked,
    )
    assert ref.direct_phase_override() == {
        "F1": (172.5, 0.0),
        "F2": (27.5, 0.0),
    }
    # 自动相位的实际结果落档(规范 G1)
    record = ref.phase_record()
    assert record["F2"]["phase_mode"] == "auto"
    assert record["F2"]["actual_p0"] == pytest.approx(27.5)
    assert record["F2"]["actual_p1"] == pytest.approx(0.0)
    # direct_phase 与 phases 合并:直接维以 direct_phase 为准
    assert reference_phase(
        {"direct_phase": {"F2": [1.0, 2.0]}, "phases": {"F1": [3.0, 0.0]}}
    ) == {"F1": [3.0, 0.0], "F2": [1.0, 2.0]}


def test_reference_phase_handles_nus_flat_direct_phase() -> None:
    """NUS 重构路线把直接维相位记成扁平 [p0, p1],需映射到 F{ndim}。"""
    effective = {"phases": {"F1": [172.5, 0.0]}, "direct_phase": [27.5, 0.0]}
    assert reference_phase(effective, ndim=2) == {
        "F1": [172.5, 0.0],
        "F2": [27.5, 0.0],
    }


def test_error_hierarchy() -> None:
    assert issubclass(DatasetError, Exception)
    assert issubclass(SweepError, Exception)


def test_version_is_0_2() -> None:
    assert API_VERSION == "0.2"


# ------------------------------------------------------------ 参考工作流
def test_reference_workflow_writes_script_and_two_peak_tables(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """规范 B:1 个参考脚本 + 2 张参考峰表(parabolic / gaussian)。"""
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        tmp_path / "reference",
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        window_ppm=1.0,
        backend=backend,
    )
    ref = result.reference
    assert ref is not None
    assert Path(ref.script_path).is_file() and ref.script_sha256
    parabolic_path = Path(ref.peak_table_parabolic_path)
    gaussian_path = Path(ref.peak_table_gaussian_path)
    assert parabolic_path.is_file() and gaussian_path.is_file()
    header = parabolic_path.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert header == list(PEAK_TABLE_COLUMNS)
    assert gaussian_path.read_text(encoding="utf-8").splitlines()[0].split(",") == (
        list(PEAK_TABLE_COLUMNS)
    )
    rows_p = read_peak_table(parabolic_path)
    rows_g = read_peak_table(gaussian_path)
    assert [row["reference_peak_id"] for row in rows_p] == ["R0001", "R0002"]
    assert [row["reference_peak_id"] for row in rows_g] == ["R0001", "R0002"]
    assert all(row["workflow_id"] == "reference" for row in rows_p)
    assert all(row["localization_method"] == "parabolic" for row in rows_p)
    # 抛物线表:高斯专属字段按规范填 NaN,结构仍与高斯表一致
    assert math.isnan(rows_p[0]["fit_success"])
    assert math.isnan(rows_p[0]["FWHM_H"]) and math.isnan(rows_p[0]["FWHM_N"])
    assert math.isnan(rows_p[0]["fit_rmse"])
    assert math.isnan(rows_p[0]["boundary_hit"])
    assert rows_p[0]["fallback"] is False
    # 高斯表:真跑了拟合的峰有 FWHM/rmse
    fitted = [row for row in rows_g if row["fit_success"]]
    assert fitted, rows_g
    assert fitted[0]["FWHM_H"] > 0 and fitted[0]["FWHM_N"] > 0
    assert fitted[0]["fit_rmse"] >= 0
    assert ref.peak_localization["parabolic"]["n_peaks"] == 2
    assert ref.peak_localization["gaussian"]["n_peaks"] == 2
    # 参考相位(自动识别)的实际结果落档
    assert ref.phase_record()["F2"]["phase_mode"] == "auto"


# ------------------------------------------------------------------ workflow
def test_workflows_are_traceable_and_use_both_localizations(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """规范 C/D/E/G:W0001… + 三层参数 + 峰表 + 完整日志 + 版本 + 状态。

    2026-09-14:组合模式独立选峰(不跟踪参考峰表),精修方式由 localization 指定;
    这里 localization='both' → 同一张谱各出一张 parabolic / gaussian 峰表。
    """
    root = tmp_path / "workflows"
    peaks = _write_peak_table(tmp_path / "reference.list")
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[
            {"window.F1.off": 0.35, "zero_fill": 1},
            {"window.F1.off": 0.45, "zero_fill": 2},
        ],
        params={"phase_route": "none"},
        peaks=peaks,
        localization="both",
        backend=backend,
    )
    ref = result.reference
    assert ref is not None
    assert [run.workflow_id for run in result.runs] == ["W0001", "W0002"]
    assert [run.condition for run in result.runs] == ["A", "A"]
    for run in result.runs:
        run_dir = Path(run.run_dir)
        assert run_dir.name == "A"
        for name in (
            "process.com",
            "spectrum.ft2",
            "peak_table_parabolic.csv",
            "peak_table_gaussian.csv",
            "log.txt",
            "run.json",
        ):
            assert (run_dir / name).is_file(), name
        assert run.status in (STATUS_SUCCESS, STATUS_WARNING)
        # 以参考脚本为模板:记录参考脚本与哈希
        assert run.base_script["sha256"] == ref.script_sha256
        assert run.base_script["path"] == ref.script_path
        # 用户参数 vs 实际参数
        assert run.parameters_requested == run.combo
        # 组合模式:阈值锁定在参考(逐 workflow 留档来源)
        detection = run.parameters_resolved["detection"]
        assert detection["source"] == "reference(locked)"
        assert detection["independent"] is True
        assert detection["methods"] == ["parabolic", "gaussian"]
        assert run.parameters_used["zero_fill"] == run.combo["zero_fill"]
        assert run.parameters_used["window"]["F1"]["off"] == pytest.approx(
            run.combo["window.F1.off"]
        )
        # 相位:自动识别结果 + 锁定
        assert run.phase_locked
        assert run.phase["F2"]["phase_mode"] == "auto_reference_locked"
        assert run.phase["F2"]["actual_p0"] == pytest.approx(0.0)
        assert run.parameters_resolved["phase"]["F2"]["actual_p1"] == pytest.approx(
            0.0
        )
        # 版本表(软件 + 依赖;真机还会带 nmrpipe/smile)
        assert run.versions.get("nmrforge")
        payload = json.loads(Path(run.run_dir, "run.json").read_text(encoding="utf-8"))
        assert payload["workflow_id"] == run.workflow_id
        assert payload["parameters_requested"] and payload["parameters_used"]
        assert payload["versions"].get("nmrforge")
        # 两张峰表:同一套字段、各自是本谱独立检出的峰(不跟踪参考峰表)
        rows_p = read_peak_table(Path(run.peak_table_path("parabolic")))
        rows_g = read_peak_table(Path(run.peak_table_path("gaussian")))
        assert len(rows_p) == len(rows_g) == 2
        assert [int(row["peak_id"]) for row in rows_p] == [1, 2]
        assert all(row["reference_peak_id"] == "" for row in rows_p)
        assert all(row["reference_peak_id"] == "" for row in rows_g)
        assert all(row["assignment"] == "" for row in rows_p)
        assert all(row["workflow_id"] == run.workflow_id for row in rows_p)
        assert all(row["workflow_id"] == run.workflow_id for row in rows_g)
        assert all(row["detected"] for row in rows_p)
        assert all(row["SNR"] > 0 for row in rows_p)
        assert all(row["localization_method"] == "parabolic" for row in rows_p)
        assert all(row["localization_method"] == "gaussian" for row in rows_g)
        assert run.parameters_resolved["peak_counts"] == {
            "parabolic": 2, "gaussian": 2,
        }
        # 完整日志(不是只有尾部)
        log = Path(run.log_path).read_text(encoding="utf-8")
        assert "parameters_used" in log
        assert "--- processing log ---" in log
        assert "fake process" in log
    # 组合级记录:workflow.json + log.txt
    wf_dir = root / "study" / "workflows" / "W0001"
    record = json.loads((wf_dir / "workflow.json").read_text(encoding="utf-8"))
    assert record["workflow_id"] == "W0001"
    assert record["status"] in (STATUS_SUCCESS, STATUS_WARNING)
    assert record["parameters_requested"] == {
        "window.F1.off": 0.35,
        "zero_fill": 1,
    }
    assert record["conditions"] == ["A"]
    assert (wf_dir / "log.txt").is_file()
    # 参数 → 峰位:0.35 与 0.45 相差 2.5 点(15N),亚像素测量应还原
    by_off = {
        round(float(run.combo["window.F1.off"]), 3): run for run in result.runs
    }
    assert by_off[0.35].measurements[0].positions["15N"] == pytest.approx(
        _n15_ppm(_PEAK_A[0] + 1.25), abs=0.3 * _n15_step()
    )
    assert by_off[0.45].measurements[0].positions["15N"] == pytest.approx(
        _n15_ppm(_PEAK_A[0] - 1.25), abs=0.3 * _n15_step()
    )
    # fid 只转换一次(参考运行时)
    assert backend.convert_calls == 1


def test_zero_fill_keeps_physical_edge_margin_and_window(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """端到端:选峰边距与逐组合测量窗口都按物理宽度换算并留档(方案 A)。"""
    root = tmp_path / "zf_study"
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        axes={"zero_fill": [1, 4]},
        params={"phase_route": "none"},
        backend=backend,
    )
    assert len(result.runs) == 2
    assert all(run.status in (STATUS_SUCCESS, STATUS_WARNING) for run in result.runs)

    # 参考谱选峰:边距 = 3×该轴(15N)线宽折算 ppm,并记下等效点数与点距
    detection = result.reference.peak_params["detection"]
    assert detection["edge_margin_source"] == "ppm(物理宽度)"
    assert detection["axis0_nucleus"] == "15N"
    assert detection["edge_margin_ppm"] == pytest.approx(
        3 * 15.0 / _N15_OBS, rel=0.02
    )
    assert detection["edge_margin_points"] >= 1
    assert detection["axes"][0]["ppm_per_point"] > 0

    # 逐组合:边距物理宽度一致,点数随填零变密
    by_fill = {
        int(round(float(run.combo["zero_fill"]))): run.window
        for run in result.runs
    }
    for factor in (1, 4):
        spec = by_fill[factor]["0"]
        assert spec["source"] == "ppm(物理宽度)"
        assert spec["nucleus"] == "15N"
        assert spec["ppm"] == pytest.approx(3 * 15.0 / _N15_OBS, rel=0.02)
    assert by_fill[4]["0"]["points"] == pytest.approx(
        4 * by_fill[1]["0"]["points"], rel=0.5
    )
    assert by_fill[4]["0"]["ppm_per_point"] == pytest.approx(
        by_fill[1]["0"]["ppm_per_point"] / 4.0, rel=0.05
    )

    # 记录:manifest 与 measurement.json 里能直接读到换算过程
    manifest = json.loads(
        Path(result.records["manifest"]).read_text(encoding="utf-8")
    )
    measurement = manifest["measurement"]
    assert measurement["reference"][0]["edge_margin"] == detection
    seen = measurement["window_points_seen"]["0"]
    assert seen["nucleus"] == "15N"
    assert max(seen["points"]) > min(seen["points"])  # 点数确实随填零变
    assert seen["ppm"][0] == pytest.approx(3 * 15.0 / _N15_OBS, rel=0.02)
    assert measurement["window_by_axis"]["0"]["source"] == "ppm(物理宽度)"
    assert Path(result.records["measurement"]).is_file()
    runs_json = json.loads(Path(result.records["runs"]).read_text(encoding="utf-8"))
    assert all(run["window"] for run in runs_json)


def test_records_are_written_with_unified_peak_tables(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """records:manifest/workflows/runs/两张长表;不含 CSP/统计产物。"""
    root = tmp_path / "records"
    result = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}, {"zero_fill": 2}],
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        localization="both",
        backend=_FakeSweepBackend(),
    )
    for name in (
        "manifest",
        "sweep_plan",
        "runs",
        "workflows",
        "measurement",
        "peak_table_parabolic",
        "peak_table_gaussian",
    ):
        assert Path(result.records[name]).is_file(), name
    manifest = json.loads(
        Path(result.records["manifest"]).read_text(encoding="utf-8")
    )
    assert "CSP" in manifest["boundary"]  # 边界声明写进清单
    assert manifest["plan"]["workflow_ids"] == ["W0001", "W0002"]
    assert manifest["references"][0]["peak_tables"]["parabolic"]["path"]
    rows = read_peak_table(Path(result.records["peak_table_parabolic"]))
    assert len(rows) == 4  # 2 workflow × 2 峰
    assert {row["workflow_id"] for row in rows} == {"W0001", "W0002"}
    grows = read_peak_table(Path(result.records["peak_table_gaussian"]))
    assert {row["localization_method"] for row in grows} == {"gaussian"}
    summary = result.summary
    assert summary["n_workflows"] == 2
    assert summary["workflow_ids"] == ["W0001", "W0002"]
    assert summary["status_counts"]["n_runs"] == 2
    assert summary["per_workflow"]["W0001"]["conditions"]["A"]["status"] in (
        STATUS_SUCCESS,
        STATUS_WARNING,
    )
    # 断点续跑:再跑一次不新增处理调用
    calls_before = len(_backend_calls(result))
    again = run_parameter_study(
        root,
        None,
        combos=[{"zero_fill": 1}, {"zero_fill": 2}],
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        localization="both",
        backend=result.session.backend,
    )
    assert len(again.runs) == 2
    assert len(_backend_calls(again)) == calls_before


def test_resume_invalidates_changed_plan_and_hides_stale_workflows(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """W0001 输入改变必须重跑；缩短计划后旧 W0002 不得混入汇总。"""
    root = tmp_path / "resume_changed"
    backend = _FakeSweepBackend()
    peaks = _write_peak_table(tmp_path / "resume.list")
    first = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}, {"zero_fill": 2}],
        params={"phase_route": "none"},
        peaks=peaks,
        backend=backend,
    )
    calls_before = len(backend.process_calls)
    assert all(run.resume_fingerprint for run in first.runs)

    second = run_parameter_study(
        root,
        None,
        combos=[{"zero_fill": 4}],
        params={"phase_route": "none"},
        peaks=peaks,
        backend=backend,
        resume=True,
    )
    assert len(backend.process_calls) == calls_before + 1
    assert [run.workflow_id for run in second.runs] == ["W0001"]
    assert second.runs[0].parameters_requested == {"zero_fill": 4}
    assert len(load_runs(second.session)) == 1
    workflows = load_workflows(second.session)
    assert [item["workflow_id"] for item in workflows] == ["W0001"]
    stored = json.loads(Path(second.records["workflows"]).read_text(encoding="utf-8"))
    assert [item["workflow_id"] for item in stored] == ["W0001"]


def _backend_calls(result) -> list:
    return list(getattr(result.session.backend, "process_calls", []))


def test_combination_mode_picks_peaks_independently(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """2026-09-14 规范:组合模式不做参考峰跟踪,峰来自该组合自己的谱。

    每个组合在自己的候选谱上用**参考锁定阈值**独立选峰 → 该组合自己的完整
    峰表;reference_peak_id / assignment 留空(与参考峰表的匹配是外部工作)。
    """
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "independent", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    plan = plan_sweep(reference, combos=[{"zero_fill": 1}])
    runs = run_sweep(session, plan, reference=reference, resume=False)
    run = runs[0]
    assert run.status in (STATUS_SUCCESS, STATUS_WARNING)
    table = read_peak_table(Path(run.peak_table_path("parabolic")))
    assert table  # 该组合自己检出的峰,不是参考峰表行
    assert all(row["detected"] for row in table)
    assert all(row["reference_peak_id"] == "" for row in table)
    assert all(row["assignment"] == "" for row in table)
    assert [int(row["peak_id"]) for row in table] == list(
        range(1, len(table) + 1)
    )
    assert all(row["condition"] == "A" for row in table)
    # 旧口径(参考里有、本谱测不到就 detected=false 保留一行)已废弃
    assert not any(w["code"] == "peak_not_detected" for w in run.warnings)
    detection = run.parameters_resolved["detection"]
    assert detection["source"] == "reference(locked)"
    assert detection["reference_matching"] == "external"
    assert detection["sigma_multiplier"] == pytest.approx(35.0)


def test_gaussian_fallback_is_recorded_not_silent(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """规范 G3:高斯拟合失败/回退必须逐峰落表 + workflow 警告。"""
    from core.peaks import localize as lz

    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "fallback", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    plan = plan_sweep(reference, axes={"zero_fill": [1]})

    real = lz.localize_peak

    def forced(data, index, **kwargs):
        """强制高斯路径失败(确定性构造,不依赖数据偶然性)。"""
        if str(kwargs.get("method")) == "gaussian":
            return lz.PeakLocalization(
                requested_method="gaussian",
                actual_method="parabolic",
                position=tuple(float(v) for v in index),
                success=False,
                fallback=True,
                reason="forced_test_failure",
            )
        return real(data, index, **kwargs)

    monkeypatch.setattr(lz, "localize_peak", forced)
    runs = run_sweep(
        session,
        plan,
        reference=reference,
        localization="gaussian",
        resume=False,
    )
    run = runs[0]
    table = read_peak_table(Path(run.peak_table_path("gaussian")))
    assert table
    assert all(row["fallback"] is True for row in table)
    assert all(row["fit_success"] is False for row in table)
    assert {row["fallback_reason"] for row in table} == {"forced_test_failure"}
    assert any(w["code"] == "gaussian_fallback" for w in run.warnings)
    assert run.status == STATUS_WARNING
    assert run.peak_localization["gaussian"]["n_fallback"] == len(table)
    assert run.peak_localization["gaussian"]["fallback_reasons"] == {
        "forced_test_failure": len(table)
    }


def test_gaussian_localization_exception_becomes_failed_run(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gaussian 精修异常必须落成 failed run.json,而不是终止整轮。"""
    from core.peaks import localize as lz

    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "gaussian_error", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    plan = plan_sweep(reference, axes={"zero_fill": [1]})
    real_localize = lz.localize_peak

    def explode_gaussian(data, index, **kwargs):
        if str(kwargs.get("method")) == "gaussian":
            raise RuntimeError("forced gaussian error")
        return real_localize(data, index, **kwargs)

    monkeypatch.setattr(lz, "localize_peak", explode_gaussian)
    runs = run_sweep(
        session,
        plan,
        reference=reference,
        localization="gaussian",
        resume=False,
    )
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert "forced gaussian error" in runs[0].message
    payload = json.loads(
        Path(runs[0].run_dir, "run.json").read_text(encoding="utf-8")
    )
    assert payload["status"] == "failed"
    assert payload["resume_fingerprint"]


def test_non_2d_gaussian_fallback_rows_set_flag() -> None:
    """非 2D 的显式 fallback_reason 同时必须令 fallback=true。"""
    from nmrforge_api.peak_tables import gaussian_fallback_rows

    measurement = PeakMeasurement(
        peak_id=1,
        assignment="G1",
        reference={"1H": 8.0},
        positions={"1H": 8.01},
        found=True,
    )
    rows = gaussian_fallback_rows(
        [measurement],
        workflow_id="W0001",
        condition="A",
        reason="not_2d",
    )
    assert rows[0]["fallback"] is True
    assert rows[0]["fallback_reason"] == "not_2d"

def test_two_conditions_share_parameters_and_peak_identity(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """规范 I:同一 workflow 对 A/B 用同一组参数,各条件各出峰表。

    2026-09-14:组合模式独立选峰 → 峰表属于本条件自己的谱(reference_peak_id
    留空);参考层仍各条件共享同一份参考峰身份表。
    """
    root = tmp_path / "ab"
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        root,
        datasets={
            "A": bruker_dir / "hsqc_2d",
            "B": bruker_dir / "hsqc_small",
        },
        axes={"zero_fill": [1, 2]},
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        window_ppm=1.0,
        backend=backend,
    )
    assert result.conditions == ["A", "B"]
    assert len(result.runs) == 4  # 2 workflow × 2 条件
    by_workflow: dict[str, list] = {}
    for run in result.runs:
        by_workflow.setdefault(run.workflow_id, []).append(run)
    assert set(by_workflow) == {"W0001", "W0002"}
    for _workflow_id, runs in by_workflow.items():
        assert {run.condition for run in runs} == {"A", "B"}
        requested = {json.dumps(run.parameters_requested, sort_keys=True) for run in runs}
        assert len(requested) == 1  # 同一组用户参数
        for run in runs:
            assert Path(run.run_dir).name == run.condition
            assert Path(run.run_dir, "peak_table_parabolic.csv").is_file()
            rows = read_peak_table(Path(run.peak_table_path("parabolic")))
            # 组合独立选峰:每条件出自己那张谱的峰表(reference_peak_id 留空)
            assert rows
            assert all(row["reference_peak_id"] == "" for row in rows)
            assert all(row["condition"] == run.condition for row in rows)
    # B 条件的参考沿用主条件的峰身份
    refs = {key: ref for key, ref in result.references.items()}
    shared = [ref for ref in refs.values() if ref.peak_source.startswith("shared:")]
    assert len(shared) == 1
    assert shared[0].condition == "B"
    assert shared[0].peak_count == 2
    # 每条件各转换一次 fid(参考)
    assert backend.convert_calls == 2


def test_two_conditions_use_their_own_reference_parameter_bases(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """A/B 共享组合覆盖，但未覆盖参数必须分别继承各自参考。"""
    from nmrforge_api.reference import save_reference

    root = tmp_path / "ab_reference_bases"
    backend = _FakeSweepBackend()
    references = run_reference_study(
        root,
        datasets={
            "A": bruker_dir / "hsqc_2d",
            "B": bruker_dir / "hsqc_small",
        },
        params={"phase_route": "none"},
        backend=backend,
    )
    expected = {"A": 0.31, "B": 0.77}
    for condition, off in expected.items():
        reference = references.reference(condition)
        assert reference is not None
        reference.sweep_params = merge_overrides(
            reference.sweep_params, {"window.F1.off": off}
        )
        save_reference(references.session, reference)

    result = run_combination_study(
        str(root),
        combos=[{"zero_fill": 2}],
        direct_range=(10.0, 6.5),
        backend=backend,
    )
    assert {run.condition for run in result.runs} == {"A", "B"}
    for run in result.runs:
        assert run.parameters_used["window"]["F1"]["off"] == pytest.approx(
            expected[run.condition]
        )
        assert run.parameters_used["zero_fill"] == 2
        assert run.parameters_used["ext_lo"] == "10"
        assert run.parameters_used["ext_hi"] == "6.5"


def test_external_peak_identity_is_propagated_to_all_conditions(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """外部峰表替换主条件身份后，次条件必须重新复制同一份身份表。"""
    external = tmp_path / "one-reference.list"
    export_peaks_poky(
        external,
        [
            {
                "N_shift": _n15_ppm(_PEAK_B[0]),
                "H_shift": _h1_ppm(_PEAK_B[1]),
                "Intensity": 100.0,
                "label": "ONLY",
            }
        ],
    )
    result = run_parameter_study(
        tmp_path / "external_ab",
        datasets={
            "A": bruker_dir / "hsqc_2d",
            "B": bruker_dir / "hsqc_small",
        },
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        peaks=external,
        backend=_FakeSweepBackend(),
    )
    refs = list(result.references.values())
    assert [ref.peak_count for ref in refs] == [1, 1]
    assert len({ref.peak_table_sha256 for ref in refs}) == 1
    assert refs[1].peak_source == "shared:A"
    # 参考身份(外部 .list)仍各条件共享;组合峰表改为独立选峰 → 不写参考身份
    for run in result.runs:
        rows = read_peak_table(Path(run.peak_table_path("parabolic")))
        assert rows
        assert all(row["reference_peak_id"] == "" for row in rows)


def test_stop_on_error_keeps_running_successful_conditions(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """stop_on_error 只在失败时停止，成功的 A 后仍必须执行 B。"""
    result = run_parameter_study(
        tmp_path / "stop_success",
        datasets={
            "A": bruker_dir / "hsqc_2d",
            "B": bruker_dir / "hsqc_small",
        },
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        backend=_FakeSweepBackend(),
    )
    plan = plan_sweep(result.reference, combos=[{"zero_fill": 2}])
    runs = run_sweep(
        result.session,
        plan,
        reference=result.reference,
        resume=False,
        stop_on_error=True,
    )
    assert {run.condition for run in runs} == {"A", "B"}
    assert all(run.status in (STATUS_SUCCESS, STATUS_WARNING) for run in runs)


def test_run_parameter_study_nus_2d(tmp_path: Path, bruker_dir: Path) -> None:
    """2D NUS:参考与 workflow 都走 reconstruct_nus,候选隔离 + 相位锁定。"""
    root = tmp_path / "nus_study"
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        root,
        bruker_dir / "nus_2d",
        axes={"nSigma": [3.0, 5.0, 7.0]},
        params={"phase_route": "none"},
        backend=backend,
    )
    reference = result.reference
    assert reference.sampling == "nus"
    assert reference.ndim == 2
    assert reference.direct_phase == {"F1": [0.0, 0.0], "F2": [0.0, 0.0]}
    assert reference.peak_source == "auto"
    assert len(result.runs) == 3
    assert all(run.status in (STATUS_SUCCESS, STATUS_WARNING) for run in result.runs)
    assert all(run.phase_locked for run in result.runs)
    # 每个组合都通过 reconstruct_nus 的候选输出参数隔离产物
    candidate_calls = [c for c in backend.reconstruct_calls if c["out_file"]]
    assert len(candidate_calls) == 3
    assert all(c["params"]["direct_phase"] == [0.0, 0.0] for c in candidate_calls)
    assert all(c["script_name"].endswith(".com") for c in candidate_calls)
    # 候选谱互不覆盖,且参考谱保持独立
    assert len({run.spectrum_path for run in result.runs}) == 3
    assert len({run.spectrum_sha256 for run in result.runs}) == 3
    # SMILE 自动分档的实际结果落档(规范 G2)
    for run in result.runs:
        smile = run.parameters_resolved["smile"]
        assert smile["nsigma"]["actual"] == pytest.approx(
            float(run.combo["nSigma"])
        )
        assert smile["nsigma"]["source"] == "user"
        assert run.parameters_resolved["spectrum_noise_sigma"]["value"] > 0
    # 默认精修方式 parabolic → 只出抛物线表(要高斯必须显式指定)
    for run in result.runs:
        assert Path(run.peak_table_path("parabolic")).is_file()
        assert not run.peak_table_path("gaussian")


def test_reference_state_persists_across_sessions(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """分步 CLI(独立进程)能看到已登记的 fid/活动谱:项目状态必须落盘。"""
    root = tmp_path / "persist"
    backend = _FakeSweepBackend()
    run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        backend=backend,
    )
    # 新会话 = 新进程视角:从 project.json 重新加载
    session2 = open_study(root, backend=backend)
    entry = session2.data_entry()
    assert entry.fid_path and Path(entry.fid_path).exists()
    assert entry.spectrum_path and Path(entry.spectrum_path).is_file()
    reference = load_reference(session2)
    assert reference is not None
    assert reference.peak_source == "auto"
    assert Path(reference.peak_table_path).is_file()
    assert Path(reference.peak_table_parabolic_path).is_file()
    assert Path(reference.peak_table_gaussian_path).is_file()
    # 一条 pick_peaks 运行记录也已落盘
    assert any(
        run.workflow_ref == "pick_peaks"
        for run in session2.manager.project.workflow_runs
    )
    # 计划与运行记录可读回
    loaded_plan = load_plan(session2)
    assert loaded_plan is not None
    assert "base_params" not in loaded_plan.to_dict()
    assert len(load_runs(session2)) == 1
    assert len(load_workflows(session2)) == 1


def test_old_absolute_base_sweep_plan_is_rejected() -> None:
    with pytest.raises(SweepError, match="旧版 SweepPlan"):
        SweepPlan.from_dict({"combos": [{"zero_fill": 1}], "base_params": {}})


def test_nus_param_key_alias_normalized() -> None:
    """nSigma/nsigma 两种写法都要落到后端输入键 nsigma。"""
    from nmrforge_api.sweep import normalize_nus_params

    assert normalize_nus_params({"nSigma": 5, "thresh": 0.95}) == {
        "nsigma": 5,
        "thresh": 0.95,
    }
    # 已用小写时保持原值(不覆盖)
    assert normalize_nus_params({"nsigma": 3, "nSigma": 9}) == {
        "nsigma": 3,
        "nSigma": 9,
    }
    assert normalize_nus_params({"zero_fill": 2}) == {"zero_fill": 2}


def test_plan_sweep_accepts_explicit_combos(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """设计由外部决定:显式组合表原样执行(顺序保留),接口不做设计决策。"""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "combos", backend=backend)
    session.dataset = add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    rows = [
        {"window.F1.off": 0.35, "zero_fill": 1},
        {"window.F1.off": 0.35, "zero_fill": 2},
        {"window.F1.off": 0.45, "zero_fill": 1},
        {"window.F1.off": 0.45, "zero_fill": 2},
    ]
    plan = plan_sweep(reference, combos=rows)
    assert plan.design == "explicit"
    assert plan.n_full == 4
    assert plan.n_workflows == 4
    assert plan.workflow_ids() == ["W0001", "W0002", "W0003", "W0004"]
    assert [dict(c) for c in plan.combos] == rows      # 原样、保序
    assert plan.diagnostics["n_runs"] == 4
    assert plan.diagnostics["duplicated_rows"] == 0
    assert plan.diagnostics["max_abs_correlation"] == 0.0
    assert plan.grid_sha256 == plan_sweep(reference, combos=rows).grid_sha256

    calls_before = len(backend.process_calls)   # 参考运行本身已调用一次
    runs = run_sweep(session, plan, reference=reference, resume=False)
    assert [run.parameters_requested for run in runs] == rows
    assert all(run.status in (STATUS_SUCCESS, STATUS_WARNING) for run in runs)
    assert len(backend.process_calls) == calls_before + 4


def test_plan_sweep_requires_exactly_one_design_input(
    tmp_path: Path, bruker_dir: Path
) -> None:
    session = open_study(tmp_path / "one_input", backend=_FakeSweepBackend())
    session.dataset = add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    with pytest.raises(SweepError, match="只能给一个"):
        plan_sweep(reference)
    with pytest.raises(SweepError, match="只能给一个"):
        plan_sweep(reference, axes={"zero_fill": [1]}, combos=[{"zero_fill": 1}])


def test_combo_table_roundtrip_and_validation(tmp_path: Path) -> None:
    from nmrforge_api import combos_from_rows, load_combo_table, write_combo_table

    rows = [
        {"window.F1.off": 0.35, "zero_fill": 1, "phase_delta.F2.p0": -5},
        {"window.F1.off": 0.45, "zero_fill": 2, "phase_delta.F2.p0": 5},
    ]
    table = write_combo_table(tmp_path / "design.csv", rows)
    assert load_combo_table(table) == rows
    with pytest.raises(SweepError, match="不在声明水平"):
        combos_from_rows([{"a": 3}], axes={"a": [1, 2]})
    with pytest.raises(SweepError, match="未在 axes 中声明"):
        combos_from_rows([{"b": 1}], axes={"a": [1, 2]})


def test_plan_sweep_axis_scope_guards(tmp_path: Path, bruker_dir: Path) -> None:
    """锁定键报错;确定性/未知键只提示(确定性参数不必进网格)。"""
    session = open_study(tmp_path / "scope", backend=_FakeSweepBackend())
    session.dataset = add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    with pytest.raises(SweepError, match="phase_delta"):
        plan_sweep(reference, axes={"direct_phase": [[0.0, 0.0]]})
    plan = plan_sweep(
        reference,
        axes={
            "ext_lo": ["10.5"],
            "points_per_line": [2.0],
            "bogus.key": [1],
            "zero_fill": [1],
        },
    )
    joined = "\n".join(plan.notes)
    assert "确定性" in joined and "points_per_line" in joined
    assert "直接维范围" in joined and "ext_lo" in joined
    assert "不在后端读取" in joined and "bogus.key" in joined


def test_phase_delta_axis_shifts_locked_phase(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """相位识别偏差(±5°)作为参数轴:在参考相位上施加后传给后端并留档。"""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "phase", backend=backend)
    session.dataset = add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    assert reference.direct_phase == {"F2": [0.0, 0.0]}
    plan = plan_sweep(reference, axes={"phase_delta.F2.p0": [-5, 5]})
    runs = run_sweep(session, plan, reference=reference, resume=False)
    assert [run.phase["F2"]["actual_p0"] for run in runs] == [-5.0, 5.0]
    assert all(
        run.phase["F2"]["phase_mode"] == "manual_delta_from_reference"
        for run in runs
    )
    assert all(run.phase_locked for run in runs)
    phases = [call["phase"] for call in backend.process_calls if call["phase"]]
    assert phases[0] == {"F2": (-5.0, 0.0)}
    assert phases[1] == {"F2": (5.0, 0.0)}


def test_sweep_rejects_3d_nus(tmp_path: Path, bruker_dir: Path) -> None:
    """3D NUS 仍不支持:NUS 只开放 2D。"""
    session = open_study(tmp_path / "study", backend=_FakeSweepBackend())
    session.dataset = add_dataset(session, bruker_dir / "nus_2d")
    reference = ReferenceSpectrum(
        dataset_key="exp_001/d_001",
        exp_id="exp_001",
        data_id="d_001",
        ndim=3,
        sampling="nus",
        sweep_params={"window": {"F1": {"off": 0.4}}},
    )
    plan = plan_sweep(reference, axes={"zero_fill": [1, 2]})
    with pytest.raises(SweepError, match="2D NUS"):
        run_sweep(session, plan, reference=reference)


def test_add_dataset_rejects_non_bruker(tmp_path: Path) -> None:
    session = open_study(tmp_path / "study", backend=_FakeSweepBackend())
    bogus = tmp_path / "not_bruker"
    bogus.mkdir()
    with pytest.raises(DatasetError, match="Bruker"):
        add_dataset(session, bogus)
    with pytest.raises(DatasetError, match="不存在"):
        add_dataset(session, tmp_path / "missing")


def test_add_dataset_rejects_duplicate_condition(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """多条件:条件标签必须唯一(否则 A/B 会被同一份数据占用)。"""
    session = open_study(tmp_path / "dup", backend=_FakeSweepBackend())
    add_dataset(session, bruker_dir / "hsqc_2d", condition="A")
    with pytest.raises(DatasetError, match="条件标签"):
        add_dataset(session, bruker_dir / "hsqc_small", condition="A")


def test_condition_tokens_do_not_alias_and_reject_case_collisions(
    tmp_path: Path,
) -> None:
    assert condition_token("A") == "A"
    assert condition_token("A/B") != condition_token("A_B")
    assert condition_token("条件一") != condition_token("条件二")

    session = open_study(tmp_path / "token_collision", backend=_FakeSweepBackend())
    session.add_dataset_ref(DatasetRef("exp_001", "d_001", condition="A"))
    with pytest.raises(DatasetError, match="token 冲突"):
        session.add_dataset_ref(DatasetRef("exp_002", "d_002", condition="a"))


def test_cli_status_and_report(
    tmp_path: Path, bruker_dir: Path, capsys
) -> None:
    root = tmp_path / "study"
    result = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[{"window.F1.off": 0.40}],
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        window_ppm=1.0,
        backend=_FakeSweepBackend(),
    )
    assert result.runs[0].status in (STATUS_SUCCESS, STATUS_WARNING)
    assert cli_main(["status", "--study", str(root)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["runs"]["n_runs"] == 1
    assert payload["workflows"]["ids"] == ["W0001"]
    assert payload["references"]
    assert cli_main(["report", "--study", str(root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"]["n_runs"] == 1
    # 组合模式必须显式给参考:--reference 必填
    assert (
        cli_main(
            [
                "sweep",
                "--study",
                str(root),
                "--reference",
                str(root),
                "--grid",
                "missing.yaml",
            ]
        )
        == 2
    )


def test_api_does_not_import_qt() -> None:
    """对外接口必须能在无 Qt 环境/集群上导入。"""
    code = (
        "import sys, nmrforge_api; "
        "bad = sorted(m for m in sys.modules "
        "if m.startswith(('PyQt', 'PySide', 'shiboken'))); "
        "assert not bad, bad; "
        "assert not any(m.startswith('gui') for m in sys.modules), 'gui imported'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_statistics_helper_is_not_in_processing_contract() -> None:
    """规范 J(2026-09-13 用户裁定):σ/Δδ 只作**测试/检测辅助**,不进处理契约。

    软件本身只执行处理并留档;助手可用于回归检测「参数是否真的生效」与下游分析
    参考实现,但处理链(study/sweep/records/CLI)不调用它、也不产出对应文件。
    """
    import nmrforge_api
    from nmrforge_api.uncertainty import (
        position_uncertainty,
        uncertainty_summary,
    )

    assert callable(position_uncertainty) and callable(uncertainty_summary)
    package = Path(nmrforge_api.__file__).parent
    for name in ("study.py", "sweep.py", "records.py", "cli.py", "reference.py"):
        source = (package / name).read_text(encoding="utf-8")
        for token in (
            "position_uncertainty",
            "uncertainty_summary",
            "delta_std",
            "csp_n_weight",
            "uncertainty.csv",
        ):
            assert token not in source, (name, token)
    # 模块自述用途边界(测试/检测),避免以后被当成处理产物
    first_line = Path(nmrforge_api.uncertainty.__file__).read_text(
        encoding="utf-8"
    ).splitlines()[0]
    assert "测试" in first_line


def test_position_uncertainty_formula() -> None:
    """助手公式回归:σ 为样本标准差;Δδ 下限 = sqrt(Σ(w_n·σ_n)²)。"""

    def measurement(h: float, n: float) -> PeakMeasurement:
        return PeakMeasurement(
            peak_id=1,
            assignment="G1",
            reference={"1H": 5.0, "15N": 119.0},
            positions={"1H": h, "15N": n},
            deltas={"1H": h - 5.0, "15N": n - 119.0},
            found=True,
        )

    from nmrforge_api import position_uncertainty, uncertainty_summary

    runs = {
        "W0001": [measurement(5.0, 119.0)],
        "W0002": [measurement(5.02, 119.2)],
        "W0003": [measurement(4.98, 118.8)],
    }
    items = position_uncertainty(runs, csp_n_weight=0.2)
    assert len(items) == 1
    item = items[0]
    assert item.n_runs == 3
    assert item.sigma["1H"] == pytest.approx(0.02, abs=1e-6)
    assert item.sigma["15N"] == pytest.approx(0.2, abs=1e-6)
    expected = math.sqrt(0.02**2 + (0.2 * 0.2) ** 2)
    assert item.delta_std == pytest.approx(expected, rel=1e-6)
    assert item.delta_max == pytest.approx(expected, rel=1e-6)
    summary = uncertainty_summary(items, csp_n_weight=0.2, n_runs=3)
    assert summary["n_peaks"] == 1
    assert summary["delta_std_ppm"]["median"] == pytest.approx(expected, rel=1e-6)


def test_position_uncertainty_requires_all_nuclei() -> None:
    """助手只在所有被测核都测到的组合上统计(半边数据不参与)。"""
    from nmrforge_api import position_uncertainty

    partial = PeakMeasurement(
        peak_id=2,
        assignment="G2",
        reference={"1H": 5.0, "15N": 119.0},
        positions={"1H": 5.0},
        found=True,
    )
    full = PeakMeasurement(
        peak_id=2,
        assignment="G2",
        reference={"1H": 5.0, "15N": 119.0},
        positions={"1H": 5.0, "15N": 119.0},
        found=True,
    )
    items = position_uncertainty({"W0001": [partial], "W0002": [full]})
    assert items[0].n_runs == 1
    assert items[0].missing_runs == 1


def test_helper_detects_parameter_effect_in_end_to_end_run(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """检测用途:用 σ/Δδ 助手确认参数真的生效(全 0 = 参数被静默忽略)。

    对应真机历史上出现过的缺陷:SMILE 参数键写错 → 每个组合跑出同一张谱,
    峰位完全一致(Δδ 全 0)却仍显示 success。这里用它做回归自检。
    """
    from nmrforge_api import position_uncertainty, uncertainty_summary

    backend = _FakeSweepBackend()
    result = run_parameter_study(
        tmp_path / "detect_effect",
        bruker_dir / "hsqc_2d",
        combos=[{"window.F1.off": 0.35}, {"window.F1.off": 0.45}],
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        window_ppm=1.0,
        backend=backend,
    )
    # 参数真的生效:候选谱互不相同
    assert len({run.spectrum_sha256 for run in result.runs}) == 2
    uncertainties = position_uncertainty(result.runs, csp_n_weight=0.2)
    assert uncertainties
    assert all(item.delta_std > 0 for item in uncertainties)
    summary = uncertainty_summary(uncertainties, n_runs=len(result.runs))
    assert summary["delta_std_ppm"]["max"] > 0
    # 助手不进处理产物:records 里没有不确定度文件
    assert not any("uncertainty" in name for name in result.records)


def test_peak_threshold_is_chosen_with_reference_then_locked(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """2026-09-14(用户):阈值只在生成参考时可选,参考定了以后必须与参考一致。

    生成参考时可外部指定 σ 倍数;参考一旦冻结,后续参数扰动沿用该阈值——显式给
    不同阈值会被拒绝(不悄悄重选峰),要改阈值只能显式重建参考(force=True)。
    """
    session = open_study(tmp_path / "threshold_locked", backend=_FakeSweepBackend())
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    # 生成参考时外部指定阈值
    reference = ensure_reference_peaks(session, reference, sigma_multiplier=20)
    assert reference.peak_params["sigma_multiplier"] == pytest.approx(20.0)
    assert reference.peak_params["detection"]["sigma_multiplier"] == pytest.approx(
        20.0
    )
    assert reference.peak_params["detection"]["threshold_source"] == "user"

    def _picks() -> int:
        return sum(
            1
            for run in session.manager.project.workflow_runs
            if run.workflow_ref == "pick_peaks"
        )

    picks = _picks()
    frozen_sha = reference.peak_table_sha256
    # 同阈值 / 不给阈值 → 复用参考,不重选
    reference = ensure_reference_peaks(session, reference, sigma_multiplier=20)
    ensure_reference_peaks(session, reference)
    assert _picks() == picks
    assert reference.peak_table_sha256 == frozen_sha
    # 不同阈值 → 拒绝(参考已锁定)
    with pytest.raises(ReferenceError, match="锁定"):
        ensure_reference_peaks(session, reference, sigma_multiplier=60)
    assert _picks() == picks                     # 没有偷偷重选
    assert reference.peak_table_sha256 == frozen_sha
    # 显式重建参考才允许改阈值
    reference = ensure_reference_peaks(
        session, reference, sigma_multiplier=60, force=True
    )
    assert reference.peak_params["sigma_multiplier"] == pytest.approx(60.0)
    assert reference.peak_params["previous_sigma_multiplier"] == pytest.approx(20.0)
    assert _picks() > picks


def test_peak_threshold_defaults_to_35_sigma(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """不指定阈值时仍是既有默认 35σ(行为向后兼容)。"""
    session = open_study(tmp_path / "default_threshold", backend=_FakeSweepBackend())
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    detection = reference.peak_params["detection"]
    assert reference.peak_params["sigma_multiplier"] is None
    assert detection["sigma_multiplier"] == pytest.approx(35.0)
    assert detection["threshold_source"].startswith("default")


def test_peak_threshold_too_high_reports_error_instead_of_silence(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """阈值高到选不出峰时明确报错(不静默产出空峰表当成功)。"""
    session = open_study(tmp_path / "bad_threshold", backend=_FakeSweepBackend())
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    with pytest.raises(MeasurementError):
        ensure_reference_peaks(session, reference, sigma_multiplier=100000)


def test_peak_picking_keys_in_combination_table_are_rejected(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """阈值是参考定义的一部分:写进 workflow 组合表直接报错(不静默无效)。"""
    session = open_study(tmp_path / "grid_hint", backend=_FakeSweepBackend())
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    with pytest.raises(SweepError, match="选峰阈值"):
        plan_sweep(reference, axes={"sigma_multiplier": [20], "zero_fill": [1]})


def test_cli_peaks_applies_external_threshold_when_reference_is_built(
    tmp_path: Path, bruker_dir: Path, capsys
) -> None:
    """CLI:`peaks --sigma N` 在生成参考峰表时按外部阈值选峰;之后改阈值被拒。"""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "cli_threshold", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    build_reference(session, params={"phase_route": "none"})   # 只有参考谱/脚本
    root = session.root
    # 参考峰表尚未生成 → 此时指定阈值 = 生成参考时选阈值
    assert cli_main(["peaks", "--study", str(root), "--sigma", "20"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["conditions"][0]["params"]["sigma_multiplier"] == pytest.approx(20.0)
    detection = payload["conditions"][0]["params"]["detection"]
    assert detection["sigma_multiplier"] == pytest.approx(20.0)
    assert payload["conditions"][0]["params"]["detection"]["threshold_source"] == "user"
    # 参考已定(20σ)→ 再改阈值报错(退出码 2)
    assert cli_main(["peaks", "--study", str(root), "--sigma", "60"]) == 2
    assert "锁定" in capsys.readouterr().out


def test_frozen_default_threshold_cannot_be_changed_later(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """参考按默认 35σ 冻结后,再指定别的阈值同样被拒(与参考一致才允许)。"""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "frozen_default", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)      # 默认 35σ
    assert reference.peak_params["detection"]["sigma_multiplier"] == pytest.approx(
        35.0
    )
    # 与参考一致(35σ)可以显式给 → 复用
    ensure_reference_peaks(session, reference, sigma_multiplier=35)
    # 与参考不一致 → 拒绝
    with pytest.raises(ReferenceError, match="锁定"):
        ensure_reference_peaks(session, reference, sigma_multiplier=20)


def test_workflow_records_reference_locked_threshold(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """每条 workflow 记录写明「与参考一致的选峰阈值」。"""
    result = run_parameter_study(
        tmp_path / "locked_records",
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        sigma_multiplier=25,
        backend=_FakeSweepBackend(),
    )
    run = result.runs[0]
    locked = run.parameters_resolved["detection"]
    assert locked["sigma_multiplier"] == pytest.approx(25.0)
    assert locked["sigma_multiplier_origin"] == "reference"
    assert locked["source"] == "reference(locked)"
    assert locked["independent"] is True
    assert locked["reference_matching"] == "external"
    assert result.reference.peak_params["sigma_multiplier"] == pytest.approx(25.0)
    payload = json.loads(Path(run.run_dir, "run.json").read_text(encoding="utf-8"))
    assert payload["parameters_resolved"]["detection"]["source"] == (
        "reference(locked)"
    )


def _write_bruker_full_sampling_as_nus(tmp_path: Path) -> Path:
    """Bruker 2D 数据:标注 NUS(NusAMOUNT=25)但 ser 全格无零行 = 实际满采样。"""
    ds = tmp_path / "full_as_nus"
    ds.mkdir(parents=True, exist_ok=True)
    x_n, td_rows = 64, 16            # FnMODE=5 → 复点网格 8,声明全格 16 行
    (ds / "acqus").write_text(
        f"##$TD= {x_n}\n##$FnMODE= 0\n##$NusAMOUNT= 25\n##$NusTD= 0\n"
        "##$DTYPE= 0\n",
        encoding="utf-8",
    )
    (ds / "acqu2s").write_text(
        f"##$TD= {td_rows}\n##$FnMODE= 5\n##$NusTD= {td_rows}\n##$NUC1= <15N>\n",
        encoding="utf-8",
    )
    data = np.full((td_rows, x_n), 5.0, dtype="<i4")
    data.tofile(ds / "ser")
    return ds


def test_disguised_full_sampling_is_processed_as_uniform(tmp_path: Path) -> None:
    """标注 NUS 但实际满采样 → API 按 uniform 处理,并留档有效采样(2026-09-14)。"""
    dataset = _write_bruker_full_sampling_as_nus(tmp_path)
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        tmp_path / "fs_study",
        dataset,
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        backend=backend,
    )
    reference = result.reference
    assert reference is not None
    assert reference.sampling == "uniform"
    assert reference.sampling_schedule == "full_sampling"
    assert any("满采样" in line for line in reference.sampling_evidence)
    # 全程走 uniform:候选谱由 process() 产出,没有 SMILE 重建调用
    assert backend.process_calls
    assert not backend.reconstruct_calls
    run = result.runs[0]
    mapping = run.parameters_resolved["sampling"]
    assert mapping["effective"] == "uniform"
    assert mapping["route"] == "process"
    assert mapping["schedule"] == "full_sampling"
    assert any("满采样" in line for line in mapping["evidence"])
    payload = json.loads(Path(run.run_dir, "run.json").read_text(encoding="utf-8"))
    assert payload["parameters_resolved"]["sampling"]["effective"] == "uniform"
    manifest = json.loads(Path(result.records["manifest"]).read_text(encoding="utf-8"))
    assert manifest["references"][0]["sampling"] == "uniform"
    assert manifest["references"][0]["sampling_schedule"] == "full_sampling"


def test_combination_mode_requires_explicit_reference(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """组合模式必须显式指定参考:空参考、未建参考都明确报错(不隐式兜底)。"""
    with pytest.raises(ReferenceError, match="显式指定参考"):
        run_combination_study("", combos=[{"zero_fill": 1}])
    root = tmp_path / "no_reference"
    backend = _FakeSweepBackend()
    session = open_study(root, backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    with pytest.raises(ReferenceError, match="参考模式"):
        run_combination_study(str(root), combos=[{"zero_fill": 1}])


def test_reference_mode_then_combination_mode_with_explicit_reference(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """参考模式只建参考;组合模式显式给参考才跑 workflow,且不重建参考。"""
    root = tmp_path / "two_modes"
    backend = _FakeSweepBackend()
    reference_result = run_reference_study(
        root, bruker_dir / "hsqc_2d", backend=backend
    )
    assert reference_result.conditions == ["A"]
    reference = reference_result.reference()
    assert reference is not None
    assert Path(reference.peak_table_parabolic_path).is_file()
    assert Path(reference.peak_table_gaussian_path).is_file()
    assert Path(reference_result.records["reference"]).is_file()
    frozen_script = reference.script_sha256
    frozen_peaks = reference.peak_table_sha256

    def _picks() -> int:
        return sum(
            1
            for run in reference_result.session.manager.project.workflow_runs
            if run.workflow_ref == "pick_peaks"
        )

    picks = _picks()
    result = run_combination_study(
        f"{root}#A", combos=[{"zero_fill": 1}], backend=backend
    )
    assert [run.workflow_id for run in result.runs] == ["W0001"]
    assert result.summary["reference_spec"] == f"{root}#A"
    manifest = json.loads(Path(result.records["manifest"]).read_text(encoding="utf-8"))
    assert manifest["mode"] == "combination"
    assert manifest["reference_spec"] == f"{root}#A"
    # 组合模式不重建参考(选峰与参考哈希都不变)
    assert _picks() == picks
    after = load_reference(result.session, result.session.dataset)
    assert after is not None
    assert after.script_sha256 == frozen_script
    assert after.peak_table_sha256 == frozen_peaks
    # 参考也可用 reference.json 路径显式指定
    spec = str(Path(reference.peak_table_path).parent / "reference.json")
    again = run_combination_study(spec, combos=[{"zero_fill": 1}], backend=backend)
    assert again.summary["reference_spec"] == spec


def test_reference_mode_reports_both_peak_tables(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """参考模式产物:1 脚本 + 2 张参考峰表,并写 records/reference.json。"""
    result = run_reference_study(
        tmp_path / "reference_mode",
        bruker_dir / "hsqc_2d",
        backend=_FakeSweepBackend(),
    )
    reference = result.reference()
    assert reference is not None
    tables = result.peak_tables
    assert Path(tables["parabolic"]).is_file() and Path(tables["gaussian"]).is_file()
    payload = json.loads(Path(result.records["reference"]).read_text(encoding="utf-8"))
    assert payload["mode"] == "reference"
    assert payload["references"][0]["peak_tables"]["parabolic"]["path"]
    assert payload["references"][0]["sampling"] == "uniform"


def test_direct_range_parser_normalises_order() -> None:
    """直接维范围:(high, low) 与反序都规范成 ext_lo=高端 / ext_hi=低端。"""
    from nmrforge_api import parse_direct_range

    direct = parse_direct_range((10.5, 6.5))
    assert direct is not None
    assert (direct.lo, direct.hi) == (10.5, 6.5)
    assert direct.swapped is False
    assert direct.params() == {"ext_lo": "10.5", "ext_hi": "6.5"}
    assert direct.to_dict()["requested"] == [10.5, 6.5]

    swapped = parse_direct_range((6.5, 10.5))
    assert swapped is not None
    assert swapped.params() == {"ext_lo": "10.5", "ext_hi": "6.5"}
    assert swapped.swapped is True
    assert swapped.to_dict()["swapped_to_nmrpipe_order"] is True

    # dict 写法与显式 ext_lo/ext_hi;params 兼容旧写法
    assert parse_direct_range({"lo": 10.0, "hi": 7.0}).params() == {
        "ext_lo": "10",
        "ext_hi": "7",
    }
    assert parse_direct_range(ext_lo="9.5", ext_hi="6.0").params() == {
        "ext_lo": "9.5",
        "ext_hi": "6",
    }
    assert parse_direct_range(params={"ext_lo": "11", "ext_hi": "7"}).lo == 11.0
    # 多入口同时给时:显式参数覆盖 direct_range，direct_range 覆盖 params。
    explicit = parse_direct_range(
        (10.5, 6.5), ext_lo="9.5", ext_hi="7.0",
        params={"ext_lo": "12", "ext_hi": "5"},
    )
    assert explicit is not None
    assert explicit.params() == {"ext_lo": "9.5", "ext_hi": "7"}
    partial = parse_direct_range(
        {"lo": 10.0}, ext_hi=6.0, params={"ext_lo": 12.0, "ext_hi": 5.0}
    )
    assert partial is not None
    assert partial.params() == {"ext_lo": "10", "ext_hi": "6"}
    assert parse_direct_range(None) is None
    # 非法:两端相同 / 只给一端 / 非数值
    with pytest.raises(SweepError):
        parse_direct_range((7.0, 7.0))
    with pytest.raises(SweepError):
        parse_direct_range(ext_lo="9.5")
    with pytest.raises(SweepError):
        parse_direct_range(("a", "b"))


def test_reference_mode_accepts_direct_range(tmp_path: Path, bruker_dir: Path) -> None:
    """参考模式可指定直接维范围:落到后端参数并留档;变化时重建参考。"""
    root = tmp_path / "direct_range_reference"
    backend = _FakeSweepBackend()
    result = run_reference_study(
        root,
        bruker_dir / "hsqc_2d",
        direct_range=(10.5, 6.5),
        backend=backend,
    )
    reference = result.reference()
    assert reference is not None
    assert str(reference.params.get("ext_lo")) == "10.5"
    assert str(reference.params.get("ext_hi")) == "6.5"
    calls = [call["params"] for call in backend.process_calls]
    assert calls and str(calls[-1].get("ext_lo")) == "10.5"
    assert str(calls[-1].get("ext_hi")) == "6.5"
    first_run_id = reference.run_id
    # 改成另一个范围 → 自动重建参考(不是静默复用)
    again = run_reference_study(
        root, direct_range=(11.0, 6.0), backend=backend
    )
    rebuilt = again.reference()
    assert rebuilt is not None
    assert str(rebuilt.params.get("ext_lo")) == "11"
    assert str(rebuilt.params.get("ext_hi")) == "6"
    assert rebuilt.run_id != first_run_id  # 真的重跑了参考


def test_combination_mode_direct_range_override(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """组合模式可覆盖直接维范围(基值 + 逐组合),参考谱不重建。"""
    root = tmp_path / "direct_range_combos"
    backend = _FakeSweepBackend()
    run_reference_study(root, bruker_dir / "hsqc_2d", backend=backend)
    frozen = load_reference(open_study(root, backend=backend), None)
    assert frozen is not None
    frozen_script = frozen.script_sha256
    result = run_combination_study(
        str(root),
        combos=[
            {"zero_fill": 1},
            {"zero_fill": 1, "ext_lo": "9", "ext_hi": "7"},
        ],
        direct_range=(10.0, 6.5),
        backend=backend,
    )
    by_id = {run.workflow_id: run for run in result.runs}
    base = by_id["W0001"].parameters_resolved["direct_range"]
    assert base["ext_lo"] == "10" and base["ext_hi"] == "6.5"
    assert base["source"] == "reference_or_base"
    combo = by_id["W0002"].parameters_resolved["direct_range"]
    assert combo["ext_lo"] == "9" and combo["ext_hi"] == "7"
    assert combo["source"] == "combo"
    # 参考谱不受组合模式影响
    after = load_reference(result.session, result.session.dataset)
    assert after is not None and after.script_sha256 == frozen_script
    assert result.summary["direct_range"]["ext_lo"] == 10.0


def test_empty_combo_cells_mean_unspecified(tmp_path: Path, bruker_dir: Path) -> None:
    """组合表留空 = 不覆盖该参数(真机发现的「空值当成覆盖」修正)。"""
    root = tmp_path / "empty_cells"
    backend = _FakeSweepBackend()
    run_reference_study(
        root, bruker_dir / "hsqc_2d", direct_range=(10.0, 6.5), backend=backend
    )
    table = tmp_path / "combos.csv"
    table.write_text(
        "zero_fill,ext_lo,ext_hi\n1,,\n1,9,7\n", encoding="utf-8"
    )
    result = run_combination_study(
        str(root), combos=load_combo_table(table), backend=backend
    )
    by_id = {run.workflow_id: run for run in result.runs}
    first = by_id["W0001"].parameters_resolved["direct_range"]
    # 空单元格 → 沿用参考基底(10 / 6.5),且来源不是 combo
    assert first["ext_lo"] == "10" and first["ext_hi"] == "6.5"
    assert first["source"] == "reference_or_base"
    second = by_id["W0002"].parameters_resolved["direct_range"]
    assert str(second["ext_lo"]) == "9" and str(second["ext_hi"]) == "7"
    assert second["source"] == "combo"


def test_combo_table_supports_per_dimension_keys(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """组合表按维指定:window/baseline/zero_fill 的点号键逐轴生效并留档。"""
    root = tmp_path / "per_axis"
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[
            {
                "window.F1.off": 0.35,
                "window.F2.off": 0.45,
                "zero_fill.F1": 2,
                "baseline.F2.enabled": False,
            }
        ],
        params={"phase_route": "none"},
        backend=backend,
    )
    run = result.runs[0]
    # requested 保留用户原样的点号键;used 是合并后的逐轴结构
    assert run.parameters_requested["window.F1.off"] == pytest.approx(0.35)
    used = run.parameters_used
    assert used["window"]["F1"]["off"] == pytest.approx(0.35)
    assert used["window"]["F2"]["off"] == pytest.approx(0.45)
    assert used["zero_fill"]["F1"] == 2
    assert used["baseline"]["F2"]["enabled"] is False
    # 后端真的收到逐轴参数(而不是被拍平)
    sent = backend.process_calls[-1]["params"]
    assert sent["window"]["F1"]["off"] == pytest.approx(0.35)
    assert sent["window"]["F2"]["off"] == pytest.approx(0.45)
    assert sent["zero_fill"]["F1"] == 2
    assert sent["baseline"]["F2"]["enabled"] is False
    payload = json.loads(Path(run.run_dir, "run.json").read_text(encoding="utf-8"))
    assert payload["parameters_used"]["zero_fill"]["F1"] == 2


# ----------------------------------------- 组合模式:独立选峰改版(2026-09-14)
def test_combination_threshold_keys_are_rejected(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """阈值只在生成参考时选择:组合表里出现阈值键 -> SweepError(锁定在参考)。"""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "locked_threshold_keys", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    for key, value in (
        ("sigma_multiplier", 20),
        ("min_snr", 20),
        ("threshold_sigma", 20),
        ("detection.sigma_multiplier", 20),
    ):
        with pytest.raises(SweepError, match="选峰阈值"):
            plan_sweep(reference, combos=[{"zero_fill": 1, key: value}])
    # 允许的 detection 键只有精修方式;其它 detection.* 键是未知键(报错不静默)
    plan = plan_sweep(reference, combos=[{"zero_fill": 1, "localization": "both"}])
    assert plan.combos[0]["localization"] == "both"
    assert any("精修方式" in note for note in plan.notes)
    with pytest.raises(SweepError, match="未知的 detection 键"):
        plan_sweep(reference, combos=[{"zero_fill": 1, "detection.max_peaks": 5}])


def test_combination_localization_selection_and_per_combo_override(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """默认 parabolic 只出一张表;gaussian / both 显式指定;逐组合可覆盖。"""
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        tmp_path / "localization_override",
        bruker_dir / "hsqc_2d",
        combos=[
            {"zero_fill": 1, "localization": "gaussian"},
            {"zero_fill": 2, "localization": "parabolic"},
        ],
        params={"phase_route": "none"},
        backend=backend,
    )
    by_id = {run.workflow_id: run for run in result.runs}
    assert by_id["W0001"].peak_table_path("gaussian")
    assert not by_id["W0001"].peak_table_path("parabolic")
    assert by_id["W0002"].peak_table_path("parabolic")
    assert not by_id["W0002"].peak_table_path("gaussian")
    assert by_id["W0001"].parameters_resolved["detection"]["methods"] == ["gaussian"]
    assert by_id["W0002"].parameters_resolved["detection"]["methods"] == ["parabolic"]
    grow = read_peak_table(Path(by_id["W0001"].peak_table_path("gaussian")))
    assert grow and all(row["localization_method"] == "gaussian" for row in grow)
    assert all(row["fit_success"] for row in grow)
    prow = read_peak_table(Path(by_id["W0002"].peak_table_path("parabolic")))
    assert prow and all(row["localization_method"] == "parabolic" for row in prow)
    assert math.isnan(prow[0]["fit_success"])  # 不适用列写 NaN,两表结构一致
    assert math.isnan(prow[0]["FWHM_H"]) and math.isnan(prow[0]["FWHM_N"])

    both = run_parameter_study(
        tmp_path / "localization_both",
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        localization="both",
        edge_margin_ppm=0.5,          # 显式物理边距(ppm)
        backend=_FakeSweepBackend(),
    )
    run = both.runs[0]
    assert Path(run.peak_table_path("parabolic")).is_file()
    assert Path(run.peak_table_path("gaussian")).is_file()
    assert run.parameters_resolved["detection"]["methods"] == ["parabolic", "gaussian"]
    # 边距由外部指定:显式 ppm 口径逐 workflow 留档
    detection = run.parameters_resolved["detection"]
    assert detection["edge_margin_source"] == "ppm(显式)"
    assert detection["edge_margin_ppm"] == pytest.approx(0.5, rel=0.3)


def test_localization_rerun_removes_unselected_run_and_record_tables(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """both→单方法重跑时，运行目录和汇总目录都只保留当前选择。"""
    root = tmp_path / "localization_cleanup"
    backend = _FakeSweepBackend()
    first = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1}],
        params={"phase_route": "none"},
        localization="both",
        backend=backend,
    )
    run_dir = Path(first.runs[0].run_dir)
    stale_sidecar = run_dir / "peak_table_parabolic.csv.localization.json"
    stale_sidecar.write_text("{}\n", encoding="utf-8")

    gaussian = run_combination_study(
        str(root),
        combos=[{"zero_fill": 1}],
        localization="gaussian",
        resume=False,
        backend=backend,
    )
    run_dir = Path(gaussian.runs[0].run_dir)
    assert (run_dir / "peak_table_gaussian.csv").is_file()
    assert not (run_dir / "peak_table_parabolic.csv").exists()
    assert not stale_sidecar.exists()
    assert "peak_table_gaussian" in gaussian.records
    assert "peak_table_parabolic" not in gaussian.records
    records_dir = gaussian.session.records_dir
    assert not (records_dir / "peak_table_parabolic.csv").exists()
    assert "peak_positions" not in gaussian.records
    assert not (records_dir / "peak_positions.csv").exists()

    parabolic = run_combination_study(
        str(root),
        combos=[{"zero_fill": 1}],
        localization="parabolic",
        resume=False,
        backend=backend,
    )
    run_dir = Path(parabolic.runs[0].run_dir)
    assert (run_dir / "peak_table_parabolic.csv").is_file()
    assert not (run_dir / "peak_table_gaussian.csv").exists()
    assert "peak_table_parabolic" in parabolic.records
    assert "peak_table_gaussian" not in parabolic.records
    assert not (parabolic.session.records_dir / "peak_table_gaussian.csv").exists()


def test_combination_detection_keys_do_not_reach_backend_params(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """detection 类键只影响选峰:绝不作为处理参数喂给后端。"""
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        tmp_path / "detection_keys",
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 1, "localization": "parabolic"}],
        params={"phase_route": "none"},
        backend=backend,
    )
    run = result.runs[0]
    sent = backend.process_calls[-1]["params"]
    for key in ("localization", "sigma_multiplier", "min_snr", "detection"):
        assert key not in sent
    assert run.parameters_requested["localization"] == "parabolic"
    assert "localization" not in run.parameters_used


class _Fake3DAxes:
    """最小 3D 谱轴替身:只提供 detect_and_localize 会用到的属性。"""

    data = np.zeros((4, 4, 4))
    ppm = [np.linspace(0.0, 1.0, 4)] * 3
    nuclei = ["15N", "13C", "1H"]
    logical_to_storage = [0, 1, 2]
    obs = [60.8, 151.0, 600.0]


def test_detect_and_localize_gaussian_rejects_non_2d(tmp_path: Path) -> None:
    """组合模式:非 2D 谱请求 gaussian 必须明确报错(不静默换算法)。"""
    spectrum = _write_ft2(tmp_path / "ref.ft2")
    with pytest.raises(MeasurementError, match="only for 2D spectra"):
        detect_and_localize(spectrum, method="gaussian", axes=_Fake3DAxes())
    with pytest.raises(MeasurementError, match="未知峰定位方法"):
        detect_and_localize(spectrum, method="lorentzian", axes=_Fake3DAxes())


def test_combination_zero_peak_warning_is_explicit(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """组合在锁定阈值下一个峰都没检出 → 明确警告 peak_count_zero(不静默当成功)。"""
    import nmrforge_api.peaks as api_peaks

    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "zero_peaks", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    plan = plan_sweep(reference, combos=[{"zero_fill": 1}])
    real = api_peaks.detect_and_localize

    def empty_detection(*args, **kwargs):
        rows, meta = real(*args, **kwargs)
        meta = dict(meta)
        meta.update({"n_peaks": 0, "n_fallback": 0, "n_boundary_hit": 0})
        return [], meta

    monkeypatch.setattr(api_peaks, "detect_and_localize", empty_detection)
    runs = run_sweep(session, plan, reference=reference, resume=False)
    run = runs[0]
    assert run.status == STATUS_WARNING
    assert any(w["code"] == "peak_count_zero" for w in run.warnings)
    # 空峰表仍然写出(只有表头):产物不静默消失
    assert read_peak_table(Path(run.peak_table_path("parabolic"))) == []
    assert run.parameters_resolved["peak_counts"]["parabolic"] == 0
    assert run.peak_localization["parabolic"]["n_peaks"] == 0


# --------------------------------- 参考运行期决定继承 + workflow 脚本留档(2026-09-15)
def test_combination_inherits_reference_runtime_decisions(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """参考的自动决定(如直接维 POLY -time)必须被组合继承;组合显式给值优先。"""
    from nmrforge_api.reference import (
        reference_runtime_decisions,
        sanitize_sweep_params,
        save_reference,
    )

    cleaned = sanitize_sweep_params(
        {
            "window": {"F1": {"type": "none"}},
            "phase_route": "unified",
            "diagnostics": {"apply_poly_time": True},
        }
    )
    assert cleaned == {"window": {"F1": {"type": "none"}}, "direct_poly_time": True}
    assert reference_runtime_decisions({}) == {}
    # 顶层已显式给值时不覆盖
    assert sanitize_sweep_params(
        {"direct_poly_time": False, "diagnostics": {"apply_poly_time": True}}
    ) == {"direct_poly_time": False}

    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "runtime_decisions", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    # 模拟参考峰表建立时的老记录:决定只在 params.diagnostics 里
    reference.params["diagnostics"] = {"apply_poly_time": True}
    reference.sweep_params.pop("direct_poly_time", None)
    save_reference(session, reference)

    plan = plan_sweep(reference, combos=[{"zero_fill": 2}])
    runs = run_sweep(session, plan, reference=reference, resume=False)
    used = runs[0].parameters_used
    assert used["direct_poly_time"] is True      # 参考的决定被继承
    assert used["zero_fill"] == 2                # 组合指定的仍然生效

    plan2 = plan_sweep(reference, combos=[{"direct_poly_time": False}])
    runs2 = run_sweep(session, plan2, reference=reference, resume=False)
    assert runs2[0].parameters_used["direct_poly_time"] is False


def test_workflow_script_is_saved_in_run_dir_and_reference_work_dir(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """规范 D1:每个 workflow 存完整脚本;脚本与参考共用该条件的工作目录。"""
    from core.project.manager import sha256_file

    backend = _FakeSweepBackend()
    result = run_parameter_study(
        tmp_path / "script_saved",
        bruker_dir / "hsqc_2d",
        combos=[{"zero_fill": 2, "window.F1.off": 0.45}],
        params={"phase_route": "none"},
        backend=backend,
    )
    reference = result.reference
    assert reference is not None and reference.work_dir
    work = Path(reference.work_dir)
    assert work.is_dir()
    assert (work / "W0001_A.com").is_file()      # 候选脚本与参考同目录

    run = result.runs[0]
    saved = Path(run.script_path)
    assert saved.is_file()
    assert saved.parent == Path(run.run_dir) and saved.name == "process.com"
    assert run.script_sha256 == sha256_file(saved)
    log = Path(run.log_path).read_text(encoding="utf-8")
    assert "处理脚本" in log and "W0001_A.com" in log
    payload = json.loads(Path(run.run_dir, "run.json").read_text(encoding="utf-8"))
    assert payload["script_path"] == run.script_path and payload["script_sha256"]


def test_legacy_reference_script_found_in_data_level_dir(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """旧参考没有 work_dir 时,组合脚本仍能从数据级 <data>.nmrpipe 找到并留档。"""
    from nmrforge_api.reference import save_reference

    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "legacy_script", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    reference.work_dir = ""                      # 模拟旧参考(未记录工作目录)
    save_reference(session, reference)

    plan = plan_sweep(reference, combos=[{"zero_fill": 1}])
    runs = run_sweep(session, plan, reference=reference, resume=False)
    run = runs[0]
    assert run.status in (STATUS_SUCCESS, STATUS_WARNING)
    assert Path(run.script_path).is_file()
    dataset = run.dataset
    data_level = (
        session.root
        / str(dataset["exp_id"])
        / str(dataset["data_id"])
        / f"{dataset['data_id']}.nmrpipe"
    )
    assert (data_level / "W0001_A.com").is_file()


def test_missing_workflow_script_is_reported_not_silent(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """找不到脚本时必须给 processing_script_not_found 警告(不静默留空)。"""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "script_missing", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    plan = plan_sweep(reference, combos=[{"zero_fill": 1}])

    real_process = backend.process

    def process_with_relocated_script(*args, **kwargs):
        out = real_process(*args, **kwargs)
        name = str(kwargs.get("script_name") or "")
        if name:
            src = Path(str(backend.work_dir)) / name
            if src.is_file():
                src.rename(src.with_name(f"moved_{name}"))
        return out

    backend.process = process_with_relocated_script
    runs = run_sweep(session, plan, reference=reference, resume=False)
    run = runs[0]
    assert run.status == STATUS_WARNING
    assert any(w["code"] == "processing_script_not_found" for w in run.warnings)
    assert run.script_path == "" and run.script_sha256 == ""
    assert Path(run.peak_table_path("parabolic")).is_file()   # 峰表仍然产出


# ---------------------------------- 2026-09-16:基线渲染口径 + 参考优化开关 + 生效自检
def test_baseline_order_renders_with_auto_flag(bruker_dir: Path, tmp_path: Path) -> None:
    """mode=order 必须渲染 ``POLY -ord N -auto``:裸 -ord N 在 NMRPipe 里是恒等。"""
    from backend.script_generator import generate_process_script
    from core.planning.method_selector import select_method
    from nmrforge_api import add_dataset, open_study
    from workflow.stepwise import read_experiment

    session = open_study(tmp_path / "poly_render", backend=_FakeSweepBackend())
    add_dataset(session, bruker_dir / "hsqc_2d")
    experiment = read_experiment(session.manager, "exp_001", "d_001")
    plan = select_method(experiment)
    script = generate_process_script(
        experiment,
        plan,
        in_file="d_001.fid",
        out_file="out.ft2",
        baseline={
            "F1": {"enabled": True, "mode": "order", "order": 3},
            "F2": {"enabled": True, "mode": "auto", "order": 0},
        },
    )
    assert "| nmrPipe -fn POLY -ord 3 -auto" in script      # 频域 order 模式
    assert "| nmrPipe -fn POLY -auto \\" in script          # auto 模式保持
    assert "| nmrPipe -fn POLY -ord 3 \\" not in script     # 不允许裸 -ord
    off = generate_process_script(
        experiment,
        plan,
        in_file="d_001.fid",
        out_file="out.ft2",
        baseline={"F1": {"enabled": False, "mode": "order", "order": 3},
                  "F2": {"enabled": False, "mode": "auto", "order": 0}},
    )
    assert "POLY -ord" not in off and "POLY -auto" not in off


def test_reference_optimize_switch_is_external_and_recorded(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """参考优化可用 params.reference_optimize 关闭(仅测试用),且落档、不进组合基底。"""
    import workflow.baseline_optimize as baseline_optimize

    def explode(*args, **kwargs):
        raise AssertionError("外部关闭后不应再调用基线优化")

    monkeypatch.setattr(baseline_optimize, "optimize_baseline", explode)
    backend = _FakeSweepBackend()
    result = run_reference_study(
        tmp_path / "ref_opt_off",
        bruker_dir / "hsqc_2d",
        params={
            "phase_route": "none",
            "baseline": {"F1": {"enabled": False, "mode": "auto", "order": 0}},
            "reference_optimize": {"baseline": "off", "window": "off"},
        },
        backend=backend,
    )
    reference = result.reference()
    assert reference is not None
    # 开关被记录(可审计),且沿用调用方给的 baseline
    assert reference.params["reference_optimize"] == {
        "baseline": "off", "window": "off",
    }
    assert reference.params["baseline"]["F1"]["enabled"] is False
    # 参考阶段的开关不属于处理参数,不得进入组合基底
    assert "reference_optimize" not in reference.sweep_params
    assert Path(reference.script_path).is_file()   # 参考脚本照常冻结


def test_window_subparam_without_type_is_rejected(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """窗子参数必须与窗型成对:该轴 type=none 时写 off/end/… 直接报错(曾全程空转)。"""
    from nmrforge_api.reference import save_reference

    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "window_gate", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    reference.sweep_params["window"] = {"F1": {"type": "none"}, "F2": {"type": "none"}}
    save_reference(session, reference)

    with pytest.raises(SweepError, match="不会生效"):
        plan_sweep(reference, combos=[{"window.F1.off": 0.35}])
    # 成对写 type 就允许
    plan = plan_sweep(
        reference,
        combos=[{"window.F1.type": "sine_bell", "window.F1.off": 0.35}],
    )
    assert plan.combos[0]["window.F1.type"] == "sine_bell"
    # baseline.order 而 mode≠order → 提示(不阻断)
    plan2 = plan_sweep(reference, combos=[{"baseline.F1.order": 3}])
    assert any("order 不会生效" in note for note in plan2.notes)


def test_no_spectrum_change_warning_and_script_diff(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """参数没改谱 → no_spectrum_change 警告 + script_diff 留档(按条件)。"""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "no_change", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    # 假后端只按 window.F1.off / zero_fill 改谱 → 这两个都不写就是“没改谱”
    plan = plan_sweep(reference, combos=[{"baseline.F1.order": 3}])
    runs = run_sweep(session, plan, reference=reference, resume=False)
    run = runs[0]
    assert any(w["code"] == "no_spectrum_change" for w in run.warnings)
    assert run.status == STATUS_WARNING
    assert run.script_diff  # 有留档
    payload = json.loads(Path(run.run_dir, "run.json").read_text(encoding="utf-8"))
    assert payload["script_diff"]

    # 真的改了参数(假后端按 off 平移峰位)→ 谱变化 → 不再报该警告
    plan2 = plan_sweep(reference, combos=[{"window.F1.off": 0.45, "zero_fill": 2}])
    runs2 = run_sweep(session, plan2, reference=reference, resume=False)
    run2 = runs2[0]
    assert not any(w["code"] == "no_spectrum_change" for w in run2.warnings)
    assert run2.script_diff and run2.script_diff["n_changed"] > 0


# ------------------------------------- Phase 12:批量失败隔离 + requested vs actual
def test_sweep_failure_is_isolated_and_parameters_recorded(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单个 workflow 失败不吞掉其它 workflow;失败/成功两类都留 requested/actual。

    Phase 12「Batch: failure isolation + requested vs actual parameters」:
    第一个 workflow 的后端处理抛错,第二个必须继续跑;run.json 里 requested
    始终是用户原样参数,used 是合并后的实际参数(两类运行都要能审计)。
    """
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "isolation", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    plan = plan_sweep(
        reference, combos=[{"zero_fill.F1": 1}, {"zero_fill.F1": 2}]
    )

    real_process = backend.process
    calls = {"n": 0}

    def flaky_process(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("forced workflow failure")
        return real_process(*args, **kwargs)

    monkeypatch.setattr(backend, "process", flaky_process)
    runs = run_sweep(session, plan, reference=reference, resume=False)

    assert len(runs) == 2
    assert calls["n"] == 2, "失败后必须继续执行后面的 workflow(隔离)"
    failed, ok = runs
    assert failed.status == "failed"
    assert "forced workflow failure" in failed.message
    assert ok.status in ("success", "success_with_warning")

    # requested = 用户原样;used = 合并后的实际参数(失败也保留)
    assert failed.parameters_requested == {"zero_fill.F1": 1}
    assert ok.parameters_requested == {"zero_fill.F1": 2}
    assert failed.parameters_used["zero_fill"]["F1"] == 1
    assert ok.parameters_used["zero_fill"]["F1"] == 2

    failed_payload = json.loads(
        Path(failed.run_dir, "run.json").read_text(encoding="utf-8")
    )
    assert failed_payload["status"] == "failed"
    assert failed_payload["parameters_requested"] == {"zero_fill.F1": 1}
    assert failed_payload["parameters_used"]["zero_fill"]["F1"] == 1
    assert not failed.peak_table_path("parabolic")

    ok_payload = json.loads(Path(ok.run_dir, "run.json").read_text(encoding="utf-8"))
    assert ok_payload["status"] in ("success", "success_with_warning")
    assert ok_payload["parameters_used"]["zero_fill"]["F1"] == 2
    assert Path(ok.peak_table_path("parabolic")).is_file()


# ------------------------------------- Phase 21:用户可见错误信息(CLI 出口)
def test_cli_unexpected_error_is_actionable_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """非预期异常不能只甩类型名/堆栈:给一句可执行的提示 + debug 通道(Phase 21)。"""
    from nmrforge_api.cli import DEBUG_ENV, describe_exception

    bad = tmp_path / "not_a_dir.txt"
    bad.write_text("x", encoding="utf-8")
    assert cli_main(["status", "--study", str(bad)]) == 2
    out = capsys.readouterr().out
    assert "错误:" in out and "提示:" in out and DEBUG_ENV in out
    assert "Traceback" not in out, "默认不能把裸 traceback 甩给用户"

    # 异常 → 提示的映射:路径、缺字段、内容非法各自有可照做的说法
    assert "找不到文件或目录" in describe_exception(
        FileNotFoundError(2, "no such file", "x.json")
    )
    assert "缺少必需字段" in describe_exception(KeyError("peak_id"))
    assert "输入内容不合法" in describe_exception(ValueError("bad axis spec"))
    # 结构不符类:保留原始文本,但必须带一句解释(不能只出现 NoneType/KeyError 之名)
    structure = describe_exception(AttributeError("'NoneType' object has no attribute 'x'"))
    assert structure.startswith("输入数据与预期结构不符")


def test_cli_debug_flag_and_env_show_the_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """--debug / NMRFORGE_DEBUG=1 才打印完整堆栈(默认只给提示)。"""
    from nmrforge_api.cli import DEBUG_ENV

    bad = tmp_path / "not_a_dir.txt"
    bad.write_text("x", encoding="utf-8")

    assert cli_main(["status", "--study", str(bad), "--debug"]) == 2
    captured = capsys.readouterr()
    assert "完整堆栈(debug)" in captured.out
    assert "Traceback" in captured.err, "堆栈走 stderr(debug 通道),不混进给用户看的 stdout"

    monkeypatch.setenv(DEBUG_ENV, "1")
    assert cli_main(["status", "--study", str(bad)]) == 2
    assert "Traceback" in capsys.readouterr().err
