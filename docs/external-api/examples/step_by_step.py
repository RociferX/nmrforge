"""分步示例:逐段控制,便于接入自建流程或调试。

顺序:open_study → add_dataset → build_reference → ensure_reference_peaks
      → plan_sweep → run_sweep → position_uncertainty → write_records
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from nmrforge_api import (
    add_dataset,
    build_reference,
    ensure_reference_peaks,
    load_reference,
    open_study,
    plan_sweep,
    position_uncertainty,
    run_sweep,
    uncertainty_summary,
    write_records,
)

STUDY = Path(os.environ.get("NMRFORGE_API_STUDY", "~/studies/hsqc_params")).expanduser()
DATA = os.environ.get("NMRFORGE_API_DATA", "")
AXES = {"window.F1.off": [0.35, 0.45], "zero_fill": [1, 2]}


def log(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    session = open_study(STUDY)
    if DATA:
        dataset = add_dataset(session, DATA)
        print("数据集:", json.dumps(dataset.to_dict(), ensure_ascii=False))

    reference = load_reference(session) or build_reference(session, progress=log)
    reference = ensure_reference_peaks(session, reference)   # 软件自动选峰
    print(f"参考峰表 {reference.peak_count} 峰(来源 {reference.peak_source})")

    plan = plan_sweep(reference, axes=AXES)
    print(f"组合 {plan.n_combos} 个,网格哈希 {plan.grid_sha256[:12]},相位锁定 {plan.phase_locked}")

    runs = run_sweep(session, plan, reference=reference, progress=log)
    uncertainties = position_uncertainty(runs, csp_n_weight=0.2)
    summary = uncertainty_summary(
        uncertainties, csp_n_weight=0.2,
        n_runs=sum(1 for run in runs if run.status == "success"),
    )
    records = write_records(
        session, reference=reference, plan=plan, runs=runs,
        uncertainties=uncertainties, summary=summary,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("记录:", json.dumps(records, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
