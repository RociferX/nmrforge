"""统一优化结果报告格式(0.2.157):pipeline 参数报告与日志末尾汇总共用。

数据来源:生成谱图实际生效参数(stepwise merged_params / unified 流程
结果,键:phase_route、direct_phase、phases、baseline、window、
zero_fill、diagnostics、backend_runs)。数据质量诊断在此直接展示
详情,不再引用运行日志。
"""

from __future__ import annotations

from typing import Any

_PHASE_ROUTE_LABELS = {"unified": "统一自动处理", "none": "None(逃生口)"}


def format_phase_pair(value: Any) -> str:
    """相位对 ((p0,p1) 元组或 {p0,p1,source} dict) → 可读文本。"""
    if isinstance(value, dict):
        p0 = value.get("p0", "")
        p1 = value.get("p1", "")
        text = f"p0={p0}° p1={p1}°"
        if value.get("source"):
            text += " (" + str(value.get("source")) + ")"
        return text
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return f"p0={value[0]}° p1={value[1]}°"
    return str(value)


def format_opt_mode_map(cfg: Any) -> str:
    """{axis: {mode/type/size...}} → 'F1=auto F2=none' 紧凑文本;
    非 dict 值(旧格式整数等)原样返回。"""
    if not isinstance(cfg, dict):
        return str(cfg)
    parts: list[str] = []
    for axis, conf in sorted(cfg.items()):
        if isinstance(conf, dict):
            mode = conf.get("mode") or conf.get("type") or "默认"
            size = conf.get("size")
            parts.append(f"{axis}={mode}" + (f"×{size}" if size else ""))
        else:
            parts.append(f"{axis}={conf}")
    return " ".join(parts) or "默认"


def format_optimization_report(params: dict) -> list[str]:
    """统一优化结果报告行(带两空格缩进,0.2.157)。"""
    lines: list[str] = []
    route = params.get("phase_route")
    if route is not None:
        lines.append(
            f"  相位优化途径: {_PHASE_ROUTE_LABELS.get(str(route), route)}"
        )
    direct = params.get("direct_phase")
    if direct is not None:
        lines.append(f"  直接维相位: {format_phase_pair(direct)}")
    # 0.2.162-补15:用户指定终跑直接维范围时展示(空端显示默认)
    final_lo = params.get("final_ext_lo")
    final_hi = params.get("final_ext_hi")
    if final_lo is not None or final_hi is not None:
        lo = str(final_lo) if final_lo not in (None, "") else "默认"
        hi = str(final_hi) if final_hi not in (None, "") else "默认"
        lines.append(f"  直接维范围(终跑): {lo} - {hi} ppm")
    phases = params.get("phases") or {}
    if phases:
        lines.append("  逐维相位:")
        for axis, pair in sorted(phases.items()):
            lines.append(f"    {axis}: {format_phase_pair(pair)}")
    baseline = params.get("baseline")
    if baseline:
        lines.append(f"  基线: {format_opt_mode_map(baseline)}")
    window = params.get("window")
    if window:
        lines.append(f"  窗函数: {format_opt_mode_map(window)}")
    zero_fill = params.get("zero_fill")
    if zero_fill:
        lines.append(f"  填零: {format_opt_mode_map(zero_fill)}")
    diagnostics = params.get("diagnostics") or {}
    reports = diagnostics.get("reports") or []
    if reports:
        lines.append("  数据质量诊断:")
        for i, report in enumerate(reports, 1):
            lines.append(f"    {i}. {report}")
    elif diagnostics:
        lines.append("  数据质量诊断: 无")
    runs = params.get("backend_runs")
    if runs is not None:
        lines.append(f"  后端运行次数: {runs}")
    return lines
