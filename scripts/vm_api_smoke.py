"""VM 真机冒烟:用真实 NMRPipe 跑一遍 nmrforge_api 参数扫描(小网格)。

用途:验证对外接口在真实 NMRPipe + 真实 Bruker 数据上的端到端链路
(参考谱/参考脚本冻结 → 参数组合 → 候选谱 → 同一批峰峰位 → 记录),并把
关键结果打印成一行 ``RESULT_JSON`` 便于人工/脚本核对。

用法(VM 上,数据与峰表路径按环境变量或参数给)::

    ~/NMRForge/nmrforge/bin/python scripts/vm_api_smoke.py \
        --data ~/nmr_corpus_work/bmr6980/n15hsqc.fid \
        --peaks ~/nmr_corpus_work/bmr6980/reference_peaks.csv

默认参数轴为 ``window.F1.off = [0.35, 0.45] x zero_fill = [1, 2]``(4 组合)。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from nmrforge_api import run_parameter_study


def _default(name: str, fallback: str) -> str:
    return os.environ.get(name, fallback)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="nmrforge_api VM 真机冒烟(小网格)")
    parser.add_argument(
        "--study",
        default=_default("NMRFORGE_API_STUDY", "/home/<lab-user>/studies/nmrforge_api_smoke"),
        help="研究根目录(会被复用/断点续跑)",
    )
    parser.add_argument("--data", required=True, help="Bruker 原始数据目录")
    parser.add_argument(
        "--peaks",
        default="",
        help="可选:外部参考峰表(.list 或 peak_id,H_ppm,N_ppm CSV);缺省=软件自动选峰",
    )
    parser.add_argument(
        "--axes",
        default='{"window.F1.off": [0.35, 0.45], "zero_fill": [1, 2]}',
        help="参数网格 JSON(点号键)",
    )
    parser.add_argument("--window-pts", type=int, default=3)
    parser.add_argument("--max-runs", type=int, default=8)
    parser.add_argument("--max-peaks", type=int, default=0)
    parser.add_argument("--fresh", action="store_true", help="先删除研究目录再跑")
    args = parser.parse_args(argv)

    study = Path(args.study).expanduser()
    if args.fresh and study.exists():
        import shutil

        shutil.rmtree(study)
    axes = json.loads(args.axes)

    def log(message: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    started = time.time()
    result = run_parameter_study(
        study,
        Path(args.data).expanduser(),
        axes=axes,
        peaks=Path(args.peaks).expanduser() if args.peaks else None,
        max_runs=args.max_runs,
        max_peaks=args.max_peaks,
        window_pts=args.window_pts,
        progress=log,
    )
    payload = {
        "elapsed_s": round(time.time() - started, 1),
        "reference": {
            "spectrum": result.reference.frozen_spectrum,
            "script": result.reference.script_path,
            "script_sha256": result.reference.script_sha256,
            "phase_route": result.reference.phase_route,
            "phase_locked": bool(result.reference.direct_phase),
            "phases": result.reference.direct_phase,
            "sampling": result.reference.sampling,
        },
        "peaks": {
            "path": result.reference.peak_table_path,
            "sha256": result.reference.peak_table_sha256,
            "source": result.reference.peak_source,
            "params": result.reference.peak_params,
            "count": result.reference.peak_count,
        },
        "runs": [
            {
                "run_id": run.run_id,
                "combo": run.combo,
                "status": run.status,
                "measured": len(run.measurements),
                "found": sum(1 for item in run.measurements if item.found),
                "wall_s": run.wall_time_s,
            }
            for run in result.runs
        ],
        "summary": result.summary,
        "records": result.records,
    }
    print("RESULT_JSON " + json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
