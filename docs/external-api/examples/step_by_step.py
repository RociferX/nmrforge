"""示例 2:分步执行(参考 → 峰表 → plan → 批量 → 记录)。

用法::

    python docs/external-api/examples/step_by_step.py --study ~/studies/s2 \
        --dataset ~/data/apo --combos combos.csv
"""

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
    parser = argparse.ArgumentParser(description="nmrforge_api 分步示例")
    parser.add_argument("--study", required=True, help="研究根目录")
    parser.add_argument("--dataset", required=True, help="Bruker 目录")
    parser.add_argument("--condition", default="A", help="条件标签(缺省 A)")
    parser.add_argument("--combos", required=True, help="组合表")
    parser.add_argument("--window-ppm", type=float, default=None)
    args = parser.parse_args(argv)

    session = open_study(args.study)
    if session.dataset_by_condition(args.condition) is None:
        add_dataset(session, args.dataset, condition=args.condition)

    reference = build_reference(session, progress=print)
    reference = ensure_reference_peaks(session, reference)
    print("参考脚本:", reference.script_path)
    print("参考峰表:", reference.peak_table_parabolic_path,
          "|", reference.peak_table_gaussian_path)
    print("自动相位实际值:", reference.phase_record())

    plan = plan_sweep(reference, combos=load_combo_table(args.combos))
    print("workflow_ids:", plan.workflow_ids())
    runs = run_sweep(
        session,
        plan,
        reference=reference,
        window_ppm=args.window_ppm,
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
