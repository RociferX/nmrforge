#!/usr/bin/env python
"""Benchmark framework for nmrForge.

This ships the **framework**, not results. Running it writes machine-readable rows into
``benchmark_results.csv`` (one row per measurement), including the software version, Python
version, platform and git commit that produced them, so a number can never be quoted without its
context.

Three groups of measurements are defined, following the project's own review criteria:

Runtime
    data-understanding throughput, peak-localisation throughput, and - when an NMRPipe
    installation is present - conversion, processing, peak-picking and batch throughput.
Reliability
    experiment-type and sampling classification against the repository's labelled fixtures,
    reported as a success rate.
Measurement quality
    peak-position error for a synthetic Gaussian peak placed at a known, deliberately off-grid
    position, reported in points and in ppm.

Measurements that need an external engine are **skipped, not estimated**: without NMRPipe the rows
are written with ``status=skipped_no_engine``. No benchmark numbers are hardcoded anywhere in this
repository, and no documentation may quote performance figures that this script has not produced.

Usage::

    python benchmarks/run_benchmarks.py
    python benchmarks/run_benchmarks.py --out results/benchmark_results.csv --repeats 5
    python benchmarks/run_benchmarks.py --dataset example_data/hsqc_2d   # adds FID read throughput
"""

from __future__ import annotations

import argparse
import csv
import platform
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures" / "bruker"

# 期望值来自 fixture 目录名(tests/fixtures/bruker/<name>),只做自一致性核对:
# 数据集名里的 "nus" 表示非均匀采样,其余为均匀采样。unknown_2d 不参与判定。
EXPECTED_EXPERIMENT = {
    "hsqc_2d": "HSQC",
    "hsqc_small": "HSQC",
    "nus_2d": "HSQC",
    "hnca_3d": "HNCA",
    "hnca_small": "HNCA",
    "nus_3d": "HNCA",
}
EXPECTED_SAMPLING = {
    "hsqc_2d": "uniform",
    "hsqc_small": "uniform",
    "hnca_3d": "uniform",
    "hnca_small": "uniform",
    "nus_2d": "nus",
    "nus_3d": "nus",
}

COLUMNS = [
    "benchmark",
    "dataset",
    "metric",
    "value",
    "unit",
    "status",
    "notes",
    "software_version",
    "python_version",
    "platform",
    "git_commit",
    "timestamp_utc",
]


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001 - provenance is best effort, never fatal
        return ""


def _row(benchmark: str, metric: str, value, unit: str, **kwargs) -> dict:
    row = {column: "" for column in COLUMNS}
    row.update(
        {
            "benchmark": benchmark,
            "metric": metric,
            "value": value,
            "unit": unit,
            "status": kwargs.pop("status", "ok"),
            "dataset": kwargs.pop("dataset", ""),
            "notes": kwargs.pop("notes", ""),
        }
    )
    row.update(kwargs)
    return row


def _synthetic_gaussian_2d(shape=(64, 128), center=(20.37, 45.62), sigma=(1.6, 1.3)):
    """合成 2D 高斯峰:中心故意落在格点之间,便于测量亚格点定位误差。"""
    i1 = np.arange(shape[0], dtype=float)[:, None]
    i2 = np.arange(shape[1], dtype=float)[None, :]
    data = np.exp(
        -((i1 - center[0]) ** 2) / (2 * sigma[0] ** 2)
        - ((i2 - center[1]) ** 2) / (2 * sigma[1] ** 2)
    )
    return data, center, sigma


def bench_data_understanding(repeats: int) -> list[dict]:
    from core.data.bruker_reader import read_dataset

    datasets = sorted(p for p in FIXTURES.iterdir() if p.is_dir())
    per_dataset: list[float] = []
    for _ in range(repeats):
        for path in datasets:
            start = time.perf_counter()
            read_dataset(path)
            per_dataset.append((time.perf_counter() - start) * 1000.0)
    rows = [
        _row(
            "runtime",
            "data_understanding_mean",
            round(statistics.fmean(per_dataset), 3),
            "ms/dataset",
            dataset=f"{len(datasets)} fixture datasets",
            notes=f"repeats={repeats}; parse+classify, no engine",
        ),
        _row(
            "runtime",
            "data_understanding_p95",
            round(sorted(per_dataset)[int(0.95 * (len(per_dataset) - 1))], 3),
            "ms/dataset",
            dataset=f"{len(datasets)} fixture datasets",
            notes=f"repeats={repeats}",
        ),
    ]
    return rows


