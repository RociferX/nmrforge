"""Create a four-panel evidence figure for automatic versus comparison spectra across the four
processing paths (2D/3D × uniform/NUS).

The truth figure is dedicated to truth recovery and requires a deposited peak list for every
case. The 3D and NUS datasets here may not have one. This script compares spectra and QC scores:
each path gets one row, with the automatic final spectrum on the left and a selected comparison
spectrum on the right. Both use the same ppm window and contour convention, normalized to each
spectrum's own maximum; row titles include the overall QC score.

For 3D spectra, the default is a signed HN projection along 13C, selecting the voxel with the
largest absolute value and retaining its sign. --projection can instead draw HN, HC, NC, or all
three 2D projections; --h-slice draws an N/C plane at an explicit 1H ppm. These views complement
full-coordinate matching rather than replacing it. The script does not generate evaluation
criteria: spectra are the primary evidence, and scores are copied from QC reports.

Usage (real data; paths appear only on the command line):

    nmrforge/bin/python scripts/vm_four_path_figure.py \
        --case "3D,<automatic.ft3>,<comparison.ft3>,<auto-qc.json>,<comparison-qc.json>" \
        --h-slice 8.25 --out docs/evidence/four-path-compare.png

The comparison may be an NMRPipe spectrum file or a Bruker pdata/1 directory. QC JSON comes from
vm_qc_score.py --json; if omitted, the row title contains the path label without a score.

Axis order varies by experiment. The script reads nucleus labels associated with each data axis
and rearranges them to an internal orientation. Bruker pdata must provide explicit nucleus
labels; ppm ranges alone cannot identify them. HN projections are overviews and cannot establish
3D carbon-axis peak positions or independent truth-recovery rates.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Contour levels as fractions of each spectrum's own maximum; normalize each separately.
LEVELS = (0.075, 0.1, 0.2, 0.35, 0.5, 0.7, 0.9)
CONTOUR_LINEWIDTHS = (0.25, 0.25, 0.4, 0.4, 0.4, 0.4, 0.4)
COLOR = "#1f4e79"
#: Keep figure text ASCII because the real machine lacks Chinese fonts.


def _nucleus_family(label: str) -> str:
    """Map isotope-labeled nucleus names to H, N, or C families."""
    text = str(label or "").upper().replace(" ", "")
    core = text.lstrip("0123456789") or text
    if core.startswith("H"):
        return "H"
    if "N" in core:
        return "N"
    if "C" in core:
        return "C"
    return core


def _load(path: Path) -> dict:
    """Read one spectrum using nmrglue for pdata directories and the product reader otherwise.

