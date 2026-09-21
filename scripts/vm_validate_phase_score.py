"""VM phase score +/-5° calibration + 3D NUS gate path product review (0.2.45). Evaluate the margin
of phase score under +/-5° phase error on real data (sampleA 2D uniform, sampleB 3D NUS), and
review gate path product FDTRANSPOSED=0, the main peak position is stable: - 2D uniform:process
+ direct_phase_override candidate by candidate (base phase +/-5° neighborhood); - 3D
NUS:reconstruct_nus once (SMILE guardrail: thread 2) + finalize_nus candidate by candidate
(indirect dimension F2/F1, no rerun SMILE). Output: per candidate phase score/negative
area/entropy/FDTRANSPOSED/main peak, baseline margin, and relative threshold (default 1.0
points, corresponding to confidence log "flat score surface <1 points") judgment."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np


def _metrics(path: Path) -> dict:
    """Phase score + product review indicator (FDTRANSPOSED/main peak)."""
    import nmrglue as ng

    from core.qc import phase_quality

    dic, data = ng.pipe.read(str(path))
    arr = np.asarray(data)
    ph = phase_quality.evaluate(arr)
    idx = np.unravel_index(int(np.argmax(np.abs(arr))), arr.shape)
    return {
        "score": round(float(ph.score), 3),
        "negative_area": round(float(ph.negative_area_fraction), 4),
        "entropy": round(float(ph.entropy), 4),
        "shape": list(arr.shape),
        "FDTRANSPOSED": int(dic.get("FDTRANSPOSED", 0) or 0),
        "main_idx": (int(idx[0]), int(idx[1])),
    }


def _slug(label: str) -> str:
    return (
        label.replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
        .replace(",", "_")
        .replace("=", "_")
        .replace(".", "_")
    )


def _report(label: str, m: dict) -> None:
    print(
        f"  {label:<24} score={m['score']:>7.3f} "
        f"neg={m['negative_area']:>6.4f} ent={m['entropy']:>6.4f} "
        f"FDTRANSPOSED={m['FDTRANSPOSED']} main={m['main_idx']}"
    )


def parse_phases(spec: str) -> dict[str, tuple[float, float]]:
    """Parse a benchmark phase of the form "F2:0,10;F1:0,0"."""
    phases: dict[str, tuple[float, float]] = {}
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        axis, _, pair = part.partition(":")
        p0, p1 = pair.strip().split(",")
        phases[axis.strip()] = (float(p0), float(p1))
    return phases


def perturb_candidates(
    base: dict[str, tuple[float, float]], delta: float = 5.0
) -> list[tuple[str, dict[str, tuple[float, float]]]]:
    """Baseline phase +/-delta neighborhood candidate (axis-wise p0/p1 alone and jointly
    perturbed)."""
    out: list[tuple[str, dict[str, tuple[float, float]]]] = [("base", dict(base))]
    for axis in sorted(base):
        p0, p1 = base[axis]
        for d0 in (-delta, delta):
            cand = dict(base)
            cand[axis] = (p0 + d0, p1)
            out.append((f"{axis} p0={p0 + d0:g}", cand))
        for d1 in (-delta, delta):
            cand = dict(base)
            cand[axis] = (p0, p1 + d1)
            out.append((f"{axis} p1={p1 + d1:g}", cand))
        for d0 in (-delta, delta):
            for d1 in (-delta, delta):
                cand = dict(base)
                cand[axis] = (p0 + d0, p1 + d1)
                out.append((f"{axis} ({p0 + d0:g},{p1 + d1:g})", cand))
    return out


def summarize(
    kind: str,
    base_m: dict,
    rows: list[tuple[str, float, dict]],
    threshold: float,
) -> tuple[bool, float]:
    """Baseline vs +/-5° Neighborhood: Margin = base_score - Neighborhood's highest score."""
    max_neighbor = max(r[2]["score"] for r in rows)
    margin = float(base_m["score"]) - float(max_neighbor)
    passed = margin >= threshold
    fd_values = [base_m["FDTRANSPOSED"]] + [r[2]["FDTRANSPOSED"] for r in rows]
    base_idx = tuple(base_m["main_idx"])
    shifted = [
        r[2]["main_idx"]
        for r in rows
        if any(abs(a - b) > 1 for a, b in zip(r[2]["main_idx"], base_idx))
    ]
    if any(v != 0 for v in fd_values):
        print(f"[{kind}] WARN FDTRANSPOSED Non-zero: {fd_values}")
    else:
        print(f"[{kind}] FDTRANSPOSED=0 All candidates passed")
    if shifted:
        print(
            f"[{kind}] WARN Main peak position drift >1 point: base={base_idx}, "
            f"{sorted(set(shifted))}"
        )
    else:
        print(f"[{kind}] Main peak position is stable (+/-1 point): base={base_idx}")
    verdict = "pass" if passed else "insufficient"
    print(
        f"[{kind}] in conclusion: {verdict} "
        f"(margin {margin:.3f} {'>=' if passed else '<'} threshold {threshold:g})"
    )
    return passed, margin


