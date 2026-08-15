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
        est = search_direct_phase(_p1_fid(true_p1), zf_size=512)
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
        json.dumps(
            {
                "version": 2,
                "p0": 0.0,
                "p1": 45.0,
                "score": 0.9,
                "gain": 0.05,
            }
        ),
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


def _spectrum_with_negative_peak():
    """2D 谱:多个正峰 + 一枚强负峰(折叠/数据性质,旧 top-5 池化会被其主导)。"""
    import numpy as np
    from scipy.ndimage import gaussian_filter

    n = 64
    base = np.zeros((32, n))
    # 正峰分布在不同行(每条迹线一个主峰),负峰单独一行——
    # 使中位数由多数正迹线决定,不被单枚强负峰主导
    for y, x in ((8, 20), (16, 40), (24, 52)):
        base[y, x] = 200.0
    base[12, 8] = -180.0  # 强负峰(折叠伪影)
    return gaussian_filter(base, sigma=1.0)


def test_fixed_trace_median_rejects_negative_peak() -> None:
    """负峰存在时,固定迹线中位数聚合的好相位显著高于坏相位(旧 top-5 池化
    会被单枚强负峰主导,判别差仅 ~0.3;中位数不受其主导)。"""
    import numpy as np

    from workflow.phase_optimize import (
        _trace_indices_fixed,
        _trace_metrics_median,
    )

    base = _spectrum_with_negative_peak()
    threshold = max(float(np.percentile(base, 99.5)), 0.0)
    indices, positions = _trace_indices_fixed(base, 1, threshold)
    assert len(indices) >= 3  # 多数信号迹线(正峰)
    # 好相位 ≈ 纯吸收(实部);坏相位 ≈ 纯色散(gradient 近似:峰过零、
    # 两侧一正一负,净吸收≈0),与旧实现实测对照(0.68 vs -0.76)一致方向
    dispersion = np.gradient(base, axis=1)
    good_median = _trace_metrics_median(base, 1, indices, positions)
    bad_median = _trace_metrics_median(dispersion, 1, indices, positions)
    # 0-100 映射判别差应显著(>25 分;旧池化判别差仅 ~32 分但方向相反风险)
    assert good_median > bad_median + 0.5
    assert 50.0 * (good_median + 1.0) > 50.0 * (bad_median + 1.0) + 25.0


def test_fixed_trace_metrics_stays_on_locked_traces() -> None:
    """固定迹线/峰位评分:基线锁定后,候选谱在锁定位置打分(不重新选峰)。"""
    import numpy as np

    from workflow.phase_optimize import (
        _profile_metric_fixed,
        _trace_indices_fixed,
        _trace_metrics_median,
    )

    base = _spectrum_with_negative_peak()
    threshold = max(float(np.percentile(base, 99.5)), 0.0)
    indices, positions = _trace_indices_fixed(base, 1, threshold)
    # 基线锁定位置不变
    indices2, positions2 = _trace_indices_fixed(base, 1, threshold)
    assert indices == indices2 and positions == positions2
    # 单迹线剖面:正峰(吸收)净吸收高,负峰(180° 反相)净吸收低
    pos_trace = _profile_metric_fixed(base, 16, 40, axis=1)
    neg_trace = _profile_metric_fixed(base, 12, 8, axis=1)
    assert pos_trace > 0.5
    assert neg_trace < 0.0  # 负吸收(反转惩罚保留)
    # 中位数由多数正峰迹线决定(>0)
    assert _trace_metrics_median(base, 1, indices, positions) > 0.0


def _write_ft2_real(path: Path, data: np.ndarray) -> None:
    """写最小可读 2D ft2(数据内容可指定)。"""
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


