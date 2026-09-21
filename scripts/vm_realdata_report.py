"""Real-data aggregate report: run a real Bruker dataset on a machine with NMRPipe and emit
desensitised aggregate metrics only.

Why a separate script: ``vm_sample_regression.py`` / ``vm_sample_compare.py`` are for debugging
one item at a time - they print paths, header fields and absolute peak positions. The only form
an outside reader can cite over time is **aggregate numbers**.

Usage (on the machine; the dataset path appears on the command line only)::

    nmrforge/bin/python scripts/vm_realdata_report.py \
        --dataset <Bruker dataset directory> --tag <anonymous tag> \
        --root <scratch root> --repeats 3 [--json <output path>] [--classify-only] \
        [--manual <manually processed .ft2>]

``--manual`` adds an "automatic vs manual" comparison: point it at the manually processed NMRPipe
spectrum (for example the ``test.ft2`` the laboratory produced with its own ``xy.com``). The axes
of the two spectra are matched by **nucleus** (through the product's own spectrum reader), and
the script reports:

* the main-peak position difference (automatic - manual, per nucleus, in ppm); absolute positions
  are used to compute the difference and do not enter the output;
* the peak tables: **both spectra are picked at the product's default detection threshold**
  (2D and 3D), with peak and fallback counts;
* shift and matching: the shift is voted from the pairwise `manual - automatic` differences and
  matching is greedy nearest neighbour at **half** the automatic spectrum's median line width per
  nucleus, reporting the matched fraction on both sides (detection and refinement are untouched;
  no candidate peaks are swapped in to inflate the match rate);
* the ratio of the main-peak FWHM (automatic / manual) plus both absolute line widths.

The output is one JSON document (also written to ``--json`` when given) containing:

* ``input``: file count, total bytes and the sha256 of the whole dataset directory (files sorted
  by name, the path itself is not hashed);
* ``classification``: the experiment type, sampling mode and dimensionality the software works out;
* ``runs[]``: per repeat, the import / fid / spectrum seconds, the final spectrum shape,
  whether each localisation method succeeded or fell back, and the F1/F2 FWHM in ppm;
* ``aggregate``: median and p95 timings, the **maximum repeat-to-repeat peak difference**
  (repeatability), the maximum difference between the two methods, the median line width and the
  F2/F1 ratio, and the fallback counts;
* ``manual_comparison`` (with ``--manual``): the four results above (peak tables / shift /
  matching / line widths).

Never in the output: dataset paths, file names, sample names, host names. Absolute main-peak ppm
values are only used inside the script to compute differences and are stripped before printing
(keys starting with ``_``).

ppm axes always come from ``workflow.pick_peaks.read_spectrum_axes`` (shared with peak picking
and the viewer); this script does not rebuild header fields itself. The old Hz-as-ppm misreading
in ``vm_sample_compare.py`` is recorded in ``docs/evidence/real-data-comparison.md``
section 5.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _fingerprint(dataset: Path) -> dict:
    """Input fingerprint: file count, total bytes and the chained sha256 of every file's name and
    content (file names only, never the directory path)."""
    digest = hashlib.sha256()
    files = sorted(p for p in dataset.rglob("*") if p.is_file())
    total = 0
    for path in files:
        payload = path.read_bytes()
        total += len(payload)
        digest.update(path.name.encode("utf-8"))
        digest.update(hashlib.sha256(payload).digest())
    return {"files": len(files), "bytes": total, "sha256": digest.hexdigest()}


def _classify(dataset: Path) -> dict:
    from core.data.bruker_reader import read_dataset

    experiment = read_dataset(dataset)
    dims = getattr(experiment, "dimensions", None)
    if dims is None:
        dims = getattr(experiment, "dims", None)
    return {
        "experiment_type": str(experiment.experiment_type.name),
        "sampling": str(experiment.sampling.mode.value),
        "dimensions": int(len(dims)) if dims is not None else None,
        "sampling_evidence": " | ".join(
            str(item) for item in (getattr(experiment.sampling, "evidence", None) or [])
        )
        or str(getattr(experiment.sampling, "evidence", "") or ""),
    }


def _nucleus_family(label: str) -> str:
    """Fold ``15N`` / ``HN`` / ``1H`` / ``13C`` into ``N`` / ``H`` / ``C`` so the axes of two
    spectra can be matched by nucleus.

    The isotope prefix is stripped first: ``1H`` starts with a digit, so without that step
    ``startswith("H")`` is false and the label lands in its own family. Measured case: the
    software writes ``1H`` while the manual spectrum writes ``HN``.
    """
    text = str(label or "").upper().replace(" ", "")
    core = text.lstrip("0123456789") or text
    if core.startswith("H"):
        return "H"
    if "N" in core:
        return "N"
    if "C" in core:
        return "C"
    return core


def _slice_along(data: np.ndarray, index: tuple[int, ...], axis: int) -> np.ndarray:
    """The one-dimensional trace through the peak point along ``axis``."""
    slicer: list = [index[i] if i != axis else slice(None) for i in range(data.ndim)]
    return data[tuple(slicer)]



def _spec_from_axes(spectrum) -> dict:
    """``SpectrumAxes`` -> this script's internal shape (modulus data, nucleus -> axis)."""
    data = np.abs(np.asarray(spectrum.data, dtype=float))
    axes: dict[str, tuple[np.ndarray, float, str, int]] = {}
    by_position: list[tuple[np.ndarray, float, str]] = []
    for position, ppm_axis in enumerate(spectrum.ppm):
        values = np.asarray(ppm_axis, dtype=float)
        step = abs(float(values[1] - values[0])) if values.size > 1 else 0.0
        if position < len(spectrum.nuclei):
            label = str(spectrum.nuclei[position])
        else:
            label = f"F{position + 1}"
        axes[_nucleus_family(label)] = (values, step, label, position)
        by_position.append((values, step, label))
    return {"data": data, "axes": axes, "by_position": by_position}


