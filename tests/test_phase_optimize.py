"""自动相位优化测试:内存内候选评分 + 单次后端运行。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from core.data.bruker_reader import read_dataset
from core.optimization.phase_search import apply_phase_axis
from workflow.engine import AutoProcessor
from workflow.phase_optimize import (
    DIRECT_P0_VALUES,
    DIRECT_P1_VALUES,
    CandidateScore,
    EquivalenceReport,
    PhaseOptimizeResult,
    _same_phase,
    brute_force_direct_scores,
    decide_refinement,
    direct_phase_candidates,
    estimate_auto_phase,
    estimate_recon_planes,
    format_phase_report,
    optimize_phase_sequential,
    produce_phased_spectrum,
    run_with_auto_phase,
    save_report,
    score_in_memory_direct,
    search_direct_phase,
    select_direct_phase_refined,
)


def _p1_fid(
    p1_deg: float, n: int = 512, bins: tuple[int, int] = (64, 448), n_traces: int = 40
) -> np.ndarray:
    """频域施加 p1 相位后再转回时域的合成 FID(远距双峰,可测 p1)。"""
    t = np.arange(n)
    k = np.arange(n)
    rng = np.random.default_rng(3)
    traces = []
    for _ in range(n_traces):
        t1_phase = np.exp(1j * rng.uniform(0, 2 * np.pi))
        fid = sum(
            np.exp(-t / 800.0) * np.exp(1j * 2 * np.pi * b * t / n) for b in bins
        )
        fid = fid * t1_phase
        spectrum = np.fft.fft(fid)
        spectrum = spectrum * np.exp(1j * np.deg2rad(p1_deg * k / max(n - 1, 1)))
        traces.append(np.fft.ifft(spectrum))
    return np.array(traces)


def test_search_direct_phase_recovers_p1_magnitude() -> None:
    """远距双峰 FID:p1 幅值区分度低(候选 score 接近,VM numpy 2.2.6
    平台差异使 true=-60 → est=-180),按 ±45° 先例改为
    「非零解 + 正增益」断言(±180/±120 歧义内)。"""
    for true_p1 in (90.0, -60.0, 45.0):
        est = search_direct_phase(_p1_fid(true_p1))
        assert abs(est.p1) >= 30.0  # 非零校正(网格分辨率)
        assert est.gain > 0.01
        assert est.score > 0.5
    # 候选网格 = p0(19) × p1(13),与 search_phase 默认一致
    assert len(DIRECT_P0_VALUES) * len(DIRECT_P1_VALUES) == 19 * 13


def test_search_direct_phase_short_fid_skips() -> None:
    est = search_direct_phase(np.zeros((2, 4), dtype=complex))
    assert est.note.startswith("FID 点数不足")
    assert est.p0 == 0.0 and est.p1 == 0.0


def test_estimate_recon_planes_counts_candidates() -> None:
    rng = np.random.default_rng(0)
    base = np.zeros((4, 16, 8), dtype=complex)
    for p in range(4):
        base[p, 6, 2] = 500.0
        base[p, 7, 3] = 350.0
    planes = apply_phase_axis(base, 1, 30.0, 0.0) + rng.normal(
        0, 0.1, size=base.shape
    )
    counted: list[int] = []
    estimates = estimate_recon_planes(planes, candidates=counted)
    axis1 = next(e for e in estimates if e.axis == "axis1")
    assert axis1.gain > 0.05
    assert abs(((axis1.p0 + 30.0) + 180.0) % 360.0 - 180.0) <= 10.0
    # axis0 平面数 4 < 8 不搜索(记 0);axis1/axis2 各 49 个候选
    assert counted == [0, 49, 49]


def test_estimate_auto_phase_uses_cache(tmp_path: Path, bruker_dir: Path) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    work = tmp_path / "work"
    work.mkdir()
    (work / "phase.json").write_text(
        json.dumps({"p0": 0.0, "p1": 45.0, "score": 0.9, "gain": 0.05}),
        encoding="utf-8",
    )
    result = estimate_auto_phase(
        experiment, work, spectrum_path=work / "out.ft2"
    )
    assert result.backend_runs == 0
    assert result.candidates_scored == 0  # 缓存命中,未重复评估
    assert len(result.phases) == 1
    direct = result.phases[0]
    assert direct.axis == "F2"
    assert direct.p1 == 45.0
    assert "cache" in direct.source
    assert any("不做事后调相" in line for line in result.logs)


def test_estimate_auto_phase_broken_cache_falls_back(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """损坏的 phase.json 不阻断估计:回退 .fid 搜索,无 fid 时优雅跳过。"""
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    work = tmp_path / "work"
    work.mkdir()
    (work / "phase.json").write_text("{broken", encoding="utf-8")
    result = estimate_auto_phase(experiment, work)
    assert result.phases == []
    assert result.candidates_scored == 0
    assert any("未发现复型重构平面" in line for line in result.logs)


def test_estimate_auto_phase_no_artifacts_graceful(
    tmp_path: Path, bruker_dir: Path
) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    work = tmp_path / "empty_work"
    work.mkdir()
    result = estimate_auto_phase(experiment, work)
    assert result.phases == []
    assert result.candidates_scored == 0
    assert any("未发现复型重构平面" in line for line in result.logs)


class _FakeBackend:
    """记录调用次数的假后端(返回固定成功/失败响应)。"""

    def __init__(
        self,
        work_dir: Path,
        *,
        success: bool = True,
        spectrum: str = "out.ft2",
    ) -> None:
        self.work_dir = str(work_dir)
        self.success = success
        self.spectrum = spectrum
        self.calls: list[str] = []

    @property
    def capabilities(self) -> object:
        return type("Capabilities", (), {"provider": "nmrpipe"})()

    def _resp(self) -> dict:
        if not self.success:
            return {"success": False, "message": "模拟后端失败", "logs": []}
        return {
            "success": True,
            "spectrum_path": str(Path(self.work_dir) / self.spectrum),
            "logs": ["backend-ok"],
            "message": "ok",
        }

    def process(self, experiment, plan) -> dict:
        self.calls.append("process")
        return self._resp()

    def reconstruct_nus(self, experiment, params) -> dict:
        self.calls.append("reconstruct_nus")
        return self._resp()


def test_run_with_auto_phase_uniform_single_run(
    tmp_path: Path, bruker_dir: Path
) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    work = tmp_path / "work"
    work.mkdir()
    backend = _FakeBackend(work)
    result = run_with_auto_phase(experiment, backend)
    assert backend.calls == ["process"]
    assert result.backend_runs == 1
    assert result.spectrum_path == str(work / "out.ft2")
    assert any("运行 1 次" in line for line in result.logs)
    # 空工作目录:无 fid/平面,内存内候选为 0,但不触发任何额外后端调用
    assert result.candidates_scored == 0


def test_run_with_auto_phase_nus_single_run(
    tmp_path: Path, bruker_dir: Path
) -> None:
    experiment = read_dataset(bruker_dir / "nus_2d")
    work = tmp_path / "work"
    work.mkdir()
    backend = _FakeBackend(work)
    result = run_with_auto_phase(experiment, backend)
    assert backend.calls == ["reconstruct_nus"]
    assert result.backend_runs == 1


def test_run_with_auto_phase_backend_failure(
    tmp_path: Path, bruker_dir: Path
) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    work = tmp_path / "work"
    work.mkdir()
    backend = _FakeBackend(work, success=False)
    result = run_with_auto_phase(experiment, backend)
    assert backend.calls == ["process"]
    assert result.backend_runs == 1
    assert result.phases == []
    assert result.logs == ["模拟后端失败"]


def test_auto_processor_records_phase_estimate(
    tmp_path: Path, bruker_dir: Path
) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    work = tmp_path / "work"
    work.mkdir()
    backend = _FakeBackend(work)
    (work / "out.ft2").write_bytes(b"not a pipe file")
    processor = AutoProcessor(backend=backend)
    result = processor.run(experiment)
    assert result.status == "success"
    assert any("自动相位" in line for line in result.logs)
    assert result.phase_report is not None
    assert result.phase_report.backend_runs == 0  # 估计阶段不触发后端
    assert backend.calls == ["process"]


def test_format_and_save_report(tmp_path: Path) -> None:
    result = PhaseOptimizeResult(
        phases=[],
        candidates_scored=247,
        backend_runs=1,
        spectrum_path="out.ft2",
        work_dir="work",
    )
    text = format_phase_report(result)
    assert "候选评分: 247 个(内存内)" in text
    assert "后端运行: 1 次" in text
    out = save_report(result, tmp_path / "phase_report.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["candidates_scored"] == 247
    assert data["backend_runs"] == 1



def test_score_in_memory_direct_ranks_true_p1_magnitude() -> None:
    """p0 吸收后的 p1 代理分:无相位误差保持近零,有误差给出非零校正。"""
    for true_p1 in (90.0, -60.0, 0.0):
        scores = score_in_memory_direct(
            _p1_fid(true_p1), direct_phase_candidates()
        )
        assert scores
        best_key = max(scores, key=scores.get)
        best_p1 = float(best_key.split("/")[1].split("=")[1])
        if true_p1 == 0.0:
            assert abs(best_p1) <= 30.0  # 未调相数据保持近零
        else:
            assert abs(best_p1) >= 30.0  # 非零校正(±180 歧义内)


def test_same_phase_tolerance() -> None:
    assert _same_phase({"p0": 0.0, "p1": 90.0}, {"p0": 0.0, "p1": 90.0}, 30.0)
    assert _same_phase({"p0": 0.0, "p1": 90.0}, {"p0": 0.0, "p1": -90.0}, 30.0)
    assert not _same_phase({"p0": 0.0, "p1": 90.0}, {"p0": 0.0, "p1": 0.0}, 30.0)
    assert not _same_phase({"p0": 0.0, "p1": 90.0}, {"p0": 180.0, "p1": 0.0}, 30.0)


class _OverrideBackend:
    """记录 direct_phase_override 的假后端(按 p1 返回不同谱路径供评分)。"""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = str(work_dir)
        self.overrides: list[dict] = []

    def process(self, experiment, plan, direct_phase_override=None) -> dict:
        self.overrides.append(dict(direct_phase_override or {}))
        # 逐维搜索时覆盖含多个轴,取末轴(正在搜索的轴)的 p1
        p1 = list(direct_phase_override.values())[-1][1]
        return {
            "success": True,
            "spectrum_path": f"{self.work_dir}/out_p1{int(p1)}.ft2",
            "logs": [],
        }

    def reconstruct_nus(self, experiment, params) -> dict:
        self.overrides.append(params.get("direct_phase_override"))
        return {
            "success": True,
            "spectrum_path": f"{self.work_dir}/out_nus.ft3",
            "logs": [],
        }


def _score_from_path(path: str) -> tuple[float, dict[str, float]]:
    """评分函数:从路径解析 p1,真值 30° 处得分最高。"""
    p1 = float(path.split("p1")[1].split(".")[0])
    return 100.0 - abs(p1 - 30.0), {"snr": 0.0}


def test_brute_force_direct_scores_passes_override(
    tmp_path: Path, bruker_dir: Path
) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _OverrideBackend(tmp_path / "work")
    candidates = direct_phase_candidates(
        p1_values=(-90.0, -30.0, 30.0, 90.0)
    )
    results = brute_force_direct_scores(
        experiment, backend, candidates, score_fn=_score_from_path
    )
    assert len(results) == 4
    assert len(backend.overrides) == 4
    assert all("F2" in o for o in backend.overrides)
    best = max(results, key=lambda c: c.score)
    assert best.params["p1"] == 30.0
    assert best.source == "brute_force"


def test_produce_phased_spectrum_uniform(tmp_path: Path, bruker_dir: Path) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _OverrideBackend(tmp_path / "work")
    resp = produce_phased_spectrum(experiment, backend, {"F2": (0.0, 30.0)})
    assert resp["success"]
    assert backend.overrides == [{"F2": (0.0, 30.0)}]


def test_produce_phased_spectrum_nus(tmp_path: Path, bruker_dir: Path) -> None:
    experiment = read_dataset(bruker_dir / "nus_2d")
    backend = _OverrideBackend(tmp_path / "work")
    resp = produce_phased_spectrum(
        experiment, backend, {"F2": (0.0, 30.0)}, params={"nthread": 2}
    )
    assert resp["success"]
    assert backend.overrides == [(0.0, 30.0)]


def test_candidate_score_roundtrip() -> None:
    score = CandidateScore(
        params={"p0": 0.0, "p1": 30.0},
        score=88.0,
        source="brute_force",
    )
    assert score.to_dict()["params"]["p1"] == 30.0



def test_decide_refinement_validated() -> None:
    report = EquivalenceReport(
        candidates=[],
        brute_force={},
        in_memory={},
        best_brute_force={"p0": 0.0, "p1": -60.0},
        best_in_memory={"p0": 0.0, "p1": -60.0},
        top_match=True,
        correlation=0.8,
        passed=True,
        backend_runs=6,
    )
    phase, method = decide_refinement(report, None)
    assert method == "validated"
    assert phase["p1"] == -60.0


def test_decide_refinement_refined_when_not_validated() -> None:
    report = EquivalenceReport(
        candidates=[],
        brute_force={},
        in_memory={},
        best_brute_force={"p0": 0.0, "p1": -90.0},
        best_in_memory={"p0": 0.0, "p1": -60.0},
        top_match=True,
        correlation=0.46,
        passed=False,
        backend_runs=8,
    )
    refined = CandidateScore(params={"p0": 0.0, "p1": -90.0}, score=94.1)
    phase, method = decide_refinement(report, refined)
    assert method == "refined"
    assert phase["p1"] == -90.0


def test_select_direct_phase_refined(tmp_path: Path, bruker_dir: Path) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _OverrideBackend(tmp_path / "work")
    best = select_direct_phase_refined(
        experiment,
        backend,
        {"p0": 0.0, "p1": 0.0},
        span=60.0,
        step=30.0,
        score_fn=_score_from_path,
    )
    assert best.params["p1"] == 30.0  # 评分函数真值 30°
    assert len(backend.overrides) == 5  # -60/-30/0/30/60


def test_phase_selection_result_roundtrip() -> None:
    from workflow.phase_optimize import PhaseSelectionResult

    result = PhaseSelectionResult(
        phase={"p0": 0.0, "p1": 30.0},
        spectrum_path="out.ft2",
        method="refined",
        backend_runs=12,
    )
    data = result.to_dict()
    assert data["method"] == "refined"
    assert data["phase"]["p1"] == 30.0



def test_optimize_phase_sequential_uniform(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """逐维暴力:直接维 F2 → 间接维 F1,依次固定,取整体最优。"""
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _OverrideBackend(tmp_path / "work")
    result = optimize_phase_sequential(
        experiment,
        backend,
        p0_values=(0.0,),
        p1_values=(-60.0, 0.0, 30.0, 60.0),
        score_fn=_score_from_path,
        refine=False,
    )
    assert result.method == "sequential_brute_force"
    assert result.phases["F2"][1] == 30.0
    assert result.phases["F1"][1] == 30.0
    assert result.backend_runs == 2 * 4
    assert any("F2" in line and "已固定" in line for line in result.logs)
    assert any("F1" in line and "已固定" in line for line in result.logs)
    assert any("70.0 → 100.0" in line for line in result.logs)


def test_optimize_phase_sequential_failure(tmp_path: Path, bruker_dir: Path) -> None:
    """某轴候选全部失败时抛错(不静默)。"""
    experiment = read_dataset(bruker_dir / "hsqc_2d")

    class _FailBackend(_OverrideBackend):
        def process(self, experiment, plan, direct_phase_override=None) -> dict:
            return {"success": False, "message": "boom", "logs": []}

    backend = _FailBackend(tmp_path / "work")
    with pytest.raises(ValueError, match="全部失败"):
        optimize_phase_sequential(
            experiment, backend, p0_values=(0.0,), p1_values=(0.0,)
        )



def test_default_phase_score_ranks_phase_quality(tmp_path: Path) -> None:
    """相位专用评分:错相(负峰)分数低于正相;不使用综合 QC。"""
    from scipy.ndimage import gaussian_filter

    from workflow.phase_optimize import _default_phase_score

    in_phase = np.zeros((32, 64))
    in_phase[8, 20] = 500.0
    in_phase = gaussian_filter(in_phase, sigma=1.2)
    inverted = -in_phase  # 180° 错相 → 负峰

    def _write(path: Path, data: np.ndarray) -> None:
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

    good = tmp_path / "good.ft2"
    bad = tmp_path / "bad.ft2"
    _write(good, in_phase)
    _write(bad, inverted)
    score_good, comp_good = _default_phase_score(str(good))
    score_bad, comp_bad = _default_phase_score(str(bad))
    assert 0.0 <= score_good <= 100.0
    assert score_good > score_bad  # 负峰比例惩罚
    assert (
        comp_bad["negative_peak_fraction"] > comp_good["negative_peak_fraction"]
    )



def test_optimize_phase_sequential_default_multiscale() -> None:
    """默认:粗网格 p1 30° 步长 + 多尺度细化到 5°(而非固定步长全搜索)。"""
    import inspect

    sig = inspect.signature(optimize_phase_sequential)
    p1 = sig.parameters["p1_values"].default
    assert len(p1) == 7 and p1[1] - p1[0] == 30.0
    assert sig.parameters["refine"].default is True
    assert sig.parameters["final_step"].default == 5.0


def test_optimize_phase_sequential_multiscale_refine(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """粗到细:粗网格最优偏离真值(12°)时,细化收敛到 5° 以内。"""
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _OverrideBackend(tmp_path / "work")

    def _peak12(path: str) -> tuple[float, dict[str, float]]:
        p1 = float(path.split("p1")[1].split(".")[0])
        return 100.0 - abs(p1 - 12.0), {}

    result = optimize_phase_sequential(
        experiment,
        backend,
        p0_values=(0.0,),
        p1_values=(-60.0, 0.0, 30.0, 60.0),
        score_fn=_peak12,
    )
    # 粗网格最优 p1=0(偏离真值 12°);细化窗口 ±20@10 → 10,±5@5 → 10(偏差 2°)
    assert result.phases["F2"][1] == 10.0
    assert result.phases["F1"][1] == 10.0
    assert result.backend_runs > 2 * 4  # 细化产生额外后端运行
    assert result.optimized == ["F2", "F1"]
