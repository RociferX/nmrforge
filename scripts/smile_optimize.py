"""NMRForge SMILE 参数优化 CLI(可选工具,不进入自动处理)。

与 GUI 的 smile 步骤同源(workflow.smile_optimize.scan_smile_parameters):
以终跑脚本为模板只换 SMILE 参数——每组参数跑一次候选谱、取指标后立刻删除,
最后输出「参数组合排序表(CSV/JSON)+ 前三名脚本」;不替换活动谱。

用法(VM):
    PYTHONPATH=$HOME/NMRForge python scripts/smile_optimize.py <数据集目录> \
        [--grid-size 4] [--rank-mode true_peaks|consistency] [--out 输出目录]

0.2.199-补29hz-修24:旧两阶段链删除后直接调用新扫描链(与 GUI 一致);
--grid 仍支持 "nsigma,thresh;nsigma,thresh" 自定义网格。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from pathlib import Path

from core.data.bruker_reader import read_dataset, read_segments
from workflow.smile_optimize import (
    SMILE_GRID_DEFAULT,
    SMILE_GRID_MAX,
    SMILE_GRID_MIN,
    SMILE_HOLDOUT_RATIO,
    scan_smile_parameters,
    smile_grid,
)

# 排序表列(与 workflow.smile_optimize.write_smile_scan_output 同口径)
_RANK_FIELDS = (
    "rank", "index", "nsigma", "thresh", "net_peaks", "stable_count",
    "suspect_count", "peak_count", "mean_snr", "quality", "smile_rms_ratio",
    "holdout_rmse", "holdout_corr", "composite", "ok", "error",
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


def _write_outputs(
    out_dir: Path, rows: list[dict], scripts: dict[int, str]
) -> dict[str, str]:
    """写排序表(CSV+JSON)与前三名脚本(CLI 不接项目树,直接落目录)。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "smile_ranking.csv"
    json_path = out_dir / "smile_ranking.json"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(_RANK_FIELDS))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in _RANK_FIELDS})
    json_path.write_text(
        json.dumps(
            {"rows": rows, "count": len(rows)}, ensure_ascii=False, indent=2
        )
        + chr(10),
        encoding="utf-8",
        newline="",
    )
    paths = {"csv": str(csv_path), "json": str(json_path)}
    for rank, script in sorted(scripts.items()):
        if not script:
            continue
        target = out_dir / f"nus_rank{rank}.com"
        target.write_text(script, encoding="utf-8", newline="")
        paths[f"rank{rank}"] = str(target)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SMILE 参数优化(可选工具)")
    parser.add_argument("datasets", nargs="+", help="Bruker 数据集目录(多个=多段实验)")
    parser.add_argument("--ext-lo", default="9.0", help="1H 提取窗口低 ppm")
    parser.add_argument("--ext-hi", default="7.5", help="1H 提取窗口高 ppm")
    parser.add_argument("--nthread", type=int, default=2, help="SMILE 线程数")
    parser.add_argument("--grid", default="", help="自定义网格(默认按 --grid-size)")
    parser.add_argument(
        "--grid-size", type=int, default=SMILE_GRID_DEFAULT,
        help=f"优化程度 n×n({SMILE_GRID_MIN}..{SMILE_GRID_MAX},默认 {SMILE_GRID_DEFAULT})",
    )
    parser.add_argument(
        "--rank-mode", default="true_peaks",
        choices=("true_peaks", "consistency"),
        help="排序口径:净真峰优先 / 一致性优先(留出残差)",
    )
    parser.add_argument(
        "--holdout-ratio", type=float, default=SMILE_HOLDOUT_RATIO,
        help="一致性口径的留出比例(默认 0.25)",
    )
    parser.add_argument("--scan-dir", default="", help="候选谱临时目录(默认系统临时目录)")
    parser.add_argument("--out", default="", help="排序表/脚本输出目录(默认数据集同级)")
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.datasets]
    experiment = read_segments(paths) if len(paths) > 1 else read_dataset(paths[0])
    print(
        f"实验: {experiment.experiment_type.name} {experiment.ndim}D "
        f"{experiment.sampling.mode.value}"
    )
    if int(getattr(experiment, "ndim", 2) or 2) != 2:
        print(
            "错误: SMILE 优化目前只对 2D NUS 提供(与 GUI 入口一致)",
            file=sys.stderr,
        )
        return 1

    from backend.nmrpipe_backend import NMRPipeBackend

    base = {
        "ext_lo": args.ext_lo,
        "ext_hi": args.ext_hi,
        "nthread": args.nthread,
        "holdout_ratio": args.holdout_ratio,
    }
    grid = _parse_grid(args.grid) if args.grid else smile_grid(args.grid_size)
    print(f"网格: {len(grid)} 组,排序口径 {args.rank_mode}")

    tmp_holder = None
    if args.scan_dir:
        scan_dir = Path(args.scan_dir)
    else:
        tmp_holder = tempfile.TemporaryDirectory(prefix="smile_scan_")
        scan_dir = Path(tmp_holder.name)
    try:
        backend = NMRPipeBackend(work_dir=str(scan_dir))
        health = backend.health_check()
        if not health["ok"]:
            print(f"错误: {health['message']}", file=sys.stderr)
            return 1

        def _progress(index: int, total: int, label: str) -> None:
            print(label, flush=True)

        result = scan_smile_parameters(
            experiment,
            backend,
            base,
            scan_dir=scan_dir,
            grid=grid,
            rank_mode=args.rank_mode,
            progress=_progress,
        )
    except RuntimeError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    finally:
        if tmp_holder is not None:
            tmp_holder.cleanup()

    rows = list(result.get("rows") or [])
    header = (
        f"{'rank':>4} {'nSigma':>6} {'thresh':>6} {'net':>4} "
        f"{'stable':>6} {'suspect':>7} {'peaks':>5} "
        f"{'meanSNR':>7} {'quality':>7}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row.get('rank', 0):>4} {float(row.get('nsigma', 0.0)):>6.1f} "
            f"{float(row.get('thresh', 0.0)):>6.2f} {row.get('net_peaks', 0):>4} "
            f"{row.get('stable_count', 0):>6} {row.get('suspect_count', 0):>7} "
            f"{row.get('peak_count', 0):>5} {float(row.get('mean_snr', 0.0)):>7.2f} "
            f"{float(row.get('quality', 0.0)):>7.1f}"
        )

    out_dir = Path(args.out) if args.out else paths[0].parent / "smile_optimize"
    written = _write_outputs(out_dir, rows, dict(result.get("scripts") or {}))
    print()
    print(f"排序表: {written['csv']}")
    for key in sorted(k for k in written if k.startswith("rank")):
        print(f"{key} 脚本: {written[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
