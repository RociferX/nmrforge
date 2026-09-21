r"""Construct NUS dataset from fully sampled Bruker 2D data (user plan: full sampling -> synthesis
NUS). Idea (isomorphic with 3D NUS real data): - directory put nuslist (sampling_detector based
on this judgment NUS); - ser only retains the FID rows of sampling point (States super complex
number: every t1 complex point mult rows); - multi-point grid = acqu2s TD / mult (consistent
with the program 2D convention), NusTD writing line unit; - first verify that the number of file
lines (ser bytes / acqus TD) is consistent with acqu2s TD, if inconsistent, an error will be
reported directly (no semi-finished product is produced); - acqu2s sets NusTD (unit: incremental
line, consistent with the real NUS The data is consistent; nuslist index is the complex point),
acqus assumes NusAMOUNT(<100). Usage (VM): nmrforge/bin/python
scripts/vm_sample_make_nus.py \ <full sampling directory > <output directory > [--points 32]
[--seed 42] Output: The constructed NUS dataset (for vm_sample_regression.py processing)."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.data.bruker_dtype import sample_dtype  # noqa: E402


def _param(text: str, key: str) -> int:
    match = re.search(r"^##\$\s*" + re.escape(key) + r"\s*=\s*(\S+)", text, re.M)
    return int(match.group(1)) if match else 0


def _parse_acqus(text: str) -> dict[str, str]:
    """Get a scalar key value (DTYPE/BYTORDA etc.) from an acqus text."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^##\$\s*([A-Za-z0-9_]+)\s*=\s*(\S+)", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _set_param(text: str, key: str, value: int) -> str:
    if re.search(r"^##\$\s*" + re.escape(key) + r"\s*=", text, re.M):
        text = re.sub(
            r"^##\$\s*" + re.escape(key) + r"\s*=\s*\S+",
            f"##${key}= {value}",
            text,
            count=1,
            flags=re.M,
        )
    else:
        text += f"\n##${key}= {value}\n"
    return text


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--points", type=int, default=32)
    parser.add_argument("--seed", type=int, default=42)
    opts = parser.parse_args(args)

    src = Path(opts.dataset)
    dst = Path(opts.out_dir)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    acqu2s = (dst / "acqu2s").read_text(encoding="utf-8", errors="replace")
    acqus = (dst / "acqus").read_text(encoding="utf-8", errors="replace")
    fnmode = _param(acqu2s, "FnMODE")
    mult = 2 if fnmode in (0, 1, 2, 4, 5, 6) else 1  # States/TPPI Supercomplex component.
    # Direct dimension The number of sample values per line = acqus TD (xN convention of
    # NMRPipe/bruk2pipe); the number of bytes of each sample value is determined by DTYPE/BYTORDA
    # (int32/float64/float32).
    dt = sample_dtype(_parse_acqus(acqus))
    x_n = _param(acqus, "TD")
    if x_n <= 0:
        print(f"acqus TD is invalid: {x_n}")
        return 1
    ser = np.fromfile(src / "ser", dtype=dt)
    ser_points = int(ser.size)  # Number of sample values (int32 is not assumed).
    if ser_points % x_n:
        print(
            f"ser size does not match direct dimension TD: {ser_points} "
            f"sample values are not {x_n} integer multiple"
            f"(element type {dt.str[1:]})"
        )
        return 1
    rows_file = ser_points // x_n  # The actual number of increments (lines) in file.
    # The number of increments (lines) for the full sample source.
    td_rows = _param(acqu2s, "TD")
    # 0.2.199-patch29hz-Repair 11: The grid uses **acqu2s TD** (consistent with the 2D convention of
    # the program: `script_generator.effective_td` takes F1 for 2D TD // Super complex components
    # are not accepted NusTD); Previously, **NusTD** was written as TD//mult (complex point unit),
    # and the program passed it as -yT NusTD//2 nusExpand, less than the maximum index of nuslist ->
    # nusExpand crashes out of bounds (rc=139 / stderr channel error) -> conversion is stuck. The
    # real NUS and NusTD are line units (sampleJ 292↔145, sampleC 40↔19), and the line units are
    # written here.
    if td_rows <= 0 or td_rows % mult:
        print(
            f"acqu2s TD is invalid: {td_rows}(Must be a hypercomplex component {mult} an integer "
            f"multiple of)"
        )
        return 1
    if rows_file != td_rows:
        print(
            f"ser row number({rows_file}=byte/{x_n}) and acqu2s TD({td_rows}) mismatch: "
            f"not canonical fully sampled 2D data"
            "(the metadata and the file disagree); check the acquisition parameters first"
        )
        return 1
    grid = td_rows // mult  # Indirect dimension complex point grid = nuslist index range.
    nus_td = td_rows
    if grid <= 0:
        print(f"Invalid complex point mesh: {grid}")
        return 1
    if opts.points >= grid:
        print(f"number of sampling points {opts.points} Need to be smaller than the grid {grid}")
        return 1

    rng = np.random.default_rng(opts.seed)
    points = sorted(int(v) for v in rng.choice(grid, size=opts.points, replace=False))
    # NusExpand -off takes the first point of nuslist: the real Bruker nuslist first point is 0, and
    # non-0 first point will cause expansion offset (2D NUS verification found -off 19 -> 15N offset
    # ~2.6 ppm).
    if 0 not in points:
        points[0] = 0
    (dst / "nuslist").write_text(
        "\n".join(str(p) for p in points) + "\n", encoding="utf-8"
    )

    fids = ser.reshape(rows_file, x_n)  # (Incremental row, direct dimension int32).
    rows = [mult * p + k for p in points for k in range(mult)]
    fids[np.array(rows, dtype=int)].astype(dt).tofile(dst / "ser")

    acqu2s = _set_param(acqu2s, "NusTD", nus_td)
    acqus = _set_param(acqus, "NusAMOUNT", round(100 * opts.points / grid))
    (dst / "acqu2s").write_text(acqu2s, encoding="utf-8")
    (dst / "acqus").write_text(acqus, encoding="utf-8")

    new_size = (dst / "ser").stat().st_size
    print(f"Construction completed: {dst}")
    print(
        f" complex point grid={grid}(NusTD={nus_td} row) sampling point ={opts.points} "
        f"({100*opts.points/grid:.1f}%)"
    )
    print(f" The first 8 lines of nuslist: {(dst / 'nuslist').read_text().splitlines()[:8]}")
    print(f"  ser: {len(rows)} Increment row x {x_n} {dt.str[1:]} = {new_size} byte")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
