"""nmrforge_api(v0.2,2026-09-13 规范)回归。

覆盖:参考工作流(1 脚本 + 2 峰表)、workflow_id、三层参数留档、同一张谱两种
定位的两张峰表、detected=false 保留、状态三值、完整日志与版本、两条件 A/B
同参数同峰身份、自动参数实际值、软件边界(不做 CSP/统计)、CLI 与「不依赖 Qt」。

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
    SweepError,
    add_dataset,
    build_reference,
    condition_token,
    ensure_reference_peaks,
    expand_grid,
    load_plan,
    load_runs,
    load_workflows,
    measure_peak_positions,
    merge_overrides,
    open_study,
    plan_sweep,
    read_peak_table,
    run_parameter_study,
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
    """规范 C/D/E/G:W0001… + 三层参数 + 两张峰表 + 完整日志 + 版本 + 状态。"""
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
        window_ppm=1.0,
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
        # 两张峰表:同字段、同峰身份、逐峰 SNR
        rows_p = read_peak_table(Path(run.peak_table_path("parabolic")))
        rows_g = read_peak_table(Path(run.peak_table_path("gaussian")))
        assert len(rows_p) == len(rows_g) == 2
        assert [row["reference_peak_id"] for row in rows_p] == ["R0001", "R0002"]
        assert [row["reference_peak_id"] for row in rows_g] == ["R0001", "R0002"]
        assert all(row["workflow_id"] == run.workflow_id for row in rows_p)
        assert all(row["workflow_id"] == run.workflow_id for row in rows_g)
        assert all(row["detected"] for row in rows_p)
        assert all(row["SNR"] > 0 for row in rows_p)
        assert all(row["assignment"] for row in rows_p)
        assert all(row["localization_method"] == "gaussian" for row in rows_g)
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
        _n15_ppm(_PEAK_A[0] + 1.25), abs=0.2 * _n15_step()
    )
    assert by_off[0.45].measurements[0].positions["15N"] == pytest.approx(
        _n15_ppm(_PEAK_A[0] - 1.25), abs=0.2 * _n15_step()
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

    # 逐组合:物理窗口一致,点数随填零变密
    by_fill = {
        int(round(float(run.combo["zero_fill"]))): run.window
        for run in result.runs
    }
    for factor in (1, 4):
        spec = by_fill[factor]["0"]
        assert spec["source"] == "ppm(自动:1.5×线宽)"
        assert spec["nucleus"] == "15N"
        assert spec["ppm"] == pytest.approx(1.5 * 15.0 / _N15_OBS, rel=0.02)
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
    assert seen["ppm"][0] == pytest.approx(1.5 * 15.0 / _N15_OBS, rel=0.02)
    assert measurement["window_by_axis"]["0"]["source"] == "ppm(自动:1.5×线宽)"
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
        window_ppm=1.0,
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
        window_ppm=1.0,
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


def test_undetected_peak_is_kept_with_detected_false(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """规范 F3/G3:测不到的参考峰保留记录(detected=false)+ 明确警告。"""
    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "detect", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    plan = plan_sweep(reference, axes={"zero_fill": [1]})
    rows = [
        {
            "N_shift": _n15_ppm(_PEAK_A[0]),
            "H_shift": _h1_ppm(_PEAK_A[1]),
            "label": "G1",
        },
        {"C_shift": 40.0, "label": "C1"},  # 当前谱没有 13C 轴 → 测不到
    ]
    runs = run_sweep(
        session,
        plan,
        reference=reference,
        peaks=rows,
        window_ppm=1.0,
        resume=False,
    )
    run = runs[0]
    table = read_peak_table(Path(run.peak_table_path("parabolic")))
    assert len(table) == 2  # 行保留,不删
    assert table[1]["reference_peak_id"] == "R0002"
    assert table[1]["detected"] is False
    assert math.isnan(table[1]["H_ppm"]) and math.isnan(table[1]["N_ppm"])
    assert any(w["code"] == "peak_not_detected" for w in run.warnings)
    assert run.status == STATUS_WARNING
    assert run.peak_localization["parabolic"]["n_detected"] == 1


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
        session, plan, reference=reference, window_ppm=1.0, resume=False
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


def test_gaussian_measurement_exception_becomes_failed_run(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gaussian 测量异常必须落成 failed run.json，而不是终止整轮。"""
    import nmrforge_api.sweep as sweep_module

    backend = _FakeSweepBackend()
    session = open_study(tmp_path / "gaussian_error", backend=backend)
    add_dataset(session, bruker_dir / "hsqc_2d")
    reference = build_reference(session, params={"phase_route": "none"})
    reference = ensure_reference_peaks(session, reference)
    plan = plan_sweep(reference, axes={"zero_fill": [1]})
    real_measure = sweep_module.measure_peak_positions

    def explode_gaussian(*args, **kwargs):
        if kwargs.get("refine") == "gaussian":
            raise RuntimeError("forced gaussian error")
        return real_measure(*args, **kwargs)

    monkeypatch.setattr(sweep_module, "measure_peak_positions", explode_gaussian)
    runs = run_sweep(session, plan, reference=reference, resume=False)
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
    """规范 I:同一 workflow 对 A/B 用同一组参数,峰身份共享,各出峰表。"""
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
        identities = set()
        for run in runs:
            assert Path(run.run_dir).name == run.condition
            assert Path(run.run_dir, "peak_table_parabolic.csv").is_file()
            assert Path(run.run_dir, "peak_table_gaussian.csv").is_file()
            rows = read_peak_table(Path(run.peak_table_path("parabolic")))
            identities.add(tuple(row["reference_peak_id"] for row in rows))
        assert len(identities) == 1  # 峰身份跨条件共享
    # B 条件的参考沿用主条件的峰身份
    refs = {key: ref for key, ref in result.references.items()}
    shared = [ref for ref in refs.values() if ref.peak_source.startswith("shared:")]
    assert len(shared) == 1
    assert shared[0].condition == "B"
    assert shared[0].peak_count == 2
    # 每条件各转换一次 fid(参考)
    assert backend.convert_calls == 2


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
    for run in result.runs:
        rows = read_peak_table(Path(run.peak_table_path("parabolic")))
        assert [(row["reference_peak_id"], row["assignment"]) for row in rows] == [
            ("R0001", "ONLY")
        ]


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
    # 两张峰表都在
    for run in result.runs:
        assert Path(run.peak_table_path("parabolic")).is_file()
        assert Path(run.peak_table_path("gaussian")).is_file()


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
    assert load_plan(session2) is not None
    assert len(load_runs(session2)) == 1
    assert len(load_workflows(session2)) == 1


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
        axes={"ext_lo": ["10.5"], "bogus.key": [1], "zero_fill": [1]},
    )
    joined = "\n".join(plan.notes)
    assert "确定性" in joined and "ext_lo" in joined
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
        run_sweep(session, plan, reference=reference, peaks=[{"N_shift": 1.0}])


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
    assert cli_main(["sweep", "--study", str(root), "--grid", "missing.yaml"]) == 2


def test_api_does_not_import_qt() -> None:
    """对外接口必须能在无 Qt 环境/集群上导入。"""
    code = (
        "import sys, nmrforge_api; "
        "assert not any(m.startswith('PyQt6') for m in sys.modules), "
        "sorted(m for m in sys.modules if m.startswith('PyQt6')); "
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
