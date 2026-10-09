"""Compare signed 2D planes/projections for visual evidence, not 3D ground truth.

Detect each displayed projection independently. Known single-sign spectra use
the product's dominant-sign rule; mixed spectra require an explicit both mode.
The default display-oriented candidate threshold is 5% of its baseline-centered maximum
absolute voxel. An optional sigma floor is explicit, not a hidden extra cutoff.
This is an operational rule, not a calibrated probability or thermal S/N claim.
No offsets are fitted internally. Explicit comparison-axis offsets can reconcile
reference-frame conventions; they translate ppm coordinates, never stored spectra.
The primary metric is reference-candidate coverage, not true-peak recovery.
Unmatched candidates are retained for visual inspection, not classified as errors.
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

from scripts.vm_four_path_figure import (  # noqa: E402
    _available_pairs,
    _common_spectra,
    _load,
    _projection_plane,
    _shift_spectrum,
)


def _detect_plane(path, plane, x_ppm, y_ppm, pair, sigma, fraction, sign_mode="dominant"):
    from core.qc.noise import estimate
    from nmrforge_api.peaks import detect_and_localize
    from workflow.pick_peaks import SpectrumAxes

    names = {"H": "1H", "N": "15N", "C": "13C"}
    axes = SpectrumAxes(dic={}, data=plane, ppm=[y_ppm, x_ppm],
                        nuclei=[names[pair[1]], names[pair[0]]], logical_to_storage=[0, 1])
    noise = estimate(plane)
    centered_max = float(np.max(np.abs(plane - noise.baseline)))
    height_cutoff = max(fraction * centered_max, sigma * noise.global_sigma)
    resolved_sigma = height_cutoff / noise.global_sigma if noise.global_sigma > 0 else 1.0
    rows, meta = detect_and_localize(
        path / "2rr" if path.is_dir() else path, axes=axes,
        sigma_multiplier=resolved_sigma, sign_mode=sign_mode,
    )
    # API intensity is already relative to the global median baseline.
    rows = [row for row in rows if abs(float(row["intensity"])) >= height_cutoff]
    points = np.asarray([[row["F2_ppm"], row["F1_ppm"]] for row in rows], dtype=float)
    signs = np.asarray([1 if row["intensity"] > 0 else -1 for row in rows], dtype=int)
    return rows, points.reshape(-1, 2), signs, {
        "requested_sigma_floor": sigma, "resolved_sigma_multiplier": resolved_sigma,
        "requested_sign_mode": sign_mode, "sign_mode": meta.get("sign_mode", sign_mode),
        "positive_candidates": int((signs > 0).sum()),
        "negative_candidates": int((signs < 0).sum()),
        "detected_before_height_filter": meta["n_peaks"],
        "noise_estimate": float(noise.global_sigma), "baseline": float(noise.baseline),
        "max_abs_centered_voxel": centered_max, "height_cutoff": height_cutoff,
        "count": len(rows), "shape": list(plane.shape),
    }


def _match_indices(left, right, left_signs, right_signs, tolerance):
    candidates = []
    for i, point in enumerate(left):
        delta = point - right
        distances = np.linalg.norm(delta / tolerance, axis=1)
        for j in np.flatnonzero((distances <= 1) & (right_signs == left_signs[i])):
            candidates.append((float(distances[j]), i, int(j)))
    used_left, used_right, pairs = set(), set(), []
    for _distance, i, j in sorted(candidates):
        if i not in used_left and j not in used_right:
            used_left.add(i)
            used_right.add(j)
            pairs.append((i, j))
    return pairs


def _agreement(left_rows, right_rows, indices, pair):
    if not indices:
        return {"intensity_pearson": None, "median_fwhm_ratio_by_nucleus": {n: None for n in pair}}
    heights = np.asarray([[abs(left_rows[i]["intensity"]), abs(right_rows[j]["intensity"])]
                          for i, j in indices], dtype=float)
    correlation = None
    if len(heights) >= 3 and np.all(np.std(heights, axis=0) > 0):
        correlation = float(np.corrcoef(heights.T)[0, 1])
    widths = {}
    for nucleus, logical in zip(pair, (2, 1), strict=True):
        key = f"FWHM_F{logical}"
        ratios = []
        for i, j in indices:
            a, b = left_rows[i].get(key), right_rows[j].get(key)
            if a is not None and b is not None:
                a, b = float(a), float(b)
                if math.isfinite(a) and math.isfinite(b) and a > 0 and b > 0:
                    ratios.append(a / b)
        widths[nucleus] = float(np.median(ratios)) if ratios else None
    return {"intensity_pearson": correlation, "median_fwhm_ratio_by_nucleus": widths}


def _unmatched_candidates(rows, points, signs, matched_indices, pair):
    return [
        {"candidate_index_1based": i + 1,
         "ppm": {n: float(points[i, k]) for k, n in enumerate(pair)},
         "intensity": float(row["intensity"]), "sign": int(signs[i])}
        for i, row in enumerate(rows) if i not in matched_indices
    ]


def compare(spectrum: Path, reference: Path, *, pairs="all", sigma=0.0,
            min_height_fraction=0.05, tolerances=None, reference_offsets=None,
            sign_mode="dominant"):
    if sign_mode not in {"auto", "dominant", "positive", "negative", "both"}:
        raise ValueError("unknown peak sign mode")
    if not math.isfinite(sigma) or sigma < 0:
        raise ValueError("sigma floor must be nonnegative and finite")
    if not math.isfinite(min_height_fraction) or not 0 <= min_height_fraction <= 1:
        raise ValueError("height fraction must be finite and between zero and one")
    if sigma == 0 and min_height_fraction == 0:
        raise ValueError("at least one positive detection threshold is required")
    tolerances = tolerances or {"H": .02, "N": .20, "C": .15}
    if any(not math.isfinite(float(tolerances[n])) or tolerances[n] <= 0 for n in "HNC"):
        raise ValueError("all nuclear tolerances must be positive and finite")
    right = _shift_spectrum(_load(reference), reference_offsets)
    offsets = {n: float(v) for n, v in (reference_offsets or {}).items() if v}
    left, right = _common_spectra(_load(spectrum), right)
    if pairs == "all":
        selected = _available_pairs(left)
    elif pairs in {"HN", "HC", "NC"}:
        selected = [pairs]
    else:
        raise ValueError("pairs must be all, HN, HC or NC")
    result = {
        "kind": "display_projection_comparison_not_independent_truth",
        "projection": "signed_max_absolute_voxel; all-axis common crop before projection",
        "source_ndim": left["data"].ndim, "offset_fitted": False,
        "reference_offset_ppm": offsets, "offset_source": "explicit" if offsets else "none",
        "matching": "same polarity; joint normalized distance <=1; greedy one-to-one",
        "primary_metric": "reference_candidate_coverage; not true-peak recovery",
        "requested_sigma_floor": sigma,
        "requested_sign_mode": sign_mode,
        "min_height_fraction": min_height_fraction, "tolerance_ppm": tolerances,
        "spectrum_sha256": {}, "planes": {},
    }
    for label, path in (("automatic", spectrum), ("comparison", reference)):
        result["spectrum_sha256"][label] = hashlib.sha256(
            (path / "2rr" if path.is_dir() else path).read_bytes()).hexdigest()
    for pair in selected:
        a = _projection_plane(left, pair)
        b = _projection_plane(right, pair)
        lr, lp, ls, lm = _detect_plane(spectrum, *a, pair, sigma, min_height_fraction,
                                     sign_mode=sign_mode)
        rr, rp, rs, rm = _detect_plane(reference, *b, pair, sigma, min_height_fraction,
                                     sign_mode=sign_mode)
        tolerance = np.asarray([tolerances[n] for n in pair])
        indices = _match_indices(lp, rp, ls, rs, tolerance)
        delta = np.asarray([lp[i] - rp[j] for i, j in indices])
        result["planes"][pair] = {
            "automatic": lm, "comparison": rm, "matched": len(indices),
            "automatic_matched_fraction": len(indices) / len(lr) if lr else None,
            "comparison_matched_fraction": len(indices) / len(rr) if rr else None,
            "reference_candidate_coverage": len(indices) / len(rr) if rr else None,
            "automatic_unmatched_candidates": _unmatched_candidates(
                lr, lp, ls, {i for i, _ in indices}, pair),
            "reference_unmatched_candidates": _unmatched_candidates(
                rr, rp, rs, {j for _, j in indices}, pair),
            "median_abs_delta_ppm": {n: float(np.median(np.abs(delta[:, k]))) if len(indices)
                                     else None for k, n in enumerate(pair)},
            "windows_ppm": {n: [max(float(a[k+1].min()), float(b[k+1].min())),
                                min(float(a[k+1].max()), float(b[k+1].max()))]
                            for k, n in enumerate(pair)},
            **_agreement(lr, rr, indices, pair),
        }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spectrum", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--pairs", choices=("all", "HN", "HC", "NC"), default="all")
    parser.add_argument("--sigma", type=float, default=0,
                        help="Optional noise-estimate floor; default 0 uses only display height")
    parser.add_argument("--min-height-fraction", type=float, default=.05)
    parser.add_argument("--sign-mode", default="dominant",
                        choices=("auto", "dominant", "positive", "negative", "both"),
                        help="Known single-sign experiments use dominant; mixed use both")
    parser.add_argument("--tol-h", type=float, default=.02)
    parser.add_argument("--tol-n", type=float, default=.20)
    parser.add_argument("--tol-c", type=float, default=.15)
    for nucleus in "HNC":
        parser.add_argument(
            f"--reference-shift-{nucleus.lower()}", type=float, default=0.,
            help=f"Explicit ppm to add to the comparison {nucleus} axis before cropping",
        )
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = compare(args.spectrum, args.reference, pairs=args.pairs, sigma=args.sigma,
                         min_height_fraction=args.min_height_fraction,
                         tolerances={"H": args.tol_h, "N": args.tol_n, "C": args.tol_c},
                         reference_offsets={n: getattr(args, f"reference_shift_{n.lower()}")
                                            for n in "HNC"}, sign_mode=args.sign_mode)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    text = json.dumps(result, indent=2, sort_keys=True, allow_nan=False)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
