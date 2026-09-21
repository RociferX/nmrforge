"""Real-engine regression: water peak 4.7 ppm direction profile analysis (software noext vs
manual)."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spectra", nargs="+", type=Path)
    opts = parser.parse_args(argv)

    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from viewer.spectrum import Spectrum

    for path in opts.spectra:
        spec = Spectrum.load_from_ft2(str(path))
        x = spec.x_axis
        y = spec.y_axis
        data = spec.data
        i4 = x.index_at(4.7)
        i45 = x.index_at(4.5)
        i49 = x.index_at(4.9)
        i7 = x.index_at(7.5)
        col_vals = np.abs(data[:, i4])
        max15n = float(col_vals.max())
        argmax15n = int(col_vals.argmax())
        # 1H direction profile: Look at the water peak column vs adjacent column in the 15N maximum
        # row.
        row = np.abs(data[argmax15n, :])
        nearby = [i45, i49, i7]
        print(f"[{path.name}] axes: {y.label} {y.ppm[0]:.1f}..{y.ppm[-1]:.1f} | "
              f"{x.label} {x.ppm[0]:.2f}..{x.ppm[-1]:.2f}")
        print(f"  water@4.7 col={i4} max15n={max15n:.3e} at row {argmax15n} "
              f"(15N {y.ppm_at(argmax15n):.2f})")
        for i in [i4] + nearby:
            print(f"    1H {x.ppm_at(i):.2f} ppm (idx {i}): row abs={row[i]:.3e}")
        print(f"  water col peak / 1H-7.5ppm row peak = "
              f"{max15n / max(row[i7], 1e-30):.1f}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
