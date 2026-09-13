"""VM 真机冒烟:用真实 NMRPipe 跑一遍 nmrforge_api(参考 + 参数组合)。

用途:验证对外接口在真实 NMRPipe + 真实 Bruker 数据上的端到端链路
(参考谱/参考脚本冻结 → 两张参考峰表 → 参数组合 → 候选谱 → 两种定位 →
 两张统一峰表 → records),并把关键结果打印成一行 ``RESULT_JSON``。

用法(VM 上,数据路径按环境变量或参数给)::

    ~/NMRForge/nmrforge/bin/python scripts/vm_api_smoke.py \
        --data ~/nmr_corpus_work/bmr6980/n15hsqc.fid \
        --peaks ~/nmr_corpus_work/bmr6980/reference_peaks.csv

默认参数组合为 ``window.F1.off = [0.35, 0.45] × zero_fill = [1, 2]``(4 个
workflow)。两条件(A/B)示例::

    ... scripts/vm_api_smoke.py --fresh \
        --data-a <apo 目录> --data-b <holo 目录> \
        --combos combos.csv

NUS 2D 数据同理,把轴换成 SMILE 参数即可::

    ... scripts/vm_api_smoke.py --fresh --data <2D NUS 目录> \
        --axes '{"nsigma": [3, 5, 7], "thresh": [0.95]}'
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from nmrforge_api import load_combo_table, run_parameter_study


def _default(name: str, fallback: str) -> str:
    return os.environ.get(name, fallback)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="nmrforge_api VM 真机冒烟(参考 + 小批量 workflow)"
    )
    parser.add_argument(
        "--study",
        default=_default("NMRFORGE_API_STUDY", "/home/<lab-user>/studies/nmrforge_api_smoke"),
        help="研究根目录(会被复用/断点续跑)",
    )
    parser.add_argument("--data", default="", help="条件 A 的 Bruker 原始数据目录")
    parser.add_argument("--data-a", default="", help="条件 A(与 --data 等价)")
    parser.add_argument("--data-b", default="", help="条件 B(可选,两条件同参数)")
    parser.add_argument(
        "--peaks",
        default="",
        help="可选:外部参考峰表(.list 或 peak_id,H_ppm,N_ppm CSV);缺省=自动选峰",
    )
    parser.add_argument(
        "--axes",
        default='{"window.F1.off": [0.35, 0.45], "zero_fill": [1, 2]}',
        help="参数网格 JSON(点号键);与 --combos 二选一",
    )
    parser.add_argument(
        "--combos",
        default="",
        help="显式组合表 CSV/TSV/YAML/JSON(与 --axes 二选一)",
    )
    parser.add_argument(
        "--window-ppm",
        type=float,
        default=None,
        help="峰位搜索窗口半径(ppm);缺省=1.5×该轴核素线宽折算 ppm",
    )
    parser.add_argument(
        "--window-pts",
        type=int,
        default=None,
        help="峰位搜索窗口半径(数据点;显式口径,跨分辨率不可比,不推荐)",
    )
    parser.add_argument("--max-runs", type=int, default=8)
    parser.add_argument("--max-peaks", type=int, default=0)
    parser.add_argument("--fresh", action="store_true", help="先删除研究目录再跑")
    args = parser.parse_args(argv)

    source_a = args.data_a or args.data
    if not source_a:
        parser.error("必须给 --data(或 --data-a)")
    datasets = {"A": source_a}
    if args.data_b:
        datasets["B"] = args.data_b

    kwargs: dict = {"datasets": datasets, "max_runs": args.max_runs}
    if args.combos:
        kwargs["combos"] = load_combo_table(args.combos)
    else:
        kwargs["axes"] = json.loads(args.axes)

    study = Path(args.study).expanduser()
    if args.fresh and study.exists():
        import shutil

        shutil.rmtree(study)

    def log(message: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    started = time.time()
    result = run_parameter_study(
        study,
        peaks=Path(args.peaks).expanduser() if args.peaks else None,
        max_peaks=args.max_peaks,
        window_pts=args.window_pts,
        window_ppm=args.window_ppm,
        progress=log,
        **kwargs,
    )
    payload = {
        "elapsed_s": round(time.time() - started, 1),
        "references": [
            {
                "condition": reference.condition,
                "dataset": reference.dataset_key,
                "spectrum": reference.frozen_spectrum,
                "script": reference.script_path,
                "script_sha256": reference.script_sha256,
                "phase_route": reference.phase_route,
                "phases": reference.direct_phase,
                "phase_record": reference.phase_record(),
                "sampling": reference.sampling,
                "peak_list": reference.peak_table_path,
                "peak_count": reference.peak_count,
                "peak_source": reference.peak_source,
                "peak_tables": reference.peak_tables,
                "peak_localization": reference.peak_localization,
            }
            for reference in result.references.values()
        ],
        "workflows": [
            {
                "workflow_id": run.workflow_id,
                "condition": run.condition,
                "dataset": run.dataset,
                "parameters_requested": run.parameters_requested,
                "parameters_resolved": run.parameters_resolved,
                "status": run.status,
                "warnings": [warning.get("code") for warning in run.warnings],
                "script_sha256": run.script_sha256,
                "spectrum_sha256": run.spectrum_sha256,
                "peak_tables": run.peak_tables,
                "peak_localization": run.peak_localization,
                "wall_s": run.wall_time_s,
                "window": run.window,
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
