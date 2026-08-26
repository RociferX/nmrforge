"""逐维基线优化(用户方案,G2B-007 第 4 点)。

对每维(直接+间接)用 core.qc.baseline_quality 评分,网格
mode ∈ {off, auto} × order ∈ {1,2,3},选每维最优写回 baseline 配置;
最终谱由真实管线渲染(baseline 写回 → render_scripts / backend.process,
uniform 与 NUS 均支持)。score 可注入。

0.2.132(条纹伪影修复):
- 评分轴修正:score_fn 使用逻辑轴对应的 numpy 轴(axis_index),与
  baseline.apply 的校正轴一致(旧代码误用 enumerate 序号,对直接维
  在前的 dimensions 顺序评错轴,间接轴高分校正"吃平"整列被误选);
- 硬性条纹否决:逐迹校正(plain polyfit,与真实脚本 POLY 一致)在
  corrected 谱上引入明显迹间断层(相邻迹端部均值跳变 >> 中位水平)时
  该候选不计入择优 → 只剩 off;确保写回的 POLY 不会让终谱出现竖线;
- 无实质增益保持 off:增益 ≤ 0.5(评分单位)不再写 POLY -auto。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.data.internal_data_model import Experiment
from core.processing import baseline as baseline_proc
from core.processing.axes import axis_index


@dataclass
class BaselineOptimizeResult:
    """逐维基线优化结果。"""

    baseline: dict[str, dict[str, Any]]
    scores: dict[str, dict[str, float]]
    spectrum_path: str
    logs: list[str] = field(default_factory=list)
    optimized: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _default_score(data: np.ndarray, axis: int) -> float:
    """默认评分:core.qc.baseline_quality(轴移到末尾评估)。"""
    from core.qc import baseline_quality

    moved = np.moveaxis(np.asarray(data), axis, -1)
    return float(baseline_quality.evaluate(moved).score)


def _fmt_cfg(cfg: dict[str, Any]) -> str:
    """紧凑打印基线配置(enabled/mode/order)。"""
    return (
        f"enabled={cfg.get('enabled', True)}"
        f",mode={cfg.get('mode', 'auto')}"
        f",order={cfg.get('order', 0)}"
    )


# 硬性条纹否决的候选分数(有限负数,避免 -inf 进入 GUI/JSON 序列化)
_VETOED_SCORE = -1e9


def _stripe_ratio(data: np.ndarray, axis: int) -> float:
    """逐迹校正引入的迹间断层指标(条纹):相邻迹端部均值跳变 max / 中位。

    与 core.qc.baseline_quality 的罚项同一度量;此处用于硬性否决。
    """
    real = np.real(np.asarray(data))
    n = real.shape[axis]
    if n < 8:
        return 0.0
    edge = max(int(n * 0.08), 2)
    moved = np.moveaxis(real, axis, -1)
    flat = moved.reshape(-1, n)
    left = flat[:, :edge].mean(axis=1)
    right = flat[:, -edge:].mean(axis=1)
    jumps = np.abs(np.diff(0.5 * (left + right)))
    if jumps.size == 0:
        return 0.0
    med = float(np.median(jumps))
    floor = float(np.max(np.abs(real))) * 1e-4 + 1e-12
    return float(np.max(jumps)) / max(med, floor)


def _has_stripe_artifact(data: np.ndarray, axis: int, threshold: float = 8.0) -> bool:
    """校正候选存在明显条纹(稀疏强峰拉偏逐迹拟合)时返回 True。"""
    return _stripe_ratio(data, axis) > threshold


def _decimated(data: np.ndarray, axis: int, max_traces: int) -> np.ndarray:
    """非 axis 维度按步长抽样(迹线子采样),控制逐候选评分开销。

    评分指标为全局均值/条纹比,对迹线子采样近似不变(0.2.199-补8);
    3D 最大轴数万条迹,全量稳健拟合开销大,子采样到 ≤max_traces 条。
    """
    n = max(data.shape[axis], 1)
    n_traces = max(data.size // n, 1)
    if data.ndim < 2 or n_traces <= max_traces:
        return data
    per = max(
        1,
        int(math.ceil((n_traces / max_traces) ** (1.0 / (data.ndim - 1)))),
    )
    slices = [
        (
            slice(None, None, per)
            if (a != axis and data.shape[a] >= 2 * per)
            else slice(None)
        )
        for a in range(data.ndim)
    ]
    return data[tuple(slices)]


def optimize_baseline(
    experiment: Experiment,
    spectrum_path: Path | str,
    *,
    grid: list[tuple[str, int]] | None = None,
    score_fn: Callable[[np.ndarray, int], float] | None = None,
    progress: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    max_traces: int = 4096,
) -> BaselineOptimizeResult:
    """逐维基线优化:每维网格 mode∈{off,auto}×order∈{1,2,3},内存内评分,
    选每维最优写回 baseline 配置。直接全网格优化;无实质增益(≤0.5)或候选
    引入明显条纹时保持 off 配置。日志逐轴说明配置变化与分数增益。
    score_fn(data, np_axis) 返回该轴基线质量分(默认 baseline_quality);
    off=不校正。progress 逐轴/候选输出进度,cancel 置位时在候选间检查并抛
    「任务已取消」(0.2.199-补7:3D 稳健逐迹拟合可达数万迹,需进度与可取消);
    max_traces:候选评分的迹线子采样上限(0.2.199-补8,默认 4096,越小越快,
    评分近似不变;off 评分≥95 的轴跳过候选直接保持 off)。
    返回 {"baseline", "scores", "logs", "optimized", "skipped"}。
    """
    import nmrglue as ng

    _dic, data = ng.pipe.read(str(spectrum_path))
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        arr = arr.real
    axes = [dim.logical_axis for dim in experiment.dimensions]
    grid = grid if grid is not None else [
        ("off", 0),
        ("auto", 1),
        ("order", 1),
        ("order", 2),
        ("order", 3),
    ]
    score_fn = score_fn or _default_score
    default_cfg: dict[str, Any] = {"enabled": True, "mode": "auto", "order": 0}
    off_cfg: dict[str, Any] = {"enabled": False, "mode": "auto", "order": 0}
    min_gain = 0.5  # 无实质增益(评分单位)时保持 off,避免无意义/有害写回
    baseline_cfg: dict[str, dict[str, Any]] = {}
    scores: dict[str, dict[str, float]] = {}
    logs: list[str] = []
    optimized: list[str] = []
    unchanged: list[str] = []
    for index, axis in enumerate(axes, start=1):
        if progress is not None:
            progress(
                f"基线优化中(内存评分): 轴 {axis}({index}/{len(axes)}),"
                "逐候选评分中"
            )
        np_axis = axis_index(axis, arr.ndim)
        # 0.2.199-补8:候选评分的稳健逐迹拟合在迹线子采样副本上进行
        # (评分指标为全局均值/条纹比,子采样近似不变),3D 开销降约 10 倍
        base = _decimated(arr, np_axis, max_traces)
        current_score = float(score_fn(base, np_axis))
        # 基线已良好(≥95)时跳过整轴候选,直接保持 off(省去全网格拟合)
        if current_score >= 95.0:
            baseline_cfg[axis] = dict(off_cfg)
            scores[axis] = {"off:0": current_score}
            logs.append(
                f"{axis}: 基线已良好(score={current_score:.1f}≥95),"
                "保持 off(跳过候选)"
            )
            unchanged.append(axis)
            continue
        axis_scores: dict[str, float] = {}
        best: tuple[float, str, int] | None = None
        for mode, order in grid:
            if cancel is not None and cancel():
                raise RuntimeError("任务已取消:基线优化被用户终止")
            if mode == "off":
                work = base  # 不校正直接评分,无需复制
            else:
                work = base.copy()
                work = baseline_proc.apply(
                    work,
                    baseline_proc.BaselineParams(
                        method="polynomial", axis=axis, order=max(order, 1)
                    ),
                )
                # 硬性条纹否决:plain polyfit 与真实脚本 POLY 一致,校正后
                # 若出现明显迹间断层则该候选不可写回(否则终谱出现竖线)
                if _has_stripe_artifact(work, np_axis):
                    axis_scores[f"{mode}:{order}"] = _VETOED_SCORE
                    continue
            value = float(score_fn(work, np_axis))
            axis_scores[f"{mode}:{order}"] = value
            if best is None or value > best[0]:
                best = (value, mode, order)
            if progress is not None:
                progress(
                    f"基线优化中(内存评分): {axis} {mode}:{order} "
                    f"score={value:.1f}"
                )
        if best is None:
            best = (float(current_score), "off", 0)
            axis_scores["off:0"] = float(current_score)
        score, mode, order = best
        if mode == "off":
            new_cfg: dict[str, Any] = {"enabled": False, "mode": "auto", "order": 0}
        elif mode == "auto":
            new_cfg = {"enabled": True, "mode": "auto", "order": 0}
        else:
            new_cfg = {"enabled": True, "mode": "order", "order": order}
        gain = score - current_score
        if gain <= min_gain:
            # 逐迹校正无实质增益 → 保持 off(0.2.132:不再写 POLY -auto,
            # 避免无基线问题时逐迹均值校正引入条纹)
            baseline_cfg[axis] = dict(off_cfg)
            logs.append(
                f"{axis}: 候选未优于当前配置,保持 off "
                f"(score={score:.1f})"
            )
            unchanged.append(axis)
        else:
            baseline_cfg[axis] = new_cfg
            logs.append(
                f"{axis}: 基线已优化 {_fmt_cfg(default_cfg)} → {_fmt_cfg(new_cfg)} "
                f"(score={current_score:.1f} → {score:.1f}, +{gain:.1f})"
            )
            optimized.append(axis)
        scores[axis] = axis_scores
    logs.append(
        "基线优化总结: "
        + ("已优化 " + ",".join(optimized) if optimized else "已优化 无")
        + "; "
        + ("未优化 " + ",".join(unchanged) if unchanged else "未优化 无")
    )
    return BaselineOptimizeResult(
        baseline=baseline_cfg,
        scores=scores,
        spectrum_path=str(spectrum_path),
        logs=logs,
        optimized=optimized,
        skipped=[],
    )


__all__ = ["BaselineOptimizeResult", "optimize_baseline"]
