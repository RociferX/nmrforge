"""SMILE 参数优化(可选,用户后选优化项;不进入自动处理流程)。

用户方案(2026-09-10/11):**以终跑脚本为模板,只换 SMILE 参数** ——
  1) 直接维处理跑一次(3D 拿切片文件;2D 单文件整脚本逐组跑);
  2) 每组参数跑一次「SMILE + 间接维」得到候选谱;
  3) 立刻评估(峰 + 质量分 + SMILE 拟合残差 + 一致性残差)后**删除**候选谱;
  4) 按排序口径比较参数组合 → 参数组合排序表(CSV/JSON)+ 前三名脚本;
     「按 Rank1 重跑」用的脚本始终是**全采样**脚本。

运行方式跟排序口径(修23):净真峰优先 → 全采样重建(峰数=终谱口径);
一致性优先 → 留出(train)重建(留出点不参与重建,残差作依据);每候选只跑一次。
"""

from __future__ import annotations

import json
import time
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
    repeats: int = 1
    n_combos: int = 0
    rank: int = 0
    true_peak_count: int = 0
    decision: str = ""
    overall: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    spectrum_path: str = ""
    stable_peaks: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# 优化程度 2x2..5x5:从 5 档默认值均匀取样(0.2.199-补29hz-修4)
_NSIGMA_FULL: tuple[float, ...] = (3.0, 4.0, 5.0, 6.0, 7.0)
_THRESH_FULL: tuple[float, ...] = (0.90, 0.93, 0.95, 0.97, 0.99)
SMILE_GRID_MIN, SMILE_GRID_MAX = 2, 5
SMILE_GRID_DEFAULT = 4  # 0.2.199-补29hz-修5(用户):默认 4x4=16 组
# 0.2.199-补29hz-修10(用户):留出采样点残差默认每 4 个采样点留 1 个(25%),
# 用于「没有全采样参考时判断真伪峰」的排序依据。
# 0.2.199-补29hz-修23(用户):按排序口径选运行方式(每候选一次)——
#   净真峰优先:全采样重建,峰计数/质量分就是终谱口径(不做留出);
#   一致性优先:留出重建,留出点不参与重建,残差作排序依据。
SMILE_HOLDOUT_RATIO = 0.25
# 0.2.199-补29hz-修16(用户):这一步的目的是「尽量重构出更多真峰」,所以候选评估
# 用**独立的低阈值**(3σ,峰检测算法默认档),与「峰挑选」步骤的阈值(默认 35σ,
# 那是出峰表用的)无关 —— 阈值太高会把弱真峰一起漏掉,排序就没法反映重建好坏。
# 伪峰由排序口径本身抑制:只在个别参数组合出现的峰算「疑伪峰」并扣分。
SMILE_SCAN_SIGMA = 3.0


def smile_scan_sign_mode(experiment: Any) -> str:
    """SMILE 候选评估的峰符号模式 —— 与选峰步骤同源(presets peak_sign)。

    mixed 实验(HNCACB/ CBCANCO 等正负共存)用 both,uniform/未知用 dominant
(只留占多数符号的峰,不关心正负),避免把反相真峰当噪声漏掉。
    """
    from core.experiments.registry import get as get_template

    etype = getattr(experiment, "experiment_type", None)
    name = str(getattr(etype, "name", "") or "")
    template = get_template(name) if name else None
    peak_sign = str(getattr(template, "peak_sign", "uniform") or "uniform")
    return "both" if peak_sign == "mixed" else "dominant"


def smile_scan_edge_margin() -> int:
    """候选评估排除上下边缘轴峰 —— 与选峰步骤同一常量(workflow.pick_peaks)。"""
    from workflow.pick_peaks import PICK_EDGE_MARGIN

    return int(PICK_EDGE_MARGIN)


def evaluate_candidate_peaks(
    arr: Any, *, sign_mode: str = "dominant"
) -> list[Any]:
    """候选谱峰检测:低阈值(SMILE_SCAN_SIGMA)+ 同源符号模式 + 排除轴峰。

    用户 2026-09-11:「SMILE 这一步是为了尽量重构出多真峰」——所以这里刻意用低
    阈值(3σ)尽量不漏真峰,候选之间的差别靠「稳定峰 − 疑伪峰」体现。
    """
    params = peak_detection.PeakDetectionParams(
        sigma_multiplier=SMILE_SCAN_SIGMA,
        min_snr=SMILE_SCAN_SIGMA,
        sign_mode=sign_mode,
        edge_margin=smile_scan_edge_margin(),
    )
    return peak_detection.detect(np.asarray(arr), params)


