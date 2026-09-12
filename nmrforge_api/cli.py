"""命令行入口:``python -m nmrforge_api <命令>``。

命令(全部无 GUI、可在集群上跑):

    init       建研究并导入数据集
    reference  跑自动优化,冻结参考谱/参考脚本
    peaks      在参考谱上选峰(或登记外部峰表)
    sweep      按网格 YAML/JSON 扫描参数并落盘记录
    report     用已有 run.json 重算汇总(不重跑处理)
    status     打印研究现状

网格文件格式(YAML 或 JSON)::

    axes:
      zero_fill: [1, 2, 4]
      "window.F1.off": [0.35, 0.45, 0.55]
    max_runs: 128
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from nmrforge_api.errors import SensitivityError
from nmrforge_api.peaks import pick_reference_peaks
from nmrforge_api.records import write_records
from nmrforge_api.reference import build_reference, load_reference, set_reference_peaks
from nmrforge_api.session import add_dataset, open_study
from nmrforge_api.study import reference_peaks
from nmrforge_api.sweep import (
    DEFAULT_MAX_RUNS,
    load_plan,
    load_runs,
    plan_sweep,
    run_sweep,
)
from nmrforge_api.uncertainty import (
    DEFAULT_CSP_N_WEIGHT,
    position_uncertainty,
    uncertainty_summary,
)


def _load_mapping(path: Path | str) -> dict[str, Any]:
    import yaml

    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise SensitivityError(f"文件读不到: {source} ({exc})") from exc
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise SensitivityError(f"文件不是键值表(YAML/JSON): {source}")
    return data


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_init(args: argparse.Namespace) -> int:
    session = open_study(args.study, name=args.name)
    if args.dataset:
        dataset = add_dataset(session, args.dataset, title=args.name or args.title)
        _print({"study": str(session.root), "dataset": dataset.to_dict()})
    else:
        _print({"study": str(session.root), "dataset": session.dataset.to_dict()
                if session.dataset else None})
    return 0


def cmd_reference(args: argparse.Namespace) -> int:
    session = open_study(args.study, name=args.name)
    params = _load_mapping(args.params) if args.params else None
    reference = build_reference(
        session,
        params=params,
        phase_route=args.phase_route,
        progress=print,
        force=args.force,
    )
    _print(
        {
            "spectrum": reference.frozen_spectrum,
            "script": reference.script_path,
            "script_sha256": reference.script_sha256,
            "phase_route": reference.phase_route,
            "sampling": reference.sampling,
            "sweep_supported": reference.sweep_supported,
        }
    )
    return 0


def cmd_peaks(args: argparse.Namespace) -> int:
    session = open_study(args.study, name=args.name)
    reference = load_reference(session)
    if args.peak_table:
        target = session.reference_dir_for() / "reference.list"
        target.write_text(
            Path(args.peak_table).read_text(encoding="utf-8"), encoding="utf-8"
        )
        reference = set_reference_peaks(session, target, reference)
    else:
        peak_path = pick_reference_peaks(
            session,
            sigma_multiplier=args.sigma,
            out_path=session.reference_dir_for() / "reference.list",
        )
        reference = set_reference_peaks(session, peak_path, reference)
    peaks = reference_peaks(session, reference)
    _print({"peak_table": reference.peak_table_path, "count": len(peaks)})
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    session = open_study(args.study, name=args.name)
    reference = load_reference(session)
    config = _load_mapping(args.grid)
    axes = config.get("axes")
    if not isinstance(axes, dict) or not axes:
        raise SensitivityError(f"网格文件缺少 axes: {args.grid}")
    max_runs = int(config.get("max_runs", args.max_runs or DEFAULT_MAX_RUNS))
    base = config.get("base_params")
    plan = plan_sweep(
        reference,
        axes=axes,
        max_runs=max_runs,
        base_params=base if isinstance(base, dict) else None,
    )
    runs = run_sweep(
        session,
        plan,
        reference=reference,
        window_pts=args.window_pts,
        resume=not args.no_resume,
        progress=print,
    )
    uncertainties = position_uncertainty(runs, csp_n_weight=args.csp_n_weight)
    summary = uncertainty_summary(
        uncertainties,
        csp_n_weight=args.csp_n_weight,
        n_runs=sum(1 for r in runs if r.status == "success"),
    )
    records = write_records(
        session,
        reference=reference,
        plan=plan,
        runs=runs,
        uncertainties=uncertainties,
        summary=summary,
        peaks=reference_peaks(session, reference),
    )
    _print({"runs": len(runs), "summary": summary, "records": records})
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    session = open_study(args.study, name=args.name)
    reference = load_reference(session)
    plan = load_plan(session)
    if plan is None:
        raise SensitivityError("找不到扫描计划(records/sweep_plan.json)")
    runs = load_runs(session)
    uncertainties = position_uncertainty(runs, csp_n_weight=args.csp_n_weight)
    summary = uncertainty_summary(
        uncertainties,
        csp_n_weight=args.csp_n_weight,
        n_runs=sum(1 for r in runs if r.status == "success"),
    )
    records = write_records(
        session,
        reference=reference,
        plan=plan,
        runs=runs,
        uncertainties=uncertainties,
        summary=summary,
        peaks=reference_peaks(session, reference),
    )
    _print({"runs": len(runs), "summary": summary, "records": records})
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    session = open_study(args.study, name=args.name)
    reference = load_reference(session)
    plan = load_plan(session)
    runs = load_runs(session)
    _print(
        {
            "study": str(session.root),
            "dataset": session.dataset.to_dict() if session.dataset else None,
            "reference": reference.to_dict() if reference else None,
            "n_combos": plan.n_combos if plan else 0,
            "runs": {
                "total": len(runs),
                "success": sum(1 for r in runs if r.status == "success"),
                "failed": sum(1 for r in runs if r.status != "success"),
            },
            "records_dir": str(session.records_dir),
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nmrforge_api",
        description="NMRForge 参数敏感性研究接口(无 GUI 命令行)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def _common(handler: argparse.ArgumentParser) -> None:
        handler.add_argument("--study", required=True, help="研究根目录")
        handler.add_argument("--name", default="", help="新建研究时的项目名")

    init = sub.add_parser("init", help="建研究并导入 Bruker 数据集")
    _common(init)
    init.add_argument("--dataset", help="公开库下载并解压后的 Bruker 目录")
    init.add_argument("--title", default="", help="实验标题")
    init.set_defaults(func=cmd_init)

    reference = sub.add_parser("reference", help="生成并冻结参考谱/参考脚本")
    _common(reference)
    reference.add_argument("--params", help="自动处理的输入参数(YAML/JSON)")
    reference.add_argument("--phase-route", default=None)
    reference.add_argument("--force", action="store_true", help="重建参考谱")
    reference.set_defaults(func=cmd_reference)

    peaks = sub.add_parser("peaks", help="参考峰表:选峰或登记外部峰表")
    _common(peaks)
    peaks.add_argument("--sigma", type=float, default=None, help="选峰阈值(σ 倍数)")
    peaks.add_argument("--peak-table", default="", help="外部峰表路径(.list)")
    peaks.set_defaults(func=cmd_peaks)

    sweep = sub.add_parser("sweep", help="按网格扫描参数并落盘记录")
    _common(sweep)
    sweep.add_argument("--grid", required=True, help="网格 YAML/JSON")
    sweep.add_argument("--max-runs", type=int, default=0)
    sweep.add_argument("--window-pts", type=int, default=3)
    sweep.add_argument("--csp-n-weight", type=float, default=DEFAULT_CSP_N_WEIGHT)
    sweep.add_argument("--no-resume", action="store_true", help="不跳过已完成组合")
    sweep.set_defaults(func=cmd_sweep)

    report = sub.add_parser("report", help="用已有 run.json 重算汇总")
    _common(report)
    report.add_argument("--csp-n-weight", type=float, default=DEFAULT_CSP_N_WEIGHT)
    report.set_defaults(func=cmd_report)

    status = sub.add_parser("status", help="打印研究现状")
    _common(status)
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except SensitivityError as exc:
        print(f"错误: {exc}")
        return 2


__all__ = ["build_parser", "main"]
