"""nmrforge_api(参数敏感性接口 v0.1)回归。

覆盖:网格展开/覆盖合并、亚像素峰位测量精度、不确定度公式、端到端研究
(参考谱→扫描→峰位→汇总→记录)、断点续跑、NUS 边界、导入失败提示、
CLI 与「不依赖 Qt」契约。
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
    DatasetError,
    SensitivityError,
    SweepError,
    expand_grid,
    load_plan,
    load_runs,
    measure_peak_positions,
    merge_overrides,
    open_study,
    position_uncertainty,
    run_parameter_study,
    uncertainty_summary,
)
from nmrforge_api.cli import main as cli_main
from nmrforge_api.peaks import (
    PeakMeasurement,
    peak_coordinates,
    read_reference_peaks,
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
    from nmrglue.fileio import pipe

    shape = (_N15_SIZE, _H1_SIZE)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    arr = np.zeros(shape, dtype=float)
    for index, (y, x) in enumerate((_PEAK_A, _PEAK_B), start=1):
        cy, cx = y + shift_y, x + shift_x
        arr += (120.0 - 20.0 * (index - 1)) * np.exp(
            -(((yy - cy) ** 2) / (2 * 1.2**2) + ((xx - cx) ** 2) / (2 * 1.4**2))
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
        target = work / (out_file or f"{experiment.dataset_id}.ft2")
        if out_file:
            target = work / "_intermediate" / out_file
        _write_ft2(target, shift_y=shift, shift_x=shift / 2.0)
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


def test_measure_peak_positions_recovers_subpoint_shift(tmp_path: Path) -> None:
    peaks = _write_peak_table(tmp_path / "ref.list")
    from core.peaks.peak_table import load_peaks

    rows = load_peaks(peaks)
    # 平移 +1.25 点(15N)/ +0.625 点(1H)后,测量值应还原到 1/20 点以内
    spectrum = _write_ft2(tmp_path / "shift.ft2", shift_y=1.25, shift_x=0.625)
    measured = measure_peak_positions(spectrum, rows, window_pts=3)
    assert len(measured) == 2
    for index, (y, x) in enumerate((_PEAK_A, _PEAK_B)):
        item = measured[index]
        assert item.found
        expected_n = _n15_ppm(y + 1.25)
        expected_h = _h1_ppm(x + 0.625)
        assert abs(item.positions["15N"] - expected_n) < 0.2 * _n15_step()
        assert abs(item.positions["1H"] - expected_h) < 0.2 * _h1_step()
        assert not item.window_edge
        assert not item.boundary
        assert not item.out_of_range
    # 整数截断会差整整 1 个点;这里必须明显好于它
    assert abs(measured[0].positions["15N"] - _n15_ppm(_PEAK_A[0])) > 0.5 * _n15_step()


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


def test_position_uncertainty_formula() -> None:
    def measurement(run: str, h: float, n: float) -> PeakMeasurement:
        return PeakMeasurement(
            peak_id=1,
            assignment="G1",
            reference={"1H": 5.0, "15N": 119.0},
            positions={"1H": h, "15N": n},
            deltas={"1H": h - 5.0, "15N": n - 119.0},
            found=True,
        )

    runs = {
        "s0001": [measurement("s0001", 5.0, 119.0)],
        "s0002": [measurement("s0002", 5.02, 119.2)],
        "s0003": [measurement("s0003", 4.98, 118.8)],
    }
    items = position_uncertainty(runs, csp_n_weight=0.2)
    assert len(items) == 1
    item = items[0]
    assert item.n_runs == 3
    sigma_h = 0.02
    sigma_n = 0.2
    assert item.sigma["1H"] == pytest.approx(sigma_h, abs=1e-6)
    assert item.sigma["15N"] == pytest.approx(sigma_n, abs=1e-6)
    expected = math.sqrt(sigma_h**2 + (0.2 * sigma_n) ** 2)
    assert item.delta_std == pytest.approx(expected, rel=1e-6)
    assert item.delta_max == pytest.approx(expected, rel=1e-6)
    summary = uncertainty_summary(items, csp_n_weight=0.2, n_runs=3)
    assert summary["n_peaks"] == 1
    assert summary["delta_std_ppm"]["median"] == pytest.approx(expected, rel=1e-6)


def test_position_uncertainty_requires_all_nuclei() -> None:
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
    items = position_uncertainty({"a": [partial], "b": [full]})
    assert items[0].n_runs == 1
    assert items[0].missing_runs == 1


# ------------------------------------------------------------------ 端到端
def test_run_parameter_study_end_to_end(
    tmp_path: Path, bruker_dir: Path
) -> None:
    dataset = bruker_dir / "hsqc_2d"
    root = tmp_path / "study"
    peaks = _write_peak_table(tmp_path / "reference.list")
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        root,
        dataset,
        axes={"window.F1.off": [0.35, 0.40, 0.45], "zero_fill": [1, 2]},
        params={"phase_route": "none"},
        peaks=peaks,
        backend=backend,
    )
    assert len(result.runs) == 6
    assert all(run.status == "success" for run in result.runs)
    assert backend.convert_calls == 1  # fid 只转一次

    reference = result.reference
    assert Path(reference.frozen_spectrum).is_file()
    assert Path(reference.script_path).is_file()
    assert reference.script_sha256 and reference.spectrum_sha256
    # 扫描不得替换项目里的活动谱
    session = result.session
    active = Path(session.data_entry().spectrum_path)
    from core.project.manager import sha256_file

    assert sha256_file(active) == reference.spectrum_sha256

    # 每个组合都留下脚本 + 谱 + 两个峰的测量
    for run in result.runs:
        assert Path(run.script_path).is_file()
        assert Path(run.spectrum_path).is_file()
        assert len(run.measurements) == 2
        assert all(m.found for m in run.measurements)

    # 相位锁定:参考记录的 direct_phase 必须原样传给每个组合
    assert all(run.phase_locked for run in result.runs)
    locked_calls = [call for call in backend.process_calls if call["phase"]]
    assert len(locked_calls) == len(result.runs)
    assert locked_calls[0]["phase"] == {"F2": (0.0, 0.0)}
    # 参数 → 峰位:0.35 与 0.45 相差 2.5 点(15N),测量应还原
    by_off = {
        round(float(run.combo["window.F1.off"]), 3): run for run in result.runs
    }
    low = by_off[0.35].measurements[0].positions["15N"]
    high = by_off[0.45].measurements[0].positions["15N"]
    expected_low = _n15_ppm(_PEAK_A[0] + 1.25)
    expected_high = _n15_ppm(_PEAK_A[0] - 1.25)
    assert low == pytest.approx(expected_low, abs=0.2 * _n15_step())
    assert high == pytest.approx(expected_high, abs=0.2 * _n15_step())

    # 不确定度与记录
    assert all(item.delta_std > 0 for item in result.uncertainties)
    summary = result.summary
    assert summary["n_peaks"] == 2
    assert summary["delta_std_ppm"]["median"] > 0
    assert summary["csp_n_weight"] == 0.2
    for name in (
        "manifest",
        "sweep_plan",
        "runs",
        "peak_positions",
        "uncertainty",
        "uncertainty_summary",
    ):
        assert Path(result.records[name]).is_file(), name
    manifest = json.loads(Path(result.records["manifest"]).read_text(encoding="utf-8"))
    assert manifest["sweep"]["axes"]["window.F1.off"] == [0.35, 0.4, 0.45]
    assert manifest["sweep"]["grid_sha256"]
    assert manifest["reference"]["script_sha256"] == reference.script_sha256
    rows = Path(result.records["peak_positions"]).read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1 + 6 * 2 * 2  # 表头 + 6 组合 × 2 峰 × 2 核

    # 断点续跑:再跑一次不新增处理调用
    calls_before = len(backend.process_calls)
    again = run_parameter_study(
        root,
        None,
        axes={"window.F1.off": [0.35, 0.40, 0.45], "zero_fill": [1, 2]},
        params={"phase_route": "none"},
        peaks=peaks,
        backend=backend,
    )
    assert len(backend.process_calls) == calls_before
    assert [run.run_id for run in again.runs] == [run.run_id for run in result.runs]

    # 计划/运行记录可读回
    session2 = open_study(root, backend=backend)
    assert load_plan(session2) is not None
    assert len(load_runs(session2)) == 6
    assert load_reference(session2) is not None


def test_run_parameter_study_auto_picks_reference_peaks(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """默认不需要外部峰表:参考峰位由 NMRForge 自动选峰产生并冻结。"""
    root = tmp_path / "auto_peaks"
    backend = _FakeSweepBackend()
    result = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        axes={"zero_fill": [1, 2]},
        params={"phase_route": "none"},
        backend=backend,
    )
    reference = result.reference
    assert reference.peak_source == "auto"
    assert reference.peak_table_path.endswith("reference.list")
    assert Path(reference.peak_table_path).is_file()
    assert reference.peak_table_sha256
    assert reference.peak_count == 2  # 合成谱只有 2 个远高于 35σ 的峰
    assert len(result.runs) == 2
    assert all(run.status == "success" for run in result.runs)
    assert all(len(run.measurements) == reference.peak_count for run in result.runs)
    manifest = json.loads(
        Path(result.records["manifest"]).read_text(encoding="utf-8")
    )
    assert manifest["peaks"]["source"] == "auto"
    assert manifest["peaks"]["sha256"] == reference.peak_table_sha256
    assert manifest["peaks"]["count"] == reference.peak_count
    # 峰位不确定度基于自动选出的峰,而不是外部峰表
    assert {item.peak_id for item in result.uncertainties} == {1, 2}


def test_run_parameter_study_nus_2d(tmp_path: Path, bruker_dir: Path) -> None:
    """2D NUS:参考与扫描都走 reconstruct_nus,候选隔离 + 相位锁定。"""
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
    assert all(run.status == "success" and run.phase_locked for run in result.runs)
    assert all(
        len(run.measurements) == reference.peak_count for run in result.runs
    )
    # 每个组合都通过 reconstruct_nus 的候选输出参数隔离产物
    candidate_calls = [c for c in backend.reconstruct_calls if c["out_file"]]
    assert len(candidate_calls) == 3
    assert all(c["params"]["direct_phase"] == [0.0, 0.0] for c in candidate_calls)
    assert all(c["script_name"].endswith(".com") for c in candidate_calls)
    # 候选谱互不覆盖,且参考谱保持独立
    assert len({run.spectrum_path for run in result.runs}) == 3
    assert all(run.spectrum_sha256 for run in result.runs)
    # SMILE 参数确实进入了网格,而且候选谱真的不同(键别名漏了会全同 → σ=0)
    assert {round(run.combo["nSigma"], 1) for run in result.runs} == {3.0, 5.0, 7.0}
    assert len({run.spectrum_sha256 for run in result.runs}) == 3
    assert result.summary["delta_std_ppm"]["max"] > 0
    assert result.summary["n_peaks"] == reference.peak_count


def test_reference_state_persists_across_sessions(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """分步 CLI(独立进程)能看到已登记的 fid/活动谱:项目状态必须落盘。"""
    root = tmp_path / "persist"
    backend = _FakeSweepBackend()
    run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        axes={"zero_fill": [1]},
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
    # 一条 pick_peaks 运行记录也已落盘
    assert any(
        run.workflow_ref == "pick_peaks" for run in session2.manager.project.workflow_runs
    )


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


def test_sweep_rejects_3d_nus(tmp_path: Path, bruker_dir: Path) -> None:
    """3D NUS 仍不支持:NUS 只开放 2D。"""
    from nmrforge_api import add_dataset, plan_sweep, run_sweep
    from nmrforge_api.reference import ReferenceSpectrum

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
    from nmrforge_api import add_dataset

    session = open_study(tmp_path / "study", backend=_FakeSweepBackend())
    bogus = tmp_path / "not_bruker"
    bogus.mkdir()
    with pytest.raises(DatasetError, match="Bruker"):
        add_dataset(session, bogus)
    with pytest.raises(DatasetError, match="不存在"):
        add_dataset(session, tmp_path / "missing")


def test_cli_status(tmp_path: Path, bruker_dir: Path, capsys) -> None:
    root = tmp_path / "study"
    result = run_parameter_study(
        root,
        bruker_dir / "hsqc_2d",
        axes={"window.F1.off": [0.40]},
        params={"phase_route": "none"},
        peaks=_write_peak_table(tmp_path / "reference.list"),
        backend=_FakeSweepBackend(),
    )
    assert result.runs[0].status == "success"
    assert cli_main(["status", "--study", str(root)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["runs"]["total"] == 1
    assert payload["reference"]["script_sha256"]
    assert cli_main(["report", "--study", str(root)]) == 0
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


def test_reference_phase_uses_all_axes_when_direct_missing() -> None:
    """统一路线把相位写在 phases(各轴 PS),锁定时必须全部继承。"""
    effective = {
        "phases": {"F1": [172.5, 0.0], "F2": [27.5, 0.0]},
    }
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
    # direct_phase 与 phases 合并:直接维以 direct_phase 为准
    assert reference_phase(
        {"direct_phase": {"F2": [1.0, 2.0]}, "phases": {"F1": [3.0, 0.0]}}
    ) == {"F1": [3.0, 0.0], "F2": [1.0, 2.0]}


def test_reference_phase_handles_nus_flat_direct_phase() -> None:
    """NUS 重构路线把直接维相位记成扁平 [p0, p1],需映射到 F{ndim}。"""
    effective = {
        "phases": {"F1": [172.5, 0.0]},
        "direct_phase": [27.5, 0.0],
    }
    assert reference_phase(effective, ndim=2) == {
        "F1": [172.5, 0.0],
        "F2": [27.5, 0.0],
    }


def test_error_hierarchy() -> None:
    assert issubclass(DatasetError, SensitivityError)
    assert issubclass(SweepError, SensitivityError)
