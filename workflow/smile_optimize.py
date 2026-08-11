"""SMILE 参数优化（可选，用户后选优化项；**不进入自动处理流程**）。

对 nSigma × thresh（及可选 xQ3/scaling）参数网格逐组执行 SMILE 重构并评分，
返回按总分排序的候选列表，把每组的「参数组合 + 评分」展示给用户。

用法：
    results = optimize_smile_parameters(experiment, backend)
    print(format_results(results))
    save_report(results, Path("smile_optimize_report.json"))

注意：AutoProcessor.run 与 NMRPipeBackend.reconstruct_nus 的默认单次运行
不受本模块影响；本模块仅供用户显式调用的参数选优。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.data.internal_data_model import Experiment
from core.qc import spectrum_quality


@dataclass
class SmileParameterResult:
    """一个参数组（SMILE 参数组合）的重构与评分结果。"""

    params: dict[str, Any]
    decision: str = ""
    overall: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    spectrum_path: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_smile_grid(
    nsigma_values: tuple[float, ...] = (3.0, 5.0, 7.0),
    thresh_values: tuple[float, ...] = (0.90, 0.95, 0.99),
    smile_xq3: float = 2.0,
    smile_scaling: bool = True,
) -> list[dict[str, Any]]:
    """默认参数网格：nSigma × thresh（xQ3/scaling 固定为实验室模板值）。"""
    grid: list[dict[str, Any]] = []
    for nsigma in nsigma_values:
        for thresh in thresh_values:
            grid.append(
                {
                    "nsigma": nsigma,
                    "thresh": thresh,
                    "smile_xq3": smile_xq3,
                    "smile_scaling": smile_scaling,
                }
            )
    return grid


def _default_reader(path: str) -> tuple[dict[str, Any], Any]:
    """默认谱图读取器（nmrglue NMRPipe 格式）。"""
    import nmrglue as ng

    return ng.pipe.read(path)


def optimize_smile_parameters(
    experiment: Experiment,
    backend: Any,
    grid: list[dict[str, Any]] | None = None,
    *,
    ext_lo: str = "9.0",
    ext_hi: str = "7.5",
    nthread: int = 2,
    timeout_s: float = 600.0,
    progress: Callable[[int, int, str], None] | None = None,
    reader: Callable[[str], tuple[dict[str, Any], Any]] | None = None,
) -> list[SmileParameterResult]:
    """逐组执行 SMILE 重构并评分，返回按总分降序的候选列表。

    backend 需提供 ``reconstruct_nus(experiment, params) -> dict``
    （success/spectrum_path/message）；评分用 core.qc.spectrum_quality。
    """
    grid = grid if grid is not None else default_smile_grid()
    reader = reader or _default_reader
    results: list[SmileParameterResult] = []
    total = len(grid)
    for index, params in enumerate(grid, start=1):
        if progress is not None:
            progress(index, total, f"参数组 {params}")
        run_params: dict[str, Any] = {
            "ext_lo": ext_lo,
            "ext_hi": ext_hi,
            "nthread": nthread,
            "timeout_s": timeout_s,
            **params,
        }
        result = SmileParameterResult(params=dict(params))
        try:
            resp = backend.reconstruct_nus(experiment, run_params)
            if not resp.get("success"):
                result.decision = "failed"
                result.message = str(resp.get("message", "重构失败"))
            else:
                spec = Path(resp["spectrum_path"])
                result.spectrum_path = str(spec)
                _dic, data = reader(str(spec))
                quality = spectrum_quality.evaluate(data)
                result.decision = quality.decision.value
                result.overall = quality.score.overall
                result.components = asdict(quality.score.components)
        except Exception as exc:  # noqa: BLE001
            result.decision = "error"
            result.message = str(exc)
        results.append(result)
    results.sort(key=lambda r: r.overall, reverse=True)
    return results


def format_results(results: list[SmileParameterResult]) -> str:
    """把候选列表渲染为对齐的参数组合 + 评分表格。"""
    header = (
        f"{'nSigma':>6} {'thresh':>6} {'xQ3':>4} {'scaling':>7} "
        f"{'decision':>8} {'overall':>7} {'snr':>5} {'phase':>5} "
        f"{'base':>5} {'art':>5}"
    )
    lines = [header, "-" * len(header)]
    for result in results:
        comp = result.components
        lines.append(
            f"{result.params.get('nsigma', 0):>6} {result.params.get('thresh', 0):>6} "
            f"{result.params.get('smile_xq3', 1):>4} "
            f"{str(result.params.get('smile_scaling', False)):>7} "
            f"{result.decision:>8} {result.overall:>7.1f} "
            f"{comp.get('snr', 0):>5.0f} {comp.get('phase', 0):>5.0f} "
            f"{comp.get('baseline', 0):>5.0f} {comp.get('artifact', 0):>5.0f}"
        )
    return "\n".join(lines)


def save_report(results: list[SmileParameterResult], path: Path | str) -> Path:
    """保存参数组 + 评分报告（JSON）。"""
    out = Path(path)
    out.write_text(
        json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out