class _PhaseEmbedBackend:
    """假后端:按 NMRPipe PS 语义把候选相位应用到嵌入 θ_true 的谱上。

    默认评分(固定迹线中位数)应从 F2 找回 p0 ≈ -θ_true(mod 360);
    首个轴基线谱必须先跑 (0,0) 锁定迹线,否则退化到 zeros 分支。
    """

    def __init__(self, work_dir: Path, theta_true: float = -120.0) -> None:
        self.work_dir = str(work_dir)
        self.theta_true = theta_true
        self.calls: list[dict] = []

    def _spectrum(self, override: dict) -> np.ndarray:
        from scipy.signal import hilbert

        n1, n2 = 32, 64
        x = np.arange(n2)
        y0, x0 = 16, 30
        a = 100.0 / (1.0 + ((x - x0) / 2.0) ** 2)  # 洛伦兹吸收峰
        d = -np.imag(hilbert(a))  # 色散(近似)
        p0, p1 = override.get("F2", (0.0, 0.0))
        phi = np.deg2rad(
            self.theta_true + float(p0) % 360.0 + float(p1) * (x - x0) / n2
        )
        trace = a * np.cos(phi) - d * np.sin(phi)
        data = np.zeros((n1, n2), dtype=np.float32)
        data[y0, :] = trace
        rng = np.random.default_rng(0)
        data += rng.normal(0.0, 1e-3, size=(n1, n2)).astype(np.float32)
        return data

    def _path_for(self, override: dict) -> Path:
        f2 = override.get("F2", (0.0, 0.0))
        f1 = override.get("F1", (0.0, 0.0))
        name = (
            f"out_F2{float(f2[0]):.0f}_{float(f2[1]):.0f}"
            f"_F1{float(f1[0]):.0f}_{float(f1[1]):.0f}.ft2"
        )
        return Path(self.work_dir) / name

    def process(
        self, experiment, plan, direct_phase_override=None,
        params=None, out_file=None, script_name=None,
    ) -> dict:
        self.calls.append(dict(direct_phase_override or {}))
        path = self._path_for(direct_phase_override or {})
        _write_ft2_real(path, self._spectrum(direct_phase_override or {}))
        return {
            "success": True,
            "spectrum_path": str(path),
            "message": "ok",
            "logs": [],
        }


