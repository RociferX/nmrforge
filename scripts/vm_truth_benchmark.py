"""Ground-truth evidence: on a machine with NMRPipe, compare automatic processing against
**published**
peak positions.

Why this script exists: ``vm_realdata_report.py`` compares "automatic processing vs another
researcher's
manual processing", which is a difference **between two workflows** and cannot show that the result
matches
the truth. The only way to show that is to take the **published (deposited) chemical shifts** of
the same
sample and condition as the expected peak positions, match them one-to-one under a tolerance, and
report
"the fraction that would match if you simply shifted things around" as the background.

Conventions (same source as ``workflow/truth_benchmark.py``; the evidence must report them
together):

* expected positions come from a CSV exported from the deposited shifts (``peak_id,H_ppm,N_ppm``);
  only
  those inside the **1H/15N window the spectrum actually covers** are kept (peaks outside cannot be
  detected at all, so counting them in the denominator would be unfair);
* the deposited reference and the spectrum's own reference differ by a constant -> calibrate once
  with a
  grid scan (coarse + refine) and then **freeze** it; the calibration target carries its own null
  distribution (the distribution of match counts over the whole coarse grid), which must be
  reported too;
* matching: ``d = hypot(dH/tol_H, dN/tol_N) <= 1``, one-to-one greedy nearest first; an expected
  peak that
  was taken away is recorded as ``not_detected`` and an unmatched detection as
  ``unmatched_detection``
  (not a "false peak"); the per-peak detail CSV **carries one block per tolerance tier** (a
  ``level``
  column = that tier's ``tol_H/tol_N``), so the evidence figure can separate "matched at the tight
  tolerance / only at the loose one / at neither" - the middle tier is a **positional offset**, not
  the
  software losing the peak;
* the tolerance ladder is ``(0.01,0.05) / (0.02,0.10) / (0.05,0.50)`` ppm - a loose tolerance
  matches
  anything, so only the tight tiers say whether "the position really lines up";
* the background control uses **per-peak independent** decoys (each expected peak shifted randomly
  by
  >= 5 matching radii, fixed seed): shifting the whole table would be absorbed by re-calibrating the
  reference and the control would lose its meaning.

Usage (real machine; data paths only ever appear on the command line):::

    nmrforge/bin/python scripts/vm_truth_benchmark.py \
        --dataset <Bruker dataset directory> --expected <expected peak CSV> --tag <anonymous label>
        \
        --root <scratch study root> --thresholds 12 [--json <output path>] \
        [--matches <CSV basename>] [--keep]

The output JSON contains **aggregates only**: the input fingerprint (files/bytes/sha256), the
sha256 and
row count of the expected table, the spectrum shape and the window/zero-fill lines of the automatic
processing script, the detection count per threshold, and per tolerance tier the recovery /
unmatched
detections / not-detected / position residuals, plus the decoy background. Dataset paths, file
names,
sample names and host names never enter the output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Tolerance ladder (ppm): tight -> loose. On a crowded spectrum a loose tolerance matches anything,
#: so only the tight tiers are informative.
TOLERANCE_LADDER: tuple[tuple[float, float], ...] = (
    (0.01, 0.05),
    (0.02, 0.10),
    (0.05, 0.50),
)
#: Tolerance used to calibrate the reference (the loosest tier: the constant is global, align
#: loosely first)
CALIBRATION_TOLERANCE = (0.05, 0.50)
#: Decoy rounds and random seed (fixed -> the evidence can be recomputed)
DECOY_ROUNDS = 200
DECOY_SEED = 20260922


def _fingerprint(dataset: Path) -> dict:
    """Input fingerprint: file count + total bytes + a sha256 chain over the files (names only, no
    paths)."""
    digest = hashlib.sha256()
    files = sorted(p for p in dataset.rglob("*") if p.is_file())
    total = 0
    for path in files:
        payload = path.read_bytes()
        total += len(payload)
        digest.update(path.name.encode("utf-8"))
        digest.update(hashlib.sha256(payload).digest())
    return {"files": len(files), "bytes": total, "sha256": digest.hexdigest()}


def _run_processing(dataset: Path, root: Path) -> dict:
    """Run the product's automatic path once (import -> fid -> final spectrum); return it and the
    scripts."""
    from backend.nmrpipe_backend import NMRPipeBackend
    from core.project import ProjectManager
    from workflow.import_workflow import import_data
    from workflow.stepwise import generate_fid, generate_spectrum

    project_dir = root / "project"
    if project_dir.exists():
        shutil.rmtree(project_dir)
    manager = ProjectManager.create_project(project_dir, "truth_benchmark")
    entry = manager.create_experiment(title="truth benchmark")
    started = time.perf_counter()
    imported = import_data(manager, entry.id, dataset)
    import_s = time.perf_counter() - started
    backend = NMRPipeBackend()
    started = time.perf_counter()
    generate_fid(manager, entry.id, imported.data_id, backend)
    fid_s = time.perf_counter() - started
    started = time.perf_counter()
    spectrum = Path(generate_spectrum(manager, entry.id, imported.data_id, backend))
    spectrum_s = time.perf_counter() - started
    manager.save()
    scripts = sorted(project_dir.glob("**/process/*.com"))
    return {
        "spectrum": spectrum,
        "scripts": scripts,
        "import_s": round(import_s, 3),
        "fid_s": round(fid_s, 3),
        "spectrum_s": round(spectrum_s, 3),
    }


def _window_lines(scripts: list[Path]) -> list[str]:
    """Window/zero-fill lines of the automatic processing script (anonymous: parameters only, no
    paths)."""
    lines: list[str] = []
    pattern = re.compile(r"-(fn|zf)\s+(SP|GM|EM|ZF|auto|zf|[-\d.]+)")
    for script in scripts:
        for line in script.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("|") and pattern.search(stripped):
                payload = stripped.lstrip("|").strip()
                payload = re.sub(r"\s*\|\s*$", "", payload)
                lines.append(payload)
    return lines


def _axis_span(axes, nucleus: str, margin_ppm: float = 0.0) -> tuple[float, float] | None:
    """Actual ppm coverage of the spectrum on one nucleus axis (with an optional margin).

    ``ppm_at_fraction`` takes a **fractional index in points**, not a 0-1 ratio - use ``size - 1``
    for
    the last point, otherwise the first point is taken as the whole axis (seen once on the real
    machine).
    """
    index = axes.storage_of(nucleus)
    if index is None:
        return None
    last = float(np.asarray(axes.data).shape[index] - 1)
    low = float(axes.ppm_at_fraction(index, 0.0))
    high = float(axes.ppm_at_fraction(index, last))
    return (min(low, high) - margin_ppm, max(low, high) + margin_ppm)


def _in_span(value: float, span: tuple[float, float] | None) -> bool:
    if span is None:
        return True
    return span[0] <= value <= span[1]


def _quality(data) -> dict:
    """The product's overall spectrum quality score (same function and ``auto`` sign convention as
    the GUI)."""
    import dataclasses

    from core.qc.spectrum_quality import evaluate as evaluate_quality

    result = evaluate_quality(np.asarray(data, dtype=float), sign_mode="auto")
    payload = dataclasses.asdict(result.score)
    payload["decision"] = str(result.decision)
    payload["reasons"] = [str(item) for item in result.reasons][:3]
    return _round(payload)


def _detected_rows(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        if not row.get("detected", True):
            continue
        out.append(
            {
                "peak_id": str(row.get("peak_id")),
                "H_ppm": float(row["H_ppm"]),
                "N_ppm": float(row["N_ppm"]),
                "intensity": float(row.get("intensity") or 0.0),
                "SNR": float(row.get("SNR") or 0.0),
            }
        )
    return out


def _round(value, digits: int = 6):
    """Round scalars and blank out NaN/inf; dicts/lists are handled recursively (calibration
    nests)."""
    if isinstance(value, dict):
        return {key: _round(item, digits) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round(item, digits) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if np.isnan(number) or np.isinf(number):
            return None
        return round(number, digits)
    return value


def _clean_stats(stats: dict) -> dict:
    return {key: _round(value) for key, value in stats.items()}


def evaluate(
    detected: list[dict],
    expected: list[dict],
    *,
    thresholds: list[float],
    dataset_tag: str,
) -> dict:
    """Build one data-set report over the tolerance ladder (calibration plus decoys)."""
    from workflow.truth_benchmark import (
        chance_match_stats,
        detection_stats,
        estimate_offset,
        match_one_to_one,
        shift_peaks,
    )

    calibration = estimate_offset(
        detected,
        expected,
        tol_h=CALIBRATION_TOLERANCE[0],
        tol_n=CALIBRATION_TOLERANCE[1],
    )
    # No prefix: the expected_id in the match detail must be the expected table's peak_id, because
    # the
    # figure and the per-peak review join on it.
    calibrated = shift_peaks(expected, calibration["dH"], calibration["dN"])
    levels = []
    tight_rows: list[dict] = []
    level_rows: list[dict] = []
    for tol_h, tol_n in TOLERANCE_LADDER:
        zero_rows = match_one_to_one(detected, expected, tol_h=tol_h, tol_n=tol_n)
        cal_rows = match_one_to_one(detected, calibrated, tol_h=tol_h, tol_n=tol_n)
        # Tag every tier's per-peak detail with its level and write them into one CSV: the evidence
        # figure must separate tight / loose-only / neither - those last two are not the same thing.
        for row in cal_rows:
            row["level"] = f"{tol_h}/{tol_n}"
        if tol_h == TOLERANCE_LADDER[0][0]:
            # The figure annotates with the **tightest** tier (the headline convention of the page)
            tight_rows = cal_rows
        level_rows.extend(cal_rows)
        chance = chance_match_stats(
            detected,
            calibrated,
            n_decoys=DECOY_ROUNDS,
            seed=DECOY_SEED,
            tol_h=tol_h,
            tol_n=tol_n,
            per_peak=True,
        )
        levels.append(
            {
                "tol_h": tol_h,
                "tol_n": tol_n,
                "zero_shift": _clean_stats(
                    detection_stats(zero_rows, n_detected=len(detected))
                ),
                "calibrated": _clean_stats(
                    detection_stats(cal_rows, n_detected=len(detected))
                ),
                "chance_per_peak_decoy": _clean_stats(chance),
            }
        )
    return {
        "tag": dataset_tag,
        "n_expected_total": len(expected),
        "n_detected": len(detected),
        #: Per-peak match detail (calibrated, tight tier) - used for the CSV
        "calibration": _round(calibration),
        "levels": levels,
        #: Tightest tier's per-peak detail - used for the report JSON
        "matches": tight_rows,
        #: Every tier's per-peak detail (with a ``level`` column); the figure splits tiers from it
        "matches_by_level": level_rows,
    }


def main(argv: list[str] | None = None) -> int:
    from nmrforge_api import detect_and_localize
    from workflow.pick_peaks import read_spectrum_axes
    from workflow.truth_benchmark import load_expected_csv, write_matches_csv

    parser = argparse.ArgumentParser(
        description="nmrForge ground-truth benchmark evidence (real machine)"
    )
    parser.add_argument("--dataset", required=True, help="Bruker dataset directory")
    parser.add_argument("--expected", required=True, help="expected peak CSV (peak_id,H_ppm,N_ppm)")
    parser.add_argument(
        "--tag", required=True, help="anonymous label (the only identifier in the output)"
    )
    parser.add_argument("--root", required=True, help="study root (scratch directory)")
    parser.add_argument(
        "--thresholds", default="35", help="detection thresholds (sigma multiples, comma separated)"
    )
    parser.add_argument("--json", default="", help="output JSON path")
    parser.add_argument(
        "--matches",
        default="",
        help="CSV basename for the per-peak match detail (one file per threshold)",
    )
    parser.add_argument(
        "--keep", action="store_true", help="keep the study root (deleted after the run by default)"
    )
    args = parser.parse_args(argv)

    dataset = Path(args.dataset).resolve()
    root = Path(args.root).resolve()
    if not dataset.is_dir():
        print(f"dataset not found: {dataset}", file=sys.stderr)
        return 2
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    report: dict = {"tag": args.tag, "input": _fingerprint(dataset)}

    expected_path = Path(args.expected).resolve()
    expected_payload = expected_path.read_bytes()
    raw_expected = load_expected_csv(expected_path)
    report["expected_table"] = {
        "sha256": hashlib.sha256(expected_payload).hexdigest(),
        "rows_in_file": len(raw_expected),
    }

    run = _run_processing(dataset, root)
    spectrum = run["spectrum"]
    if not spectrum.is_file():
        print("the final spectrum was not produced", file=sys.stderr)
        return 3
    axes = read_spectrum_axes(spectrum)
    h_span = _axis_span(axes, "1H")
    n_span = _axis_span(axes, "15N")
    expected = [
        row for row in raw_expected
        if _in_span(float(row["H_ppm"]), h_span) and _in_span(float(row["N_ppm"]), n_span)
    ]
    quality = _quality(axes.data)
    report["spectrum"] = {
        "shape": [int(value) for value in np.asarray(axes.data).shape],
        "quality": quality,
        "H_span_ppm": [_round(h_span[0], 4), _round(h_span[1], 4)] if h_span else None,
        "N_span_ppm": [_round(n_span[0], 4), _round(n_span[1], 4)] if n_span else None,
        "processing_lines": _window_lines(run["scripts"]),
        "seconds": {
            "import": run["import_s"], "fid": run["fid_s"], "spectrum": run["spectrum_s"]
        },
    }
    report["expected_table"]["rows_in_spectrum_window"] = len(expected)

    thresholds = [float(item) for item in str(args.thresholds).split(",") if item.strip()]
    report["thresholds"] = []
    for threshold in thresholds:
        rows, details = detect_and_localize(spectrum, sigma_multiplier=threshold)
        detected = _detected_rows(rows)
        block = {
            "sigma_multiplier": threshold,
            "detected_peaks": len(detected),
            "noise_sigma": _round(details.get("noise_sigma")),
            "edge_margin_ppm": details.get("edge_margin_ppm"),
        }
        block["evaluation"] = evaluate(
            detected, expected, thresholds=thresholds, dataset_tag=args.tag
        )
        if args.matches:
            base = Path(args.matches)
            target_csv = base.with_name(f"{base.stem}_sigma{int(threshold)}{base.suffix}")
            write_matches_csv(target_csv, block["evaluation"].pop("matches_by_level", []))
            block["matches_csv"] = str(target_csv.name)
        report["thresholds"].append(block)

    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False)
    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload + "\n", encoding="utf-8", newline="\n")
    print(payload)
    if not args.keep:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