def _load_spectrum(path: Path) -> dict:
    """Read one NMRPipe spectrum (2D/3D); the axes come from the product's own reader.

    ``workflow.pick_peaks.read_spectrum_axes`` is shared with peak picking and the viewer -
    do not rebuild ``ORIG``/``SW`` here: NMRPipe ppm is
    ``ORIG/OBS + (size-1-i)*SW/(size*OBS)`` (the last point sits at ``ORIG/OBS``). The old
    ``ORIG/OBS - i*SW/(size*OBS)`` form was short by one whole window width - on 2D it put
    the 1H axis 4.0 ppm and the 15N axis 32.9 ppm off, which is where the bogus
    "automatic vs manual differ by 1.094 ppm" came from (the peak tables show ~0.09 ppm).
    """
    from workflow.pick_peaks import read_spectrum_axes

    return _spec_from_axes(read_spectrum_axes(path))


def _numeric_column(rows: list[dict], key: str) -> np.ndarray:
    """One numeric column of a peak table (blank / illegal / NaN rows are dropped)."""
    values: list[float] = []
    for row in rows:
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if np.isfinite(number):
            values.append(number)
    return np.asarray(values, dtype=float)


def _table_points(rows: list[dict], families: list[str]) -> list[dict[str, float]]:
    """Peak table -> per-peak coordinates (rows missing a family are dropped).

    Column names are fixed per nucleus (``H_ppm`` / ``N_ppm``).
    """
    columns = {"H": "H_ppm", "N": "N_ppm"}
    points: list[dict[str, float]] = []
    for row in rows:
        point: dict[str, float] = {}
        for family in families:
            key = columns.get(family)
            if key is None:
                continue
            raw = row.get(key)
            if raw in (None, ""):
                continue
            try:
                number = float(raw)
            except (TypeError, ValueError):
                continue
            if np.isfinite(number):
                point[family] = number
        if len(point) == len(families):
            points.append(point)
    return points


