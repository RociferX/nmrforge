"""NMRForge SMILE 参数优化 CLI（可选工具，不进入自动处理）。

用法（VM）：
    PYTHONPATH=$HOME/NMRForge python scripts/smile_optimize.py <数据集目录> [更多段目录...]
    [--ext-lo 9.0] [--ext-hi 7.5] [--nthread 2] [--grid "5.0,0.95;6.0,0.99"]
    [--out report.json]

0.2.162-补:ext/线程等作为 base_params 传入(只优化 SMILE 参数);新增 --grid
自定义网格;进度输出「正在优化 x/N + 当前参数」。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.data.bruker_reader import read_dataset, read_segments
from workflow.smile_optimize import (
    default_smile_grid,
    format_results,
    optimize_smile_parameters,
    save_report,
)


def _parse_grid(text: str) -> list[dict[str, float]]:
    """解析 "nsigma,thresh;nsigma,thresh" 形式的网格。"""
    grid: list[dict[str, float]] = []
    for pair in text.split(";"):
        pair = pair.strip()
        if not pair:
            continue
        nsigma, thresh = (float(v.strip()) for v in pair.split(","))
        grid.append({"nsigma": nsigma, "thresh": thresh})
    if not grid:
        raise SystemExit("错误: --grid 格式应为 \"nsigma,thresh;nsigma,thresh\"")
    return grid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SMILE 参数优化（可选工具）")
    parser.add_argument("datasets", nargs="+", help="Bruker 数据集目录（多个=多段实验）")
    parser.add_argument("--ext-lo", default="9.0", help="1H 提取窗口低 ppm（默认 9.0）")
    parser.add_argument("--ext-hi", default="7.5", help="1H 提取窗口高 ppm（默认 7.5）")
    parser.add_argument("--nthread", type=int, default=2, help="SMILE 线程数（默认 2）")
    parser.add_argument("--grid", default="", help="自定义网格(默认 5×5=25 组)")
    parser.add_argument("--work-dir", default="", help="工作目录（默认数据集同级 <id>.nmrpipe）")
    parser.add_argument("--out", default="", help="JSON 报告输出路径（默认数据集同级）")
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.datasets]
    experiment = read_segments(paths) if len(paths) > 1 else read_dataset(paths[0])
    print(
        f"实验: {experiment.experiment_type.name} {experiment.ndim}D "
        f"{experiment.sampling.mode.value}"
    )

    from backend.nmrpipe_backend import NMRPipeBackend

    backend = NMRPipeBackend(work_dir=args.work_dir)
    health = backend.health_check()
    if not health["ok"]:
        print(f"错误: {health['message']}", file=sys.stderr)
        return 1

    grid = _parse_grid(args.grid) if args.grid else None
    print(f"网格: {len(grid)} 组 {grid}" if grid else f"网格: 默认 {len(default_smile_grid())} 组")

    def _progress(index: int, total: int, label: str) -> None:
        print(label, flush=True)

    def _on_result(result) -> None:
        print(
            f"  → {result.decision} {result.overall:.1f} "
            f"(nSigma={result.params.get('nsigma')}, "
            f"thresh={result.params.get('thresh')})",
            flush=True,
        )

    results = optimize_smile_parameters(
        experiment,
        backend,
        base_params={
            "ext_lo": args.ext_lo,
            "ext_hi": args.ext_hi,
            "nthread": args.nthread,
        },
        grid=grid,
        progress=_progress,
        on_result=_on_result,
    )
    print(format_results(results))

    out = Path(args.out) if args.out else paths[0].parent / "smile_optimize_report.json"
    save_report(results, out)
    print(f"\n报告已保存: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
