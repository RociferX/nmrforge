"""Compare controlled pseudo-NUS and uniform spectra in all nuclear dimensions.

This is a validation tool, not an API peak-correspondence feature or independent
ground truth. Detect both spectra with the same explicit threshold, restrict the
denominator to the common physical window, and match once without fitting an
offset. Match candidates must share polarity and have joint normalized distance
at most one. Repeated nuclei are rejected rather than collapsed. Report all
axes, including carbon in 3D, and content hashes for reproducibility.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.vm_four_path_figure import _nucleus_family  # noqa: E402


def _points(rows: list[dict], families: list[str], windows: dict):
    result = []
    for row in rows:
        coordinates = {}
        for logical in range(1, 4):
            family = _nucleus_family(row.get(f"F{logical}_nucleus", ""))
            value = row.get(f"F{logical}_ppm")
            if family in families and value is not None:
                if family in coordinates:
                    raise ValueError("repeated nuclei cannot be collapsed into one coordinate")
                coordinates[family] = float(value)
        if set(coordinates) != set(families):
            raise ValueError("detected peak lacks complete logical-axis coordinates")
        if not all(math.isfinite(value) for value in coordinates.values()):
            raise ValueError("non-finite peak coordinate")
        if all(windows[f][0] <= coordinates[f] <= windows[f][1] for f in families):
            height = float(row["intensity"])
            if not math.isfinite(height) or height == 0:
                raise ValueError("detected peak has invalid signed height")
            result.append(([coordinates[f] for f in families], 1 if height > 0 else -1))
    return result


def _match(source, target, tolerance):
    candidates = []
    for i, (point, polarity) in enumerate(source):
        for j, (other, other_polarity) in enumerate(target):
            if polarity != other_polarity:
                continue
            delta = np.asarray(point) - np.asarray(other)
            distance = float(np.linalg.norm(delta / tolerance))
            if distance <= 1:
                candidates.append((distance, i, j, delta))
    used_source, used_target, differences = set(), set(), []
    for _distance, i, j, delta in sorted(candidates, key=lambda item: item[:3]):
        if i not in used_source and j not in used_target:
            used_source.add(i)
            used_target.add(j)
            differences.append(delta)
    return differences


def compare(uniform: Path, nus: Path, sigma: float, tolerances: dict) -> dict:
    from nmrforge_api.peaks import detect_and_localize
    from workflow.pick_peaks import read_spectrum_axes

    if not math.isfinite(sigma) or sigma <= 0:
        raise ValueError("sigma must be positive and finite")
    loaded = [read_spectrum_axes(path) for path in (uniform, nus)]
    nuclei = [[_nucleus_family(label) for label in axes.nuclei] for axes in loaded]
    if any(len(set(names)) != len(names) for names in nuclei) or set(nuclei[0]) != set(nuclei[1]):
        raise ValueError("spectra must have the same unique nuclear axes")
    if any(not np.all(np.isfinite(axes.data)) for axes in loaded):
        raise ValueError("non-finite spectrum cannot be evidence")
    families = sorted(nuclei[0])
    if len(families) not in {2, 3} or not set(families) <= {"H", "N", "C"}:
        raise ValueError("only explicit unique H/N/C 2D/3D axes are supported")
    tolerance = np.asarray([tolerances[f] for f in families], dtype=float)
    if not np.all(np.isfinite(tolerance)) or np.any(tolerance <= 0):
        raise ValueError("all tolerances must be positive and finite")
    windows = {}
    for family in families:
        scales = [axes.ppm[names.index(family)] for axes, names in zip(loaded, nuclei, strict=True)]
        lo = max(float(np.min(scale)) for scale in scales)
        hi = min(float(np.max(scale)) for scale in scales)
        if not hi > lo:
            raise ValueError(f"no common {family} window")
        windows[family] = [lo, hi]
    tables, counts = [], []
    for path, axes in zip((uniform, nus), loaded, strict=True):
        rows, _meta = detect_and_localize(path, axes=axes, sigma_multiplier=sigma, sign_mode="auto")
        tables.append(_points(rows, families, windows))
        counts.append(len(rows))
    differences = _match(*tables, tolerance)
    matched = len(differences)
    return {
        "kind": "same_source_controlled_downsampling_comparison_not_independent_truth",
        "nuclei": families, "ndim": len(families), "sigma_multiplier": sigma,
        "windows_ppm": windows, "tolerance_ppm": tolerances, "offset_fitted": False,
        "polarity_matched": True,
        "matching": "joint normalized distance <= 1; greedy closest first",
        "uniform_detected": counts[0], "nus_detected": counts[1],
        "uniform_in_window": len(tables[0]), "nus_in_window": len(tables[1]), "matched": matched,
        "uniform_matched_fraction": matched / len(tables[0]) if tables[0] else None,
        "nus_matched_fraction": matched / len(tables[1]) if tables[1] else None,
        "median_abs_delta_ppm": {
            family: float(np.median(np.abs(np.asarray(differences)[:, index]))) if matched else None
            for index, family in enumerate(families)
        },
        "spectrum_sha256": {
            label: hashlib.sha256(path.read_bytes()).hexdigest()
            for label, path in (("uniform", uniform), ("nus", nus))
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uniform", required=True, type=Path)
    parser.add_argument("--nus", required=True, type=Path)
    parser.add_argument("--sigma", type=float, default=35)
    parser.add_argument("--tol-h", type=float, default=0.02)
    parser.add_argument("--tol-n", type=float, default=0.20)
    parser.add_argument("--tol-c", type=float, default=0.15)
    parser.add_argument("--json", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = compare(args.uniform, args.nus, args.sigma,
                         {"H": args.tol_h, "N": args.tol_n, "C": args.tol_c})
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    args.json.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, indent=2, sort_keys=True, allow_nan=False)
    args.json.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