def _tolerances(rows: list[dict], families: list[str]) -> dict[str, float]:
    """Matching tolerance: half the automatic median line width per nucleus.

    The line width comes from the peak table's FWHM column; a missing column falls back
    to 1e-6.
    """
    columns = {"H": "FWHM_H", "N": "FWHM_N"}
    out: dict[str, float] = {}
    for family in families:
        values = _numeric_column(rows, columns.get(family, ""))
        median = float(np.median(values)) if values.size else 0.0
        out[family] = max(median * 0.5, 1e-6)
    return out


def _vote_offset(software: np.ndarray, manual: np.ndarray, tolerance: float) -> float | None:
    """Vote for the ``manual - automatic`` constant shift (ppm).

    Pairwise differences are binned at the tolerance and the heaviest bin's mean wins.
    """
    if software.size == 0 or manual.size == 0 or tolerance <= 0.0:
        return None
    diffs = (manual[:, None] - software[None, :]).ravel()
    index = np.round(diffs / tolerance).astype(int)
    index = index - int(index.min())
    counts = np.bincount(index)
    winner = int(np.argmax(counts))
    if counts[winner] <= 1:
        return None
    return float(np.mean(diffs[index == winner]))


def _match_counts(source: list[dict], target: list[dict], tolerances: dict) -> int:
    """Greedy nearest-neighbour match: each source peak takes the closest free target peak
    within tolerance."""
    used: set[int] = set()
    matched = 0
    for point in source:
        best, best_index = None, -1
        for index, other in enumerate(target):
            if index in used:
                continue
            scale = max(abs(point[family] - other[family]) / tolerances[family] for family in point)
            if scale <= 1.0 and (best is None or scale < best):
                best, best_index = scale, index
        if best_index >= 0:
            used.add(best_index)
            matched += 1
    return matched


def _fwhm_ppm(profile: np.ndarray, index: int, step: float) -> float | None:
    """FWHM (ppm) of a one-dimensional slice. Magnitudes are used so negative peaks work too;
    returns None when the half-height crossings cannot be bracketed."""
    prof = np.abs(np.asarray(profile, dtype=float))
    if prof.size < 5:
        return None
    base = float(prof.min())
    peak = float(prof[index])
    if not np.isfinite(peak) or peak <= base:
        return None
    half = (peak + base) / 2.0
    left = index
    while left > 0 and prof[left] > half:
        left -= 1
    right = index
    while right < prof.size - 1 and prof[right] > half:
        right += 1
    if left == index or right == index:
        return None

    def crossing(a: int, b: int) -> float:
        va, vb = float(prof[a]), float(prof[b])
        if vb == va:
            return float(a)
        return a + (half - va) / (vb - va)

    return abs(crossing(right, right - 1) - crossing(left, left + 1)) * step


def _main_peak(spec: dict) -> dict:
    """Per-nucleus ppm and FWHM of the strongest point.

    The ppm value exists only to form differences and is stripped before printing.
    """
    data = spec["data"]
    index = tuple(int(i) for i in np.unravel_index(int(np.argmax(data)), data.shape))
    out: dict[str, dict] = {}
    for family, (axis, step, label, position) in spec["axes"].items():
        i = index[position]
        profile = _slice_along(data, index, position)
        out[family] = {
            "label": label,
            "ppm": float(axis[i]),
            "fwhm_ppm": _fwhm_ppm(profile, i, step),
        }
    return out



