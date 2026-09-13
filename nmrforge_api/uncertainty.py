"""峰位离散度 / Δδ 汇总——**测试与检测辅助,不属于处理契约**(2026-09-13)。

用途边界(用户 2026-09-13 裁定「作为测试时用来检测的用途」):

- `run_parameter_study` / `run_sweep` / CLI / `records/` **都不调用本模块**,
  也不产出 σ/Δδ 文件——软件本身只执行处理并留档(provenance + QC);
- 本模块供**测试/复核**与下游分析侧使用:回归时用它检测「参数是否真的生效」
(全 0 的 σ/Δδ = 参数被静默忽略,历史上真机出过此缺陷)、比较两种定位算法
  的峰位差,或作为下游统计实现的参考;
- 写到论文里的显著性判断仍由下游分析代码按自己的统计假设完成。

---

把「同一批峰在多组处理参数下的位置」汇总成峰位不确定度与 CSP 下限。

指标定义(写进论文方法部分时直接用):

- σ_n:某核在该峰上的峰位标准差(样本标准差,按组合数 ddof=1);
- range_n = max - min(该核的极差,受组合数影响,宜与 σ 一起看);
- **Δδ 下限(主指标)** ``delta_std = sqrt(Σ_n (w_n · σ_n)²)``,权重默认
  1H = 1、15N = ``csp_n_weight``(默认 0.2,即 15N-HSQC 常用 1/5 加权),
  其余核权重 1;该式等价于假设各核峰位误差独立,是**解析组合**;
- ``delta_max``:同一公式下逐组合相对均值的 Δδ 最大值(经验最坏情形);
- 数据集级汇总给出各峰 ``delta_std`` 的中位数/p90/最大值——这个数值就是
  「CSP 差异要被当真,至少得大于的处理不确定度」。

注意:这里量化的是**处理参数引入的位置不确定度**,不含采样噪声/峰重叠/
指认错误;它是 CSP 显著性判据的下限,不是全部误差预算。
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from nmrforge_api.peaks import PeakMeasurement

DEFAULT_CSP_N_WEIGHT = 0.2
DEFAULT_NUCLEI: tuple[str, ...] = ("1H", "15N")

DEFINITION = (
    "σ 为同一批峰在各处理参数组合下的峰位标准差;Δδ 下限 = "
    "sqrt(Σ_n (w_n·σ_n)²),w(1H)=1,w(15N)=csp_n_weight,其余核 1;"
    "delta_max 为逐组合相对峰位均值的同式 Δδ 最大值。"
)


@dataclass
class PeakUncertainty:
    """单个峰的峰位不确定度汇总。"""

    peak_id: int
    assignment: str = ""
    n_runs: int = 0
    missing_runs: int = 0
    mean: dict[str, float] = field(default_factory=dict)
    sigma: dict[str, float] = field(default_factory=dict)
    value_range: dict[str, float] = field(default_factory=dict)
    delta_std: float = 0.0
    delta_max: float = 0.0
    worst_run: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "peak_id": int(self.peak_id),
            "assignment": self.assignment,
            "n_runs": int(self.n_runs),
            "missing_runs": int(self.missing_runs),
            "mean": {k: float(v) for k, v in self.mean.items()},
            "sigma": {k: float(v) for k, v in self.sigma.items()},
            "range": {k: float(v) for k, v in self.value_range.items()},
            "delta_std_ppm": float(self.delta_std),
            "delta_max_ppm": float(self.delta_max),
            "worst_run": self.worst_run,
        }


def _weight(nucleus: str, csp_n_weight: float) -> float:
    if nucleus == "15N":
        return float(csp_n_weight)
    return 1.0


def _normalize(
    runs: Mapping[str, Sequence[PeakMeasurement]] | Iterable[Any],
) -> dict[str, list[PeakMeasurement]]:
    if isinstance(runs, Mapping):
        return {str(k): list(v) for k, v in runs.items()}
    out: dict[str, list[PeakMeasurement]] = {}
    for item in runs:
        run_id = str(getattr(item, "run_id", "") or "")
        if not run_id:
            continue
        out[run_id] = list(getattr(item, "measurements", []) or [])
    return out


def position_uncertainty(
    runs: Mapping[str, Sequence[PeakMeasurement]] | Iterable[Any],
    *,
    csp_n_weight: float = DEFAULT_CSP_N_WEIGHT,
    nuclei: Sequence[str] | None = None,
) -> list[PeakUncertainty]:
    """按峰汇总跨组合的峰位不确定度(只统计 ``status=success`` 的组合)。"""
    by_run = _normalize(runs)
    wanted = tuple(nuclei) if nuclei else DEFAULT_NUCLEI
    per_peak: dict[int, dict[str, Any]] = {}
    for run_id, measurements in by_run.items():
        for measurement in measurements:
            entry = per_peak.setdefault(
                measurement.peak_id,
                {
                    "assignment": measurement.assignment,
                    "values": {},
                    "missing": 0,
                    "runs": 0,
                },
            )
            entry["runs"] += 1
            if not measurement.found:
                entry["missing"] += 1
                continue
            for nucleus in wanted:
                if nucleus in measurement.positions:
                    entry["values"].setdefault(nucleus, {})[run_id] = float(
                        measurement.positions[nucleus]
                    )

    results: list[PeakUncertainty] = []
    for peak_id in sorted(per_peak):
        entry = per_peak[peak_id]
        # 只有所有被测核都测到的组合才进入统计,避免「半边数据」拉偏 σ
        key_sets = [set(entry["values"].get(n, {}).keys()) for n in wanted]
        common = set.intersection(*key_sets) if key_sets and all(key_sets) else set()
        item = PeakUncertainty(
            peak_id=peak_id,
            assignment=str(entry["assignment"] or ""),
            n_runs=len(common),
            missing_runs=entry["runs"] - len(common),
        )
        if not common:
            results.append(item)
            continue
        ordered = sorted(common)
        for nucleus in wanted:
            values = [entry["values"][nucleus][run_id] for run_id in ordered]
            item.mean[nucleus] = statistics.fmean(values)
            item.sigma[nucleus] = (
                statistics.stdev(values) if len(values) > 1 else 0.0
            )
            item.value_range[nucleus] = max(values) - min(values)
        item.delta_std = math.sqrt(
            sum(
                (_weight(n, csp_n_weight) * item.sigma.get(n, 0.0)) ** 2
                for n in wanted
            )
        )
        worst_run = ""
        worst_delta = 0.0
        for run_id in ordered:
            delta = math.sqrt(
                sum(
                    (
                        _weight(n, csp_n_weight)
                        * (
                            entry["values"][n][run_id]
                            - item.mean[n]
                        )
                    )
                    ** 2
                    for n in wanted
                )
            )
            if delta > worst_delta:
                worst_delta = delta
                worst_run = run_id
        item.delta_max = worst_delta
        item.worst_run = worst_run
        results.append(item)
    return results


def uncertainty_summary(
    uncertainties: Sequence[PeakUncertainty],
    *,
    csp_n_weight: float = DEFAULT_CSP_N_WEIGHT,
    n_runs: int = 0,
) -> dict[str, Any]:
    """数据集级汇总:峰位不确定度分布 → CSP 判据下限。"""
    usable = [u for u in uncertainties if u.n_runs > 0]
    delta = sorted(u.delta_std for u in usable)
    summary: dict[str, Any] = {
        "csp_n_weight": float(csp_n_weight),
        "n_runs": int(n_runs or (usable[0].n_runs if usable else 0)),
        "n_peaks": len(usable),
        "n_peaks_unmeasured": len(uncertainties) - len(usable),
        "definition": DEFINITION,
    }
    if delta:
        summary["delta_std_ppm"] = {
            "min": delta[0],
            "median": _percentile(delta, 50.0),
            "p90": _percentile(delta, 90.0),
            "max": delta[-1],
        }
    else:
        summary["delta_std_ppm"] = {}
    sigma: dict[str, dict[str, float]] = {}
    for nucleus in sorted({n for u in usable for n in u.sigma}):
        values = sorted(u.sigma.get(nucleus, 0.0) for u in usable)
        sigma[nucleus] = {
            "median": _percentile(values, 50.0),
            "p90": _percentile(values, 90.0),
            "max": values[-1] if values else 0.0,
        }
    summary["sigma_ppm"] = sigma
    return summary


def _percentile(sorted_values: Sequence[float], percent: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * (float(percent) / 100.0)
    lower = int(math.floor(rank))
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = rank - lower
    return float(
        sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight
    )


__all__ = [
    "DEFAULT_CSP_N_WEIGHT",
    "DEFAULT_NUCLEI",
    "DEFINITION",
    "PeakUncertainty",
    "position_uncertainty",
    "uncertainty_summary",
]
