r"""Example 1: Run a parameter combination study in one step (two conditions A/B). Usage:: python
docs/external-api/examples/run_study.py --study ~/studies/s1 \ --a ~/data/apo --b ~/data/holo
--combos combos.csv."""

from __future__ import annotations

import argparse
from pathlib import Path

from nmrforge_api import load_combo_table, run_parameter_study


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="nmrforge_api One-step example")
    parser.add_argument("--study", required=True, help="Study the root directory")
    parser.add_argument("--a", required=True, help="Bruker directory for condition A")
    parser.add_argument("--b", default="", help="Bruker directory for condition B (optional)")
    parser.add_argument("--combos", help="Combination table CSV/TSV/YAML/JSON")
    parser.add_argument("--axes", help="axis grid JSON (optional with --combos)")
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
        parser.error("Must give --combos or --axes")

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