def compare_with_manual(software: Path, manual: Path) -> dict:
    """Automatic vs manual spectrum: pick at the default threshold, match the peak tables.

    Each spectrum is picked with the product's default detection threshold and localised
    by the three-point parabola; the shift is voted from the pairwise ``manual -
    automatic`` differences, and matching is greedy nearest neighbour at half the
    automatic spectrum's median line width per nucleus. Detection and refinement are
    untouched and no candidate peaks are swapped in to inflate the match rate.
    """
    from nmrforge_api.peaks import detect_and_localize
    from workflow.pick_peaks import read_spectrum_axes

    auto_axes = read_spectrum_axes(software)
    hand_axes = read_spectrum_axes(manual)
    auto = _spec_from_axes(auto_axes)
    hand = _spec_from_axes(hand_axes)
    auto_rows, auto_meta = detect_and_localize(software, axes=auto_axes)
    hand_rows, hand_meta = detect_and_localize(manual, axes=hand_axes)

    families = [f for f in ("N", "H") if f in auto["axes"] and f in hand["axes"]]
    result: dict = {
        "software_shape": [int(v) for v in auto["data"].shape],
        "manual_shape": [int(v) for v in hand["data"].shape],
        "shared_nuclei": families,
        "peak_table": {
            "threshold_sigma": float(auto_meta.get("sigma_multiplier", 0.0) or 0.0),
            "localization_method": str(auto_meta.get("localization_method", "")),
            "software_peaks": len(auto_rows),
            "manual_peaks": len(hand_rows),
            "software_fallback": int(auto_meta.get("n_fallback", 0) or 0),
            "manual_fallback": int(hand_meta.get("n_fallback", 0) or 0),
        },
    }
    if not families:
        result["error"] = "the two spectra share no alignable nucleus"
        return result

    auto_peak = _main_peak(auto)
    hand_peak = _main_peak(hand)
    result["main_peak_delta_ppm"] = {
        family: abs(auto_peak[family]["ppm"] - hand_peak[family]["ppm"]) for family in families
    }
    result["fwhm_ppm"] = {
        "software": {f: auto_peak[f]["fwhm_ppm"] for f in families},
        "manual": {f: hand_peak[f]["fwhm_ppm"] for f in families},
    }
    result["fwhm_ratio_software_over_manual"] = {
        f: (
            auto_peak[f]["fwhm_ppm"] / hand_peak[f]["fwhm_ppm"]
            if auto_peak[f]["fwhm_ppm"] and hand_peak[f]["fwhm_ppm"]
            else None
        )
        for f in families
    }
    result["_main_peak_ppm"] = {
        "software": {f: auto_peak[f]["ppm"] for f in families},
        "manual": {f: hand_peak[f]["ppm"] for f in families},
        "labels": {f: auto_peak[f]["label"] for f in families},
    }

    tolerances = _tolerances(auto_rows, families)
    auto_points = _table_points(auto_rows, families)
    hand_points = _table_points(hand_rows, families)
    offsets = {
        family: _vote_offset(
            np.asarray([point[family] for point in auto_points], dtype=float),
            np.asarray([point[family] for point in hand_points], dtype=float),
            tolerances[family],
        )
        for family in families
    }
    hand_on_software_scale = [
        {family: point[family] - (offsets.get(family) or 0.0) for family in families}
        for point in hand_points
    ]
    matched_auto = _match_counts(auto_points, hand_on_software_scale, tolerances)
    matched_hand = _match_counts(hand_on_software_scale, auto_points, tolerances)
    result["offset_ppm"] = {
        family: (offsets[family] if offsets[family] is not None else None) for family in families
    }
    result["match"] = {
        "tolerance_ppm": tolerances,
        "software_matched": matched_auto,
        "manual_matched": matched_hand,
        "software_fraction": (matched_auto / len(auto_points)) if auto_points else None,
        "manual_fraction": (matched_hand / len(hand_points)) if hand_points else None,
    }
    return result


