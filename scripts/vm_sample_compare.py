r"""Real-engine regression check: compare the main peaks of the software spectrum and the manual
spectrum/water peak direction. Usage (VM, after vm_sample_ext step regression):
nmrforge/bin/python scripts/vm_sample_compare.py \ <software spectrum ft2> <manual
spectrum ft2> [--software-noext <ft2>] Output: - two spectra shape/FDTRANSPOSED/Axis range; -
projection + peak positioning: software spectrum main peak, Main peak of the manual spectrum
(ppm), and (optional) axis of the uncropped software spectrum water peak 4.7 ppm (F2=1H vertical
line / F1=15N horizontal line judgment)."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def _load(path: Path):
    import nmrglue as ng

    dic, data = ng.pipe.read(str(path))
    sw1 = float(dic["FDF1SW"])
    sw2 = float(dic["FDF2SW"])
    orig1 = float(dic["FDF1ORIG"])
    orig2 = float(dic["FDF2ORIG"])
    label1 = str(dic.get("FDF1LABEL", "F1"))
    label2 = str(dic.get("FDF2LABEL", "F2"))
    n1, n2 = data.shape
    ppm1 = orig1 - np.arange(n1) * (sw1 / n1)
    ppm2 = orig2 - np.arange(n2) * (sw2 / n2)
    return {
        "dic": dic,
        "data": data,
        "ppm1": ppm1,
        "ppm2": ppm2,
        "label1": label1,
        "label2": label2,
    }


def _find_peak(spec: dict) -> tuple[float, float]:
    idx = np.unravel_index(int(np.argmax(spec["data"])), spec["data"].shape)
    return float(spec["ppm1"][idx[0]]), float(spec["ppm2"][idx[1]])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("software", type=Path)
    parser.add_argument("manual", type=Path)
    parser.add_argument("--software-noext", type=Path, default=None)
    opts = parser.parse_args(argv)

    sw = _load(opts.software)
    manual = _load(opts.manual)
    print(f"software {opts.software}:")
    print(f"  shape={sw['data'].shape} FDTRANSPOSED={sw['dic'].get('FDTRANSPOSED')} "
          f"labels=({sw['label1']},{sw['label2']})")
    print(f"  axes: {sw['label1']} {sw['ppm1'][0]:.2f}..{sw['ppm1'][-1]:.2f} ppm | "
          f"{sw['label2']} {sw['ppm2'][0]:.2f}..{sw['ppm2'][-1]:.2f} ppm")
    p1, p2 = _find_peak(sw)
    print(f"  main peak = ({p1:.2f} {sw['label1']}, {p2:.2f} {sw['label2']})")

    print(f"manual {opts.manual}:")
    print(f"  shape={manual['data'].shape} FDTRANSPOSED={manual['dic'].get('FDTRANSPOSED')} "
          f"labels=({manual['label1']},{manual['label2']})")
    print(f"  axes: {manual['label1']} {manual['ppm1'][0]:.2f}..{manual['ppm1'][-1]:.2f} ppm | "
          f"{manual['label2']} {manual['ppm2'][0]:.2f}..{manual['ppm2'][-1]:.2f} ppm")
    m1, m2 = _find_peak(manual)
    print(f"  main peak = ({m1:.2f} {manual['label1']}, {m2:.2f} {manual['label2']})")

    if opts.software_noext:
        noext = _load(opts.software_noext)
        print(f"software-noext {opts.software_noext}:")
        print(f"  shape={noext['data'].shape} FDTRANSPOSED={noext['dic'].get('FDTRANSPOSED')} "
              f"labels=({noext['label1']},{noext['label2']})")
        print(f"  axes: {noext['label1']} {noext['ppm1'][0]:.2f}..{noext['ppm1'][-1]:.2f} ppm | "
              f"{noext['label2']} {noext['ppm2'][0]:.2f}..{noext['ppm2'][-1]:.2f} ppm")
        # Water peak 4.7 ppm: Project on the 1H axis to find the maximum value; vertical line =
        # water peak energy is concentrated in a single 1H ppm column (stretched along the 15N
        # direction), horizontal line = concentrated in a single 15N row.
        h_axis = 0 if noext["label1"] == "1H" else 1
        n_axis = 1 - h_axis
        data = noext["data"]
        col_prof = data.sum(axis=n_axis)
        row_prof = data.sum(axis=h_axis)
        peak_col = int(np.argmax(col_prof))
        peak_row = int(np.argmax(row_prof))
        ppm_col = (noext["ppm2"] if h_axis == 1 else noext["ppm1"])[peak_col]
        ppm_row = (noext["ppm1"] if h_axis == 1 else noext["ppm2"])[peak_row]
        print(f"  water-probe: 1H axis={noext['label1'] if h_axis==0 else noext['label2']}"
              f"  peak col ppm={ppm_col:.2f} (sum over 15N),"
              f" peak row ppm={ppm_row:.2f} (sum over 1H)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
