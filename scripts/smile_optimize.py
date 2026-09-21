r"""NMRForge SMILE parameter optimisation CLI (optional tool, does not enter automatic processing).
It has the same origin as the smile step of GUI (workflow.smile_optimize.scan_smile_parameters):
use the final script as the template and only replace the SMILE parameter -- each set of
parameters is run once for the candidate spectrum, and is deleted immediately after taking the
indicator. Finally, the "parameter combination sorting list (CSV/JSON) + the top three scripts"
is output; the active spectrum is not replaced. Usage (VM): PYTHONPATH=$HOME/NMRForge python
scripts/smile_optimize.py <dataset directory > \ [--grid-size 4] [--rank-mode
true_peaks|consistency] [--out output directory ] 0.2.199-patch29hz - Fix 24: The new scan chain
is called directly after the old two-stage chain is deleted (consistent with GUI); --grid is
still supported "nsigma,thresh;nsigma,thresh" custom grid."""

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

# Sort table column (same caliber as workflow.smile_optimize.write_smile_scan_output).
_RANK_FIELDS = (
    "rank", "index", "nsigma", "thresh", "net_peaks", "stable_count",
    "suspect_count", "peak_count", "mean_snr", "quality", "smile_rms_ratio",
    "holdout_rmse", "holdout_corr", "composite", "ok", "error",
)


def _parse_grid(text: str) -> list[dict[str, float]]:
    """Parse meshes of the form "nsigma,thresh;nsigma,thresh"."""
    grid: list[dict[str, float]] = []
    for pair in text.split(";"):
        pair = pair.strip()
        if not pair:
            continue
        nsigma, thresh = (float(v.strip()) for v in pair.split(","))
        grid.append({"nsigma": nsigma, "thresh": thresh})
    if not grid:
        raise SystemExit("Error: --grid format expected \"nsigma, thresh; nsigma, thresh\"")
    return grid


def _write_outputs(
    out_dir: Path, rows: list[dict], scripts: dict[int, str]
) -> dict[str, str]:
    """Write the sorting table (CSV+JSON) and the top three scripts (CLI does not connect to the
    project tree, but directly falls into directory)."""
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
    parser = argparse.ArgumentParser(description="SMILE parameter optimisation (optional tool)")
    parser.add_argument("datasets", nargs="+", help=(
        "Bruker dataset directory (multiple=multiple experiments)"
    ))
    parser.add_argument("--ext-lo", default="9.0", help="1H extraction window low ppm")
    parser.add_argument("--ext-hi", default="7.5", help="1H extraction window height ppm")
    parser.add_argument("--nthread", type=int, default=2, help="SMILE Number of threads")
    parser.add_argument("--grid", default="", help="Custom grid (default by --grid-size)")
    parser.add_argument(
        "--grid-size", type=int, default=SMILE_GRID_DEFAULT,
        help=(
            f"optimisation degree n x n({SMILE_GRID_MIN}..{SMILE_GRID_MAX}, default "
            f"{SMILE_GRID_DEFAULT})"
        ),
    )
    parser.add_argument(
        "--rank-mode", default="true_peaks",
        choices=("true_peaks", "consistency"),
        help="Sorting caliber: net true peak priority / consistency priority (leave residuals)",
    )
    parser.add_argument(
        "--holdout-ratio", type=float, default=SMILE_HOLDOUT_RATIO,
        help="The proportion reserved for consistency caliber (default 0.25)",
    )
    parser.add_argument("--scan-dir", default="", help=(
        "candidate spectrum temporary directory (default system temporary directory)"
    ))
    parser.add_argument("--out", default="", help=(
        "Sort table/script output directory (default dataset sibling)"
    ))
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.datasets]
    experiment = read_segments(paths) if len(paths) > 1 else read_dataset(paths[0])
    print(
        f"experiment: {experiment.experiment_type.name} {experiment.ndim}D "
        f"{experiment.sampling.mode.value}"
    )
    if int(getattr(experiment, "ndim", 2) or 2) != 2:
        print(
            (
                "Error: SMILE optimisation is currently only available for 2D NUS (consistent with "
                "the GUI entry)"
            ),
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
    print(f"Grid: {len(grid)} group, sorting caliber {args.rank_mode}")

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
            print(f"Error: {health['message']}", file=sys.stderr)
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
        print(f"Error: {exc}", file=sys.stderr)
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
    print(f"Sorting table: {written['csv']}")
    for key in sorted(k for k in written if k.startswith("rank")):
        print(f"{key} script: {written[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