def _peak_metrics(spectrum: Path) -> dict:
    """Localisation and line width at the strongest feature (2D or 3D).

    Absolute ppm goes into ``_positions`` and is stripped before printing.
    """
    from core.peaks.localize import localize_peak, ppm_from_point

    spec = _load_spectrum(spectrum)
    data = spec["data"]
    ndim = int(data.ndim)
    index = tuple(int(i) for i in np.unravel_index(int(np.argmax(data)), data.shape))
    axes_ppm = [entry[0] for entry in spec["by_position"]]
    steps = [entry[1] for entry in spec["by_position"]]
    labels = [entry[2] for entry in spec["by_position"]]
    metrics: dict = {
        "shape": [int(value) for value in data.shape],
        "labels": labels,
        "ppm_per_point": [round(value, 8) for value in steps],
        "fwhm_ppm": [
            _fwhm_ppm(_slice_along(data, index, axis), index[axis], steps[axis])
            for axis in range(ndim)
        ],
    }
    positions: dict[str, list[float]] = {}
    for method in ("parabolic", "gaussian"):
        if method == "gaussian" and ndim != 2:
            metrics["gaussian_skipped"] = f"the 2D Gaussian fit does not apply to {ndim}D spectra"
            continue
        kwargs = {"ppm_axes": axes_ppm} if method == "gaussian" else {}
        try:
            result = localize_peak(data, index, method=method, **kwargs)
        except Exception as exc:  # noqa: BLE001 - a failure must be recorded, not hidden
            metrics[f"{method}_error"] = f"{type(exc).__name__}: {exc}"
            continue
        positions[method] = [
            float(ppm_from_point(axes_ppm[axis], result.position[axis])) for axis in range(ndim)
        ]
        metrics[f"{method}_success"] = bool(result.success)
        metrics[f"{method}_fallback"] = bool(result.fallback)
        metrics[f"{method}_actual"] = str(result.actual_method)
        if getattr(result, "reason", ""):
            metrics[f"{method}_reason"] = str(result.reason)
    if len(positions) == 2:
        metrics["parabolic_vs_gaussian_delta_ppm"] = max(
            abs(positions["parabolic"][axis] - positions["gaussian"][axis])
            for axis in range(ndim)
        )
    metrics["_positions"] = positions
    return metrics
def run_once(dataset: Path, root: Path, index: int) -> dict:
    from backend.nmrpipe_backend import NMRPipeBackend
    from core.project import ProjectManager
    from workflow.import_workflow import import_data
    from workflow.stepwise import generate_fid, generate_spectrum

    project_dir = root / f"run_{index:02d}"
    if project_dir.exists():
        shutil.rmtree(project_dir)

    manager = ProjectManager.create_project(project_dir, f"realdata_report_{index:02d}")
    entry = manager.create_experiment(title="real-data aggregate report")

    started = time.perf_counter()
    imported = import_data(manager, entry.id, dataset)
    import_s = time.perf_counter() - started

    backend = NMRPipeBackend()
    started = time.perf_counter()
    generate_fid(manager, entry.id, imported.data_id, backend)
    fid_s = time.perf_counter() - started

    started = time.perf_counter()
    spectrum: Path | None = None
    error = ""
    try:
        spectrum = Path(generate_spectrum(manager, entry.id, imported.data_id, backend))
    except Exception as exc:  # noqa: BLE001 - record the failure instead of only raising
        error = f"{type(exc).__name__}: {exc}"
    spectrum_s = time.perf_counter() - started
    manager.save()

    payload = {
        "import_s": round(import_s, 3),
        "fid_s": round(fid_s, 3),
        "spectrum_s": round(spectrum_s, 3),
        "total_s": round(import_s + fid_s + spectrum_s, 3),
        "spectrum_exists": bool(spectrum and spectrum.is_file()),
        "peak": _peak_metrics(spectrum) if spectrum and spectrum.is_file() else {},
        "_spectrum": str(spectrum) if spectrum else "",
    }
    if error:
        payload["error"] = error
    return payload


