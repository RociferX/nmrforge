"""NMRForge SMILE 参数优化 CLI（可选工具，不进入自动处理）。

用法（VM）：
    PYTHONPATH=$HOME/NMRForge python scripts/smile_optimize.py <数据集目录> [更多段目录...]
    [--ext-lo 9.0] [--ext-hi 7.5] [--nthread 2] [--out report.json]

输出：参数组 + 评分表格（stdout）+ JSON 报告。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.data.bruker_reader import read_dataset, read_segments
from workflow.smile_optimize import (
    format_results,
    optimize_smile_parameters,
    save_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SMILE 参数优化（可选工具）")
    parser.add_argument("datasets", nargs="+", help="Bruker 数据集目录（多个=多段实验）")
    parser.add_argument("--ext-lo", default="9.0", help="1H 提取窗口低 ppm（默认 9.0）")
    parser.add_argument("--ext-hi", default="7.5", help="1H 提取窗口高 ppm（默认 7.5）")
    parser.add_argument("--nthread", type=int, default=2, help="SMILE 线程数（默认 2）")
    parser.add_argument("--out", default="", help="JSON 报告输出路径（默认数据集同级）")
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.datasets]
    experiment = read_segments(paths) if len(paths) > 1 else read_dataset(paths[0])
    print(
        f"实验: {experiment.experiment_type.name} {experiment.ndim}D "
        f"{experiment.sampling.mode.value}"
    )

    from backend.nmrpipe_backend import NMRPipeBackend

    backend = NMRPipeBackend()
    health = backend.health_check()
    if not health["ok"]:
        print(f"错误: {health['message']}", file=sys.stderr)
        return 1

    results = optimize_smile_parameters(
        experiment,
        backend,
        ext_lo=args.ext_lo,
        ext_hi=args.ext_hi,
        nthread=args.nthread,
    )
    print(format_results(results))

    out = Path(args.out) if args.out else paths[0].parent / "smile_optimize_report.json"
    save_report(results, out)
    print(f"\n报告已保存: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
