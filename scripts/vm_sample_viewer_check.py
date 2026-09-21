"""Real-engine regression: spectrum axis conversion, software vs manual notation (sampleA)
using viewer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("software", type=Path)
    parser.add_argument("manual", type=Path)
    parser.add_argument("--noext", type=Path)
    opts = parser.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from viewer.spectrum import Spectrum

    def report(name: str, path: Path) -> None:
        spec = Spectrum.load_from_ft2(str(path))
        x = spec.x_axis  # F2
        y = spec.y_axis  # F1
        data = spec.data
        idx = np.unravel_index(int(np.argmax(data)), data.shape)
        print(f"[{name}] {path}")
        print(f"  shape={data.shape} labels=({y.label},{x.label})")
        print(f"  y-axis ppm {y.ppm[0]:.3f}..{y.ppm[-1]:.3f} (size={y.size})")
        print(f"  x-axis ppm {x.ppm[0]:.3f}..{x.ppm[-1]:.3f} (size={x.size})")
        print(f"  main peak = ({y.ppm_at(idx[0]):.2f} {y.label}, "
              f"{x.ppm_at(idx[1]):.2f} {x.label})")
        return spec

    report("software", opts.software)
    report("manual", opts.manual)

    if opts.noext:
        spec = Spectrum.load_from_ft2(str(opts.noext))
        x = spec.x_axis
        y = spec.y_axis
        data = spec.data
        print(f"[noext] axes: {y.label} {y.ppm[0]:.2f}..{y.ppm[-1]:.2f} | "
              f"{x.label} {x.ppm[0]:.2f}..{x.ppm[-1]:.2f}")
        # Water peak 4.7 ppm columns(data shape = [15N rows, 1H columns]).
        i_col = x.index_at(4.7)
        col_energy = float(np.abs(data[:, i_col]).sum())
        col_peak = float(np.abs(data[:, i_col]).max())
        i_row = int(np.argmax(np.abs(data[:, i_col])))
        row_energy = float(np.abs(data[i_row, :]).sum())
        row_peak = float(np.abs(data[i_row, :]).max())
        print(f"  water col idx={i_col} ppm={x.ppm_at(i_col):.3f}, "
              f"row idx={i_row} ppm={y.ppm_at(i_row):.3f}")
        print(f" Column 1H (vertical bar candidate) energy={col_energy:.3e} peak={col_peak:.3e} | "
              f"15N row energy={row_energy:.3e} peak={row_peak:.3e}")
        verdict = "VERTICAL" if col_energy > 5 * row_energy else "not vertical"
        print(f"  verdict: {verdict}")
        strong = np.where(np.abs(data[:, i_col]) > 0.2 * col_peak)[0]
        if strong.size:
            print(f"  15N span rows {strong.min()}..{strong.max()} "
                  f"({strong.size}/{data.shape[0]} rows), "
                  f"ppm {y.ppm[strong.min()]:.1f}..{y.ppm[strong.max()]:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
