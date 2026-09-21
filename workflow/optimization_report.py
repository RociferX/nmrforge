"""Unified optimisation result report format (0.2.157): pipeline parameter report is shared with
log end summary. Data source: Generate spectrum actual effective parameter (stepwise
merged_params / unified process result, key: phase_route, direct_phase, phases, baseline,
window, zero_fill, diagnostics, backend_runs). Data quality diagnosis details are displayed
directly here, and the running log is no longer referenced."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ui_support.i18n import tr

_PHASE_ROUTE_LABELS = {"unified": tr(
    "Unified automatic "
    "processing",
), "none": tr(
    "None(escape "
    "hatch)",
)}


def format_phase_pair(value: Any) -> str:
    """Phase pair ((p0,p1) tuple or {p0,p1,source} dict) -> readable text."""
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
    """{axis: {mode/type/size...}} -> 'F1=auto F2=none' compact text; non-dict values (old format
    integers, etc.) are returned unchanged."""
    if not isinstance(cfg, dict):
        return str(cfg)
    parts: list[str] = []
    for axis, conf in sorted(cfg.items()):
        if isinstance(conf, dict):
            mode = conf.get("mode") or conf.get("type") or tr("default")
            size = conf.get("size")
            parts.append(f"{axis}={mode}" + (f"×{size}" if size else ""))
        else:
            parts.append(f"{axis}={conf}")
    return " ".join(parts) or tr("default")


REPORT_MARK = tr("== spectrum quality and data quality report ==")


def report_text_from_logs(logs: list[str]) -> str | None:
    """Extract the report text (0.2.199-patch29d) from the unified process log.
    phase_routes._append_final_summary will append all the lines starting with "== spectrum
    quality and data quality report ==" into logs; the working thread generates the spectrum and
    writes {spectrum} accordingly. quality.json, GUI When the cache misses, the record is read
    directly, and the entire ft3 is no longer reread in the main thread."""
    for i, line in enumerate(logs):
        if line.strip().startswith(REPORT_MARK):
            return "\n".join(logs[i:])
    return None


def write_quality_record(
    spectrum_path: str,
    params: dict,
    text: str,
) -> None:
    """Write {spectrum}.quality.json to report the cache record (same fingerprint as GUI
    _cached_spectrum_report). fp = mtime_ns|size,params_fp = sha256(params)[:16];GUI will be
    reused only after verification when reading is consistent. Changes in the spectrum or
    parameter will automatically become invalid. Silent failure when writing to disk (cache
    only)."""
    import hashlib
    import json
    from pathlib import Path

    from core.project.manager import atomic_write_text

    p = Path(spectrum_path)
    try:
        if not p.is_file():
            return
        st = p.stat()
        fp = f"{st.st_mtime_ns}|{st.st_size}"
        params_fp = hashlib.sha256(
            json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
        atomic_write_text(
            Path(f"{spectrum_path}.quality.json"),
            json.dumps(
                {"fp": fp, "params_fp": params_fp, "text": text},
                ensure_ascii=False,
            ),
        )
    except OSError:
        pass


def spectrum_quality_report_lines(
    spectrum_path: str,
    *,
    optimization_logs: list[str] | None = None,
    axis_names: list[str] | None = None,
    sign_mode: str = "auto",
    baseline_scores: dict[str, float] | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[str]:
    """◆ Final spectrum chart quality section (0.2.169-supplement): comprehensive judgment + sub-
    item grade score + baseline indicator (worst storage axis) + inspection description +
    baseline uneven reason. The summary at the end of the log is shared with the pipeline
    parameter report; The spectrum is unreadable/If the evaluation fails, return a single line
    description..

    sign_mode defaults to ``"auto"`` (2026-09-20): a standalone evaluation entry point
    does not know the experiment type, so QC judges "single-sign positive / single-sign
    negative / both signs coexist" by itself and writes that judgement into the
    inspection notes (a spectrum the user processed may be single-sign and all
    negative); callers inside the pipeline still pass the experiment convention
    explicitly (``"uniform"``/``"mixed"``).
    """
    import numpy as np

    def _grade(score: float) -> str:
        return tr(
            "Good",
        ) if score >= 75.0 else (tr(
            "Needs attention",
        ) if score >= 50.0 else tr(
            "Poor",
        ))

    try:
        import nmrglue as ng

        from core.qc import baseline_quality, spectrum_quality

        # 0.2.199-patch29z: Output progress of each stage of quality assessment (after the final run
        # is completed, the user can see what is currently being evaluated instead of no log for a
        # long time).
        if progress is not None:
            progress(tr("spectrum quality assessment: reading final spectrum"))
        _dic, data = ng.pipe.read(str(spectrum_path))
        arr = np.asarray(data)
        if progress is not None:
            progress(tr("spectrum quality evaluation: scoring SNR / phase / baseline / artefacts"))
        q = spectrum_quality.evaluate(arr, sign_mode=sign_mode)
        if progress is not None:
            progress(tr("spectrum quality assessment: baseline worst axis analysis in progress"))
        comps = q.score.components
        # 0.2.199-patch29z: The baseline score is consistent with the optimisation---Directly use
        # the optimisation grid to select the configured axis score (the worst axis representative),
        # without separately evaluating the final spectrum (the difference between the two
        # benchmarks will cause optimisation 66.8 to report 50 confusion).
        if baseline_scores and baseline_scores.values():
            opt_worst = min(baseline_scores.values())
            if 0.0 < opt_worst <= 100.0:
                comps = comps.__class__(
                    snr=comps.snr,
                    phase=comps.phase,
                    baseline=float(opt_worst),
                    artifact=comps.artifact,
                )
        decision_label = {
            "accept": tr("✓ Accept"),
            "warning": tr("⚠ Warning"),
            "rollback": tr("✗ Unqualified"),
        }.get(str(q.decision.value), str(q.decision.value))
        lines = [tr("◆ Final spectrum image quality (evaluation after processing is completed)")]
        lines.append(
            tr(
            " Comprehensive judgment: {p0}(Comprehensive score "
            "{p1:.1f})",
            p0=decision_label,
            p1=q.score.overall,
        ))
        for label, key in (
            (tr("signal-to-noise ratio"), "snr"),
            (tr("phase"), "phase"),
            (tr("baseline"), "baseline"),
            (tr("artifact"), "artifact"),
        ):
            score = float(getattr(comps, key))
            lines.append(tr(
                "   - {p0}: {p1}({p2:.0f} "
                "point)",
                p0=label,
                p1=_grade(score),
                p2=score,
            ))
        worst_idx, bm = baseline_quality.worst_axis(arr)
        axis_label = ""
        if axis_names and 0 <= worst_idx < len(axis_names):
            axis_label = tr("(worst axis {p0})", p0=axis_names[worst_idx])
        elif worst_idx >= 0:
            axis_label = tr("(Worst storage axis #{p0})", p0=worst_idx + 1)
        lines.append(
            tr(
                " Baseline indicators{p0}: slope {p1:.1f}%  offset {p2:.1f}%  curvature {p3:.1f}%  "
                "striping "
                "{p4:.2f}",
                p0=axis_label,
                p1=bm.slope * 100,
                p2=bm.offset * 100,
                p3=bm.curvature * 100,
                p4=bm.stripe,
            )
        )
        if q.reasons:
            lines.append(tr(" Inspection instructions:"))
            for reason in q.reasons:
                lines.append(f"     · {reason}")
        if bm.needs_correction:
            opt_lines = [
                line
                for line in (optimization_logs or [])
                if tr("baseline").lower() in line.lower() or line[:3] in ("F1:", "F2:", "F3:")
            ]
            opt_summary = ";".join(opt_lines) if opt_lines else tr(
                "No baseline optimisation "
                "record",
            )
            lines.append(
                tr(" Reasons for uneven baseline: ") + opt_summary
                + tr(
                    "; the quality evaluation uses the final spectrum while the baseline "
                    "optimisation uses the per-axis in-memory score of the joint spectrum, so the "
                    "two baselines differ; windowing / zero-filling change the baseline shape, and "
                    "the optimisation stays off (no correction) when the candidate gain is <=0.5 "
                    "or the striping check vetoes "
                    "it.",
                )
            )
        return lines
    except Exception as exc:  # noqa: BLE001 - Quality assessment failure does not block reporting.
        return [tr("◆ Final spectrum image quality: evaluation skipped ({p0})", p0=exc)]


def format_optimization_report(params: dict) -> list[str]:
    """Unify optimisation result reporting lines (with two-space indentation, 0.2.157)."""
    lines: list[str] = []
    route = params.get("phase_route")
    if route is not None:
        lines.append(
            tr(" phase optimisation approach: {p0}", p0=_PHASE_ROUTE_LABELS.get(str(route), route))
        )
    direct = params.get("direct_phase")
    if direct is not None:
        lines.append(tr(" direct dimension phase: {p0}", p0=format_phase_pair(direct)))
    # 0.2.162-patch15: Displayed when the user specifies the final direct dimension range (the empty
    # end is displayed by default).
    final_lo = params.get("final_ext_lo")
    final_hi = params.get("final_ext_hi")
    if final_lo is not None or final_hi is not None:
        lo = str(final_lo) if final_lo not in (None, "") else tr("default")
        hi = str(final_hi) if final_hi not in (None, "") else tr("default")
        lines.append(tr(" direct dimension range (final run): {p0} - {p1} ppm", p0=lo, p1=hi))
    phases = params.get("phases") or {}
    if phases:
        lines.append(tr(" Phase per dimension:"))
        for axis, pair in sorted(phases.items()):
            lines.append(f"    {axis}: {format_phase_pair(pair)}")
    baseline = params.get("baseline")
    if baseline:
        lines.append(tr(" Baseline: {p0}", p0=format_opt_mode_map(baseline)))
    window = params.get("window")
    if window:
        lines.append(tr(" Window function: {p0}", p0=format_opt_mode_map(window)))
    zero_fill = params.get("zero_fill")
    if zero_fill:
        lines.append(tr(" zero filling: {p0}", p0=format_opt_mode_map(zero_fill)))
    diagnostics = params.get("diagnostics") or {}
    reports = diagnostics.get("reports") or []
    if reports:
        lines.append(tr(" Data quality diagnosis:"))
        for i, report in enumerate(reports, 1):
            lines.append(f"    {i}. {report}")
    elif diagnostics:
        lines.append(tr(" Data quality diagnostics: None"))
    runs = params.get("backend_runs")
    if runs is not None:
        lines.append(tr(" Number of backend runs: {p0}", p0=runs))
    return lines
