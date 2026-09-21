#!/usr/bin/env python
"""Walkthrough: inspect a Bruker dataset with nmrForge, step by step.

This script deliberately stops before anything that needs NMRPipe, so it runs on a plain
machine with only the Python dependencies installed. It shows what nmrForge understands
about a dataset:

1. read the Bruker parameters (``acqus``/``acqu2s``/``acqu3s``);
2. report the dimensionality and each dimension's nucleus, sweep width and role;
3. report the detected experiment type and the evidence behind it;
4. report the sampling classification (uniform / NUS / uncertain) and its evidence;
5. read the time-domain data and show the storage layout the backend will use;
6. check whether NMRPipe and SMILE are available, and print the next command to run.

Usage::

    python examples/make_synthetic_dataset.py --out example_data/hsqc_2d
    python examples/quickstart.py example_data/hsqc_2d
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # allow running from a clone without `pip install -e .`
    sys.path.insert(0, str(ROOT))


def _rule(title: str) -> None:
    print()
    print(f"=== {title} " + "=" * max(0, 66 - len(title)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset", type=Path, help="Bruker dataset directory (contains acqus)")
    parser.add_argument(
        "--no-data",
        action="store_true",
        help="skip reading the time-domain data (headers only)",
    )
    args = parser.parse_args(argv)

    dataset = args.dataset
    if not (dataset / "acqus").is_file():
        print(
            f"error: {dataset} is not a Bruker dataset directory (no acqus file)",
            file=sys.stderr,
        )
        return 2

    from core.data.bruker_reader import BrukerDataError, read_data, read_dataset

    _rule("1. Bruker parameters")
    experiment = read_dataset(dataset)
    print(f"dataset id        : {experiment.dataset_id}")
    print(f"data directory    : {experiment.source_path}")
    print(f"dimensionality    : {experiment.ndim}D")
    print(f"parameter files   : {', '.join(experiment.acquisition_order)}")

    _rule("2. Dimensions")
    print(f"{'axis':<6}{'nucleus':<10}{'role':<10}{'TD':>8}{'SW (Hz)':>12}{'centre (ppm)':>14}")
    for dim in experiment.dimensions:
        print(
            f"{dim.logical_axis:<6}{dim.nucleus:<10}{dim.role.value:<10}"
            f"{dim.td:>8}{dim.sw:>12.2f}{dim.o1p:>14.3f}"
        )

    _rule("3. Experiment type")
    etype = experiment.experiment_type
    print(f"classified as     : {etype.name}  (confidence {etype.confidence:.2f})")
    for evidence in etype.evidence:
        print(f"  - {evidence}")

    _rule("4. Sampling classification")
    sampling = experiment.sampling
    print(f"mode              : {sampling.mode.value}  (confidence {sampling.confidence:.2f})")
    print(f"sampling fraction : {sampling.sampling_fraction:.3f}")
    if sampling.schedule_type:
        print(f"schedule type     : {sampling.schedule_type}")
    for evidence in sampling.evidence:
        print(f"  - {evidence}")

    _rule("5. Time-domain data")
    if args.no_data:
        print("skipped (--no-data)")
    else:
        try:
            data = read_data(experiment)
        except BrukerDataError as exc:
            print(f"could not read the time-domain data: {exc}")
        else:
            print(f"data file         : {data.data_file}")
            print(f"byte order        : {data.byte_order}-endian")
            print(f"matrix shape      : {data.matrix.shape}")
            print(f"storage layout    : {data.layout_summary}")

    _rule("6. Processing engines")
    from backend.nmrpipe_finder import find_nmrpipe_bin, find_tool

    nmrpipe_bin = find_nmrpipe_bin()
    if nmrpipe_bin is None:
        print("NMRPipe           : NOT FOUND (required for conversion, FT, phase and baseline)")
        print()
        print("Install NMRPipe and make sure its `bin` directory is on PATH, or set")
        print(
            "`backend.nmrpipe.path` in nmrforge_data/config/nmrforge.yaml. Nothing was processed."
        )
    else:
        print(f"NMRPipe           : {nmrpipe_bin}")
        for tool in ("smile", "bruker"):
            found = find_tool(tool, nmrpipe_bin)
            print(f"{tool:<18}: {found if found else 'not found'}")

    _rule("Next steps")
    print("With an NMRPipe installation available, the full processing path is:")
    print(f"  python -m nmrforge_api init      --study ./study --dataset {dataset}")
    print("  python -m nmrforge_api reference --study ./study")
    print("  python -m nmrforge_api peaks     --study ./study")
    print("Or start the GUI:")
    print("  python main.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