def bench_classification() -> list[dict]:
    from core.data.bruker_reader import read_dataset

    exp_hits = exp_total = 0
    smp_hits = smp_total = 0
    for name, expected in EXPECTED_EXPERIMENT.items():
        path = FIXTURES / name
        if not path.is_dir():
            continue
        experiment = read_dataset(path)
        exp_total += 1
        exp_hits += int(experiment.experiment_type.name == expected)
        if name in EXPECTED_SAMPLING:
            smp_total += 1
            smp_hits += int(experiment.sampling.mode.value == EXPECTED_SAMPLING[name])

    return [
        _row(
            "reliability",
            "experiment_classification_rate",
            round(exp_hits / exp_total, 4) if exp_total else "",
            "fraction",
            dataset=f"{exp_total} labelled fixtures",
            notes="expected values taken from fixture directory names",
        ),
        _row(
            "reliability",
            "sampling_classification_rate",
            round(smp_hits / smp_total, 4) if smp_total else "",
            "fraction",
            dataset=f"{smp_total} labelled fixtures",
            status="ok" if smp_total else "skipped_no_fixture",
            notes="expected values taken from fixture directory names",
        ),
    ]


def bench_localization(repeats: int) -> list[dict]:
    from core.peaks.localize import localize_peak

    data, center, _sigma = _synthetic_gaussian_2d()
    index = tuple(int(round(c)) for c in center)
    index = tuple(min(max(i, 1), n - 2) for i, n in zip(index, data.shape))

    f1_ppm = np.linspace(130.0, 110.0, data.shape[0])
    f2_ppm = np.linspace(12.0, 2.0, data.shape[1])
    ppm_per_point = (abs(f1_ppm[1] - f1_ppm[0]), abs(f2_ppm[1] - f2_ppm[0]))

    rows: list[dict] = []

    start = time.perf_counter()
    parabolic = None
    for _ in range(repeats):
        parabolic = localize_peak(data, index, method="parabolic")
    parabolic_ms = (time.perf_counter() - start) * 1000.0 / max(1, repeats)

    gaussian = None
    start = time.perf_counter()
    for _ in range(repeats):
        gaussian = localize_peak(
            data,
            index,
            method="gaussian",
            ppm_axes=[f1_ppm, f2_ppm],
            roi_f1_ppm=1.4,
            roi_f2_ppm=0.5,
        )
    gaussian_ms = (time.perf_counter() - start) * 1000.0 / max(1, repeats)

    for label, result, elapsed in (
        ("parabolic", parabolic, parabolic_ms),
        ("gaussian", gaussian, gaussian_ms),
    ):
        if result is None:
            continue
        errors_points = [
            abs(float(actual) - float(reference))
            for actual, reference in zip(result.position, center)
        ]
        errors_ppm = [err * step for err, step in zip(errors_points, ppm_per_point)]
        rows.append(
            _row(
                "runtime",
                f"peak_localization_{label}",
                round(elapsed, 3),
                "ms/peak",
                dataset="synthetic 2D Gaussian",
                notes=f"repeats={repeats}; requested={label}; actual={result.actual_method}",
            )
        )
        rows.append(
            _row(
                "measurement_quality",
                f"peak_position_error_{label}_max",
                round(max(errors_points), 5),
                "points",
                dataset="synthetic 2D Gaussian at off-grid position",
                notes=(
                    f"true center={tuple(round(c, 3) for c in center)}; "
                    f"fit_success={bool(result.success)}; fallback={bool(result.fallback)}"
                ),
            )
        )
        rows.append(
            _row(
                "measurement_quality",
                f"peak_position_error_{label}_max",
                round(max(errors_ppm), 6),
                "ppm",
                dataset="synthetic 2D Gaussian at off-grid position",
                notes=f"point spacing F1={ppm_per_point[0]:.4f} ppm, F2={ppm_per_point[1]:.4f} ppm",
            )
        )
    return rows


