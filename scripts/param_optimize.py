"""NMRForge post-processing parameter optimisation CLI (phase p0/p1 + baseline; only reconstruction
once). NUS is logically consistent with non-NUS: run reconstruction/processing first to get the
final spectrum, and then optimise phase/baseline parameter in memory (does not trigger re-
reconstruction). Usage (VM): # Automatically run reconstruction/processing once, and then
optimisation PYTHONPATH=$HOME/NMRForge python scripts/param_optimize.py <dataset directory >
[More segments...] # Existing final spectrum file, skip reconstruction, direct optimisation
PYTHONPATH=$HOME/NMRForge python scripts/param_optimize.py <dataset directory > --spectrum
out.ft3."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from core.data.bruker_reader import read_dataset, read_segments
from workflow.param_optimize import (
    format_results,
    optimize_post_parameters,
    save_report,
)


def _load_spectrum(path: str):
    import nmrglue as ng

    _dic, data = ng.pipe.read(path)
    return np.asarray(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(
        "Post-processing parameter optimisation (phase/baseline, only reconstruct once)"
    ))
    parser.add_argument("datasets", nargs="+", help=(
        "Bruker dataset directory (multiple=multiple experiments)"
    ))
    parser.add_argument("--spectrum", default="", help=(
        "Already have final spectrum (ft2/ft3), skip reconstruction and directly optimise"
    ))
    parser.add_argument("--ext-lo", default="9.0", help=(
        "1H extraction window low ppm (default 9.0)"
    ))
    parser.add_argument("--ext-hi", default="7.5", help=(
        "1H extraction window height ppm (default 7.5)"
    ))
    parser.add_argument("--nthread", type=int, default=2, help=(
        "SMILE Number of threads (default 2)"
    ))
    parser.add_argument("--work-dir", default="", help=(
        "Working directory (default dataset sibling <id>.nmrpipe)"
    ))
    parser.add_argument("--out", default="", help=(
        "JSON Report output path (default dataset sibling)"
    ))
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.datasets]
    experiment = read_segments(paths) if len(paths) > 1 else read_dataset(paths[0])

    if args.spectrum:
        print(f"Use existing final spectrum: {args.spectrum}")
        data = _load_spectrum(args.spectrum)
    else:
        from backend.nmrpipe_backend import NMRPipeBackend

        backend = NMRPipeBackend(work_dir=args.work_dir)
        health = backend.health_check()
        if not health["ok"]:
            print(f"Error: {health['message']}", file=sys.stderr)
            return 1
        print(
            f"experiment: {experiment.experiment_type.name} {experiment.ndim}D "
            f"{experiment.sampling.mode.value} -- reconstruct/process once first"
        )
        if experiment.sampling.mode.value == "nus":
            resp = backend.reconstruct_nus(
                experiment,
                {"ext_lo": args.ext_lo, "ext_hi": args.ext_hi, "nthread": args.nthread},
            )
        else:
            from core.planning.method_selector import select_method

            resp = backend.process(experiment, select_method(experiment))
        if not resp.get("success"):
            print(f"Error: {resp.get('message')}", file=sys.stderr)
            return 1
        print(f"final spectrum: {resp['spectrum_path']}")
        data = _load_spectrum(resp["spectrum_path"])

    print(f"Spectral shape: {data.shape} -- In-memory optimisation phase/baseline (no refactoring)")

    def _progress(index: int, total: int, label: str) -> None:
        print(f"[{index}/{total}] run {label}", flush=True)

    def _on_result(result) -> None:
        print(
            f"  → {result.decision} {result.overall:.1f} "
            f"(p0={result.params.get('p0')}, p1={result.params.get('p1')}, "
            f"base={result.params.get('baseline_order')})",
            flush=True,
        )

    # The experiment type is known (the dataset is required): score by the template
    # convention (single-sign spectrum = positive peaks, mixed = both signs), the same
    # convention the in-pipeline report and peak picking use.
    from workflow.phase_routes import _sign_mode

    qc_sign_mode = _sign_mode(experiment)
    print(f"Quality-score sign convention: {qc_sign_mode}")
    results = optimize_post_parameters(
        data, sign_mode=qc_sign_mode, progress=_progress, on_result=_on_result
    )
    print()
    print(format_results(results))

    out = Path(args.out) if args.out else paths[0].parent / "param_optimize_report.json"
    save_report(results, out)
    print(f"Report saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
