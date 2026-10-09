"""Convert a BMRB HSQC expected peak list to the truth benchmark CSV format (peak_id,H_ppm,N_ppm).

The truth benchmark does not fetch data; its expected peak list must be supplied as a CSV.
BMRB provides backbone amide HSQC peak lists derived from deposited chemical shifts, with
sequence, residue, shift, and atom-name columns. This converter maps those columns to the
benchmark format and records the source and input fingerprint for review.

Usage:

    # Download the first-party BMRB peak list (public, no login required).
    curl -o expected_27493_bmrb.csv \
      "https://api.bmrb.io/current/entry/27493/simulate_hsqc?format=csv&filter=backbone"

    # Convert it to the truth benchmark format.
    python scripts/bmrb_expected_to_csv.py \
        --input expected_27493_bmrb.csv \
        --entry 27493 \
        --output expected_27493.csv

    # Run the truth benchmark.
    nmrforge/bin/python scripts/vm_truth_benchmark.py \
        --dataset <Bruker directory> --expected expected_27493.csv --tag "<anonymous label>" ...

The output CSV includes a UTF-8 BOM, matching the utf-8-sig reader used by load_expected_csv.
IDs such as 27493_5_VAL map matching details back to the deposited entry for review.

Out of scope: This script does not decide which peaks should appear in the spectrum. The truth
benchmark filters against actual spectrum coverage, so peaks outside the window do not count
against the result. This script only maps columns and records provenance.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

#: BMRB simulate_hsqc column names; aliases allow for minor differences between entries.
H_COLUMNS = ("X_shift", "H_shift", "H", "H_ppm")
N_COLUMNS = ("Y_shift", "N_shift", "N", "N_ppm")
#: Accept only these backbone amide atom pairs; other atom names are not backbone HSQC peaks.
BACKBONE_ATOMS = {("H", "N")}


def _pick(row: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert a BMRB HSQC peak list into the truth-benchmark CSV format"
    )
    parser.add_argument("--input", type=Path, required=True, help="BMRB 下载的 CSV")
    parser.add_argument("--output", type=Path, required=True, help="输出 CSV")
    parser.add_argument("--entry", default="", help="BMRB 条目号(进 peak_id 与来源记录)")
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="可选的来源记录 JSON(输入 sha256、条目号、峰数、跳过的行数)",
    )
    parser.add_argument(
        "--keep-non-backbone",
        action="store_true",
        help="也保留非主链酰胺原子对(默认只留 H/N)",
    )
    args = parser.parse_args(argv)

    paths = [args.input.resolve(), args.output.resolve()]
    if args.report is not None:
        paths.append(args.report.resolve())
    if len(set(paths)) != len(paths):
        print("input, output, and report paths must be distinct")
        return 1

    if not args.input.is_file():
        print(f"input not found: {args.input}")
        return 1

    payload = args.input.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    entry = args.entry or "BMRB"

    rows: list[dict[str, str]] = []
    skipped = 0
    seen: set[str] = set()
    # BMRB downloads may include a BOM; utf-8-sig prevents a malformed first column name.
    with args.input.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            print("input has no header row")
            return 1
        for index, row in enumerate(reader):
            if not row:
                continue
            h_value = _pick(row, H_COLUMNS)
            n_value = _pick(row, N_COLUMNS)
            if not h_value or not n_value:
                skipped += 1
                continue
            x_atom = str(row.get("X_atom_name") or "H").strip().upper()
            y_atom = str(row.get("Y_atom_name") or "N").strip().upper()
            if not args.keep_non_backbone and (x_atom, y_atom) not in BACKBONE_ATOMS:
                skipped += 1
                continue
            try:
                h_ppm = float(h_value)
                n_ppm = float(n_value)
            except ValueError:
                skipped += 1
                continue
            if not math.isfinite(h_ppm) or not math.isfinite(n_ppm):
                skipped += 1
                continue
            residue = str(row.get("sequence") or (index + 1)).strip()
            residue_name = str(row.get("chem_comp_ID") or "").strip()
            peak_id = f"{entry}_{residue}_{residue_name}".rstrip("_")
            # peak_id must be unique to avoid collisions in match details, plots, and review.
            base_peak_id = peak_id
            suffix = 1
            while peak_id in seen:
                suffix += 1
                peak_id = f"{base_peak_id}#{suffix}"
            seen.add(peak_id)
            rows.append({"peak_id": peak_id, "H_ppm": f"{h_ppm:.4f}", "N_ppm": f"{n_ppm:.4f}"})

    if not rows:
        print(
            "no backbone amide peaks found — check that the file is BMRB's HSQC peak list "
            f"(columns present: {reader.fieldnames})"
        )
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    # The BOM matches load_expected_csv's utf-8-sig reader and existing expected peak lists.
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["peak_id", "H_ppm", "N_ppm"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {args.output} ({len(rows)} backbone amide peaks, {skipped} rows skipped)")
    if args.report:
        report = {
            "source": "BMRB entry HSQC peak list (derived from deposited chemical shifts)",
            "entry": entry,
            "input_sha256": digest,
            "peaks": len(rows),
            "skipped": skipped,
            "columns": list(reader.fieldnames or []),
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
