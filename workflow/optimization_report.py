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


REPORT_MARK = "== 谱图质量与数据质量报告 =="


def report_text_from_logs(logs: list[str]) -> str | None:
    """从统一流程日志里抽取报告文本(0.2.199-补29d)。

    phase_routes._append_final_summary 会把「== 谱图质量与数据质量报告 ==」
    起的全部行追加进 logs;工作线程生成谱图后据此写 {谱}.quality.json,
    GUI 缓存未命中时直接读记录,不再在主线程重读整张 ft3。
    """
    for i, line in enumerate(logs):
        if line.strip().startswith(REPORT_MARK):
            return "\n".join(logs[i:])
    return None


def write_quality_record(
    spectrum_path: str,
    params: dict,
    text: str,
) -> None:
    """写 {谱}.quality.json 报告缓存记录(与 GUI _cached_spectrum_report 同指纹)。

    fp = mtime_ns|size,params_fp = sha256(params)[:16];GUI 读取时校验一致
    才复用,谱或参数变化自动失效。写盘失败静默(仅缓存)。
    """
    import hashlib
    import json

    from pathlib import Path

    p = Path(spectrum_path)
    try:
        if not p.is_file():
            return
        st = p.stat()
        fp = f"{st.st_mtime_ns}|{st.st_size}"
        params_fp = hashlib.sha256(
            json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
        Path(f"{spectrum_path}.quality.json").write_text(
            json.dumps(
                {"fp": fp, "params_fp": params_fp, "text": text},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def spectrum_quality_report_lines(
    spectrum_path: str,
    *,
    optimization_logs: list[str] | None = None,
    axis_names: list[str] | None = None,
    sign_mode: str = "uniform",
) -> list[str]:
    """◆ 最终谱图质量 分节(0.2.169-补):综合判定 + 分项等级分数 +
    基线指标(最差存储轴)+ 检查说明 + 基线不平原因。日志末尾汇总与
    pipeline 参数报告共用;谱不可读/评估失败返回单行说明。"""
    import numpy as np

    def _grade(score: float) -> str:
        return "良好" if score >= 75.0 else ("需注意" if score >= 50.0 else "较差")

    try:
        import nmrglue as ng

        from core.qc import baseline_quality, spectrum_quality

        _dic, data = ng.pipe.read(str(spectrum_path))
        arr = np.asarray(data)
        q = spectrum_quality.evaluate(arr, sign_mode=sign_mode)
        comps = q.score.components
        decision_label = {
            "accept": "✓ 接受",
            "warning": "⚠ 警告",
            "rollback": "✗ 不合格",
        }.get(str(q.decision.value), str(q.decision.value))
        lines = ["◆ 最终谱图质量(处理完成后的评价)"]
        lines.append(f"   综合判定: {decision_label}(综合分 {q.score.overall:.1f})")
        for label, key in (
            ("信噪比", "snr"),
            ("相位", "phase"),
            ("基线", "baseline"),
            ("伪影", "artifact"),
        ):
            score = float(getattr(comps, key))
            lines.append(f"   - {label}: {_grade(score)}({score:.0f} 分)")
        worst_idx, bm = baseline_quality.worst_axis(arr)
        axis_label = ""
        if axis_names and 0 <= worst_idx < len(axis_names):
            axis_label = f"(最差轴 {axis_names[worst_idx]})"
        elif worst_idx >= 0:
            axis_label = f"(最差存储轴 #{worst_idx + 1})"
        lines.append(
            f"       基线指标{axis_label}: 斜率 {bm.slope * 100:.1f}%  "
            f"偏移 {bm.offset * 100:.1f}%  弯曲 {bm.curvature * 100:.1f}%  "
            f"条纹 {bm.stripe:.2f}"
        )
        if q.reasons:
            lines.append("   检查说明:")
            for reason in q.reasons:
                lines.append(f"     · {reason}")
        if bm.needs_correction:
            opt_lines = [
                line
                for line in (optimization_logs or [])
                if "基线" in line or line[:3] in ("F1:", "F2:", "F3:")
            ]
            opt_summary = "；".join(opt_lines) if opt_lines else "无基线优化记录"
            lines.append(
                "   基线不平原因: " + opt_summary
                + "；质量评估基于终跑谱,基线优化基于 joint 谱逐维内存评分,"
                "两基准不同;窗函数/填零会改变基线形态,且优化候选增益≤0.5 "
                "或条纹否决时保持 off(不校正)。"
            )
        return lines
    except Exception as exc:  # noqa: BLE001 - 质量评估失败不阻断报告
        return [f"◆ 最终谱图质量: 评估跳过({exc})"]


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
