"""SMILE 参数优化(可选,用户后选优化项;不进入自动处理流程)。

目标:在已有生成谱图参数(base_params)基础上只调整 SMILE 参数
(nSigma/thresh),尽可能保留真峰、剔除伪峰。两阶段:
  1) 网格扫描:各参数组合各重构 1 次(SMILE 为确定性算法,同参数同输入
     逐位一致,故扫描不做组内重复),跨组合统计每个峰出现的组合数评估
     真伪(真峰应在多个参数组合下都出现,伪峰只在个别组合出现);按
     跨组合支持/信噪比/峰数/伪影 综合评分选出最优参数。
  2) 重复去伪:对最优参数做多次重构(每次向输入 fid 注入小幅高斯噪声
     模拟测量噪声),组内稳定峰为最终保留峰,写入与 raw 同级的
     smile_optimized/ 目录。

用法:
    results = optimize_smile_parameters(experiment, backend, base_params=run_params)
    print(format_results(results))
    save_report(results, Path("smile_optimize_report.json"))
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.data.internal_data_model import Experiment
from core.qc import peak_detection, spectrum_quality


@dataclass
class SmileParameterResult:
    """一个参数组(SMILE 参数组合)的稳定性/跨组合评分与保留峰。"""

    params: dict[str, Any]
    repeats: int = 3
    decision: str = ""
    overall: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    spectrum_path: str = ""
    stable_peaks: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_smile_grid(
    nsigma_values: tuple[float, ...] = (3.0, 4.0, 5.0, 6.0, 7.0),
    thresh_values: tuple[float, ...] = (0.90, 0.93, 0.95, 0.97, 0.99),
) -> list[dict[str, Any]]:
    """默认参数网格:nSigma × thresh(0.2.162 起仅优化 SMILE 参数;
    0.2.162-补加密为 5×5=25 组,调参更细)。"""
    return [
        {"nsigma": nsigma, "thresh": thresh}
        for nsigma in nsigma_values
        for thresh in thresh_values
    ]


def _read_spectrum(path: str):
    """读取终谱(dic, data)。"""
    import nmrglue as ng

    return ng.pipe.read(str(path))


def _detect_peaks(spectrum_path: str) -> list[peak_detection.Peak]:
    """读取终谱并检测峰(2D/3D 通用)。"""
    _dic, data = _read_spectrum(spectrum_path)
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        arr = arr.real
    return peak_detection.detect(arr)


def _snap_key(position: tuple[float, ...], tol_pts: float) -> tuple[float, ...]:
    """峰位置快照键:按容差网格取整,供组内/跨组合匹配。"""
    if tol_pts > 0:
        return tuple(round(float(v) / tol_pts) * tol_pts for v in position)
    return tuple(round(float(v), 3) for v in position)


def _match_stable_peaks(
    peak_sets: list[list[peak_detection.Peak]],
    min_stability: int,
    tol_pts: float,
) -> tuple[list[peak_detection.Peak], int, dict[tuple[float, ...], int]]:
    """组内跨重构匹配峰;返回(稳定峰, 去重后总峰数, 键→出现次数)。"""
    keys: dict[tuple[float, ...], int] = {}
    best: dict[tuple[float, ...], peak_detection.Peak] = {}
    for peaks in peak_sets:
        matched: set[tuple[float, ...]] = set()
        for peak in peaks:
            key = _snap_key(peak.position, tol_pts)
            if key in matched:
                continue
            keys[key] = keys.get(key, 0) + 1
            matched.add(key)
            if key not in best or peak.height > best[key].height:
                best[key] = peak
    stable = [best[k] for k, count in keys.items() if count >= min_stability]
    stable.sort(key=lambda p: p.height, reverse=True)
    return stable, len(keys), keys


def _score_candidate(
    stable: list[peak_detection.Peak],
    union_count: int,
    cross_support: float,
    quality: Any,
) -> tuple[float, dict[str, float]]:
    """科学评分:稳定性 + 跨组合支持 + 信噪比 + 峰数 + 伪影(0.2.162)。"""
    stability = len(stable) / max(union_count, 1)
    mean_snr = float(np.mean([p.snr for p in stable])) if stable else 0.0
    artifact = float(quality.score.components.artifact)
    snr_norm = min(mean_snr / 10.0, 1.0)
    count_norm = min(len(stable) / 20.0, 1.0)
    overall = 100.0 * (
        0.30 * stability
        + 0.25 * cross_support
        + 0.25 * snr_norm
        + 0.10 * count_norm
        + 0.10 * (artifact / 100.0)
    )
    return overall, {
        "stability": round(stability, 3),
        "cross_support": round(cross_support, 3),
        "snr": round(mean_snr, 1),
        "peak_count": len(stable),
        "artifact": round(artifact, 1),
    }


def optimize_smile_parameters(
    experiment: Experiment,
    backend: Any,
    base_params: dict[str, Any] | None = None,
    grid: list[dict[str, Any]] | None = None,
    *,
    final_repeats: int = 3,
    min_stability: int = 2,
    cross_min: int = 2,
    peak_tol_pts: float = 2.0,
    fid_noise: float = 0.15,
    progress: Callable[[int, int, str], None] | None = None,
    on_result: Callable[[SmileParameterResult], None] | None = None,
) -> list[SmileParameterResult]:
    """两阶段:网格扫描(每组 1 次重构,跨组合评估峰真伪)选优 → 最优参数重复去伪峰。

    SMILE 为确定性算法,同参数同输入逐位一致,故扫描阶段不做组内重复;
    峰真伪由跨参数组合出现数评估(真峰应在多个组合下都出现)。最终阶段
    对最优参数多次重构(每次注入小幅 fid 噪声模拟测量噪声),组内稳定峰
    为保留峰。backend 需提供 reconstruct_nus(experiment, params);每组保留
    base_params 的非 SMILE 参数,只覆盖 nsigma/thresh 与 fid_noise/seed。"""
    grid = grid if grid is not None else default_smile_grid()
    base = dict(base_params or {})
    n_combos = len(grid)
    results: list[SmileParameterResult] = []
    key_to_combos: dict[tuple[float, ...], set[int]] = defaultdict(set)
    scanned: list[dict[str, Any]] = []
    total = len(grid)
    for index, params in enumerate(grid, start=1):
        if progress is not None:
            progress(index, total, f"正在优化 {index}/{total}: {params}")
        result = SmileParameterResult(params=dict(params), repeats=1)
        try:
            run_params = {
                **base,
                **params,
                "direct_phase_search": False,
                "display_phase_search": False,
                "fid_noise": float(fid_noise or 0.0),
                "fid_noise_seed": index * 1000,
            }
            resp = backend.reconstruct_nus(experiment, run_params)
            if not resp.get("success"):
                result.decision = "failed"
                result.message = str(resp.get("message", "重构失败"))
            else:
                last_spec = str(resp["spectrum_path"])
                peaks = _detect_peaks(last_spec)
                seen_keys: set[tuple[float, ...]] = set()
                for peak in peaks:
                    key = _snap_key(peak.position, peak_tol_pts)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    key_to_combos[key].add(index)
                _dic, data = _read_spectrum(last_spec)
                quality = spectrum_quality.evaluate(data)
                result.spectrum_path = last_spec
                result.stable_peaks = [
                    {
                        "position": [float(v) for v in peak.position],
                        "height": float(peak.height),
                        "snr": float(peak.snr),
                    }
                    for peak in peaks
                ]
                scanned.append(
                    {
                        "result": result,
                        "keys": seen_keys,
                        "quality": quality,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - 单组失败不阻断其余候选
            result.decision = "error"
            result.message = str(exc)
        results.append(result)
        if on_result is not None:
            on_result(result)

    # 跨组合峰真伪:某峰出现过的参数组合数(真峰应在多个组合下都出现)
    def _support(key: tuple[float, ...]) -> int:
        return len(key_to_combos.get(key, set()))

    best_entry: dict[str, Any] | None = None
    for entry in scanned:
        result = entry["result"]
        keys = entry["keys"]
        if n_combos > 1:
            true_keys = [k for k in keys if _support(k) >= cross_min]
        else:
            true_keys = list(keys)
        true_ratio = len(true_keys) / max(len(keys), 1) if keys else 0.0
        supports = [_support(k) for k in true_keys]
        cross = float(np.mean(supports)) if supports else 0.0
        if n_combos > 1 and cross > 0:
            cross_norm = min((cross - 1.0) / (n_combos - 1), 1.0)
        else:
            cross_norm = 1.0 if cross > 0 else 0.0
        true_set = set(true_keys)
        true_peaks = [
            p
            for p in result.stable_peaks
            if _snap_key(tuple(p["position"]), peak_tol_pts) in true_set
        ]
        overall, components = _score_candidate(
            [
                peak_detection.Peak(
                    position=tuple(p["position"]),
                    height=p["height"],
                    snr=p["snr"],
                )
                for p in true_peaks
            ],
            len(keys),
            cross_norm,
            entry["quality"],
        )
        components["stability"] = round(true_ratio, 3)
        result.overall = overall
        result.components = components
        result.decision = "accept" if overall >= 60.0 and true_peaks else "warning"
        result.stable_peaks = [dict(p) for p in true_peaks]
        if best_entry is None or overall > best_entry["overall"]:
            best_entry = dict(entry=entry, overall=overall)

    # 第二阶段:对最优参数多次重复重构(注入噪声)去伪峰,确定最终保留峰
    if best_entry is not None:
        best = best_entry["entry"]["result"]
        best_params = best.params
        try:
            peak_sets: list[list[peak_detection.Peak]] = []
            last_spec = ""
            for repeat in range(final_repeats):
                if progress is not None:
                    progress(
                        repeat + 1,
                        final_repeats,
                        f"去伪重复 {repeat + 1}/{final_repeats}: {best_params}",
                    )
                run_params = {
                    **base,
                    **best_params,
                    "direct_phase_search": False,
                    "display_phase_search": False,
                    "fid_noise": float(fid_noise or 0.0),
                    "fid_noise_seed": 9000 + repeat,
                }
                resp = backend.reconstruct_nus(experiment, run_params)
                if not resp.get("success"):
                    break
                last_spec = str(resp["spectrum_path"])
                peak_sets.append(_detect_peaks(last_spec))
            if last_spec:
                stable, _union, _keys = _match_stable_peaks(
                    peak_sets, min_stability, peak_tol_pts
                )
                best.stable_peaks = [
                    {
                        "position": [float(v) for v in peak.position],
                        "height": float(peak.height),
                        "snr": float(peak.snr),
                    }
                    for peak in stable
                ]
                best.spectrum_path = last_spec
                best.repeats = final_repeats
                best.overall = min(best.overall, 99.0) if stable else best.overall
                best.decision = "accept" if stable else "warning"
                best.components["peak_count"] = len(stable)
        except Exception as exc:  # noqa: BLE001
            best.message = f"最终去伪失败: {exc}"

    results.sort(key=lambda r: r.overall, reverse=True)
    return results

def write_smile_optimized_output(
    manager: Any,
    exp_id: str,
    data_id: str,
    spectrum_path: str,
    result: SmileParameterResult,
) -> tuple[Path, Path]:
    """把稳定峰写为契约 §6 峰表 CSV + 参数/评分 JSON 到 smile_optimized/。

    smile_optimized/ 与 raw/ 同级(数据基座下)。返回 (csv_path, json_path)。"""
    from core.peaks.peak_table import save_peaks
    from workflow.pick_peaks import _axes_ppm

    dic, data = _read_spectrum(spectrum_path)
    arr = np.asarray(data)
    axes = _axes_ppm(dict(dic), arr)
    out_dir = manager.data_dir(exp_id, data_id, "smile_optimized")
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{exp_id}-{data_id}_smile_optimized.csv"
    rows: list[dict[str, Any]] = []
    for i, peak in enumerate(result.stable_peaks, start=1):
        pos = [float(v) for v in peak.get("position", [])]
        row: dict[str, Any] = {
            "Peak_ID": i,
            "Intensity": float(peak.get("height", 0.0)),
            "SN": float(peak.get("snr", 0.0)),
            "label": "",
        }
        if arr.ndim == 2:
            row["H_shift"] = (
                float(axes[1][int(pos[1])])
                if len(axes) > 1 and len(pos) > 1
                else 0.0
            )
            row["N_shift"] = (
                float(axes[0][int(pos[0])]) if len(pos) > 0 else 0.0
            )
        else:
            for k in range(3):
                row[f"F{k + 1}_shift"] = (
                    float(axes[k][int(pos[k])])
                    if k < len(axes) and k < len(pos)
                    else 0.0
                )
        rows.append(row)
    save_peaks(csv_path, rows)
    json_path = out_dir / f"{exp_id}-{data_id}_smile_optimized.json"
    json_path.write_text(
        json.dumps(
            {
                "params": result.params,
                "repeats": result.repeats,
                "decision": result.decision,
                "overall": result.overall,
                "components": result.components,
                "peak_count": len(result.stable_peaks),
                "spectrum_path": result.spectrum_path,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return csv_path, json_path


def format_results(results: list[SmileParameterResult]) -> str:
    """把候选列表渲染为对齐的参数组合 + 稳定性评分表格。"""
    header = "{:>6} {:>6} {:>8} {:>7} {:>9} {:>6} {:>6} {:>6} {:>6}".format(
        "nSigma", "thresh", "decision", "overall", "stability", "cross", "snr",
        "peaks", "artifact"
    )
    lines = [header, "-" * len(header)]
    for result in results:
        comp = result.components
        lines.append(
            "{:>6} {:>6} {:>8} {:>7.1f} {:>9.2f} {:>6.2f} {:>6.1f} {:>6} {:>6.1f}".format(
                result.params.get("nsigma", 0),
                result.params.get("thresh", 0),
                result.decision,
                result.overall,
                comp.get("stability", 0),
                comp.get("cross_support", 0),
                comp.get("snr", 0),
                comp.get("peak_count", 0),
                comp.get("artifact", 0),
            )
        )
    return "\n".join(lines)


def save_report(results: list[SmileParameterResult], path: Path | str) -> Path:
    """保存参数组 + 评分报告(JSON)。"""
    out = Path(path)
    out.write_text(
        json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return out