def run_2d(dataset: Path, root: Path, base_phases: dict, threshold: float) -> None:
    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_dataset
    from core.planning.method_selector import select_method
    from core.project import ProjectManager
    from workflow.import_workflow import import_data

    work = root / "2d"
    if work.exists():
        shutil.rmtree(work)
    manager = ProjectManager.create_project(work / "proj", "calib_2d")
    entry = manager.create_experiment(title="+/-5° calibrated 2D")
    result = import_data(manager, entry.id, dataset)
    backend = NMRPipeBackend(work_dir=str(work / "nmrpipe"))
    exp = read_dataset(
        manager.data_dir(entry.id, result.data_id, "raw")
    )
    plan = select_method(exp)

    def _run(label: str, phases: dict) -> dict:
        resp = backend.process(exp, plan, direct_phase_override=phases)
        if not resp.get("success"):
            raise RuntimeError(f"[2D] {label} process failed: {resp.get('message')}")
        out = work / f"cand_{_slug(label)}.ft2"
        shutil.copy2(resp["spectrum_path"], out)
        return _metrics(out)

    print(f"[2D uniform] datum phase {base_phases}, threshold {threshold:g},+/-5° neighborhood")
    candidates = perturb_candidates(base_phases, delta=5.0)
    base_m = _run("base", base_phases)
    _report("base", base_m)
    rows: list[tuple[str, float, dict]] = []
    for label, phases in candidates[1:]:
        m = _run(label, phases)
        _report(label, m)
        rows.append((label, float(base_m["score"]) - float(m["score"]), m))
    result = summarize("2D", base_m, rows, threshold)
    manager.save()
    return result


def run_3d(dataset: Path, root: Path, base_phases: dict, threshold: float) -> None:
    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_dataset
    from core.project import ProjectManager
    from workflow.import_workflow import import_data

    work = root / "3d"
    if work.exists():
        shutil.rmtree(work)
    manager = ProjectManager.create_project(work / "proj", "calib_3d")
    entry = manager.create_experiment(title="+/-5° Calibration 3D NUS")
    result = import_data(manager, entry.id, dataset)
    backend = NMRPipeBackend(work_dir=str(work / "nmrpipe"))
    exp = read_dataset(
        manager.data_dir(entry.id, result.data_id, "raw")
    )

    resp = backend.reconstruct_nus(exp, {})
    if not resp.get("success"):
        raise RuntimeError(f"[3D] SMILE Reconstruction failed: {resp.get('message')}")
    print(
        "[3D NUS] one SMILE reconstruction finished (default nthread=2 guard rail), finalize_nus "
        "per indirect-dimension candidate"
    )
    base_resp = backend.finalize_nus(
        exp, phases=base_phases, work_dir=str(work / "nmrpipe")
    )
    if not base_resp.get("success"):
        raise RuntimeError(f"[3D] base finalize failed: {base_resp.get('message')}")
    base_out = work / "cand_base.ft3"
    shutil.copy2(base_resp["spectrum_path"], base_out)
    base_m = _metrics(base_out)
    _report("base", base_m)
    rows: list[tuple[str, float, dict]] = []
    for label, phases in perturb_candidates(base_phases, delta=5.0)[1:]:
        r = backend.finalize_nus(exp, phases=phases, work_dir=str(work / "nmrpipe"))
        if not r.get("success"):
            raise RuntimeError(f"[3D] {label} finalize failed: {r.get('message')}")
        out = work / f"cand_{_slug(label)}.ft3"
        shutil.copy2(r["spectrum_path"], out)
        m = _metrics(out)
        _report(label, m)
        rows.append((label, float(base_m["score"]) - float(m["score"]), m))
    result = summarize("3D", base_m, rows, threshold)
    manager.save()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("2d", "3d", "all"))
    parser.add_argument(
        "--dataset-2d",
        type=Path,
        default=Path("/home/<lab-user>/Desktop/data/sampleA"),
    )
    parser.add_argument(
        "--dataset-3d",
        type=Path,
        default=Path("/home/<lab-user>/Desktop/data/sampleB"),
    )
    parser.add_argument("--root", type=Path, default=Path("/home/<lab-user>/val_phase_score"))
    parser.add_argument(
        "--base-2d",
        default="F2:0,10;F1:0,0",
        help="2D benchmark phase (v2 optimisation value, optional calibration points)",
    )
    parser.add_argument(
        "--base-3d",
        default="F2:15,-110;F1:60,-115",
        help="3D NUS datum phase (v2 optimisation value, optional calibration point)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=1.0,
        help="Confidence threshold (current log \"flat scoring surface\" is <1 point)",
    )
    opts = parser.parse_args(argv if argv is not None else sys.argv[1:])
    opts.root.mkdir(parents=True, exist_ok=True)
    passed: list[bool] = []
    if opts.mode in ("2d", "all"):
        passed.append(
            run_2d(
                opts.dataset_2d,
                opts.root,
                parse_phases(opts.base_2d),
                opts.threshold,
            )[0]
        )
    if opts.mode in ("3d", "all"):
        passed.append(
            run_3d(
                opts.dataset_3d,
                opts.root,
                parse_phases(opts.base_3d),
                opts.threshold,
            )[0]
        )
    return 0 if all(passed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