The axes list nucleus labels by data-array axis position. This preserves the axis identity needed
for the 3D carbon projection.
"""
    if path.is_dir():
        return _load_pdata(path)
    from workflow.pick_peaks import read_spectrum_axes

    spectrum = read_spectrum_axes(path)
    data = np.asarray(spectrum.data, dtype=float)
    ppm = [np.asarray(axis, dtype=float) for axis in spectrum.ppm]
    nuclei = [
        _nucleus_family(
            str(spectrum.nuclei[position])
            if position < len(spectrum.nuclei)
            else f"F{position + 1}"
        )
        for position in range(data.ndim)
    ]
    if len(ppm) != data.ndim or len(nuclei) != data.ndim:
        raise SystemExit(
            f"axis metadata does not match data ndim for {path.name}: "
            f"data {data.ndim}D, ppm {len(ppm)}, nuclei {len(nuclei)}"
        )
    return {"data": data, "ppm": ppm, "nuclei": nuclei, "source": path.name}


def _assign_pdata_nuclei(ppm: list[np.ndarray], udic) -> list[str]:
    """Identify dimensions only from explicit, unique nucleus labels, not ppm ranges."""
    # Prefer the nucleus-name field in procs when it provides a direct answer.
    labels: list[str] = []
    for index in range(len(ppm)):
        label = ""
        try:
            dimension = udic[index]
        except (TypeError, KeyError, IndexError):
            dimension = None
        if isinstance(dimension, dict):
            for key in ("label", "nuc1", "NUC1"):
                value = dimension.get(key)
                if value:
                    label = str(value)
                    break
        labels.append(_nucleus_family(label) if label else "")
    # Accept labels only when every dimension is identified and the labels are unique.
    if all(label in {"H", "N", "C"} for label in labels) and len(set(labels)) == len(labels):
        return labels
    raise SystemExit(
        "pdata axis nuclei are missing or ambiguous; ppm ranges cannot identify nuclei"
    )


def _load_pdata(path: Path) -> dict:
    """Read the Bruker processed spectrum included with the data (pdata/1)."""
    import nmrglue as ng

    dic, data = ng.bruker.read_pdata(str(path))
    udic = ng.bruker.guess_udic(dic, data)
    converters = []
    for index in range(np.ndim(data)):
        try:
            converters.append(ng.fileiobase.uc_from_udic(udic, dim=index))
        except TypeError:
            converters.append(ng.fileiobase.uc_from_udic(udic)[index])
    ppm = [np.asarray(c.ppm_scale(), dtype=float) for c in converters]
    nuclei = _assign_pdata_nuclei(ppm, udic)
    return {"data": np.asarray(data, dtype=float), "ppm": ppm,
            "nuclei": nuclei, "source": path.name}


def _projection_plane(
    spectrum: dict, pair: str = "HN",
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Project onto explicit x/y nuclei, retaining the signed maximum-absolute voxel."""
    data = spectrum["data"]
    ppm = spectrum["ppm"]
    nuclei = spectrum["nuclei"]
    if pair not in {"HN", "HC", "NC"}:
        raise SystemExit("projection must be HN, HC or NC")
    if not np.all(np.isfinite(data)):
        raise SystemExit("non-finite spectrum cannot be used as evidence")
    if len(set(nuclei)) != len(nuclei):
        raise SystemExit("repeated/ambiguous nuclei require an explicit logical-axis selection")
    if data.ndim not in {2, 3} or len(ppm) != data.ndim or len(nuclei) != data.ndim:
        raise SystemExit("only explicit 2D/3D axis metadata is supported")
    if not set(pair) <= set(nuclei):
        raise SystemExit(f"spectrum lacks {pair} axes (got {nuclei})")
    for axis, scale in enumerate(ppm):
        values = np.asarray(scale, dtype=float)
        if values.ndim != 1 or len(values) != data.shape[axis] or len(values) < 2:
            raise SystemExit("ppm axis does not match spectrum shape")
        if not np.all(np.isfinite(values)) or not (
            np.all(np.diff(values) > 0) or np.all(np.diff(values) < 0)
        ):
            raise SystemExit("ppm axes must be finite and strictly monotonic")
    remaining = list(nuclei)
    plane = data
    if data.ndim == 3:
        if set(nuclei) != {"H", "N", "C"}:
            raise SystemExit(f"3D spectrum needs H/N/C axes (got {nuclei})")
        collapsed = next(i for i, nucleus in enumerate(nuclei) if nucleus not in pair)
        selected = np.argmax(np.abs(data), axis=collapsed)
        plane = np.take_along_axis(
            data, np.expand_dims(selected, collapsed), axis=collapsed,
        ).squeeze(collapsed)
        remaining.pop(collapsed)
    if remaining != [pair[1], pair[0]]:
        plane = plane.T
    return plane, ppm[nuclei.index(pair[0])], ppm[nuclei.index(pair[1])]