def bench_fid_read(dataset: Path, repeats: int) -> list[dict]:
    from core.data.bruker_reader import read_data, read_dataset

    experiment = read_dataset(dataset)
    durations: list[float] = []
    points = 0
    for _ in range(repeats):
        start = time.perf_counter()
        payload = read_data(experiment)
        durations.append(time.perf_counter() - start)
        points = int(payload.matrix.size)
    mean = statistics.fmean(durations)
    return [
        _row(
            "runtime",
            "fid_read",
            round(mean * 1000.0, 3),
            "ms",
            dataset=str(dataset),
            notes=f"repeats={repeats}; {points} complex points; NMRPipe not involved",
        ),
        _row(
            "runtime",
            "fid_read_throughput",
            round(points / mean / 1e6, 3) if mean > 0 else "",
            "Mpoints/s",
            dataset=str(dataset),
            notes=f"repeats={repeats}",
        ),
    ]


def bench_engine() -> list[dict]:
    """Engine-dependent throughput. Reported as skipped when there is no engine."""
    from backend.nmrpipe_finder import find_nmrpipe_bin, find_tool

    nmrpipe_bin = find_nmrpipe_bin()
    if nmrpipe_bin is None:
        return [
            _row(
                "runtime",
                metric,
                "",
                unit,
                dataset="requires NMRPipe",
                status="skipped_no_engine",
                notes="NMRPipe not found; nothing was estimated and no number was invented",
            )
            for metric, unit in (
                ("conversion", "s/dataset"),
                ("processing", "s/dataset"),
                ("peak_picking", "s/spectrum"),
                ("batch_throughput", "datasets/min"),
            )
        ]
    smile = find_tool("smile", nmrpipe_bin)
    return [
        _row(
            "runtime",
            metric,
            "",
            unit,
            dataset="requires NMRPipe",
            status="skipped_engine_available_not_measured",
            notes=(
                "NMRPipe was found, but this script does not run a real dataset: point it at a "
                "study with --dataset/--study to measure engine timings. Reporting an estimate "
                "here would be worse than reporting nothing."
            ),
        )
        for metric, unit in (
            ("conversion", "s/dataset"),
            ("processing", "s/dataset"),
            ("peak_picking", "s/spectrum"),
            ("batch_throughput", "datasets/min"),
        )
    ] + [
        _row(
            "reliability",
            "engine_detected",
            1,
            "bool",
            dataset=str(nmrpipe_bin),
            notes=f"SMILE: {smile if smile else 'not found'}",
        )
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "benchmark_results.csv")
    parser.add_argument("--repeats", type=int, default=3, help="repetitions per timing measurement")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="optional Bruker dataset directory; adds FID read throughput",
    )
    args = parser.parse_args(argv)

    import core

    rows: list[dict] = []
    rows += bench_data_understanding(args.repeats)
    rows += bench_classification()
    rows += bench_localization(args.repeats)
    if args.dataset is not None:
        rows += bench_fid_read(args.dataset, args.repeats)
    rows += bench_engine()

    meta = {
        "software_version": core.__version__,
        "python_version": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "git_commit": _git_commit(),
        "timestamp_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    for row in rows:
        row.update(meta)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    measured = sum(1 for row in rows if row["status"] == "ok")
    print(f"wrote {len(rows)} rows ({measured} measured) to {args.out}")
    for row in rows:
        flag = "" if row["status"] == "ok" else f" [{row['status']}]"
        print(
            f"  {row['benchmark']:<18} {row['metric']:<38} "
            f"{str(row['value']):<10} {row['unit']}{flag}"
        )
    print()
    print("These numbers describe one machine at one commit. Quote them with that context, and")
    print("never as a general performance claim in README/docs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
