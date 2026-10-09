"""Score evidence spectra with the product QC function and the explicit auto sign strategy.

Both spectra use core.qc.spectrum_quality.evaluate and sign_mode=auto.
This is not the GUI experiment-template strategy: reconstruction artefacts may be classified by
auto as mixed-sign, while the GUI's HSQC/HNCO templates use uniform. Scores cannot replace GUI
decisions or scientific validation.

Usage:

    nmrforge/bin/python scripts/vm_qc_score.py \
        --spectrum <automatic final spectrum.ft2> --label "automatic processing" --h-window 6.5,10.5
    nmrforge/bin/python scripts/vm_qc_score.py \
        --spectrum <pdata/1 directory> --label "processed spectrum shipped with the data" \
        --h-window 6.5,10.5

--reference crops each identified nucleus axis to the common window of both spectra and scores
each with the other as its reference. --h-window can further limit the proton window. Signs are
preserved, so phase errors are not hidden by taking absolute values. A shared window is not
independent accuracy validation; resolution, processing history, and sample conditions can still
affect the scores.
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


def _load(
    path: Path, h_window: tuple[float, float] | None, reference: Path | None = None,
) -> tuple[np.ndarray, dict]:
    """Crop all axes to their common windows using nucleus labels; do not infer ambiguous labels."""
    from scripts.vm_four_path_figure import _load as read
    from scripts.vm_four_path_figure import _window

    loaded = read(path)
    data, axes, nuclei = loaded["data"], loaded["ppm"], loaded["nuclei"]
    if "H" not in nuclei or len(set(nuclei)) != len(nuclei):
        raise SystemExit("unique explicit nuclei including 1H are required")
    if not np.all(np.isfinite(data)):
        raise SystemExit("non-finite spectrum cannot be used as evidence")
    other = read(reference) if reference is not None else None
    if other is not None and (
        len(set(other["nuclei"])) != len(other["nuclei"])
        or set(other["nuclei"]) != set(nuclei)
    ):
        raise SystemExit("reference must have the same unique nuclear axes")
    meta: dict = {
        "input": path.name, "kind": "bruker-pdata" if path.is_dir() else "file",
        "original_shape": list(data.shape), "nuclei": nuclei, "windows_ppm": {},
        "comparison_input": reference.name if reference is not None else None,
    }
    for index, (nucleus, ppm) in enumerate(zip(nuclei, axes, strict=True)):
        lo, hi = float(np.min(ppm)), float(np.max(ppm))
        if other is not None:
            lo, hi = _window(ppm, other["ppm"][other["nuclei"].index(nucleus)])
        if nucleus == "H" and h_window is not None:
            lo, hi = max(lo, h_window[0]), min(hi, h_window[1])
        keep = np.where((ppm >= lo) & (ppm <= hi))[0]
        if keep.size < 8:
            raise SystemExit(f"common {nucleus} window has fewer than eight points")
        data = np.take(data, keep, axis=index)
        meta["windows_ppm"][nucleus] = [lo, hi]
        if nucleus == "H":
            meta["H_span_ppm"] = [float(np.min(ppm[keep])), float(np.max(ppm[keep]))]
    meta["shape"] = list(data.shape)
    return np.asarray(data, dtype=float), meta


def _quality(data: np.ndarray) -> dict:
    """Calculate overall spectrum quality using the truth benchmark's quality-score convention."""
    from core.qc.spectrum_quality import evaluate as evaluate_quality

    result = evaluate_quality(np.asarray(data, dtype=float), sign_mode="auto")
    payload = dataclasses.asdict(result.score)
    payload["decision"] = str(result.decision)
    payload["reasons"] = [str(item) for item in result.reasons][:3]
    return json.loads(json.dumps(payload, default=float))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="给证据谱打 QC 分(显式 auto 符号策略)")
    parser.add_argument("--spectrum", required=True, help="谱文件,或 Bruker pdata/1 目录")
    parser.add_argument("--label", default="", help="报告里用的名字")
    parser.add_argument("--reference", type=Path, help="比较谱;所有核轴裁到共同窗口")
    parser.add_argument("--h-window", default="", help="可选的 1H 裁剪窗口 lo,hi(ppm);只裁 1H")
    parser.add_argument("--json", default="", help="可选的 JSON 输出路径")
    args = parser.parse_args(argv)

    h_window = None
    if args.h_window:
        parts = [item.strip() for item in args.h_window.split(",")]
        if len(parts) != 2:
            raise SystemExit("--h-window 需要 lo,hi 两段")
        h_window = (float(parts[0]), float(parts[1]))
        if not all(np.isfinite(h_window)) or not h_window[0] < h_window[1]:
            parser.error("--h-window must contain finite increasing bounds")

    data, meta = _load(Path(args.spectrum), h_window, args.reference)
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