def _hn_plane(spectrum: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compatibility entry point for the signed H/N projection."""
    return _projection_plane(spectrum, "HN")


def _available_pairs(spectrum: dict) -> list[str]:
    """Select supported planes from explicit metadata, never from ppm ranges."""
    pairs = [pair for pair in ("HN", "HC", "NC")
             if set(pair) <= set(spectrum["nuclei"])]
    if not pairs:
        raise SystemExit("spectrum has no supported explicit H/N/C plane")
    return pairs


def _shift_spectrum(spectrum: dict, offsets: dict | None) -> dict:
    """Translate explicit comparison-axis ppm coordinates without altering data."""
    if offsets is not None and not isinstance(offsets, dict):
        raise ValueError("reference offsets must be an H/N/C dictionary")
    offsets = offsets or {}
    if set(offsets) - {"H", "N", "C"}:
        raise ValueError("reference offsets must use H, N or C nuclear keys")
    parsed = {n: float(value) for n, value in offsets.items()}
    if any(not np.isfinite(value) for value in parsed.values()):
        raise ValueError("reference offsets must be finite")
    for nucleus, value in parsed.items():
        if value and spectrum["nuclei"].count(nucleus) != 1:
            raise ValueError("a nonzero reference offset requires one explicit nuclear axis")
    axes = [
        np.asarray(scale) + parsed.get(nucleus, 0.0)
        for nucleus, scale in zip(spectrum["nuclei"], spectrum["ppm"], strict=True)
    ]
    return dict(spectrum, ppm=axes)


def _common_spectra(left: dict, right: dict) -> tuple[dict, dict]:
    """Crop all nuclear axes before projecting; never include unmatched collapsed ranges."""
    if len(set(left["nuclei"])) != len(left["nuclei"]) or (
        len(set(right["nuclei"])) != len(right["nuclei"])
        or set(left["nuclei"]) != set(right["nuclei"])
    ):
        raise SystemExit("comparison requires the same explicit unique nuclear axes")
    for spectrum in (left, right):
        _projection_plane(spectrum, _available_pairs(spectrum)[0])
    windows = {
        n: _window(left["ppm"][left["nuclei"].index(n)],
                   right["ppm"][right["nuclei"].index(n)])
        for n in left["nuclei"]
    }
    result = []
    for spectrum in (left, right):
        axes, indices = [], []
        for nucleus, scale in zip(spectrum["nuclei"], spectrum["ppm"], strict=True):
            lo, hi = windows[nucleus]
            selected = np.where((scale >= lo) & (scale <= hi))[0]
            if len(selected) < 2:
                raise SystemExit("common ppm window is too small")
            indices.append(selected)
            axes.append(scale[selected])
        result.append(dict(spectrum, data=spectrum["data"][np.ix_(*indices)], ppm=axes))
    return tuple(result)


def _window(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    low = max(float(a.min()), float(b.min()))
    high = min(float(a.max()), float(b.max()))
    if not high > low:
        raise SystemExit("the two spectra share no ppm window on this axis")
    return low, high


def _nc_slice(spectrum: dict, h_ppm: float):
    """Select an N/C plane at an explicit proton ppm to show carbon-axis evidence."""
    data, axes, nuclei = spectrum["data"], spectrum["ppm"], spectrum["nuclei"]
    if data.ndim != 3 or set(nuclei) != {"H", "N", "C"} or len(set(nuclei)) != 3:
        raise SystemExit("H slice requires an explicit unique H/N/C 3D spectrum")
    if not np.isfinite(h_ppm) or not np.all(np.isfinite(data)):
        raise SystemExit("slice coordinates and spectrum must be finite")
    h_index = nuclei.index("H")
    h_axis = axes[h_index]
    if not float(h_axis.min()) <= h_ppm <= float(h_axis.max()):
        raise SystemExit("H slice falls outside the spectrum")
    selected = int(np.argmin(np.abs(h_axis - h_ppm)))
    plane = np.take(data, selected, axis=h_index)
    remaining = [label for label in nuclei if label != "H"]
    if remaining == ["N", "C"]:
        plane = plane.T
    return plane, axes[nuclei.index("N")], axes[nuclei.index("C")], float(h_axis[selected])


def _crop(
    plane: np.ndarray, ppm_h: np.ndarray, ppm_n: np.ndarray,
    h_lo: float, h_hi: float, n_lo: float, n_hi: float,
):
    rows = np.where((ppm_n >= n_lo) & (ppm_n <= n_hi))[0]
    cols = np.where((ppm_h >= h_lo) & (ppm_h <= h_hi))[0]
    if rows.size < 2 or cols.size < 2:
        raise SystemExit("common ppm window is too small to draw")
    return plane[np.ix_(rows, cols)], ppm_h[cols], ppm_n[rows]


def _flip(block, ppm_h, ppm_n):
    """Flip to increasing ppm; reverse the displayed limits when plotting."""
    if ppm_h[0] > ppm_h[-1]:
        block, ppm_h = block[:, ::-1], ppm_h[::-1]
    if ppm_n[0] > ppm_n[-1]:
        block, ppm_n = block[::-1, :], ppm_n[::-1]
    return block, ppm_h, ppm_n


def _qc_score(path: Path | None) -> str:
    """Read the overall score and decision from a vm_qc_score.py JSON report.

The report stores score fields under quality and includes decision and reasons at the same
level. Scores are omitted when no report is specified. A specified report must be readable and
contain a finite score.
"""
    if path is None:
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read QC report {path.name}: {exc}") from exc
    quality = payload.get("quality")
    if not isinstance(quality, dict):
        quality = payload
    score_block = quality.get("score")
    overall = None
    if isinstance(score_block, dict):
        overall = score_block.get("overall")
    if overall is None:
        overall = quality.get("overall")
    if overall is None:
        raise SystemExit(f"QC report {path.name} has no overall score")
    decision = quality.get("decision") or ""
    try:
        value = float(overall)
        if not np.isfinite(value):
            raise ValueError("non-finite score")
        text = f" QC={value:.1f}"
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"invalid QC score in {path.name}") from exc
    return text + (f" {decision}" if decision else "")


def _draw(ax, block, ppm_h, ppm_n, title: str, labels=("1H", "15N"), *,
          peak_points=None, peak_signs=None, peak_marker_size=4, peak_matched=None) -> None:
    import textwrap

    title = textwrap.fill(title, width=62)
    peak = float(np.max(np.abs(block))) if block.size else 0.0
    if not np.isfinite(peak) or peak <= 0.0:
        ax.text(0.5, 0.5, "no signal", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title, fontsize=10)
        return
    block, ppm_h, ppm_n = _flip(block, ppm_h, ppm_n)
    if np.max(block) >= peak * LEVELS[0]:
        ax.contour(
            ppm_h, ppm_n, block,
            levels=[peak * level for level in LEVELS],
            colors=COLOR, linewidths=CONTOUR_LINEWIDTHS,
        )
    if np.min(block) < -peak * LEVELS[0]:
        ax.contour(
            ppm_h, ppm_n, block,
            levels=sorted(-peak * level for level in LEVELS),
            colors="#a23b3b", linewidths=CONTOUR_LINEWIDTHS[::-1], linestyles="dashed",
        )
    if peak_points is not None:
        points = np.asarray(peak_points).reshape(-1, 2)
        signs = np.asarray(peak_signs)
        matched = (np.ones(len(points), dtype=bool) if peak_matched is None
                   else np.asarray(peak_matched, dtype=bool))
        if signs.shape != (len(points),) or matched.shape != (len(points),):
            raise ValueError("peak signs and match mask must align with peak points")
        selected = matched & np.isin(signs, (-1, 1))
        ax.scatter(points[selected, 0], points[selected, 1], s=peak_marker_size,
                   marker=".", color="#d62728", linewidths=0, zorder=4)
        ax.scatter(points[~matched, 0], points[~matched, 1], s=peak_marker_size,
                   marker="x", color="#b12cbd", linewidths=.5, zorder=5)
    ax.set_xlabel(f"{labels[0]} (ppm)")
    ax.set_ylabel(f"{labels[1]} (ppm)")
    ax.set_title(title, fontsize=10)
    ax.set_xlim(float(ppm_h.max()), float(ppm_h.min()))
    ax.set_ylim(float(ppm_n.max()), float(ppm_n.min()))


def _parse_case(text: str) -> dict:
    parts = [item.strip() for item in text.split(",")]
    if not 3 <= len(parts) <= 5 or not all(parts[:3]):
        raise SystemExit(
            "--case needs 'label,auto_spectrum,reference_spectrum[,auto_qc.json,ref_qc.json]'"
        )
    label, auto, reference = parts[0], Path(parts[1]), Path(parts[2])
    auto_qc = Path(parts[3]) if len(parts) > 3 and parts[3] else None
    ref_qc = Path(parts[4]) if len(parts) > 4 and parts[4] else None
    return {"label": label, "auto": auto, "reference": reference,
            "auto_qc": auto_qc, "ref_qc": ref_qc}


def main(argv: list[str] | None = None) -> int:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    parser = argparse.ArgumentParser(
        description="Processing-path evidence: automatic vs explicitly selected comparison spectra"
    )
    parser.add_argument("--case", action="append", required=True, metavar="SPEC")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=160)
    parser.add_argument("--mark-peaks", action="store_true",
                        help="Mark each panel's own independently detected peak candidates")
    parser.add_argument("--peak-height-fraction", type=float, default=.10,
                        help="Candidate height fraction of baseline-centered maximum; default .10")
    parser.add_argument("--peak-marker-size", type=float, default=4,
                        help="Scatter marker area in points squared; default 4")
    parser.add_argument("--peak-sign-mode", default="dominant",
                        choices=("auto", "dominant", "positive", "negative", "both"),
                        help="Product dominant-sign rule for same-sign spectra; both for mixed")
    for nucleus in "HNC":
        parser.add_argument(
            f"--tol-{nucleus.lower()}", type=float,
            default={"H": .02, "N": .20, "C": .15}[nucleus],
            help=f"{nucleus} matching tolerance in ppm for unmatched markers",
        )
        parser.add_argument(
            f"--reference-shift-{nucleus.lower()}", type=float, default=0.,
            help=f"ppm to add to the comparison {nucleus} axis; applies to every --case",
        )
    parser.add_argument(
        "--projection", choices=("HN", "HC", "NC", "all"), default="HN",
        help="Nuclear x/y axes; all draws HN, HC and NC for each 3D pair",
    )
    parser.add_argument(
        "--h-slice", type=float, help="3D N/C slice at this 1H ppm instead of projection",
    )
    args = parser.parse_args(argv)
    if not np.isfinite(args.peak_height_fraction) or not 0 < args.peak_height_fraction <= 1:
        parser.error("peak height fraction must be finite and between 0 and 1")
    if not np.isfinite(args.peak_marker_size) or args.peak_marker_size <= 0:
        parser.error("peak marker size must be positive and finite")
    offsets = {n: getattr(args, f"reference_shift_{n.lower()}") for n in "HNC"}
    if any(not np.isfinite(value) for value in offsets.values()):
        parser.error("reference offsets must be finite")
    tolerances = {n: getattr(args, f"tol_{n.lower()}") for n in "HNC"}
    if any(not np.isfinite(value) or value <= 0 for value in tolerances.values()):
        parser.error("matching tolerances must be positive and finite")
    if args.h_slice is not None and args.projection != "HN":
        parser.error("--h-slice cannot be combined with --projection")

    cases = [_parse_case(item) for item in args.case]
    for case in cases:
        for key in ("auto", "reference"):
            if not case[key].exists():
                parser.error(f"{case['label']}: {key} spectrum not found: {case[key]}")

    entries = []
    for case in cases:
        auto = _load(case["auto"])
        try:
            reference = _shift_spectrum(_load(case["reference"]), offsets)
        except ValueError as exc:
            parser.error(str(exc))
        if args.h_slice is None:
            auto, reference = _common_spectra(auto, reference)
        pairs = _available_pairs(auto) if args.projection == "all" else (args.projection,)
        for pair in pairs:
            entries.append((case, auto, reference, pair))
    figure, grid = plt.subplots(
        len(entries), 2, figsize=(11.0, 4.4 * len(entries)), squeeze=False,
    )
    for row, (case, auto, reference, pair) in enumerate(entries):
        labels = ("1H", "15N")
        slice_title = ""
        if args.h_slice is not None:
            auto_plane, auto_h, auto_n, selected_h = _nc_slice(auto, args.h_slice)
            ref_plane, ref_h, ref_n, reference_h = _nc_slice(reference, args.h_slice)
            labels = ("15N", "13C")
            slice_title = f" (H slices {selected_h:.3f}/{reference_h:.3f} ppm)"
        else:
            auto_plane, auto_h, auto_n = _projection_plane(auto, pair)
            ref_plane, ref_h, ref_n = _projection_plane(reference, pair)
            names = {"H": "1H", "N": "15N", "C": "13C"}
            labels = (names[pair[0]], names[pair[1]])
        h_lo, h_hi = _window(auto_h, ref_h)
        n_lo, n_hi = _window(auto_n, ref_n)
        if args.h_slice is None:
            # Full spectra were already cropped once, before projection. A
            # second grid-dependent crop would change the detection threshold.
            h_lo, h_hi = min(auto_h.min(), ref_h.min()), max(auto_h.max(), ref_h.max())
            n_lo, n_hi = min(auto_n.min(), ref_n.min()), max(auto_n.max(), ref_n.max())
        panels = (
            (grid[row][0], auto_plane, auto_h, auto_n,
             f"{case['label']} {pair} - automatic{_qc_score(case['auto_qc'])}", case["auto"]),
            (grid[row][1], ref_plane, ref_h, ref_n,
             f"{case['label']} {pair} - comparison{_qc_score(case['ref_qc'])}", case["reference"]),
        )
        prepared = []
        detection_pair = "NC" if args.h_slice is not None else pair
        for ax, plane, ppm_h, ppm_n, title, source in panels:
            if args.h_slice is None:
                block, block_h, block_n = plane, ppm_h, ppm_n
            else:
                block, block_h, block_n = _crop(
                    plane, ppm_h, ppm_n, h_lo, h_hi, n_lo, n_hi
                )
            shape = "x".join(str(int(v)) for v in plane.shape)
            points, signs = None, None
            marker_title = ""
            if args.mark_peaks:
                from scripts.vm_projection_report import _detect_plane

                rows, points, signs, _meta = _detect_plane(
                    source, block, block_h, block_n, detection_pair, 0., args.peak_height_fraction,
                    sign_mode=args.peak_sign_mode,
                )
                marker_title = f"; {len(rows)} candidates"
            prepared.append((ax, block, block_h, block_n,
                             f"{title} ({shape}){slice_title}{marker_title}", points, signs))
        masks = [None, None]
        matched_indices = []
        if args.mark_peaks:
            from scripts.vm_projection_report import _match_indices

            matched_indices = _match_indices(
                prepared[0][5], prepared[1][5], prepared[0][6], prepared[1][6],
                np.asarray([tolerances[n] for n in detection_pair]),
            )
            masks = [np.zeros(len(panel[5]), dtype=bool) for panel in prepared]
            for i, j in matched_indices:
                masks[0][i] = masks[1][j] = True
        for side, (ax, block, block_h, block_n, title, points, signs) in enumerate(prepared):
            if args.mark_peaks:
                if side == 1:
                    title += f"; {int((~masks[side]).sum())} unmatched"
                    coverage = (f"{len(matched_indices)}/{len(points)} = "
                                f"{len(matched_indices) / len(points):.2%}"
                                if len(points) else "n/a (no reference candidates)")
                    title += f"; reference coverage {coverage}"
            _draw(ax, block, block_h, block_n, title,
                  labels, peak_points=points, peak_signs=signs,
                  peak_marker_size=args.peak_marker_size,
                  peak_matched=masks[side] if side == 1 else None)
            ax.set_xlim(float(h_hi), float(h_lo))
            ax.set_ylim(float(n_hi), float(n_lo))
    alignment_title = ""
    if any(offsets.values()):
        alignment_title = "\nComparison ppm offsets: " + ", ".join(
            f"{n} {value:+.6f}" for n, value in offsets.items() if value
        )
    if args.mark_peaks:
        alignment_title += (
            f"\nCandidates at {args.peak_height_fraction * 100:g}% centered height: "
            f"{args.peak_sign_mode}; red dot; "
            "reference unmatched: purple x"
        )
    figure.suptitle(
        "Signed spectra: blue positive / dashed red negative; "
        + ("signed max-absolute projections" if args.h_slice is None else "3D N/C slices")
        + alignment_title,
        fontsize=10,
    )
    figure.tight_layout(rect=(0, 0, 1, 0.98))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.out, dpi=args.dpi)
    plt.close(figure)
    print(f"figure written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