def aggregate(runs: list[dict]) -> dict:
    totals = [run["total_s"] for run in runs]
    summary: dict = {
        "repeats": len(runs),
        "total_s_median": round(float(np.median(totals)), 3) if totals else None,
        "total_s_p95": round(_percentile(totals, 0.95), 3) if totals else None,
        "total_s_min": min(totals) if totals else None,
        "total_s_max": max(totals) if totals else None,
    }
    for key in ("import_s", "fid_s", "spectrum_s"):
        values = [run[key] for run in runs if key in run]
        summary[f"{key}_median"] = round(float(np.median(values)), 3) if values else None

    for method in ("parabolic", "gaussian"):
        seen = [
            run["peak"]["_positions"][method]
            for run in runs
            if run.get("peak", {}).get("_positions", {}).get(method)
        ]
        pairwise = 0.0
        for a in range(len(seen)):
            for b in range(a + 1, len(seen)):
                pairwise = max(
                    pairwise,
                    abs(seen[a][0] - seen[b][0]),
                    abs(seen[a][1] - seen[b][1]),
                )
        summary[f"{method}_repeat_max_delta_ppm"] = round(pairwise, 9) if seen else None
        summary[f"{method}_n_ok"] = len(seen)
        summary[f"{method}_success"] = sum(
            1 for run in runs if run.get("peak", {}).get(f"{method}_success")
        )
        summary[f"{method}_fallback"] = sum(
            1 for run in runs if run.get("peak", {}).get(f"{method}_fallback")
        )
        summary[f"{method}_errors"] = sum(
            1 for run in runs if run.get("peak", {}).get(f"{method}_error")
        )

    method_deltas = [
        run["peak"]["parabolic_vs_gaussian_delta_ppm"]
        for run in runs
        if run.get("peak", {}).get("parabolic_vs_gaussian_delta_ppm") is not None
    ]
    summary["parabolic_vs_gaussian_delta_ppm_median"] = (
        round(float(np.median(method_deltas)), 9) if method_deltas else None
    )
    summary["parabolic_vs_gaussian_delta_ppm_max"] = (
        round(max(method_deltas), 9) if method_deltas else None
    )

    series = [
        list(values)
        for values in (run.get("peak", {}).get("fwhm_ppm") for run in runs)
        if values
    ]
    width = max((len(values) for values in series), default=0)
    summary["fwhm_ppm_median"] = [
        (
            round(
                float(np.median([values[axis] for values in series if values[axis]])),
                8,
            )
            if any(axis < len(values) and values[axis] for values in series)
            else None
        )
        for axis in range(width)
    ]
    if width == 2 and series and all(all(values) for values in series):
        summary["fwhm_f2_over_f1_median"] = round(
            float(np.median([values[1] / values[0] for values in series])), 6
        )
    return summary


def _strip_private(node):
    """Drop keys starting with ``_`` (they only exist to compute differences)."""
    if isinstance(node, dict):
        return {k: _strip_private(v) for k, v in node.items() if not str(k).startswith("_")}
    if isinstance(node, list):
        return [_strip_private(item) for item in node]
    return node


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="real-data aggregate report (desensitised)")
    parser.add_argument("--dataset", type=Path, required=True, help="Bruker dataset (not printed)")
    parser.add_argument("--tag", default="dataset", help="anonymous label used instead of the path")
    parser.add_argument("--root", type=Path, default=Path.home() / "realdata-report")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--json", type=Path, default=None, help="write the same JSON to this path")
    parser.add_argument(
        "--manual",
        type=Path,
        default=None,
        help="optional: a manually processed NMRPipe .ft2 for the automatic-vs-manual comparison",
    )
    parser.add_argument(
        "--classify-only",
        action="store_true",
        help="fingerprint and classify only, no processing (useful when choosing a dataset)",
    )
    args = parser.parse_args(argv)

    dataset = args.dataset.expanduser()
    if not dataset.is_dir():
        parser.error("--dataset is not a directory")
    manual = args.manual.expanduser() if args.manual else None
    if manual is not None and not manual.is_file():
        parser.error("--manual is not a file")

    import core
    from core.version import git_commit

    payload: dict = {
        "tag": args.tag,
        "software_version": core.__version__,
        "git_commit": git_commit() or "",
        "python_version": sys.version.split()[0],
        "input": _fingerprint(dataset),
        "classification": _classify(dataset),
    }
    if not args.classify_only:
        root = args.root.expanduser()
        root.mkdir(parents=True, exist_ok=True)
        runs = [run_once(dataset, root, index) for index in range(args.repeats)]
        payload["aggregate"] = aggregate(runs)
        if manual is not None and runs and Path(runs[0]["_spectrum"]).is_file():
            payload["manual_comparison"] = compare_with_manual(
                Path(runs[0]["_spectrum"]), manual
            )
            payload["manual_comparison"]["software_repeat"] = 0
        payload["runs"] = runs

    text = json.dumps(_strip_private(payload), ensure_ascii=False, indent=2, sort_keys=True)
    print(text)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
