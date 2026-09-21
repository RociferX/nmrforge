"""VM Real machine smoke: run nmrforge_api with real NMRPipe (reference + parameter combination).
Purpose: Verify the end-to-end link of the external interface on real NMRPipe + real Bruker data
(Reference spectrum/refer to script freeze -> two reference peak tables -> parameter combination
-> candidate spectrum -> two kinds of positioning -> two unified peak tables -> records), and
print the key results into one line ``RESULT_JSON``. Usage (VM, the data path is given by
environment variables or parameters):: nmrforge/bin/python scripts/vm_api_smoke.py \
--data ~/nmr_corpus_work/bmr6980/n15hsqc.fid \\ --peaks
~/nmr_corpus_work/bmr6980/reference_peaks.csv The default parameter combination is
``window.F1.off = [0.35, 0.45] x zero_fill = [1, 2]``(4 workflows). Two conditions (A/B)
example::... scripts/vm_api_smoke.py --fresh \\ --data-a <apo directory > --data-b <holo
directory > \\ --combos combos.csv NUS The same is true for 2D data, just replace the axis with
SMILE parameter::... scripts/vm_api_smoke.py --fresh --data <2D NUS directory > \\ --axes
'{"nsigma": [3, 5, 7], "thresh": [0.95]}'."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from nmrforge_api import (
    PEAK_TABLE_COLUMNS,
    load_combo_table,
    read_peak_table,
    run_parameter_study,
)


def _default(name: str, fallback: str) -> str:
    return os.environ.get(name, fallback)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="nmrforge_api VM Real machine smoking (reference + small batch workflow)"
    )
    parser.add_argument(
        "--study",
        default=_default("NMRFORGE_API_STUDY", "/home/<lab-user>/studies/nmrforge_api_smoke"),
        help="study root directory (reused / resumed)",
    )
    parser.add_argument("--data", default="", help="Bruker raw data for condition A directory")
    parser.add_argument("--data-a", default="", help="Condition A (equivalent to --data)")
    parser.add_argument("--data-b", default="", help=
        "Condition B (optional, both conditions have the same parameter)")
    parser.add_argument(
        "--peaks",
        default="",
        help=
            "Optional: external reference peak table (.list or peak_id, H_ppm, N_ppm CSV); default "
            "= automatic peak picking",
    )
    parser.add_argument(
        "--axes",
        default='{"window.F1.off": [0.35, 0.45], "zero_fill": [1, 2]}',
        help="parameter grid JSON(dot key); choose one of --combos",
    )
    parser.add_argument(
        "--combos",
        default="",
        help="Explicit combination table CSV/TSV/YAML/JSON (optional with --axes)",
    )
    parser.add_argument(
        "--localization",
        choices=("parabolic", "gaussian", "both"),
        default="parabolic",
        help="Combination mode refinement method: parabolic (default) / gaussian (2D only) / both",
    )
    parser.add_argument(
        "--edge-margin-ppm",
        type=float,
        default=None,
        help=(
            "peak picking excludes the physical width of the edge axis peak "
            "(ppm; default = 3 x the axis nuclide line width)"
        ),
    )
    parser.add_argument("--max-runs", type=int, default=8)
    parser.add_argument("--max-peaks", type=int, default=0)
    parser.add_argument("--fresh", action="store_true", help=
        "Delete the research directory first and then run")
    args = parser.parse_args(argv)

    source_a = args.data_a or args.data
    if not source_a:
        parser.error("Must give --data (or --data-a)")
    datasets = {"A": source_a}
    if args.data_b:
        datasets["B"] = args.data_b

    kwargs: dict = {"datasets": datasets, "max_runs": args.max_runs}
    if args.combos:
        kwargs["combos"] = load_combo_table(args.combos)
    else:
        kwargs["axes"] = json.loads(args.axes)

    study = Path(args.study).expanduser()
    if args.fresh and study.exists():
        import shutil

        shutil.rmtree(study)

    def log(message: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    started = time.time()
    result = run_parameter_study(
        study,
        peaks=Path(args.peaks).expanduser() if args.peaks else None,
        max_peaks=args.max_peaks,
        localization=args.localization,
        edge_margin_ppm=args.edge_margin_ppm,
        progress=log,
        **kwargs,
    )
    def _table_report(run: Any, method: str) -> dict:
        """Peak table structure check: column header, row number, whether reference_peak_id is left
        blank."""
        path = run.peak_table_path(method)
        if not path:
            return {"method": method, "present": False}
        rows = read_peak_table(path)
        return {
            "method": method,
            "present": True,
            "path": path,
            "n_rows": len(rows),
            "columns_ok": list(rows[0].keys()) == list(PEAK_TABLE_COLUMNS)
            if rows
            else None,
            "reference_peak_id_all_empty": all(
                str(row.get("reference_peak_id", "")) == "" for row in rows
            ),
            "detected_all_true": all(bool(row.get("detected")) for row in rows),
            "peak_ids": [
                None if row.get("peak_id") != row.get("peak_id") else int(row["peak_id"])
                for row in rows
            ],
            "localization_methods": sorted(
                {str(row.get("localization_method", "")) for row in rows}
            ),
        }

    payload = {
        "elapsed_s": round(time.time() - started, 1),
        "localization_requested": args.localization,
        "references": [
            {
                "condition": reference.condition,
                "dataset": reference.dataset_key,
                "spectrum": reference.frozen_spectrum,
                "script": reference.script_path,
                "script_sha256": reference.script_sha256,
                "phase_route": reference.phase_route,
                "phases": reference.direct_phase,
                "phase_record": reference.phase_record(),
                "sampling": reference.sampling,
                "peak_list": reference.peak_table_path,
                "peak_count": reference.peak_count,
                "peak_source": reference.peak_source,
                "peak_tables": reference.peak_tables,
                "peak_localization": reference.peak_localization,
            }
            for reference in result.references.values()
        ],
        "workflows": [
            {
                "workflow_id": run.workflow_id,
                "condition": run.condition,
                "dataset": run.dataset,
                "parameters_requested": run.parameters_requested,
                "parameters_resolved": run.parameters_resolved,
                "status": run.status,
                "warnings": [warning.get("code") for warning in run.warnings],
                "script_sha256": run.script_sha256,
                "spectrum_sha256": run.spectrum_sha256,
                "peak_tables": run.peak_tables,
                "peak_localization": run.peak_localization,
                "wall_s": run.wall_time_s,
                "window": run.window,
                "detection": (run.parameters_resolved or {}).get("detection"),
                "peak_tables_report": [
                    _table_report(run, method) for method in ("parabolic", "gaussian")
                ],
            }
            for run in result.runs
        ],
        "summary": result.summary,
        "records": result.records,
    }
    print("RESULT_JSON " + json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
