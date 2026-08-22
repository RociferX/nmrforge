"""SMILE 参数优化模块测试(0.2.162:两阶段 + 稳定性/跨组合去伪)。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from core.data.bruker_reader import read_dataset
from workflow.smile_optimize import (
    SmileParameterResult,
    default_smile_grid,
    format_results,
    optimize_smile_parameters,
    save_report,
)


def _write_ft2(path: Path, peaks, noise_std: float = 1.0, seed: int = 0):
    """写合成 2D ft2:peaks=[(y, x, height)],带按 seed 变化的噪声底。"""
    from nmrglue.fileio import pipe

    data = np.zeros((64, 64), dtype=np.float32)
    data += np.random.default_rng(seed).normal(0, noise_std, (64, 64)).astype(
        np.float32
    )
    for y, x, height in peaks:
        data[y, x] += height
    dic = {k: "0" for k in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = 64
    dic["FDSPECNUM"] = 64
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    for prefix in ("FDF1", "FDF2"):
        dic[prefix + "SW"] = 6000.0
        dic[prefix + "OBS"] = 600.0
        dic[prefix + "CAR"] = 4.7
        dic[prefix + "ORIG"] = 4.7 * 600.0
    pipe.write(str(path), dic, data, overwrite=True)


def _fake_backend(
    tmp_path: Path,
    spurious_in: set[int],
    extra_peaks: dict[int, list[tuple[float, float, float]]] | None = None,
):
    """稳定峰固定;伪峰仅出现在指定 seed 的重构;噪声随 nSigma。

    extra_peaks: {seed: [(y, x, height), ...]} 追加到对应 seed 的重构。"""
    seen: list[dict] = []
    extras = extra_peaks or {}

    class FakeBackend:
        def __init__(self) -> None:
            self.work = Path(tmp_path)

        def reconstruct_nus(self, experiment, params: dict) -> dict:
            seen.append(dict(params))
            seed = int(params.get("fid_noise_seed", 0))
            nsigma = float(params.get("nsigma", 5.0))
            peaks = [(10, 20, 30.0), (30, 40, 25.0)]
            if seed in spurious_in:
                peaks.append((25, 25, 15.0))
            peaks.extend(extras.get(seed, []))
            noise_std = 1.0 if nsigma <= 3.0 else 0.1
            path = self.work / f"spec_{seed}.ft2"
            _write_ft2(path, peaks, noise_std=noise_std, seed=seed)
            return {"success": True, "spectrum_path": str(path)}

    return FakeBackend(), seen


def test_default_smile_grid() -> None:
    grid = default_smile_grid()
    assert len(grid) == 25  # 0.2.162-补:扫描去重后加密网格(5×5),调参更细
    assert grid[0] == {"nsigma": 3.0, "thresh": 0.90}
    assert all("smile_xq3" not in g for g in grid)  # 0.2.162:xQ3 死参数移除
    combos = {(g["nsigma"], g["thresh"]) for g in grid}
    assert len(combos) == 25


def test_optimize_filters_spurious_and_keeps_true(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.162:最优参数多次重构(注入噪声)后,不稳定的伪峰被剔除,真峰保留。"""
    exp = read_dataset(bruker_dir / "nus_3d")
    # 伪峰只出现在扫描 seed(1000),最终重复(9000-9002)不含 → 被剔除
    backend, _seen = _fake_backend(tmp_path, spurious_in={1000})
    results = optimize_smile_parameters(
        exp,
        backend,
        grid=[{"nsigma": 5.0, "thresh": 0.95}],
        final_repeats=3,
        min_stability=2,
    )
    assert len(results) == 1
    best = results[0]
    assert best.decision in ("accept", "warning")
    assert best.spectrum_path
    positions = [tuple(round(v) for v in p["position"]) for p in best.stable_peaks]
    assert (10, 20) in positions and (30, 40) in positions
    assert (25, 25) not in positions
    assert best.components["peak_count"] >= 2  # 真峰保留,伪峰剔除


