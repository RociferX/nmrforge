r"""Example 3: Only use the measurement layer (do not process the two peak tables of the existing
spectrum). Purpose: The reference spectrum/candidate spectrum is already in hand, but I just
want to do parabolic/2D gaussian positioning based on the same batch of reference peaks and get
the unified peak table (It is convenient to find out the threshold first/window/ROI and then
officially run the study). Usage:: python docs/external-api/examples/measure_only.py --spectrum
cand.ft2 \ --peaks reference.list --out./tables."""

from __future__ import annotations

import argparse
from pathlib import Path

from nmrforge_api import (
    measure_peak_positions,
    peak_table_rows,
    read_peak_table,
    read_reference_peaks,
    write_peak_table,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="nmrforge_api Measure examples only")
    parser.add_argument("--spectrum", required=True, help="NMRPipe spectrum(ft2)")
    parser.add_argument("--peaks", required=True, help="Reference peak table (.list/CSV)")
    parser.add_argument("--out", default="tables", help="output directory")
    parser.add_argument("--window-ppm", type=float, default=None)
    parser.add_argument("--roi-f1-ppm", type=float, default=None)
    parser.add_argument("--roi-f2-ppm", type=float, default=None)
    args = parser.parse_args(argv)

    out = Path(args.out)
    rows = read_reference_peaks(args.peaks)
    tables: dict[str, Path] = {}
    for method in ("parabolic", "gaussian"):
        measurements = measure_peak_positions(
            args.spectrum,
            rows,
            window_ppm=args.window_ppm,
            refine=method,
            roi_f1_ppm=args.roi_f1_ppm,
            roi_f2_ppm=args.roi_f2_ppm,
        )
        table_rows = peak_table_rows(
            measurements,
            workflow_id="reference",
            condition="A",
            dataset=Path(args.spectrum).stem,
            method=method,
        )
        tables[method] = write_peak_table(
            out / f"peak_table_{method}.csv", table_rows
        )
        detected = sum(1 for row in table_rows if row["detected"])
        print(f"{method}: {detected}/{len(table_rows)} detected -> {tables[method]}")

    sample = read_peak_table(tables["parabolic"])[:1]
    print("List:", list(sample[0]) if sample else [])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
