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


def test_confidence_scored_on_fake(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.162-补6:四分量可信度(snr+稳定性+峰形+局部噪声,clamp 0~100)。"""
    exp = read_dataset(bruker_dir / "nus_3d")
    # 伪峰(25,25)只出现在扫描组合1与全部最终重复;低 SNR 峰(35,35)全 seed
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
    true = by_pos[(10, 20)]
    assert true["snr_points"] == 40.0  # S/N≥10 满分
    assert true["existence_points"] == 15.0  # 全支持
    for peak in best.stable_peaks:
        raw = (
            peak["snr_points"]
            + peak["stability_points"]
            + peak["shape_points"]
            + peak["local_noise_points"]
        )
        assert peak["confidence"] == round(
            max(0.0, min(100.0, raw * 100.0 / 90.0)), 1
        )
        assert peak["grade"] in ("A", "B", "C", "D", "E")
    low = by_pos[(35, 35)]
    assert low["snr_points"] < true["snr_points"]  # 低 SNR → S/N 分低
    assert low["confidence"] <= true["confidence"]
    sp = by_pos[(25, 25)]
    assert sp["support"] >= 1
    assert sp["existence_points"] in (2.0, 15.0)  # 1/2 或 2/2 档


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
    assert payload["schema"] == "smile_reliability_v3"
    assert payload["n_combos"] == 2
    entries = {
        tuple(round(v) for v in p["position_pts"]): p for p in payload["peaks"]
    }
    true = entries[(10, 20)]
    assert true["confidence"] >= 70.0  # 全支持高 SNR → 高可信
    assert {
        "snr_points",
        "existence_points",
        "intensity_points",
        "position_points",
        "stability_points",
        "shape_points",
        "local_noise_points",
        "confidence",
        "grade",
        "flags",
    } <= set(true)
    header = csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert "Reliability(%)" in header


def test_confidence_composition_and_grade() -> None:
    """0.2.162-补8:score=(四分量和)×100/90 归一化 clamp 0~100,等级 A-E。"""
    from workflow.smile_optimize import _compose_confidence

    assert _compose_confidence(40.0, 40.0, 5.0, 5.0) == (100.0, "A")  # 90→100
    assert _compose_confidence(37.0, 30.0, 2.0, 1.0) == (77.8, "B")  # 70→77.8
    assert _compose_confidence(28.0, 25.0, 2.0, 1.0) == (62.2, "C")  # 56→62.2
    assert _compose_confidence(23.0, 15.0, 1.0, 1.0) == (44.4, "D")  # 40→44.4
    assert _compose_confidence(23.0, 10.0, 1.0, 1.0) == (38.9, "E")  # 35→38.9
    # clamp 下限:理论最低 -25 → 0
    assert _compose_confidence(0.0, -15.0, -5.0, -5.0) == (0.0, "E")


def test_snr_points_table() -> None:
    """0.2.162-补6:S/N 基础分分段表(0~40)。"""
    from workflow.smile_optimize import _snr_points

    assert _snr_points(15.0) == 40.0
    assert _snr_points(10.0) == 40.0
    assert _snr_points(9.0) == 37.0
    assert _snr_points(7.0) == 33.0
    assert _snr_points(5.5) == 28.0
    assert _snr_points(4.5) == 23.0
    assert _snr_points(3.7) == 18.0
    assert _snr_points(3.2) == 14.0
    assert _snr_points(2.7) == 10.0
    assert _snr_points(2.2) == 6.0
    assert _snr_points(1.7) == 3.0
    assert _snr_points(1.0) == 0.0
