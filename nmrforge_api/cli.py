"""命令行入口:``python -m nmrforge_api <命令>``(全部无 GUI、可在集群上跑)。

命令::

    init         建研究并导入数据集(多条件用 --condition A / B)
    reference    跑自动优化,冻结参考谱/参考脚本(每个条件一份)
    peaks        参考峰表:自动选峰或登记外部峰表,并生成两张参考峰表
    sweep        按参数组合表批量执行 workflow(别名 workflows)
    report       用已有 run.json/workflow.json 重算汇总(不重跑处理)
    status       打印研究现状(条件/参考/workflow 状态)

参数组合表(YAML/JSON 的 ``axes`` 便捷入口,或 CSV/YAML 显式组合表)::

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
from nmrforge_api.records import write_records, write_reference_records
from nmrforge_api.reference import (
    build_reference,
    ensure_reference_peaks,
    load_reference,
    load_references,
    set_reference_peaks,
)
from nmrforge_api.session import add_dataset, open_study
from nmrforge_api.study import (
    reference_peaks,
    run_combination_study,
)
from nmrforge_api.sweep import (
    DEFAULT_MAX_RUNS,
    load_combo_table,
    load_plan,
    load_runs,
    load_workflows,
    workflow_summary,
)

#: 峰定位方法(参考峰位取法/高斯 ROI 走同一套 config 缺省)
LOCALIZATION_CHOICES: tuple[str, ...] = ("parabolic", "gaussian")


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


def _selected_datasets(session: Any, condition: str) -> list[Any]:
    """--condition 过滤(空 = 全部条件)。"""
    if not condition:
        return list(session.datasets)
    ref = session.dataset_by_condition(condition)
    if ref is None:
        raise SensitivityError(f"研究里没有条件 {condition!r}")
    return [ref]


def cmd_init(args: argparse.Namespace) -> int:
    session = open_study(args.study, name=args.name)
    if args.dataset:
        dataset = add_dataset(
            session,
            args.dataset,
            condition=args.condition,
            title=args.name or args.title,
        )
        _print(
            {
                "study": str(session.root),
                "condition": dataset.condition,
                "dataset": dataset.to_dict(),
            }
        )
    else:
        _print(
            {
                "study": str(session.root),
                "datasets": [ref.to_dict() for ref in session.datasets],
            }
        )
    return 0


def cmd_reference(args: argparse.Namespace) -> int:
    session = open_study(args.study, name=args.name)
    params = _load_mapping(args.params) if args.params else None
    out: list[dict[str, Any]] = []
    for target in _selected_datasets(session, args.condition):
        reference = build_reference(
            session,
            target,
            params=params,
            phase_route=args.phase_route,
            progress=print,
            force=args.force,
        )
        out.append(
            {
                "condition": reference.condition,
                "dataset": reference.dataset_key,
                "spectrum": reference.frozen_spectrum,
                "script": reference.script_path,
                "script_sha256": reference.script_sha256,
                "phase_route": reference.phase_route,
                "phase": reference.phase_record(),
                "sampling": reference.sampling,
                "workflow_supported": reference.sweep_supported,
            }
        )
    _print(out if len(out) > 1 else out[0])
    return 0


def cmd_peaks(args: argparse.Namespace) -> int:
    session = open_study(args.study, name=args.name)
    out: list[dict[str, Any]] = []
    for target in _selected_datasets(session, args.condition):
        reference = load_reference(session, target)
        if reference is None:
            raise SensitivityError(f"条件 {target.condition!r} 还没有参考谱")
        if args.peak_table:
            # 可选:外部峰表(公开库/已指认),登记到该条件参考
            path = session.reference_dir_for(target) / "reference.list"
            path.write_text(
                Path(args.peak_table).read_text(encoding="utf-8-sig"),
                encoding="utf-8",
            )
            reference = set_reference_peaks(
                session, path, reference, source="external"
            )
            from nmrforge_api.reference import build_reference_peak_tables

            reference = build_reference_peak_tables(session, reference)
        else:
            # 默认:让 NMRForge 在参考谱上自动选峰,并生成两张参考峰表
            reference = ensure_reference_peaks(
                session,
                reference,
                sigma_multiplier=args.sigma,
                max_peaks=args.max_peaks,
                force=args.force,
                localization_method=args.localization,
                gaussian_roi_f1_ppm=args.gaussian_roi_f1_ppm,
                gaussian_roi_f2_ppm=args.gaussian_roi_f2_ppm,
            )
        peaks = reference_peaks(session, reference)
        out.append(
            {
                "condition": reference.condition,
                "dataset": reference.dataset_key,
                "peak_list": reference.peak_table_path,
                "sha256": reference.peak_table_sha256,
                "source": reference.peak_source,
                "count": len(peaks) or reference.peak_count,
                "peak_tables": reference.peak_tables,
                "localization": reference.peak_localization,
                "params": reference.peak_params,
            }
        )
    # 参考模式产物:把参考谱/脚本/两张峰表/采样/阈值写成 records/reference.json
    references = load_references(session)
    records = write_reference_records(session, references)
    primary = session.dataset
    if primary is not None and primary.key in references:
        session.save_state(reference=references[primary.key].to_dict())
    session.manager.save()
    _print(
        {
            "mode": "reference",
            "conditions": out,
            "records": records,
        }
    )
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    """**组合模式**:显式指定参考,按参数组合表跑 workflow(不生成参考)。"""
    if (args.grid is None) == (args.combos is None):
        raise SensitivityError(
            "必须且只能给一个:--grid(各轴候选值,接口展开全因子)或 "
            "--combos(外部给定的组合表:正交/部分因子/LHS…)"
        )
    spec = str(args.reference)
    if args.condition and "#" not in spec and not spec.endswith("reference.json"):
        spec = f"{spec}#{args.condition}"
    if args.combos:
        combos = load_combo_table(args.combos)
        result = run_combination_study(
            spec,
            combos=combos,
            max_runs=int(args.max_runs or DEFAULT_MAX_RUNS),
            window_pts=args.window_pts,
            window_ppm=args.window_ppm,
            roi_f1_ppm=args.gaussian_roi_f1_ppm,
            roi_f2_ppm=args.gaussian_roi_f2_ppm,
            resume=not args.no_resume,
            progress=print,
        )
    else:
        config = _load_mapping(args.grid)
        axes = config.get("axes")
        if not isinstance(axes, dict) or not axes:
            raise SensitivityError(f"网格文件缺少 axes: {args.grid}")
        max_runs = int(config.get("max_runs", args.max_runs or DEFAULT_MAX_RUNS))
        result = run_combination_study(
            spec,
            axes=axes,
            max_runs=max_runs,
            window_pts=args.window_pts,
            window_ppm=args.window_ppm,
            roi_f1_ppm=args.gaussian_roi_f1_ppm,
            roi_f2_ppm=args.gaussian_roi_f2_ppm,
            resume=not args.no_resume,
            progress=print,
        )
    _print(
        {
            "mode": "combination",
            "reference": result.summary.get("reference_spec", spec),
            "workflows": result.plan.n_workflows,
            "conditions": [ref.condition for ref in result.references.values()],
            "status": result.summary.get("status_counts", {}),
            "records": result.records,
        }
    )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    session = open_study(args.study, name=args.name)
    plan = load_plan(session)
    if plan is None:
        raise SensitivityError("找不到 workflow 计划(records/sweep_plan.json)")
    references = load_references(session)
    runs = load_runs(session)
    records = write_records(
        session,
        references=references,
        plan=plan,
        runs=runs,
        peaks=reference_peaks(session, session.dataset),
    )
    _print(
        {
            "workflows": len(load_workflows(session)),
            "status": workflow_summary(runs),
            "records": records,
        }
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    session = open_study(args.study, name=args.name)
    references = load_references(session)
    plan = load_plan(session)
    runs = load_runs(session)
    workflows = load_workflows(session)
    _print(
        {
            "study": str(session.root),
            "datasets": [ref.to_dict() for ref in session.datasets],
            "references": {
                key: {
                    "condition": ref.condition,
                    "script_sha256": ref.script_sha256,
                    "spectrum_sha256": ref.spectrum_sha256,
                    "peak_count": ref.peak_count,
                    "peak_source": ref.peak_source,
                    "peak_tables": ref.peak_tables,
                }
                for key, ref in references.items()
            },
            "workflows": {
                "planned": plan.n_workflows if plan else 0,
                "recorded": len(workflows),
                "ids": plan.workflow_ids() if plan else [],
            },
            "runs": workflow_summary(runs),
            "records_dir": str(session.records_dir),
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nmrforge_api",
        description="NMRForge 参数组合执行接口(无 GUI 命令行)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def _common(handler: argparse.ArgumentParser) -> None:
        handler.add_argument("--study", required=True, help="研究根目录")
        handler.add_argument("--name", default="", help="新建研究时的项目名")
        handler.add_argument(
            "--condition",
            default="",
            help="条件标签(A/B…);缺省表示全部条件",
        )

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
    peaks.add_argument(
        "--max-peaks", type=int, default=0, help="只保留强度前 N 个峰(0=全部)"
    )
    peaks.add_argument("--force", action="store_true", help="丢弃已有峰表重新选峰")
    peaks.add_argument(
        "--localization",
        choices=LOCALIZATION_CHOICES,
        default="parabolic",
        help="参考峰位取法:parabolic(默认)/ gaussian(2D 高斯,仅 2D)",
    )
    peaks.add_argument(
        "--gaussian-roi-f1-ppm", type=float, default=None,
        help="高斯 ROI 半径(间接维 F1,ppm;缺省读 config)",
    )
    peaks.add_argument(
        "--gaussian-roi-f2-ppm", type=float, default=None,
        help="高斯 ROI 半径(直接维 F2,ppm;缺省读 config)",
    )
    peaks.add_argument(
        "--peak-table",
        default="",
        help="可选:外部峰表(.list 或 peak_id,H_ppm,N_ppm CSV);缺省自动选峰",
    )
    peaks.set_defaults(func=cmd_peaks)

    sweep = sub.add_parser(
        "sweep",
        aliases=["workflows"],
        help="按参数组合表批量执行 workflow",
    )
    _common(sweep)
    sweep.add_argument(
        "--grid",
        default=None,
        help="轴网格 YAML/JSON(各轴候选值,接口展开全因子)",
    )
    sweep.add_argument(
        "--reference",
        required=True,
        help="组合模式必须显式指定参考:<研究根> 或 <研究根>#<条件>,"
        "或 reference.json 路径(由参考模式 reference + peaks 生成)",
    )
    sweep.add_argument(
        "--combos",
        default=None,
        help="显式组合表 CSV/TSV/YAML/JSON(外部设计:正交/部分因子/LHS…)",
    )
    sweep.add_argument("--max-runs", type=int, default=0)
    sweep.add_argument(
        "--window-pts",
        type=int,
        default=None,
        help="峰位搜索窗口半径(数据点;显式口径,跨分辨率不可比,不推荐)",
    )
    sweep.add_argument(
        "--window-ppm",
        type=float,
        default=None,
        help="峰位搜索窗口半径(ppm;缺省=1.5×该轴核素线宽折算 ppm)",
    )
    sweep.add_argument(
        "--gaussian-roi-f1-ppm", type=float, default=None,
        help="高斯 ROI 半径(间接维 F1,ppm;缺省读 config)",
    )
    sweep.add_argument(
        "--gaussian-roi-f2-ppm", type=float, default=None,
        help="高斯 ROI 半径(直接维 F2,ppm;缺省读 config)",
    )
    sweep.add_argument("--no-resume", action="store_true", help="不跳过已完成 workflow")
    sweep.set_defaults(func=cmd_sweep)

    report = sub.add_parser("report", help="用已有记录重算汇总(不重跑处理)")
    _common(report)
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