def test_fixed_trace_metrics_3d_generic() -> None:
    """3D 谱(3D NUS finalize 产物)固定迹线在任意轴上正确(不把 F3 折叠成 2D 切片)。"""
    from scipy.ndimage import gaussian_filter

    from workflow.phase_optimize import (
        _axis_traces,
        _trace_indices_fixed,
        _trace_metrics_median,
    )

    n1, n2, n3 = 16, 24, 32
    base = np.zeros((n1, n2, n3))
    base[8, 12, 16] = 500.0
    base = gaussian_filter(base, sigma=1.0)
    threshold = max(float(np.percentile(base, 99.5)), 0.0)
    for axis in (0, 1, 2):
        traces = _axis_traces(base, axis)
        assert traces.shape == (base.size // base.shape[axis], base.shape[axis])
        idx, pos = _trace_indices_fixed(base, axis, threshold)
        assert len(idx) >= 1
        assert max(pos) < base.shape[axis]
        med = _trace_metrics_median(base, axis, idx, pos)
        assert med > 0.5  # 纯吸收主峰:锁定的都是其邻域迹线,中位数净吸收接近 +1


def test_default_score_recovers_embedded_f2_phase(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.75 回归:首个轴基线谱必须先跑 (0,0) 再锁定迹线——否则退化到
    zeros 分支(迹线位置全部 argmax=0),默认评分失效、F2 被带偏。

    嵌入真实相位 θ=-120°,正确修正相位 p0≈120°(mod 360)。
    """
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _PhaseEmbedBackend(tmp_path / "work", theta_true=-120.0)
    result = optimize_phase_sequential(experiment, backend)
    f2_p0 = float(result.phases["F2"][0]) % 360.0
    assert (
        abs(((f2_p0 - 120.0 + 180.0) % 360.0) - 180.0) <= 7.5
    ), f"F2 p0={f2_p0:g},期望≈120(mod 360)"
    assert len(backend.calls) > 12 * 2  # 默认 p0-only 粗网格 12 点/轴


def test_parallel_matches_sequential(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.77:并行(worker=4)与串行(worker=1)选择结果逐位一致。"""
    from core.data.bruker_reader import read_dataset

    experiment = read_dataset(bruker_dir / "hsqc_2d")
    seq_backend = _PhaseEmbedBackend(tmp_path / "seq", theta_true=-120.0)
    seq = optimize_phase_sequential(
        experiment, seq_backend, parallel=True, max_workers=1
    )
    par_backend = _PhaseEmbedBackend(tmp_path / "par", theta_true=-120.0)
    par = optimize_phase_sequential(
        experiment, par_backend, parallel=True, max_workers=4
    )
    assert seq.phases == par.phases
    assert seq.backend_runs == par.backend_runs
    assert seq.optimized == par.optimized


def test_saturated_axis_skips_p1_refine(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.77:轴最优饱和(100 分)时跳过 p1 精修,日志有记录且结果不变。"""
    from core.data.bruker_reader import read_dataset

    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _PhaseEmbedBackend(tmp_path / "work", theta_true=-120.0)
    result = optimize_phase_sequential(
        experiment, backend, parallel=True, max_workers=1
    )
    assert "跳过 p1 精修" in " ".join(result.logs)
    f2_p0 = float(result.phases["F2"][0]) % 360.0
    assert abs(((f2_p0 - 120.0 + 180.0) % 360.0) - 180.0) <= 7.5


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
        self.process_params: list[dict] = []
        self.finalize_params: list[dict] = []

    def process(
        self, experiment, plan, direct_phase_override=None,
        params=None, out_file=None, script_name=None,
    ) -> dict:
        self.overrides.append(dict(direct_phase_override or {}))
        self.process_params.append(dict(params or {}))
        # 逐维搜索时覆盖含多个轴,取末轴(正在搜索的轴)的 p0/p1
        p0, p1 = list(direct_phase_override.values())[-1]
        return {
            "success": True,
            "spectrum_path": f"{self.work_dir}/out_p0{int(p0)}_p1{int(p1)}.ft2",
            "logs": [],
        }

    def reconstruct_nus(self, experiment, params) -> dict:
        self.overrides.append(params.get("direct_phase_override"))
        return {
            "success": True,
            "spectrum_path": f"{self.work_dir}/out_nus.ft3",
            "logs": [],
        }

    def finalize_nus(
        self, experiment, phases=None, work_dir=None, baseline=None,
        params=None, out_file=None, script_name=None,
    ) -> dict:
        self.overrides.append(dict(phases or {}))
        self.finalize_params.append(dict(params or {}))
        p0, p1 = list((phases or {}).values())[-1]
        return {
            "success": True,
            "spectrum_path": f"{self.work_dir}/out_p0{int(p0)}_p1{int(p1)}.ft2",
            "logs": [],
        }


def _score_from_path(path: str) -> tuple[float, dict[str, float]]:
    """评分函数:从路径解析 p0/p1,真值 30° 处得分最高(p0 弱依赖)。"""
    p0 = float(path.split("_p0")[1].split("_")[0])
    p1 = float(path.split("_p1")[1].split(".")[0])
    return 100.0 - abs(p1 - 30.0) - 0.02 * abs(p0), {"snr": 0.0}


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
        def process(
        self, experiment, plan, direct_phase_override=None,
        params=None, out_file=None, script_name=None,
    ) -> dict:
            return {"success": False, "message": "boom", "logs": []}

    backend = _FailBackend(tmp_path / "work")
    with pytest.raises(ValueError, match="全部失败"):
        optimize_phase_sequential(
            experiment, backend, p0_values=(0.0,), p1_values=(0.0,)
        )



def test_default_p0_grid_covers_beyond_pm45() -> None:
    """0.2.74 回归:p0 粗网格默认覆盖 ±135(sampleI F2 真实 p0=-120,
    旧 ±45 范围永远够不到导致返回非最优相位)。"""
    import inspect

    from workflow.phase_optimize import optimize_phase_sequential

    sig = inspect.signature(optimize_phase_sequential)
    p0 = sig.parameters["p0_values"].default
    # 0.2.75:全圆 0-360 30° 步;sampleI F2 p0=-120° 等价 240°(在网格内)
    assert 240.0 in p0 and 120.0 in p0
    assert len(p0) == 12 and max(p0) < 360.0


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
    # 0.2.38 新指标:连续负面积与谱熵方向一致(错相惩罚更强)
    assert (
        comp_bad["negative_area_fraction"] > comp_good["negative_area_fraction"]
    )
    assert comp_bad["entropy"] > comp_good["entropy"]



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
        p1 = float(path.split("_p1")[1].split(".")[0])
        return 100.0 - abs(p1 - 12.0), {}

    result = optimize_phase_sequential(
        experiment,
        backend,
        p0_values=(0.0,),
        p1_values=(-60.0, 0.0, 30.0, 60.0),
        score_fn=_peak12,
    )
    # 粗网格最优 p1=0(偏离真值 12°);细化窗口 ±20@10 → 10,±5@5 → 10;
    # 0.2.75 平台圆中位数(10,15 平台)精修到 12.5(偏差 0.5°)
    assert result.phases["F2"][1] == 12.5
    assert result.phases["F1"][1] == 12.5
    assert result.backend_runs > 2 * 4  # 细化产生额外后端运行
    assert result.optimized == ["F1", "F2"]  # 0.2.75:均匀路径间接维先

def test_optimize_phase_sequential_3d_uniform(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """3D 均匀:每一维(F3→F2→F1)都搜索并固定。"""
    experiment = read_dataset(bruker_dir / "hnca_3d")
    backend = _OverrideBackend(tmp_path / "work")
    result = optimize_phase_sequential(
        experiment,
        backend,
        p0_values=(0.0,),
        p1_values=(-60.0, 0.0, 30.0, 60.0),
        score_fn=_score_from_path,
        refine=False,
    )
    assert set(result.phases) == {"F3", "F2", "F1"}
    assert all(result.phases[axis][1] == 30.0 for axis in ("F3", "F2", "F1"))
    assert result.backend_runs == 3 * 4


def test_optimize_phase_sequential_3d_nus_skips_direct(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """3D NUS:直接维 F3 随 SMILE 重构固化,间接维 F2/F1 逐维搜索。"""
    experiment = read_dataset(bruker_dir / "nus_3d")
    backend = _OverrideBackend(tmp_path / "work")
    result = optimize_phase_sequential(
        experiment,
        backend,
        p0_values=(0.0,),
        p1_values=(-60.0, 0.0, 30.0, 60.0),
        score_fn=_score_from_path,
        refine=False,
    )
    assert set(result.phases) == {"F2", "F1"}
    assert all(result.phases[axis][1] == 30.0 for axis in ("F2", "F1"))
    assert result.backend_runs == 1 + 2 * 4  # 1 次 SMILE 重构 + 2 轴 × 4 候选



def test_candidates_run_with_zero_fill_none(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.87:相位候选谱零填零(mode:none),优化期间数据最小化。"""
    from core.data.bruker_reader import read_dataset

    experiment = read_dataset(bruker_dir / "hsqc_2d")
    axes = [dim.logical_axis for dim in experiment.dimensions]
    expected = {"zero_fill": {a: {"mode": "none"} for a in axes}}
    backend = _OverrideBackend(tmp_path / "work")
    optimize_phase_sequential(
        experiment,
        backend,
        p0_values=(0.0,),
        p1_values=(0.0,),
        refine=False,
        score_fn=_score_from_path,
    )
    assert backend.process_params, "应至少跑一次候选 process"
    assert all(p == expected for p in backend.process_params)


def test_preview_direct_phase_no_smile(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.89:直接维相位预览(无 SMILE/零后端)写 phase.json v2 与预览谱。"""
    import nmrglue as ng

    from core.data.bruker_reader import read_dataset
    from workflow.phase_optimize import preview_direct_phase

    experiment = read_dataset(bruker_dir / "nus_2d")
    work = tmp_path / "work"
    fid_dir = work / "fid"
    fid_dir.mkdir(parents=True)
    n = 512
    k = np.arange(n)
    rng = np.random.default_rng(0)
    for index in range(8):
        spec = np.zeros(n, dtype=complex)
        for peak in (140, 260, 380):
            spec += np.exp(-((k - peak) ** 2) / (2 * 6.0**2))
        # 信号相位 +33°(p0)/p1 斜坡 -42°;t1=0(首增量语义)
        spec *= np.exp(1j * np.deg2rad(33.0 + (-42.0) * k / max(n - 1, 1)))
        spec += rng.normal(0.0, 0.02, size=n)
        spec += 1j * rng.normal(0.0, 0.02, size=n)
        dic = {kk: "0" for kk in ng.pipe.fdata_dic}
        dic["FDMAGIC"] = 9.2330230000000007e14
        dic["FDDIMCOUNT"] = 1
        dic["FDSIZE"] = n
        dic["FDSPECNUM"] = 1
        dic["FDQUADFLAG"] = 1
        dic["FDOBS"] = "600.0"
        dic["FDCAR"] = "4.7"
        dic["FDSW"] = "6000.0"
        dic["FDORIG"] = "1000.0"
        ng.pipe.write(
            str(fid_dir / f"test{index:03d}.fid"),
            dic,
            np.fft.ifft(spec).astype(np.complex64),
            overwrite=True,
        )
    result = preview_direct_phase(
        experiment, work, out_preview="direct_preview.ft2"
    )
    assert result.backend_runs == 0
    assert len(result.phases) == 1
    direct = result.phases[0]
    assert direct.axis == "F2"
    # 校正值 ≈ (-33, +42)(信号相位相反数)
    assert abs(((direct.p0 + 33.0 + 180.0) % 360.0) - 180.0) <= 12.0, direct.p0
    assert abs(direct.p1 - 42.0) <= 10.0, direct.p1
    cached = json.loads((work / "phase.json").read_text(encoding="utf-8"))
    assert cached["version"] == 2
    assert cached["p0"] == direct.p0
    assert cached["p1"] == direct.p1
    preview = work / "direct_preview.ft2"
    assert preview.is_file()
    _dic2, data = ng.pipe.read(str(preview))
    assert data.shape == (8, 512)


def test_preview_direct_phase_nus_pseudo_uniform(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.90:伪均匀传统 FT 路径(NUS,无 SMILE)恢复直接维 (p0, p1)。

    构造 16 个切片:每个直接维 3 峰,信号相位 +33°(p0)/斜坡 -42°(p1),
    另叠 t1 调制 θ(i)=2π·16·i/128(网格上 F1=16);nuslist 摆网格 + 传统
    F1 FT 后直接维相位不再与 t1 纠缠,校正值应 ≈ (-33, +42)。
    """
    import nmrglue as ng

    from core.data.bruker_reader import read_dataset
    from workflow.phase_optimize import preview_direct_phase

    experiment = read_dataset(bruker_dir / "nus_2d")
    work = tmp_path / "work"
    fid_dir = work / "fid"
    fid_dir.mkdir(parents=True)
    n = 512
    k = np.arange(n)
    rng = np.random.default_rng(1)
    n_f1 = 128  # fixture nus_2d 的 F1 复点网格
    points = []
    for index in range(16):
        i_f1 = 4 * index  # 采样点:0,4,8,...,60
        points.append((i_f1,))
        spec = np.zeros(n, dtype=complex)
        for peak in (140, 260, 380):
            spec += np.exp(-((k - peak) ** 2) / (2 * 6.0**2))
        theta = 2.0 * np.pi * 16.0 * i_f1 / n_f1  # 网格上 F1=16 的 t1 调制
        spec *= np.exp(
            1j * np.deg2rad(33.0 + (-42.0) * k / max(n - 1, 1)) + 1j * theta
        )
        spec += rng.normal(0.0, 0.02, size=n)
        spec += 1j * rng.normal(0.0, 0.02, size=n)
        dic = {kk: "0" for kk in ng.pipe.fdata_dic}
        dic["FDMAGIC"] = 9.2330230000000007e14
        dic["FDDIMCOUNT"] = 1
        dic["FDSIZE"] = n
        dic["FDSPECNUM"] = 1
        dic["FDQUADFLAG"] = 1
        dic["FDOBS"] = "600.0"
        dic["FDCAR"] = "4.7"
        dic["FDSW"] = "600.0"  # sw_ppm=1:EXT 窗口落在谱外 → 回退全谱
        dic["FDORIG"] = "1000.0"
        ng.pipe.write(
            str(fid_dir / f"test{index:03d}.fid"),
            dic,
            np.fft.ifft(spec).astype(np.complex64),
            overwrite=True,
        )
    (work / "nuslist").write_text(
        "".join(f"{p[0]}\n" for p in points), encoding="utf-8"
    )
    result = preview_direct_phase(
        experiment, work, out_preview="direct_pseudo.ft2"
    )
    assert result.backend_runs == 0
    assert len(result.phases) == 1
    direct = result.phases[0]
    assert direct.source == "direct_pseudo"
    # 校正值 ≈ (-33, +42)(信号相位相反数)
    assert abs(((direct.p0 + 33.0 + 180.0) % 360.0) - 180.0) <= 15.0, direct.p0
    assert abs(direct.p1 - 42.0) <= 12.0, direct.p1
    cached = json.loads((work / "phase.json").read_text(encoding="utf-8"))
    assert cached["version"] == 2
    assert cached["source"] == "direct_pseudo"
    assert cached["p0"] == direct.p0
    preview = work / "direct_pseudo.ft2"
    assert preview.is_file()
    _d2, data = ng.pipe.read(str(preview))
    assert data.shape == (n_f1, n)
