"""Unified optimisation result report format (0.2.157): the pipeline parameter report is shared
with the end-of-log summary.

Data source: the effective parameters of "Generate spectrum" (stepwise merged_params / unified
process result, keys: phase_route, direct_phase, phases, baseline, window, zero_fill,
backend_runs). **The data quality diagnosis is not part of this module** -- it belongs to the
"Generate FID" step (2026-09-23 user: "the Generate spectrum step should also drop the
intermediate data quality diagnosis, because it is no longer this stage's business"), and is
carried by that step's log and step report (``direct_diagnostics.format_fid_step_report``).
Even when a caller passes the whole ``run.params["diagnostics"]`` in, this module does not
render it.
"""

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


def spectrum_report_title() -> str:
    """Title of the spectrum quality report (the single definition shared by producer and
    extractor, 2026-09-23).

    Real bug fixed on 2026-09-23: previously the producer (phase_routes._append_final_summary /
    the manual path) and the extractor (report_text_from_logs) each wrote their own title
    literal, so changing the title made the report unextractable for good -- the report in the
    "Generate spectrum" log was still announced, but {spectrum}.quality.json stopped being
    refreshed and the GUI step detail forever showed "no report record". Both sides now call
    this function. The language is taken at call time (an import-time constant would mismatch
    after a UI language switch).
    """
    return tr("== spectrum quality report ==")


def report_text_from_logs(logs: list[str]) -> str | None:
    """Extract the report text from the unified process log (0.2.199-patch29d).

    phase_routes._append_final_summary / the manual path appends every line starting with
    "== spectrum quality report ==" to logs; after generating a spectrum the worker thread
    writes {spectrum}.quality.json from it, and the GUI reads that record directly on a cache
    miss instead of re-reading the whole ft3 in the main thread. The title comes from
    ``spectrum_report_title()`` -- the same source as the producer, no more separate copies.
    """
    marks = (
        spectrum_report_title(),
        # Old title (before 2026-09-23 the data quality section was part of this report):
        # tolerated when extracting.
        tr("== spectrum quality and data quality report =="),
    )
    for i, line in enumerate(logs):
        head = line.strip()
        if any(head.startswith(mark) for mark in marks):
            return "\n".join(logs[i:])
    return None


def write_quality_record(
    spectrum_path: str,
    params: dict,
    text: str,
) -> None:
    """Write the {spectrum}.quality.json report cache record (same fingerprint as GUI
    _cached_spectrum_report).

    fp = mtime_ns|size, params_fp = sha256(params)[:16]; the GUI reuses the record only when it
    verifies identically on read, so a changed spectrum or parameter invalidates it
    automatically. A failed write is silent (cache only).
    """
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
    """◆ Final spectrum quality section (0.2.169-supplement): overall verdict + per-item grade
    scores + baseline indicators (worst storage axis) + inspection notes + reason for an uneven
    baseline. Shared by the end-of-log summary and the pipeline parameter report; an unreadable
    spectrum or a failed evaluation returns a single explanatory line.

    sign_mode defaults to ``"auto"`` (2026-09-20): a standalone evaluation entry point cannot
    know the experiment type, so QC decides by itself between "single-sign positive peaks /
    single-sign negative peaks / both signs present" and writes that verdict into the
    inspection notes (a user-supplied spectrum may be single-sign and all negative); calls
    inside the pipeline still pass the experiment convention explicitly
    (``"uniform"``/``"mixed"``).
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

        # 0.2.199-patch29z: each stage of the quality evaluation reports progress (after the
        # final run the user can see what is currently being evaluated instead of a long
        # stretch without logs)
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
        # 0.2.199-patch29z: the baseline score matches the optimisation -- use the per-axis score
        # of the configuration selected in the optimisation grid (the worst axis represents it)
        # instead of re-evaluating the final spectrum (two different baselines caused the
        # confusion of optimisation 66.8 versus report 50)
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
        lines.append(tr(
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
    except Exception as exc:  # noqa: BLE001 - a failed quality evaluation must not block the report
        return [tr("◆ Final spectrum image quality: evaluation skipped ({p0})", p0=exc)]


def format_optimization_report(params: dict) -> list[str]:
    """Unified optimisation result report lines (with a two-space indent, 0.2.157)."""
    lines: list[str] = []
    route = params.get("phase_route")
    if route is not None:
        lines.append(
            tr(" phase optimisation approach: {p0}", p0=_PHASE_ROUTE_LABELS.get(str(route), route))
        )
    direct = params.get("direct_phase")
    if direct is not None:
        lines.append(tr(" direct dimension phase: {p0}", p0=format_phase_pair(direct)))
    # 0.2.162-patch15: shown when the user sets the direct-dimension range of the final run
    # (an empty end shows the default)
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
    # 2026-09-24: the whole ``diagnostics`` block is **not rendered here** -- the FID-layer
    # diagnosis belongs to the "Generate FID" step (``run.params`` really does carry
    # diagnostics, but no caller should display that stage's conclusions in the spectrum
    # report). The historical line "Data quality diagnosis: None" is gone with it.
    runs = params.get("backend_runs")
    if runs is not None:
        lines.append(tr(" Number of backend runs: {p0}", p0=runs))
    return lines
