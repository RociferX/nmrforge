"""示例 3:只用测量层(对已有谱两张峰表,不跑处理)。

用途:参考谱/候选谱已经在手,只想按同一批参考峰做 parabolic / 2D gaussian
定位并拿统一峰表(便于先摸清阈值/窗口/ROI 再正式跑研究)。

用法::

    python docs/external-api/examples/measure_only.py --spectrum cand.ft2 \
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
    parser = argparse.ArgumentParser(description="nmrforge_api 只测量示例")
    parser.add_argument("--spectrum", required=True, help="NMRPipe 谱(ft2)")
    parser.add_argument("--peaks", required=True, help="参考峰表(.list/CSV)")
    parser.add_argument("--out", default="tables", help="输出目录")
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
    print("列:", list(sample[0]) if sample else [])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
