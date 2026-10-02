r"""Example 3: measure reference peaks on an existing spectrum.

This does not run conversion, reconstruction, or other processing. Peak localization uses the
supported three-point parabolic method.

Usage::

    python docs/external-api/examples/measure_only.py --spectrum candidate.ft2 \
        --peaks reference.list --out ./tables
"""

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
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = read_reference_peaks(args.peaks)
    measurements = measure_peak_positions(
        args.spectrum, rows, window_ppm=args.window_ppm, refine="parabolic"
    )
    table_rows = peak_table_rows(
        measurements,
        workflow_id="reference",
        condition="A",
        dataset=Path(args.spectrum).stem,
        method="parabolic",
    )
    table = write_peak_table(out / "peak_table_parabolic.csv", table_rows)
    detected = sum(1 for row in table_rows if row["detected"])
    print(f"{detected}/{len(table_rows)} detected -> {table}")

    sample = read_peak_table(table)[:1]
    print("List:", list(sample[0]) if sample else [])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
