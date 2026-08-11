"""NMRForge 后处理参数优化 CLI（相位 p0/p1 + 基线；只重构一次）。

NUS 与非 NUS 逻辑一致：先跑一次重构/处理得到终谱，再在内存内优化
相位/基线参数（不触发重新重构）。

用法（VM）：
    # 自动跑一次重构/处理，然后优化
    PYTHONPATH=$HOME/NMRForge python scripts/param_optimize.py <数据集目录> [更多段...]

    # 已有终谱文件，跳过重构，直接优化
    PYTHONPATH=$HOME/NMRForge python scripts/param_optimize.py <数据集目录> --spectrum out.ft3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from core.data.bruker_reader import read_dataset, read_segments
from workflow.param_optimize import (
    format_results,
    optimize_post_parameters,
    save_report,
)


def _load_spectrum(path: str):
    import nmrglue as ng

    _dic, data = ng.pipe.read(path)
    return np.asarray(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="后处理参数优化（相位/基线，只重构一次）")
    parser.add_argument("datasets", nargs="+", help="Bruker 数据集目录（多个=多段实验）")
    parser.add_argument("--spectrum", default="", help="已有终谱（ft2/ft3），跳过重构直接优化")
    parser.add_argument("--ext-lo", default="9.0", help="1H 提取窗口低 ppm（默认 9.0）")
    parser.add_argument("--ext-hi", default="7.5", help="1H 提取窗口高 ppm（默认 7.5）")
    parser.add_argument("--nthread", type=int, default=2, help="SMILE 线程数（默认 2）")
    parser.add_argument("--work-dir", default="", help="工作目录（默认数据集同级 <id>.nmrpipe）")
    parser.add_argument("--out", default="", help="JSON 报告输出路径（默认数据集同级）")
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.datasets]
    experiment = read_segments(paths) if len(paths) > 1 else read_dataset(paths[0])

    if args.spectrum:
        print(f"使用已有终谱: {args.spectrum}")
        data = _load_spectrum(args.spectrum)
    else:
        from backend.nmrpipe_backend import NMRPipeBackend

        backend = NMRPipeBackend(work_dir=args.work_dir)
        health = backend.health_check()
        if not health["ok"]:
            print(f"错误: {health['message']}", file=sys.stderr)
            return 1
        print(
            f"实验: {experiment.experiment_type.name} {experiment.ndim}D "
            f"{experiment.sampling.mode.value} —— 先重构/处理一次"
        )
        if experiment.sampling.mode.value == "nus":
            resp = backend.reconstruct_nus(
                experiment,
                {"ext_lo": args.ext_lo, "ext_hi": args.ext_hi, "nthread": args.nthread},
            )
        else:
            from core.planning.method_selector import select_method

            resp = backend.process(experiment, select_method(experiment))
        if not resp.get("success"):
            print(f"错误: {resp.get('message')}", file=sys.stderr)
            return 1
        print(f"终谱: {resp['spectrum_path']}")
        data = _load_spectrum(resp["spectrum_path"])

    print(f"谱形状: {data.shape} —— 内存内优化相位/基线（不重新重构）")

    def _progress(index: int, total: int, label: str) -> None:
        print(f"[{index}/{total}] 运行 {label}", flush=True)

    def _on_result(result) -> None:
        print(
            f"  → {result.decision} {result.overall:.1f} "
            f"(p0={result.params.get('p0')}, p1={result.params.get('p1')}, "
            f"base={result.params.get('baseline_order')})",
            flush=True,
        )

    results = optimize_post_parameters(data, progress=_progress, on_result=_on_result)
    print()
    print(format_results(results))

    out = Path(args.out) if args.out else paths[0].parent / "param_optimize_report.json"
    save_report(results, out)
    print(f"\n报告已保存: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
