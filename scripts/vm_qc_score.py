"""Score one spectrum's QC (real machine), with exactly the product pipeline's convention.

Section 3 of the evidence page relies on this: the automatic final spectrum and the **processed
spectrum
shipped with the data** are each scored once, through the same
``core.qc.spectrum_quality.evaluate`` and
the same ``sign_mode="auto"`` (judge "single-sign positive / single-sign negative / both signs"
first,
then score).

Usage::

    nmrforge/bin/python scripts/vm_qc_score.py \
        --spectrum <automatic final spectrum.ft2> --label "automatic processing" --h-window 6.5,10.5
    nmrforge/bin/python scripts/vm_qc_score.py \
        --spectrum <pdata/1 directory> --label "processed spectrum shipped with the data" \
        --h-window 6.5,10.5

``--h-window`` crops the 1H axis to the given window before scoring: without it the automatic
spectrum
covers only a few ppm of 1H while the shipped spectrum spans the whole 1H range (including the water
region), so the two scores are not comparable. The window **crops 1H only; 15N is untouched**.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _read_pdata(path: Path) -> tuple[np.ndarray, list[np.ndarray]]:
    """Read Bruker ``pdata/1``: return (data, ppm axis per dimension)."""
    import nmrglue as ng

    _dic, data = ng.bruker.read_pdata(str(path))
    udic = ng.bruker.guess_udic(_dic, data)
    axes = []
    for index in range(np.ndim(data)):
        try:
            converter = ng.fileiobase.uc_from_udic(udic, dim=index)
        except TypeError:  # older nmrglue versions return a list per dimension
            converter = ng.fileiobase.uc_from_udic(udic)[index]
        axes.append(np.asarray(converter.ppm_scale(), dtype=float))
    return np.asarray(data, dtype=float).real, axes


def _h_index(axes: list[np.ndarray]) -> int:
    """Which dimension is 1H: proton ppm values all sit within 20 ppm (15N is 90-140)."""
    scores = [float(np.median(np.abs(axis))) for axis in axes]
    return int(np.argmin(scores))


def _load(path: Path, h_window: tuple[float, float] | None) -> tuple[np.ndarray, dict]:
    """Read a spectrum and optionally crop the 1H axis; files use the product reader, directories
    ``pdata/1``."""
    meta: dict = {"input": path.name, "kind": "file"}
    if path.is_dir():
        data, axes = _read_pdata(path)
        meta["kind"] = "bruker-pdata"
        index = _h_index(axes)
        ppm = axes[index]
    else:
        from workflow.pick_peaks import read_spectrum_axes

        loaded = read_spectrum_axes(path)
        data = np.asarray(loaded.data, dtype=float)
        index = loaded.storage_of("1H")
        if index is None:
            raise SystemExit("this spectrum has no 1H axis")
        ppm = np.asarray(loaded.ppm[index], dtype=float)

    meta["shape"] = [int(item) for item in data.shape]
    meta["H_span_ppm"] = [round(float(np.min(ppm)), 4), round(float(np.max(ppm)), 4)]
    if h_window is not None:
        keep = np.where((ppm >= h_window[0]) & (ppm <= h_window[1]))[0]
        if keep.size < 8:
            raise SystemExit("too few points in that 1H window - wrong window?")
        data = np.take(data, keep, axis=index)
        kept = ppm[keep]
        meta["shape"] = [int(item) for item in data.shape]
        meta["H_span_ppm"] = [round(float(np.min(kept)), 4), round(float(np.max(kept)), 4)]
    return np.asarray(data, dtype=float), meta


def _quality(data: np.ndarray) -> dict:
    """Overall spectrum quality score, same convention as
    ``scripts/vm_truth_benchmark.py::_quality``."""
    from core.qc.spectrum_quality import evaluate as evaluate_quality

    result = evaluate_quality(np.asarray(data, dtype=float), sign_mode="auto")
    payload = dataclasses.asdict(result.score)
    payload["decision"] = str(result.decision)
    payload["reasons"] = [str(item) for item in result.reasons][:3]
    return json.loads(json.dumps(payload, default=float))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="score one spectrum's QC (same convention as the product)"
    )
    parser.add_argument(
        "--spectrum", required=True, help="spectrum file, or a Bruker pdata/1 directory"
    )
    parser.add_argument("--label", default="", help="name used in the report")
    parser.add_argument(
        "--h-window", default="", help="optional 1H crop window lo,hi (ppm); crops 1H only"
    )
    parser.add_argument("--json", default="", help="optional JSON output path")
    args = parser.parse_args(argv)

    h_window = None
    if args.h_window:
        parts = [item.strip() for item in args.h_window.split(",")]
        if len(parts) != 2:
            raise SystemExit("--h-window needs two parts, lo,hi")
        h_window = (float(parts[0]), float(parts[1]))

    data, meta = _load(Path(args.spectrum), h_window)
    report = {
        "label": args.label,
        **meta,
        "h_window": list(h_window) if h_window else None,
        "quality": _quality(data),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(text)
    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
