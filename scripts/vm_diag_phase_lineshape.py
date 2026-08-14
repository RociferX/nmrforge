"""VM 相位优化峰形诊断(0.2.63):三步处理 → 一维迹线吸收/色散量化 → 优化对比。

用户反馈:一维谱峰形处于「吸收 + 色散中间状态」,相位优化疑似未生效。
本脚本对真实数据:
1. import → generate_fid → generate_spectrum(默认自动直接维相位);
2. 读终谱,提取最强峰处的 F2/F1 一维迹线,量化峰形:
   - neg_frac:峰窗内负值面积占比(纯吸收≈0,色散大);
   - asymmetry:峰顶左右负瓣不对称(纯吸收≈0,色散显著);
3. 跑 optimize_phase_brute_force(完整相位优化),输出相位与门控日志;
4. 优化后谱再提取迹线对比峰形。

注意:本脚本为诊断工具,按 docs/HANDOVER.md §3 惯例归 Architect 管理。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np


def _read(path: Path):
    import nmrglue as ng

    return ng.pipe.read(str(path))


def _lineshape(trace: np.ndarray, radius: int = 8) -> dict:
    """一维迹线峰形量化:峰窗内负值占比 + 左右负瓣不对称。"""
    arr = np.asarray(trace, dtype=float)
    if arr.size < 4:
        return {"neg_frac": 0.0, "asymmetry": 0.0, "peak": 0}
    peak = int(np.argmax(np.abs(arr)))
    lo = max(0, peak - radius)
    hi = min(len(arr), peak + radius + 1)
    win = arr[lo:hi]
    total_abs = float(np.sum(np.abs(win))) + 1e-12
    neg = -float(np.sum(np.minimum(win, 0.0)))
    left = arr[max(0, peak - radius):peak]
    right = arr[peak + 1:min(len(arr), peak + radius + 1)]
    neg_left = -float(np.sum(np.minimum(left, 0.0)))
    neg_right = -float(np.sum(np.minimum(right, 0.0)))
    asym = (neg_left - neg_right) / (neg_left + neg_right + 1e-12)
    return {
        "peak": int(peak),
        "neg_frac": float(neg / total_abs),
        "asymmetry": float(asym),
        "peak_value": float(arr[peak]),
    }


def _metrics(path: Path, ndim: int) -> dict:
    _dic, data = _read(path)
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        arr = arr.real
    idx = np.unravel_index(int(np.argmax(np.abs(arr))), arr.shape)
    out: dict = {"shape": list(arr.shape), "main_idx": tuple(int(i) for i in idx)}
    if ndim == 2:
        f1_idx, f2_idx = int(idx[0]), int(idx[1])
        trace_f2 = arr[f1_idx, :]  # 沿 F2(直接维 1H)
        trace_f1 = arr[:, f2_idx]  # 沿 F1(间接维 15N)
        out["F2_lineshape"] = _lineshape(trace_f2)
        out["F1_lineshape"] = _lineshape(trace_f1)
        # 打印迹线窗口(峰顶 ±8 点,便于人工判断)
        out["F2_trace_window"] = [
            round(float(v), 3) for v in trace_f2[max(0, f2_idx - 8): f2_idx + 9]
        ]
        out["F1_trace_window"] = [
            round(float(v), 3) for v in trace_f1[max(0, f1_idx - 8): f1_idx + 9]
        ]
    elif ndim == 3:
        f1_idx, f2_idx, f3_idx = (int(i) for i in idx)
        trace_f3 = arr[f1_idx, f2_idx, :]
        out["F3_lineshape"] = _lineshape(trace_f3)
        out["F3_trace_window"] = [
            round(float(v), 3) for v in trace_f3[max(0, f3_idx - 8): f3_idx + 9]
        ]
    return out


def _fmt(m: dict) -> str:
    parts = []
    for key in ("F2_lineshape", "F1_lineshape", "F3_lineshape"):
        if key in m:
            ls = m[key]
            parts.append(
                f"{key.replace('_lineshape', '')}: neg_frac={ls['neg_frac']:.3f} "
                f"asym={ls['asymmetry']:.2f} peak={ls['peak']}"
            )
    return "; ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("/home/<lab-user>/Desktop/data/sampleA"),
        help="Bruker 数据集目录(默认 sampleA)",
    )
    parser.add_argument("--root", type=Path, default=Path("/home/<lab-user>/val_phase_lineshape"))
    parser.add_argument(
        "--skip-optimize",
        action="store_true",
        help="只做三步处理 + 峰形量化,不跑相位优化(批量筛查用)",
    )
    opts = parser.parse_args(argv if argv is not None else sys.argv[1:])
    opts.root.mkdir(parents=True, exist_ok=True)

    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_dataset
    from core.project import ProjectManager
    from workflow.import_workflow import import_data
    from workflow.stepwise import generate_fid, generate_spectrum, optimize_phase_brute_force

    work = opts.root / "proj"
    if work.exists():
        shutil.rmtree(work)
    manager = ProjectManager.create_project(work, "diag")
    entry = manager.create_experiment(title="峰形诊断")
    result = import_data(manager, entry.id, opts.dataset)
    exp = read_dataset(manager.data_dir(entry.id, result.data_id, "raw"))
    print(f"[diag] 数据集 {opts.dataset}: ndim={exp.ndim} "
          f"sampling={exp.sampling.mode.value} "
          f"axes={[d.logical_axis + ':' + (d.nucleus or '') for d in exp.dimensions]}")
    backend = NMRPipeBackend()

    generate_fid(manager, entry.id, result.data_id, backend)
    spec_before = generate_spectrum(manager, entry.id, result.data_id, backend)
    print(f"[diag] 初始谱(自动直接维相位): {spec_before}")
    m_before = _metrics(Path(spec_before), exp.ndim)
    print(f"[diag] 初始峰形: {_fmt(m_before)}")
    if "F2_trace_window" in m_before:
        print(f"[diag]   F2 峰窗(±8): {m_before['F2_trace_window']}")
        print(f"[diag]   F1 峰窗(±8): {m_before['F1_trace_window']}")
    if "F3_trace_window" in m_before:
        print(f"[diag]   F3 峰窗(±8): {m_before['F3_trace_window']}")

    if opts.skip_optimize:
        print("[diag] --skip-optimize:跳过相位优化")
        manager.save()
        return 0
    out = optimize_phase_brute_force(manager, entry.id, result.data_id, backend)
    print(f"[diag] 相位优化: phases={out['phase']} "
          f"backend_runs={out['backend_runs']} optimized={out['optimized']}")
    for line in out["logs"]:
        if any(k in line for k in ("平坦", "回退", "复核", "联合", "可复现")):
            print(f"[diag]   log: {line}")
    print(f"[diag] 优化后谱: {out['spectrum_path']}")
    m_after = _metrics(Path(out["spectrum_path"]), exp.ndim)
    print(f"[diag] 优化后峰形: {_fmt(m_after)}")
    if "F2_trace_window" in m_after:
        print(f"[diag]   F2 峰窗(±8): {m_after['F2_trace_window']}")
        print(f"[diag]   F1 峰窗(±8): {m_after['F1_trace_window']}")
    if "F3_trace_window" in m_after:
        print(f"[diag]   F3 峰窗(±8): {m_after['F3_trace_window']}")
    manager.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
