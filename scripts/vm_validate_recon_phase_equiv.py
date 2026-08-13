"""方案 A 等价性验证(0.2.47):复型重构平面内存内相位评分 vs finalize 真实管线。

对 3D NUS(sampleB)真实数据:reconstruct_nus 一次取复型平面(nus3d_rc),
候选相位网格逐点做:

- 暴力参考:finalize_nus 产实型终谱 → phase_quality(与生产路径一致);
- 内存内 M1:平面数据直接 apply_phase_axis(假设平面为频域)→ phase_quality;
- 内存内 M2:平面数据先沿间接维 FFT(模拟 finalize 的间接维 FT)再
  apply_phase_axis → phase_quality。

输出与暴力参考的 Spearman 相关 + top-match,判断内存内评分是否可用于
方案 A(3D 相位搜索从 ~118 次 finalize 降到内存内秒级评分)。

注意:本脚本属验证工具,按 docs/HANDOVER.md §3 惯例归 Architect 管理。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np


def _phase_quality_score(arr: np.ndarray) -> float:
    from core.qc import phase_quality

    return float(phase_quality.evaluate(np.asarray(arr)).score)


def _read_planes(work: Path) -> np.ndarray:
    from core.data.pipe_io import read_pipe_planes

    planes_dir = work / "nus3d_rc"
    if not planes_dir.is_dir() or not list(planes_dir.glob("test*.ft1")):
        raise FileNotFoundError(f"缺少复型重构平面: {planes_dir}")
    return read_pipe_planes(planes_dir)


def _apply_phases(planes: np.ndarray, phases: dict) -> np.ndarray:
    from core.optimization.phase_search import apply_phase_axis

    work = planes
    # 轴序:read_pipe_planes 堆叠 → axis0=平面索引(F1), axis1/axis2=平面内两轴
    for axis_name, (p0, p1) in phases.items():
        axis = {"F1": 0, "F2": 1, "F3": 2}[axis_name]
        work = apply_phase_axis(work, axis, p0, p1)
    return work


def _memory_scores(
    planes: np.ndarray, candidates: list[tuple[str, dict]]
) -> dict[str, float]:
    """M1:平面直接 apply 相位(假设频域)。"""
    scores: dict[str, float] = {}
    for label, phases in candidates:
        rotated = _apply_phases(planes, phases)
        scores[label] = _phase_quality_score(np.real(rotated))
    return scores


def _memory_fft_scores(
    planes: np.ndarray, candidates: list[tuple[str, dict]]
) -> dict[str, float]:
    """M2:先沿间接维 FFT(模拟 finalize 的 FT)再 apply 相位。"""
    ffted = np.fft.fft(planes, axis=1)
    ffted = np.fft.fft(ffted, axis=0)
    scores: dict[str, float] = {}
    for label, phases in candidates:
        rotated = _apply_phases(ffted, phases)
        scores[label] = _phase_quality_score(np.real(rotated))
    return scores


def _brute_scores(backend, exp, work: Path, candidates) -> dict[str, float]:
    import nmrglue as ng

    scores: dict[str, float] = {}
    for label, phases in candidates:
        resp = backend.finalize_nus(exp, phases=phases, work_dir=str(work))
        if not resp.get("success"):
            print(f"[brute] {label} finalize 失败: {resp.get('message')}")
            continue
        _dic, data = ng.pipe.read(str(resp["spectrum_path"]))
        arr = np.asarray(data)
        if np.iscomplexobj(arr):
            arr = arr.real
        scores[label] = _phase_quality_score(arr)
    return scores


def _spearman(a: list[float], b: list[float]) -> float:
    from scipy.stats import spearmanr

    corr, _p = spearmanr(a, b)
    corr = float(corr)
    return corr if corr == corr else 0.0


def _top_match(brute: dict[str, float], memory: dict[str, float]) -> bool:
    if not brute or not memory:
        return False
    common = [k for k in brute if k in memory]
    if not common:
        return False
    b = max(common, key=lambda k: brute[k])
    m = max(common, key=lambda k: memory[k])
    return b == m


def _compare(kind: str, brute: dict[str, float], memory: dict[str, float]) -> None:
    common = [k for k in brute if k in memory]
    if len(common) < 3:
        print(f"[{kind}] 共同候选不足 3 个,无法比较")
        return
    corr = _spearman([brute[k] for k in common], [memory[k] for k in common])
    top = _top_match(brute, memory)
    print(f"[{kind}] Spearman={corr:.3f}, top-match={top} ({len(common)} 候选)")
    for k in common:
        print(f"    {k:<28} brute={brute[k]:.3f}  memory={memory[k]:.3f}")
    passed = corr >= 0.5 and top
    print(
        f"[{kind}] 结论: {'通过' if passed else '不通过'} "
        f"(相关{'≥' if corr >= 0.5 else '<'}0.5 且 top-match="
        f"{'一致' if top else '不一致'})"
    )


def candidate_grid(
    base: dict[str, tuple[float, float]], span: float = 5.0, step: float = 5.0
) -> list[tuple[str, dict]]:
    """围绕基准相位的 p1 网格 + 全零组合。"""
    out: list[tuple[str, dict]] = []
    p1s = np.arange(base["F2"][1] - span, base["F2"][1] + span + 1e-9, step)
    q1s = np.arange(base["F1"][1] - span, base["F1"][1] + span + 1e-9, step)
    for f2p1 in p1s:
        for f1p1 in q1s:
            phases = {
                "F2": (0.0, float(f2p1)),
                "F1": (0.0, float(f1p1)),
            }
            out.append((f"F2{float(f2p1):g}_F1{float(f1p1):g}", phases))
    out.append(("all_zero", {"F2": (0.0, 0.0), "F1": (0.0, 0.0)}))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("/home/<lab-user>/Desktop/data/sampleB"))
    parser.add_argument("--root", type=Path, default=Path("/home/<lab-user>/val_recon_equiv"))
    opts = parser.parse_args(argv if argv is not None else sys.argv[1:])
    opts.root.mkdir(parents=True, exist_ok=True)

    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_dataset
    from core.project import ProjectManager
    from workflow.import_workflow import import_data

    work = opts.root / "3d"
    if work.exists():
        shutil.rmtree(work)
    manager = ProjectManager.create_project(work / "proj", "recon_equiv")
    entry = manager.create_experiment(title="方案 A 等价性验证")
    result = import_data(manager, entry.id, opts.dataset)
    backend = NMRPipeBackend(work_dir=str(work / "nmrpipe"))
    exp = read_dataset(manager.data_dir(entry.id, result.data_id, "raw"))

    resp = backend.reconstruct_nus(exp, {})
    if not resp.get("success"):
        raise RuntimeError(f"SMILE 重构失败: {resp.get('message')}")
    print(f"[3D NUS] SMILE 重构完成: {resp.get('spectrum_path')}")

    base = {"F2": (0.0, -110.0), "F1": (0.0, -115.0)}  # v2 优化相位附近
    candidates = candidate_grid(base, span=5.0, step=5.0)
    print(f"[3D NUS] 候选 {len(candidates)} 点(基准 {base} ±10°,step 5°,含全零)")

    brute = _brute_scores(backend, exp, work / "nmrpipe", candidates)
    planes = _read_planes(work / "nmrpipe")
    print(f"[3D NUS] 复型平面形状 {planes.shape}(axis0=F1 平面索引, 1=F2, 2=F3)")
    m1 = _memory_scores(planes, candidates)
    m2 = _memory_fft_scores(planes, candidates)
    _compare("M1 平面直接 apply", brute, m1)
    _compare("M2 FFT 后 apply", brute, m2)
    manager.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