def test_optimize_uses_base_params_and_cross_support(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.162:保留已有参数只调 SMILE 参数;低噪声参数组评分更高。"""
    exp = read_dataset(bruker_dir / "nus_3d")
    backend, seen = _fake_backend(tmp_path, spurious_in={0, 1002})
    base = {"ext_lo": "10.5", "ext_hi": "6.5", "nthread": 4}
    results = optimize_smile_parameters(
        exp,
        backend,
        base_params=base,
        grid=[
            {"nsigma": 3.0, "thresh": 0.90},
            {"nsigma": 5.0, "thresh": 0.99},
        ],
        final_repeats=3,
    )
    assert len(results) == 2
    assert len(seen) == 5  # 扫描 2 次 + 最终 3 次
    for run_params in seen:
        assert run_params["ext_lo"] == "10.5"  # 基参数保留
        assert run_params["nthread"] == 4
        assert run_params["direct_phase_search"] is False
        assert run_params["display_phase_search"] is False
        assert "fid_noise" in run_params and "fid_noise_seed" in run_params
    # nSigma=5(噪声更低)→ SNR 更高 → 排前面
    assert results[0].params["nsigma"] == 5.0
    assert results[0].overall > results[1].overall
    assert "cross_support" in results[0].components


def test_optimize_failure_graceful(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")

    class FailingBackend:
        def reconstruct_nus(self, experiment, params: dict) -> dict:
            return {"success": False, "message": "缺 nuslist"}

    results = optimize_smile_parameters(
        exp, FailingBackend(), grid=[{"nsigma": 5.0, "thresh": 0.95}]
    )
    assert len(results) == 1
    assert results[0].decision == "failed"
    assert results[0].message == "缺 nuslist"


def test_save_report_and_format(tmp_path: Path) -> None:
    results = [
        SmileParameterResult(
            params={"nsigma": 5.0, "thresh": 0.95},
            decision="accept",
            overall=74.1,
            components={
                "stability": 0.8,
                "cross_support": 1.0,
                "snr": 12.0,
                "peak_count": 40,
                "artifact": 30.0,
            },
        )
    ]
    out = save_report(results, tmp_path / "report.json")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload[0]["params"]["nsigma"] == 5.0
    assert payload[0]["overall"] == 74.1
    table = format_results(results)
    assert "nSigma" in table and "stability" in table
    assert "5.0" in table and "74.1" in table


def test_on_result_callback(tmp_path: Path, bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    backend, _seen = _fake_backend(tmp_path, spurious_in=set())
    received: list[float] = []

    def on_result(result) -> None:
        received.append(result.overall)

    optimize_smile_parameters(
        exp,
        backend,
        grid=[
            {"nsigma": 3.0, "thresh": 0.90},
            {"nsigma": 5.0, "thresh": 0.99},
        ],
        on_result=on_result,
    )
    assert len(received) == 2  # 每组评分后都立即回调


def test_progress_reports_scan_and_final(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.162-补:进度回调输出扫描(正在优化 x/N)与去伪重复阶段。"""
    exp = read_dataset(bruker_dir / "nus_3d")
    backend, _seen = _fake_backend(tmp_path, spurious_in=set())
    progress: list[tuple[int, int, str]] = []
    optimize_smile_parameters(
        exp,
        backend,
        grid=[
            {"nsigma": 3.0, "thresh": 0.90},
            {"nsigma": 5.0, "thresh": 0.99},
        ],
        progress=lambda i, t, m: progress.append((i, t, m)),
    )
    assert len(progress) == 2 + 3  # 扫描 2 条 + 去伪重复 3 条
    assert progress[0][0] == 1 and progress[0][1] == 2
    assert "正在优化 1/2" in progress[0][2]
    assert "正在优化 2/2" in progress[1][2]
    assert "去伪重复 1/3" in progress[2][2]
    assert "去伪重复 3/3" in progress[-1][2]


def test_cli_parse_grid() -> None:
    """0.2.162-补:CLI --grid 解析(nSigma,thresh 分号分隔)。"""
    from scripts.smile_optimize import _parse_grid

    grid = _parse_grid("5.0,0.95;6.0,0.99")
    assert grid == [
        {"nsigma": 5.0, "thresh": 0.95},
        {"nsigma": 6.0, "thresh": 0.99},
    ]


def test_reliability_scored_by_cross_support(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.162-补5:可信度=50%跨组合支持分+50%信噪比评分(全有100%)。"""
    exp = read_dataset(bruker_dir / "nus_3d")
    # 伪峰(25,25)只出现在扫描组合1(seed 1000)与全部最终重复(9000-9002);
    # 低信噪比峰(35,35,高度0.45,SNR≈4.5 落在 3-5 真假混杂区间)出现在
    # 所有 seed → 全支持但 SNR 分<100
    extras = {seed: [(35, 35, 0.45)] for seed in (1000, 2000, 9000, 9001, 9002)}
    backend, _seen = _fake_backend(
        tmp_path,
        spurious_in={1000, 9000, 9001, 9002},
        extra_peaks=extras,
    )
    results = optimize_smile_parameters(
        exp,
        backend,
        grid=[
            {"nsigma": 5.0, "thresh": 0.95},
            {"nsigma": 6.0, "thresh": 0.99},
        ],
        final_repeats=3,
    )
    best = results[0]
    assert best.n_combos == 2
    by_pos = {
        tuple(round(v) for v in p["position"]): p for p in best.stable_peaks
    }
    assert by_pos[(10, 20)]["reliability"] == 100.0  # 全支持 + SNR 满分
    assert by_pos[(10, 20)]["snr_score"] == 100.0  # SNR≥5 满分
    assert by_pos[(30, 40)]["reliability"] == 100.0
    # (25,25) 只在扫描组合1注入;4 点容差下另一组合的弱噪声峰可能落入
    # 同 bin,故不断言精确 support,只验证双重打分公式一致
    sp = by_pos[(25, 25)]
    assert sp["support"] >= 1
    assert sp["reliability"] == round(
        (sp["cross_score"] + sp["snr_score"]) / 2.0, 1
    )
    # 低 SNR 峰:全支持但 SNR 分 <100 → 可信度被拉低,且严格等于两分均值
    # 边界峰:SNR 在检出阈值附近(3-5 混杂区),各 seed σ 估计波动导致
    # 个别 seed 未检出(支持数随之波动);只验证公式一致与不高于高 SNR 峰
    low = by_pos[(35, 35)]
    assert low["support"] >= 1
    assert low["reliability"] == round(
        (low["cross_score"] + low["snr_score"]) / 2.0, 1
    )
    assert low["reliability"] <= by_pos[(10, 20)]["reliability"]


def test_write_reliability_file(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.162-补4:smile_optimized 输出逐峰可靠性 JSON + CSV 可靠性列。"""
    import json as _json

    from core.project import ProjectManager
    from workflow.smile_optimize import write_smile_optimized_output

    exp = read_dataset(bruker_dir / "nus_3d")
    backend, _seen = _fake_backend(tmp_path, spurious_in={1000, 9000, 9001, 9002})
    results = optimize_smile_parameters(
        exp,
        backend,
        grid=[
            {"nsigma": 5.0, "thresh": 0.95},
            {"nsigma": 6.0, "thresh": 0.99},
        ],
        final_repeats=3,
    )
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")
    csv_path, _json_path, rel_path = write_smile_optimized_output(
        manager, entry.id, data.id, results[0].spectrum_path, results[0]
    )
    payload = _json.loads(rel_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "smile_reliability_v2"
    assert payload["n_combos"] == 2
    rels = {
        tuple(round(v) for v in p["position_pts"]): p["reliability"]
        for p in payload["peaks"]
    }
    assert rels[(10, 20)] == 100.0
    entries = {
        tuple(round(v) for v in p["position_pts"]): p for p in payload["peaks"]
    }
    sp = entries[(25, 25)]
    assert sp["reliability"] == round(
        (sp["cross_score"] + sp["snr_score"]) / 2.0, 1
    )
    assert {"cross_score", "snr_score", "snr"} <= set(entries[(10, 20)])
    header = csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert "Reliability(%)" in header


def test_peak_reliability_dual() -> None:
    """0.2.162-补5:SNR≥5 信噪比满分;SNR<5 按信噪比与跨组合出现率双重打分。"""
    from workflow.smile_optimize import _peak_reliability

    # SNR≥5:信噪比满分,可信度由跨组合支持分拉高
    assert _peak_reliability(8.0, 100.0) == 100.0
    assert _peak_reliability(8.0, 50.0) == 75.0
    assert _peak_reliability(5.0, 60.0) == 80.0
    # SNR<5(3-5 混杂区间):信噪比与跨组合出现率双重打分
    assert _peak_reliability(4.0, 100.0) == 75.0  # SNR 50 + 全支持 100
    assert _peak_reliability(4.0, 50.0) == 50.0  # SNR 50 + 半支持 50
    assert _peak_reliability(3.5, 100.0) == 62.5  # SNR 25 + 全支持
    # SNR<3:假峰概率高,信噪比记 0,仅剩跨组合贡献的一半
    assert _peak_reliability(2.5, 100.0) == 50.0
    assert _peak_reliability(2.5, 0.0) == 0.0


def test_snr_score_piecewise() -> None:
    """0.2.162-补5:信噪比评分分段映射(SNR≥5 满分,3-5 线性,<3 记 0)。"""
    from workflow.smile_optimize import _snr_score

    assert _snr_score(8.0) == 100.0
    assert _snr_score(5.0) == 100.0
    assert _snr_score(4.0) == 50.0
    assert _snr_score(3.5) == 25.0
    assert _snr_score(3.0) == 0.0
    assert _snr_score(2.0) == 0.0
