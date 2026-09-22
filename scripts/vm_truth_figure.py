"""Ground-truth evidence figure: the automatically processed 2D HSQC with the published deposited
chemical shifts overlaid, plus residual and recovery panels.

The data come from the same run as ``scripts/vm_truth_benchmark.py`` (same study root, same report
JSON
and match CSV), so every point in the figure maps back to the aggregate numbers.

Usage (real machine):::

    nmrforge/bin/python scripts/vm_truth_figure.py \
        --case "<label1>,<spectrum.ft2>,<expected.csv>,<matches.csv>,<report.json>" \
        --case "<label2>,<spectrum.ft2>,<expected.csv>,<matches.csv>,<report.json>" \
        --out docs/evidence/truth_recovery_2026-09-22.png

Four panels: (1) the first spectrum with the expected peaks overlaid (**red filled circles =
matched at
the tight tolerance**, **amber filled circles = matched only at the loose tier**, **grey filled
circles =
matched only at the coarse tier**, black open squares = matched at no tier); (2) the same for the
second
spectrum; (3) the calibrated position residual scatter with the tight-tolerance box (open circles =
loose
tier only, crosses = coarse tier only, both outside that box); (4) recovery per tolerance tier
(with the
chance background under the same convention). The tiers exist to make the point that some peaks are
merely
shifted within a tolerance instead of being lost by the software; the looser the tier the higher
its chance
background (the coarse 0.05/0.50 ppm tier is already at 35%), so only the tight and loose tiers are
criteria and the coarse tier is a classification aid.

All labels inside the figure are English (the rule for the other figure scripts here too); they
contain
neither sample names nor file paths.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_spectrum(path: Path) -> dict:
    from workflow.pick_peaks import read_spectrum_axes

    axes = read_spectrum_axes(path)
    data = np.asarray(axes.data, dtype=float)
    h_axis = axes.storage_of("1H")
    n_axis = axes.storage_of("15N")
    if data.ndim != 2 or h_axis is None or n_axis is None:
        raise SystemExit(f"only 2D 1H-15N spectra are supported: {path}")
    return {
        "data": data,
        "ppm_h": np.asarray(axes.ppm[h_axis], dtype=float),
        "ppm_n": np.asarray(axes.ppm[n_axis], dtype=float),
        "h_span": (
            float(min(axes.ppm[h_axis][0], axes.ppm[h_axis][-1])),
            float(max(axes.ppm[h_axis][0], axes.ppm[h_axis][-1])),
        ),
        "n_span": (
            float(min(axes.ppm[n_axis][0], axes.ppm[n_axis][-1])),
            float(max(axes.ppm[n_axis][0], axes.ppm[n_axis][-1])),
        ),
    }


def _read_expected(path: Path, spec: dict) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if not row:
                continue
            h_value = float(row["H_ppm"])
            n_value = float(row.get("N_ppm") or 0.0)
            if not (spec["h_span"][0] <= h_value <= spec["h_span"][1]):
                continue
            if not (spec["n_span"][0] <= n_value <= spec["n_span"][1]):
                continue
            rows.append(
                {"peak_id": str(row.get("peak_id") or ""), "H_ppm": h_value, "N_ppm": n_value}
            )
    return rows


def _read_matches(path: Path) -> tuple[list[str], dict[str, dict[str, dict]]]:
    """Read the match detail: return (tier order, {expected peak: {tier: row}}).

    The detail CSV **carries one block per tolerance tier** (a ``level`` column); the evidence
    figure uses
    it to separate tight / loose-only / neither - the middle tier is a **positional offset**, not
    the
    software losing the peak.
    """
    levels: list[str] = []
    by_peak: dict[str, dict[str, dict]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            peak_id = str(row["expected_id"])
            level = str(row.get("level") or "")
            if level not in levels:
                levels.append(level)
            by_peak.setdefault(peak_id, {})[level] = row
    return levels, by_peak


def _matched(by_peak: dict, peak_id: str, level: str) -> bool:
    """Whether that expected peak matched in the given tier (an empty tier means there is no such
    tier)."""
    if not level:
        return False
    return (by_peak.get(peak_id, {}).get(level) or {}).get("status") == "matched"


def _load_pdata(path: Path) -> dict:
    """Read the processed spectrum **shipped with the data** (Bruker ``pdata/1``, supported by
    nmrglue).

    The axis order comes from nmrglue's unit conversion and is then confirmed against the
    **deposited
    peaks** (pick the transpose that puts them on strong signal): if that self-check fails, error
    out
    rather than emit a figure with mismatched axes.
    """
    import nmrglue as ng

    dic, data = ng.bruker.read_pdata(str(path))
    udic = ng.bruker.guess_udic(dic, data)
    # uc_from_udic returns different shapes across nmrglue versions (a list per dimension in some, a
    # single object in others); take it dimension by dimension and fall back to list indexing -
    # support
    # both so the script does not break on another machine.
    axes = []
    for index in range(np.ndim(data)):
        try:
            converter = ng.fileiobase.uc_from_udic(udic, dim=index)
        except TypeError:
            converter = ng.fileiobase.uc_from_udic(udic)[index]
        axes.append(np.asarray(converter.ppm_scale(), dtype=float))
    return {"data": np.asarray(data, dtype=float).real, "ppm": axes}


def _orient_pdata(spec: dict, expected: list[dict], offset) -> dict:
    """Pick which axis is 1H / 15N from the deposited peaks: the arrangement with higher median
    intensity."""
    best = None
    for per_axis in (spec["ppm"], list(reversed(spec["ppm"]))):
        scores = []
        for row in expected:
            h_value = row["H_ppm"] + float(offset[0])
            n_value = row["N_ppm"] + float(offset[1])
            i_h = int(np.argmin(np.abs(per_axis[0] - h_value)))
            i_n = int(np.argmin(np.abs(per_axis[1] - n_value)))
            data = spec["data"]
            scores.append(float(data[i_n, i_h]))
        median = float(np.median(scores)) if scores else 0.0
        if best is None or median > best[0]:
            best = (median, per_axis)
    h_axis, n_axis = (np.asarray(item, dtype=float) for item in best[1])
    return {
        "data": spec["data"],
        "ppm_h": h_axis,
        "ppm_n": n_axis,
        "h_span": (float(np.min(h_axis)), float(np.max(h_axis))),
        "n_span": (float(np.min(n_axis)), float(np.max(n_axis))),
        "score": best[0],
    }


def _crop(spec: dict, h_lo: float, h_hi: float, n_lo: float, n_hi: float):
    h = spec["ppm_h"]
    n = spec["ppm_n"]
    h_idx = np.where((h >= h_lo) & (h <= h_hi))[0]
    n_idx = np.where((n >= n_lo) & (n <= n_hi))[0]
    block = spec["data"][np.ix_(n_idx, h_idx)]
    return block, h[h_idx], n[n_idx]


def _draw_spectrum(
    ax,
    spec: dict,
    expected: list[dict],
    matches: dict,
    offset,
    title: str,
    shift_n: float = 0.0,
    show_peaks: bool = True,
) -> None:
    block, ppm_h, ppm_n = _crop(spec, 6.5, 10.5, 92.0, 144.0)
    peak = float(np.max(block))
    levels = [peak * frac for frac in (0.04, 0.08, 0.16, 0.32, 0.64)]
    # shift_n is a display shift for the side-by-side comparison only: spectrum and peak markers
    # move
    # together, so the panel stays self-consistent.
    ax.contour(ppm_h, ppm_n + shift_n, block, levels=levels, colors="#2b6cb0", linewidths=0.6)
    if show_peaks:
        levels, by_peak = matches
        tight_level = levels[0] if levels else ""
        loose_level = levels[1] if len(levels) > 1 else ""
        coarse_level = levels[2] if len(levels) > 2 else ""
        d_h, d_n = float(offset[0]), float(offset[1]) + shift_n
        tight: list[tuple[float, float]] = []
        loose: list[tuple[float, float]] = []
        coarse: list[tuple[float, float]] = []
        missing: list[tuple[float, float]] = []
        for row in expected:
            point = (row["H_ppm"] + d_h, row["N_ppm"] + d_n)
            if _matched(by_peak, row["peak_id"], tight_level):
                tight.append(point)
            elif _matched(by_peak, row["peak_id"], loose_level):
                loose.append(point)
            elif _matched(by_peak, row["peak_id"], coarse_level):
                coarse.append(point)
            else:
                missing.append(point)
        if tight:
            ax.plot([p[0] for p in tight], [p[1] for p in tight], "o", ms=4.2,
                    mfc="#e53e3e", mec="white", mew=0.4, label="deposited peak, tight match")
        if loose:
            ax.plot([p[0] for p in loose], [p[1] for p in loose], "o", ms=4.6,
                    mfc="#dd6b20", mec="white", mew=0.4, label="deposited peak, loose match only")
        if coarse:
            ax.plot([p[0] for p in coarse], [p[1] for p in coarse], "o", ms=4.6,
                    mfc="#718096", mec="white", mew=0.4,
                    label="deposited peak, coarse match only")
        if missing:
            ax.plot([p[0] for p in missing], [p[1] for p in missing], "s", ms=5.0,
                    mfc="none", mec="#1a202c", mew=0.9, label="deposited peak, not detected")
    ax.set_xlim(10.5, 6.5)
    ax.set_ylim(144.0, 92.0)
    ax.set_xlabel("1H (ppm)")
    ax.set_ylabel("15N (ppm)")
    ax.set_title(title, fontsize=10)
    if show_peaks:
        ax.legend(loc="lower right", fontsize=7, framealpha=0.85)


def _ascending(axis, values) -> tuple[np.ndarray, np.ndarray]:
    """Sort a ppm axis and its projection ascending and drop duplicates (``np.interp`` needs
    monotonic)."""
    axis = np.asarray(axis, dtype=float)
    values = np.asarray(values, dtype=float)
    order = np.argsort(axis)
    axis = axis[order]
    values = values[order]
    keep = np.concatenate(([True], np.diff(axis) > 0))
    return axis[keep], values[keep]


def _n_projection(spec: dict) -> tuple[np.ndarray, np.ndarray]:
    """The 15N projection over the same window (1H 6.5-10.5 / 15N 92-144) and its ppm axis."""
    block, _, n_axis = _crop(spec, 6.5, 10.5, 92.0, 144.0)
    return np.asarray(block, dtype=float).sum(axis=1), np.asarray(n_axis, dtype=float)


def _projection_offset(
    mine: dict, theirs: dict, *, span: float = 1.6, step: float = 0.005
) -> tuple[float, float, float]:
    """Shift measured by 1D 15N projection cross-correlation: (shift, correlation at zero, best).

    The shift is the amount **to add to the automatic spectrum's 15N axis** (ppm). The figure's y
    axis has
    92 at the top and 144 at the bottom, so a positive shift moves the signal down in the figure. A
    shift
    of 0 means the two spectra already coincide.
    """
    proj_a, axis_a = _n_projection(mine)
    proj_b, axis_b = _n_projection(theirs)
    # Mind the argument order: axis first, values second - passing the projection as the axis gives
    # noise.
    axis_a, proj_a = _ascending(axis_a, proj_a)
    axis_b, proj_b = _ascending(axis_b, proj_b)
    # The comparison grid takes the **coarser** of the two spectra: finer than the native grid only
    # turns interpolation artefacts into differences, lowers the correlation and pushes the optimum
    # to
    # the search boundary (measured on the real machine).
    native = max(
        float(np.mean(np.abs(np.diff(axis_a)))),
        float(np.mean(np.abs(np.diff(axis_b)))),
        0.02,
    )
    grid = np.arange(92.0, 144.0, native)
    b = np.interp(grid, axis_b, proj_b)
    b = b - float(np.mean(b))

    def correlation(delta: float) -> float:
        shifted = np.interp(grid - delta, axis_a, proj_a)
        shifted = shifted - float(np.mean(shifted))
        denom = float(np.linalg.norm(shifted) * np.linalg.norm(b))
        return float(np.dot(shifted, b) / denom) if denom > 0 else -np.inf

    deltas = np.arange(-span, span + step / 2, step)
    scores = np.asarray([correlation(float(item)) for item in deltas], dtype=float)
    best = int(np.argmax(scores))
    return float(deltas[best]), correlation(0.0), float(scores[best])


def _centroid_offset(mine: dict, theirs: dict) -> float:
    """Difference of the 15N projection centroids (automatic - shipped, ppm).

    A coarse "does one look higher overall" measure: it does not chase a single strong peak, but it
    is
    **not the reference difference** - the two spectra have different peak-intensity distributions,
    so the
    centroid gap can be two hundred times the real shift (measured on real data).
    """
    proj_a, axis_a = _n_projection(mine)
    proj_b, axis_b = _n_projection(theirs)
    weight_a = np.clip(proj_a, 0.0, None)
    weight_b = np.clip(proj_b, 0.0, None)
    centroid_a = float(np.sum(axis_a * weight_a) / np.sum(weight_a))
    centroid_b = float(np.sum(axis_b * weight_b) / np.sum(weight_b))
    return centroid_a - centroid_b


def _trim_number(value: float, _position=None) -> str:
    """Tick label without trailing zeros: ``0.0100`` -> ``0.01``, ``0.0000`` -> ``0``."""
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def main(argv: list[str] | None = None) -> int:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    parser = argparse.ArgumentParser(description="ground-truth evidence figure (real machine)")
    parser.add_argument(
        "--case",
        action="append",
        required=True,
        help="label, spectrum path, expected peak CSV, match detail CSV, report JSON",
    )
    parser.add_argument("--out", required=True, help="output PNG for the truth figure")
    parser.add_argument(
        "--compare-out",
        default="",
        help="optional: direct comparison figure vs the spectrum shipped with the data",
    )
    parser.add_argument(
        "--align",
        choices=("none", "projection", "envelope", "envelope-theirs"),
        default="none",
        help="15N display shift in the comparison figure: none = no shift (default; the measured "
        "value is only logged); projection = shift the automatic spectrum by the 15N projection "
        "cross-correlation; envelope = shift it by the 15N projection centroid difference; "
        "envelope-theirs = the same centroid difference, applied to the shipped-spectrum column",
    )
    parser.add_argument(
        "--shift-n",
        type=float,
        default=0.0,
        help="extra 15N display shift for the automatic spectrum (ppm, >0 = down)",
    )
    parser.add_argument(
        "--shift-n-theirs",
        type=float,
        default=0.0,
        help="15N display shift for the shipped-spectrum column (ppm, >0 = down)",
    )
    parser.add_argument(
        "--pdata",
        action="append",
        default=[],
        help="pdata/1 directory, same order as --case (the spectrum shipped with the data)",
    )
    args = parser.parse_args(argv)

    cases = []
    for raw in args.case:
        parts = [item.strip() for item in raw.split(",")]
        if len(parts) != 5:
            raise SystemExit("--case needs 5 parts: label,spectrum,expected,matches,report")
        label, spectrum, expected, matches, report = parts
        spec = _load_spectrum(Path(spectrum))
        report_json = json.loads(Path(report).read_text(encoding="utf-8"))
        offset = (report_json["thresholds"][0]["evaluation"]["calibration"]["dH"],
                  report_json["thresholds"][0]["evaluation"]["calibration"]["dN"])
        cases.append(
            {
                "label": label,
                "spec": spec,
                "expected": _read_expected(Path(expected), spec),
                "matches": _read_matches(Path(matches)),
                "report": report_json,
                "offset": offset,
            }
        )

    fig, axes = plt.subplots(2, 2, figsize=(10.5, 9.0))
    for ax, case in zip(axes[0], cases):
        _draw_spectrum(ax, case["spec"], case["expected"], case["matches"], case["offset"],
                       f"{case['label']} - automatic processing, peaks vs published shifts")
    # With a single case, switch off the leftover top-row axes instead of leaving empty panels.
    for ax in list(axes[0])[len(cases):]:
        ax.axis("off")

    ax = axes[1][0]
    for case, colour in zip(cases, ("#e53e3e", "#2b6cb0")):
        levels, by_peak = case["matches"]
        tight_level = levels[0] if levels else ""
        loose_level = levels[1] if len(levels) > 1 else ""
        coarse_level = levels[2] if len(levels) > 2 else ""
        dh, dn, lh, ln, ch, cn = [], [], [], [], [], []
        for row in case["expected"]:
            peak_id = str(row["peak_id"])
            hit = by_peak.get(peak_id, {}).get(tight_level) or {}
            if hit.get("status") == "matched" and hit.get("dH") not in (None, ""):
                dh.append(float(hit["dH"]))
                dn.append(float(hit["dN"]))
                continue
            loose_hit = by_peak.get(peak_id, {}).get(loose_level) or {}
            if loose_hit.get("status") == "matched" and loose_hit.get("dH") not in (None, ""):
                lh.append(float(loose_hit["dH"]))
                ln.append(float(loose_hit["dN"]))
                continue
            coarse_hit = by_peak.get(peak_id, {}).get(coarse_level) or {}
            if coarse_hit.get("status") == "matched" and coarse_hit.get("dH") not in (None, ""):
                ch.append(float(coarse_hit["dH"]))
                cn.append(float(coarse_hit["dN"]))
        ax.plot(dh, dn, "o", ms=3.0, alpha=0.75, color=colour,
                label=f"{case['label']} tight match (n={len(dh)})")
        if lh:
            # Open circles = peaks outside the tight tier but inside the loose one: just outside
            # the box.
            ax.plot(lh, ln, "o", ms=3.6, alpha=0.9, mfc="none", mec=colour,
                    label=f"{case['label']} loose match only (n={len(lh)})")
        if ch:
            # Crosses = peaks that needed the coarse tier as well: a larger shift.
            ax.plot(ch, cn, "x", ms=4.2, alpha=0.9, color="#718096",
                    label=f"{case['label']} coarse match only (n={len(ch)})")
    ax.axvline(0.01, color="grey", ls="--", lw=0.7)
    ax.axvline(-0.01, color="grey", ls="--", lw=0.7)
    ax.axhline(0.05, color="grey", ls="--", lw=0.7)
    ax.axhline(-0.05, color="grey", ls="--", lw=0.7)
    ax.set_xlabel("d 1H (ppm), dashed = tight tolerance")
    ax.set_ylabel("d 15N (ppm)")
    ax.set_title("Residuals after one frozen global reference shift", fontsize=10)
    # Strip trailing zeros from the tick labels and thin out the crowded x axis.
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.xaxis.set_major_formatter(FuncFormatter(_trim_number))
    ax.yaxis.set_major_formatter(FuncFormatter(_trim_number))
    ax.legend(fontsize=7)
    ax.grid(alpha=0.25, lw=0.4)

    ax = axes[1][1]
    width = 0.35
    for index, case in enumerate(cases):
        levels = case["report"]["thresholds"][0]["evaluation"]["levels"]
        rates = [100.0 * float(level["calibrated"]["recall"]) for level in levels]
        chance = [100.0 * float(level["chance_per_peak_decoy"]["mean"]) for level in levels]
        xs = np.arange(len(levels)) + (index - 0.5) * width
        ax.bar(xs, rates, width=width, color=("#e53e3e" if index == 0 else "#2b6cb0"),
               label=f"{case['label']} recovery")
        ax.bar(xs, chance, width=width * 0.55, color="#4a5568", alpha=0.75,
               label=f"{case['label']} chance (per-peak decoy)")
        ax.set_xticks(np.arange(len(levels)))
        ax.set_xticklabels([f"{level['tol_h']}/{level['tol_n']}" for level in levels], fontsize=8)
    ax.set_xlabel("tolerance 1H / 15N (ppm)")
    ax.set_ylabel("expected peaks recovered (%)")
    ax.set_title("Recovery vs chance level", fontsize=10)
    ax.legend(fontsize=7)

    if args.compare_out and args.pdata:
        pdata_dirs = list(args.pdata)
        while len(pdata_dirs) < len(cases):
            pdata_dirs.append(pdata_dirs[-1])
        fig2, axes2 = plt.subplots(len(cases), 2, figsize=(10.5, 4.6 * len(cases)))
        axes2 = np.atleast_2d(axes2)
        for row, (case, pdata_dir) in enumerate(zip(cases, pdata_dirs)):
            theirs = _orient_pdata(_load_pdata(Path(pdata_dir)), case["expected"], case["offset"])
            measured, corr_zero, corr_best = _projection_offset(case["spec"], theirs)
            envelope = _centroid_offset(case["spec"], theirs)
            shift = args.shift_n
            shift_theirs = args.shift_n_theirs
            if args.align == "projection":
                shift += measured
            elif args.align == "envelope":
                shift -= envelope
            elif args.align == "envelope-theirs":
                shift_theirs += envelope
            _draw_spectrum(
                axes2[row][0], case["spec"], case["expected"], case["matches"], case["offset"],
                f"{case['label']} - automatic processing (this software)"
                + (f" [15N shifted by {shift:+.2f} ppm for inspection]" if shift else ""),
                shift_n=shift,
                show_peaks=False,
            )
            _draw_spectrum(
                axes2[row][1], theirs, case["expected"], case["matches"], case["offset"],
                f"{case['label']} - processed spectrum shipped with the data (pdata/1)"
                + (f" [15N shifted by {shift_theirs:+.2f} ppm for inspection]"
                   if shift_theirs else ""),
                shift_n=shift_theirs,
                show_peaks=False,
            )
            print(
                f"{case['label']}: 15N offset vs pdata {measured:+.3f} ppm "
                f"(correlation {corr_zero:.4f} -> {corr_best:.4f}), "
                f"centroid gap {envelope:+.3f} ppm; drawn with {shift:+.3f} ppm "
                f"(pdata column {shift_theirs:+.3f} ppm)"
            )
        fig2.tight_layout()
        target2 = Path(args.compare_out)
        target2.parent.mkdir(parents=True, exist_ok=True)
        fig2.savefig(target2, dpi=200)
        print(f"wrote {target2}")

    fig.tight_layout()
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, dpi=200)
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
