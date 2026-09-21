r"""Example 2: Step by step execution (Reference -> Peak Table -> plan -> Batch -> Record). Usage::
python docs/external-api/examples/step_by_step.py --study ~/studies/s2 \ --dataset ~/data/apo
--combos combos.csv."""

from __future__ import annotations

import argparse
from pathlib import Path

from nmrforge_api import (
    add_dataset,
    build_reference,
    ensure_reference_peaks,
    load_combo_table,
    open_study,
    plan_sweep,
    run_sweep,
    write_records,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="nmrforge_api Step-by-step example")
    parser.add_argument("--study", required=True, help="Study the root directory")
    parser.add_argument("--dataset", required=True, help="Bruker directory")
    parser.add_argument("--condition", default="A", help="Conditional label (default A)")
    parser.add_argument("--combos", required=True, help="combination table")
    parser.add_argument(
        "--localization",
        choices=("parabolic", "gaussian", "both"),
        default="parabolic",
        help="Combined mode peak position refinement method (both = both peak tables are output)",
    )
    args = parser.parse_args(argv)

    session = open_study(args.study)
    if session.dataset_by_condition(args.condition) is None:
        add_dataset(session, args.dataset, condition=args.condition)

    reference = build_reference(session, progress=print)
    reference = ensure_reference_peaks(session, reference)
    print("Reference script:", reference.script_path)
    print("Reference peak table:", reference.peak_table_parabolic_path,
          "|", reference.peak_table_gaussian_path)
    print("Automatic phase actual value:", reference.phase_record())

    plan = plan_sweep(reference, combos=load_combo_table(args.combos))
    print("workflow_ids:", plan.workflow_ids())
    runs = run_sweep(
        session,
        plan,
        reference=reference,
        localization=args.localization,
        progress=print,
    )
    records = write_records(
        session,
        references={reference.dataset_key: reference},
        plan=plan,
        runs=runs,
    )
    for run in runs:
        table = Path(run.peak_table_path("parabolic"))
        print(f"{run.workflow_id} {run.condition} {run.status} {table}")
    print("records:", records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
