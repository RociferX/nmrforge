"""示例 1:一步式跑一项参数组合研究(两条件 A/B)。

用法::

    python docs/external-api/examples/run_study.py --study ~/studies/s1 \
        --a ~/data/apo --b ~/data/holo --combos combos.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

from nmrforge_api import load_combo_table, run_parameter_study


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="nmrforge_api 一步式示例")
    parser.add_argument("--study", required=True, help="研究根目录")
    parser.add_argument("--a", required=True, help="条件 A 的 Bruker 目录")
    parser.add_argument("--b", default="", help="条件 B 的 Bruker 目录(可选)")
    parser.add_argument("--combos", help="组合表 CSV/TSV/YAML/JSON")
    parser.add_argument("--axes", help="轴网格 JSON(与 --combos 二选一)")
    args = parser.parse_args(argv)

    datasets = {"A": args.a}
    if args.b:
        datasets["B"] = args.b
    kwargs: dict = {"datasets": datasets}
    if args.combos:
        kwargs["combos"] = load_combo_table(args.combos)
    elif args.axes:
        import json

        kwargs["axes"] = json.loads(args.axes)
    else:
        parser.error("必须给 --combos 或 --axes")

    result = run_parameter_study(
        args.study,
        progress=lambda message: print(message, flush=True),
        **kwargs,
    )
    print("workflow_ids:", result.summary["workflow_ids"])
    print("status:", result.summary["status_counts"])
    for run in result.runs:
        print(
            f"{run.workflow_id} {run.condition or run.dataset.get('key', '')} "
            f"{run.status} -> {run.peak_table_path('parabolic')}"
        )
    print("records:", {k: Path(v).name for k, v in result.records.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