def _subsample(values: tuple[float, ...], count: int) -> tuple[float, ...]:
    """从 values 均匀取 count 个(含首尾)。"""
    if count <= 1:
        return (values[len(values) // 2],)
    if count >= len(values):
        return tuple(values)
    picked = [round(i * (len(values) - 1) / (count - 1)) for i in range(count)]
    return tuple(values[i] for i in dict.fromkeys(picked))


def smile_grid(size: int = SMILE_GRID_DEFAULT) -> list[dict[str, Any]]:
    """按优化程度生成 n×n 网格(2x2..5x5,默认 4x4=16 组)。

    2x2 最快(4 组),5x5 最细(25 组);耗时大致与组数成正比。
    """
    count = max(SMILE_GRID_MIN, min(SMILE_GRID_MAX, int(size or SMILE_GRID_DEFAULT)))
    return default_smile_grid(
        _subsample(_NSIGMA_FULL, count), _subsample(_THRESH_FULL, count)
    )


def estimate_scan_seconds(
    experiment: Experiment, n_combos: int
) -> tuple[float, float]:
    """按数据规模粗估每组/总耗时(秒);第一组跑完由实测覆盖。

    经验模型(2026-09-10,sampleC 3D NUS 250 点/直接维 TD 2048 实测 86s/组):
    每组 ≈ 85s × (采样点数/250) × (直接维 TD/2048);2D 再 ×0.3。
    """
    sampling = getattr(experiment, "sampling", None)
    nus_points = len(getattr(sampling, "nus_list", None) or []) or 250
    direct_td = 2048.0
    for dim in getattr(experiment, "dimensions", None) or []:
        role = str(getattr(getattr(dim, "role", None), "name", ""))
        if role.startswith("DIRECT"):
            try:
                direct_td = float(getattr(dim, "td", 0) or 0) or direct_td
            except (TypeError, ValueError):
                pass
    per_group = 85.0 * (float(nus_points) / 250.0) * (direct_td / 2048.0)
    if int(getattr(experiment, "ndim", 2) or 2) <= 2:
        per_group *= 0.3
    per_group = max(5.0, per_group)
    return per_group, per_group * max(1, int(n_combos))

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


def _snap_key(position: tuple[float, ...], tol_pts: float) -> tuple[float, ...]:
    """峰位置快照键:按容差网格取整,供组内/跨组合匹配。

    0.2.199-补29eo:先归一到整数像素——亚像素修正使位置在 ±0.5px 内抖动,
    若不先取整,峰恰在 tol 网格边界(如 10pt/4pt 桶)时抖动会翻桶,
    跨组合匹配断裂(稳定峰被误判为伪峰)。
    """
    ipos = tuple(round(float(v)) for v in position)
    if tol_pts > 0:
        return tuple(round(float(v) / tol_pts) * tol_pts for v in ipos)
    return ipos


def scan_smile_parameters(
    experiment: Experiment,
    backend: Any,
    base_params: dict[str, Any] | None = None,
    *,
    scan_dir: Path | str,
    grid: list[dict[str, Any]] | None = None,
    grid_size: int = SMILE_GRID_DEFAULT,
    rank_mode: str = "true_peaks",
    cross_min: int = 2,
    peak_tol_pts: float = 4.0,
    keep_top: int = 3,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """SMILE 参数扫描(0.2.199-补29hz-修3,用户方案)。

    以终跑脚本为模板**只换 SMILE 参数**:① 直接维跑一次得到切片文件;
    ② 每组参数跑一次「SMILE + 间接维」得到终谱;③ 立即取指标;④ 删除该谱
(候选谱只短暂存在于 scan_dir,通常位于内存盘)。

    指标(无需保留谱):检出峰数、跨参数组合稳定峰数(出现 ≥cross_min 组)、
    稳定峰平均 S/N、谱图综合质量分;排序按(稳定峰数, 平均 S/N, 质量分)。
    返回 {success, message, logs, rows(按名次), scripts({名次: 脚本文本}),
    scan_dir, n_combos}。
    """
    combos = list(grid) if grid is not None else smile_grid(grid_size)
    base = dict(base_params or {})
    sign_mode = smile_scan_sign_mode(experiment)
    edge_margin = smile_scan_edge_margin()
    mode = str(rank_mode or "true_peaks").lower()
    # 修23(用户):按需要的排序方法选运行方式(每候选只跑一次)——
    #   净真峰优先 → 全采样跑(峰数/质量分即终谱口径,不留出);
    #   一致性优先 → 留出跑(留出点不参与重建,残差作排序依据)。
    holdout_ratio = 0.0
    if mode == "consistency":
        holdout_ratio = float(
            base.get("holdout_ratio", SMILE_HOLDOUT_RATIO) or 0.0
        )
    est_group, est_total = estimate_scan_seconds(experiment, len(combos))
    started = time.time()
    _first_done: list[float] = []

    def _progress(index: int, total: int, message: str) -> None:
        if progress is None:
            return
        if index == 0:
            progress(
                index,
                total,
                f"{message} | 按数据规模估算约 {est_group:.0f}s/组、"
                f"合计约 {est_total / 60.0:.1f} 分钟(第一组完成后更新)",
            )
            return
        if index >= 2 and not _first_done:
            measured = time.time() - started
            _first_done.append(measured)
            remain = measured * max(0, total - index + 1)
            progress(
                index,
                total,
                f"{message} | 实测约 {measured:.0f}s/组,"
                f"预计剩余 {remain / 60.0:.1f} 分钟",
            )
            return
        progress(index, total, message)

    def _evaluate(path: str) -> dict[str, Any]:
        """候选谱评估:峰 + 质量分(此刻谱还在,评完即被删)。"""
        _dic, data = _read_spectrum(path)
        arr = np.asarray(data)
        if np.iscomplexobj(arr):
            arr = arr.real
        # 低阈值 + 同源符号模式 + 排除轴峰(0.2.199-补29hz-修16);
        # 不跟「峰挑选」步骤的阈值(默认 35σ)——那一步是出峰表,不是评候选。
        peaks = evaluate_candidate_peaks(arr, sign_mode=sign_mode)
        quality = spectrum_quality.evaluate(arr)
        # 0.2.199-补29hz-修4:综合分在 QualityResult.score.overall 上,
        # QualityResult 本身没有 overall(此前取值恒为 0)
        _qscore = getattr(quality, "score", None)
        return {
            "peak_count": len(peaks),
            "quality": float(getattr(_qscore, "overall", 0.0) or 0.0),
            "peaks": [
                {
                    "position": [float(v) for v in peak.position],
                    "height": float(peak.height),
                    "snr": float(peak.snr),
                }
                for peak in peaks
            ],
        }

    # 口径写入日志,便于核对(与峰挑选步骤的阈值无关)
    _criteria_log = (
        f"候选评估口径:阈值 {SMILE_SCAN_SIGMA:g}σ(独立于选峰步骤)、"
        f"符号模式 {sign_mode}、排除轴峰 {edge_margin} 点"
    )
    scan = backend.smile_scan(
        experiment,
        base,
        combos,
        work_dir=scan_dir,
        evaluate=_evaluate,
        progress=_progress,
        holdout_ratio=holdout_ratio,
    )
    if not scan.get("success"):
        raise RuntimeError(str(scan.get("message", "SMILE 扫描失败")))
    scan["logs"] = [_criteria_log] + list(scan.get("logs") or [])
    candidates = list(scan.get("candidates") or [])
    n_combos = len(candidates) or 1
    effective_cross = cross_min if n_combos >= cross_min else 1
    key_to_combos: dict[tuple[float, ...], set[int]] = defaultdict(set)
    keys_by_index: dict[int, set[tuple[float, ...]]] = {}
    for entry in candidates:
        metrics = dict(entry.get("metrics") or {})
        seen: set[tuple[float, ...]] = set()
        for peak in metrics.get("peaks") or []:
            key = _snap_key(tuple(peak["position"]), peak_tol_pts)
            seen.add(key)
            key_to_combos[key].add(int(entry["index"]))
        keys_by_index[int(entry["index"])] = seen
    rows: list[dict[str, Any]] = []
    for entry in candidates:
        metrics = dict(entry.get("metrics") or {})
        params = dict(entry.get("params") or {})
        stable = 0
        suspect = 0
        snr_values: list[float] = []
        for peak in metrics.get("peaks") or []:
            key = _snap_key(tuple(peak["position"]), peak_tol_pts)
            if len(key_to_combos.get(key, set())) < effective_cross:
                # 只在个别参数组合里出现的峰:疑伪峰
                suspect += 1
                continue
            stable += 1
            try:
                snr_values.append(float(peak.get("snr", 0.0) or 0.0))
            except (TypeError, ValueError):
                continue
        mean_snr = float(np.mean(snr_values)) if snr_values else 0.0
        quality = float(metrics.get("quality", 0.0) or 0.0)
        rows.append(
            {
                "index": int(entry["index"]),
                "nsigma": float(params.get("nsigma", 0.0) or 0.0),
                "thresh": float(params.get("thresh", 0.0) or 0.0),
                "peak_count": int(metrics.get("peak_count", 0) or 0),
                "stable_count": int(stable),
                "suspect_count": int(suspect),
                "net_peaks": int(stable - suspect),
                "mean_snr": round(mean_snr, 3),
                "quality": round(quality, 2),
                "holdout_rmse": float(metrics.get("holdout_rmse", 0.0) or 0.0),
                "holdout_corr": float(metrics.get("holdout_corr", 0.0) or 0.0),
                "smile_rms_ratio": float(
                    metrics.get("smile_rms_ratio", 0.0) or 0.0
                ),
                "composite": round(stable - suspect + 0.01 * mean_snr + 0.01 * quality, 3),
                "ok": bool(entry.get("ok")),
                "error": str(metrics.get("error", "") or ""),
                "_script": str(entry.get("script", "")),
            }
        )
    # 用户目标(2026-09-10):尽量少伪峰 + 尽量多真峰 → 先按「净真峰」
    # (稳定峰 − 疑伪峰),再按稳定峰数、平均 S/N、质量分
    # 0.2.199-补29hz-修7(用户):两种排序口径——
    #   "true_peaks"(默认):净真峰(稳定峰−疑伪峰)优先,再稳定峰/平均 S/N/残差;
    #   "consistency":留出残差优先(对未参与重建的采样点预测更准),再拟合残差、
    #                  净真峰、稳定峰、平均 S/N、质量分。
    if mode == "consistency":
        rows.sort(
            key=lambda r: (
                -float(r.get("holdout_rmse", 0.0) or 0.0),
                -float(r.get("smile_rms_ratio", 0.0) or 0.0),
                r["net_peaks"],
                r["stable_count"],
                r["mean_snr"],
                r["quality"],
            ),
            reverse=True,
        )
    else:
        rows.sort(
            key=lambda r: (
                r["net_peaks"],
                r["stable_count"],
                r["mean_snr"],
                -float(r.get("holdout_rmse", 0.0) or 0.0),
                -float(r.get("smile_rms_ratio", 0.0) or 0.0),
                r["quality"],
            ),
            reverse=True,
        )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    scripts = {
        int(row["rank"]): str(row.get("_script", ""))
        for row in rows[: max(1, int(keep_top))]
    }
    for row in rows:
        row.pop("_script", None)
    return {
        "success": True,
        "message": str(scan.get("message", "")),
        "logs": list(scan.get("logs") or []),
        "rows": rows,
        "scripts": scripts,
        "scan_dir": str(scan.get("scan_dir", scan_dir)),
        "n_combos": n_combos,
        "rank_mode": mode,
    }


def write_smile_scan_output(
    manager: Any,
    exp_id: str,
    data_id: str,
    rows: list[dict[str, Any]],
    scripts: dict[int, str],
) -> dict[str, str]:
    """写「参数组合排序表」(CSV+JSON)与前三名脚本(0.2.199-补29hz-修3)。

    排序表落 `<data>/smile_optimized/`;前三脚本落 `<data>/process/
    <data_id>_nus_rankN.com`(与终跑脚本同处,可直接运行)。
    返回 {csv, json, rank1, rank2, rank3}(缺项不出现)。
    """
    import csv
    import json

    out_dir = manager.data_dir(exp_id, data_id, "smile_optimized")
    out_dir.mkdir(parents=True, exist_ok=True)
    proc_dir = manager.data_dir(exp_id, data_id, "process")
    proc_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{exp_id}-{data_id}_smile_ranking.csv"
    json_path = out_dir / f"{exp_id}-{data_id}_smile_ranking.json"
    fields = [
        "rank", "index", "nsigma", "thresh", "net_peaks", "stable_count",
        "suspect_count", "peak_count",
        "mean_snr", "quality", "smile_rms_ratio", "holdout_rmse", "holdout_corr",
        "composite", "ok", "error",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})
    json_path.write_text(
        json.dumps({"rows": rows, "count": len(rows)}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    paths: dict[str, str] = {
        "csv": str(csv_path),
        "json": str(json_path),
    }
    for rank, script in sorted(scripts.items()):
        if not script or rank < 1 or rank > 3:
            continue
        target = proc_dir / f"{data_id}_nus_rank{rank}.com"
        target.write_text(script, encoding="utf-8", newline="\n")
        paths[f"rank{rank}"] = str(target)
    return paths

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
