"""Middle Pipeline panel: Displays processing steps and status around the current sample
data/experiment type. Five-step process (Contract v1.2 / G2B-002, including optional SMILE
optimisation): Import sample data -> Generate FID -> Generate spectrum (including SMILE
reconstruction) -> [SMILE optimisation, optional] -> Peak selection. Analysis (HSQC CSP) has
been pressed The user decided to delete (2026-09-12, REPORT-008). For the deletion scope and
recovery method, see docs/tasks/archive/2026-09-12-analysis-removal.md. - The step status is
inferred based on the pre-dependency and product file (LOCKED/READY/RUNNING/SUCCESS/FAILED); -
READY The step provides a "Run" button and is executed by the corresponding method of
ProcessingController; - LOCKED The step tooltip explains which pre-step is missing; - GUI The
layer does not directly touch Backend: the only outlet is ProcessingController.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from pathlib import Path

from qtcompat.QtCore import QPoint, QRect, QSize, Qt
from qtcompat.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.project import ProjectManager
from core.project.artifacts import find_primary_spectrum
from core.user_errors import describe_exception
from gui.pipeline_state import (
    STEP_RUN_REFS,
    input_fingerprint,
    load_pipeline_state,
    record_step_success,
    script_fingerprint,
)
from gui.processing import ProcessingController
from qtcompat import Signal
from ui_support.i18n import tr
from ui_support.numeric_inputs import CommitDoubleSpinBox
from ui_support.theme import (
    PANEL_BORDER,
    STATUS_COLORS,
    SURFACE_ALT,
    TEXT_MUTED,
    TEXT_PRIMARY,
    fit_combo_width,
)

PIPELINE_STEPS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "fid",
        tr("Generate FID"),
        tr("Convert the raw Bruker data and verify the conversion settings"),
        (),
    ),
    (
        "spectrum",
        tr(
            "Generate spectrum",
        ),
        tr(
            "Optimise phase and processing parameters, then generate the final spectrum "
            "(NUS data includes SMILE reconstruction)",
        ),
        ("fid",),
    ),
    (
        "smile",
        tr(
            "SMILE optimisation",
        ),
        tr(
            "Optional for 2D NUS: compare reconstruction parameters without replacing the current "
            "spectrum",
        ),
        ("spectrum",),
    ),
    (
        "peaks",
        tr(
            "Peak picking",
        ),
        tr(
            "Detect peaks, estimate intensity and S/N, and write the peak table",
        ),
        ("spectrum",),
    ),
)


VISIBLE_PIPELINE_STEPS = PIPELINE_STEPS


def _visible_pipeline_steps():
    return VISIBLE_PIPELINE_STEPS


STEP_LABEL: dict[str, str] = {step_id: label for step_id, label, _, _ in _visible_pipeline_steps()}


STEP_LOG_SEPARATOR = "-" * 60

STATUS_TEXT = {
    "LOCKED": tr("Waiting"),
    "READY": tr("Ready"),
    "RUNNING": tr("Running"),
    "SUCCESS": tr("Completed"),
    "FAILED": tr("Failed"),
    "OUTDATED": tr("Needs update"),
}
STATUS_ICON = {
    "LOCKED": "🔒",
    "READY": "▶",
    "RUNNING": "…",
    "SUCCESS": "✓",
    "FAILED": "×",
    "OUTDATED": "!",
}


STEP_METHOD: dict[str, str] = {
    "fid": "generate_fid",
    "spectrum": "generate_spectrum",
    "smile": "optimize_smile",
    "peaks": "pick_peaks",
}


_DIRECT_RANGE_PPM: dict[str, tuple[float, float]] = {
    "1H": (0.0, 20.0),
    "2H": (0.0, 20.0),
    "13C": (-20.0, 220.0),
    "15N": (0.0, 260.0),
    "31P": (-60.0, 120.0),
    "19F": (-300.0, 100.0),
}
_DIRECT_RANGE_FALLBACK = (-100.0, 320.0)

_DEFAULT_WINDOW_PPM: dict[str, tuple[str, str]] = {
    "1H": ("10.5", "6.5"),
    "13C": ("70", "20"),
    "15N": ("130", "100"),
}


def _direct_dimension_range(nucleus: str) -> tuple[float, float]:
    """Direct dimension nuclide -> ppm range allowed for input (unknown nuclide gives loose
    range).
    """
    return _DIRECT_RANGE_PPM.get(str(nucleus or "").strip(), _DIRECT_RANGE_FALLBACK)


def _default_window_ppm(nucleus: str) -> tuple[str, str]:
    """Direct dimension nuclide -> common window for placeholder prompts (leave blank for unknown
    nuclei).
    """
    return _DEFAULT_WINDOW_PPM.get(str(nucleus or "").strip(), ("", ""))


def validate_ext_range(lo_text: str, hi_text: str, nucleus: str = "") -> str:
    """Verify the "direct dimension range" input: legitimate/Leave blank to return "", otherwise
    the error text will be returned to the user.
    """
    lo_txt = str(lo_text or "").strip()
    hi_txt = str(hi_text or "").strip()
    try:
        lo_f = float(lo_txt) if lo_txt else None
        hi_f = float(hi_txt) if hi_txt else None
    except ValueError:
        return tr("Please enter a number (ppm), or leave it blank to use the default")
    lo_min, hi_max = _direct_dimension_range(nucleus)
    tag = f"{nucleus} " if nucleus else ""
    for name, value in ((tr("High end"), lo_f), (tr("Low field end"), hi_f)):
        if value is not None and not (lo_min <= value <= hi_max):
            return tr(
                "{p0} ppm must be within {p1:g}-{p2:g} ({p3}direct dimension)",
                p0=name,
                p1=lo_min,
                p2=hi_max,
                p3=tag,
            )
    if lo_f is not None and hi_f is not None and lo_f <= hi_f:
        return tr(
            "The high field end ppm must be greater than the low field end ppm (such as 8.5-7.5)",
        )
    return ""


def _data_nodes(manager: ProjectManager, exp_id: str) -> list:
    """Returns the Data node under the experiment. Backend returns directly to entry.data after
    landing at the DataEntry level (contract v1.2 §8.1); Current compatibility stage: The
    experiment itself is used as the data node under the single data model.
    """
    entry = manager.project.experiment(exp_id) if manager.project is not None else None
    if entry is None:
        return []
    return list(getattr(entry, "data", None) or [])


def _node_artifacts(manager: ProjectManager, exp_id: str, data_id: str) -> dict[str, Path | None]:
    """The product path of each step of a single data node (schema 1.4 data-level layout)."""
    artifacts: dict[str, Path | None] = {
        "fid": None,
        "spectrum": None,
        "peaks": None,
    }
    data = manager.data(exp_id, data_id)
    fid_candidate = getattr(data, "fid_path", "") or ""
    if fid_candidate:
        path = Path(fid_candidate)
        if not path.is_absolute():
            path = manager.root / path

        if path.is_file() or path.is_dir():
            artifacts["fid"] = path
    if artifacts["fid"] is None:
        proc = manager.data_dir(exp_id, data_id, "process")
        try:
            fids = sorted(proc.glob("*.fid"))
        except OSError:
            fids = []
        if fids:
            artifacts["fid"] = fids[0]
        else:
            merged_fid = proc / "merged" / "fid"
            if merged_fid.is_dir():
                artifacts["fid"] = merged_fid
    artifacts["spectrum"] = find_primary_spectrum(manager, exp_id, data_id)
    for suffix in (".list", ".csv"):
        candidate = manager.data_dir(exp_id, data_id, "peaks") / f"{exp_id}-{data_id}{suffix}"
        if candidate.is_file():
            artifacts["peaks"] = candidate
            break
    return artifacts


def _upstream_artifact(step_id: str, artifacts: dict[str, Path | None]) -> Path | None:
    prev = {"spectrum": "fid", "peaks": "spectrum"}.get(step_id)
    return artifacts.get(prev) if prev else None


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _pipeline_flags() -> bool:
    """Pipeline simple mode switch (config/nmrforge.local.yaml -> gui.settings): After turning it
    on, only press the previous step to determine whether the product file exists (the next step
    only recognizes the file of the corresponding format), no input/script fingerprint is
    compared with the old and new product, and "expired" is not displayed.
    """
    try:
        from gui.settings import load_settings

        pipeline = load_settings().get("pipeline") or {}
    except Exception:  # noqa: BLE001
        pipeline = {}
    return bool(pipeline.get("simple_mode", False))


def _node_step_statuses(manager: ProjectManager, exp_id: str, node) -> dict[str, str]:
    """Five-step status of a single data node: product + fingerprint verification (OUTDATED) + pre-
    dependency.
    """
    data_id = getattr(node, "id", exp_id)
    artifacts = _node_artifacts(manager, exp_id, data_id)
    state = load_pipeline_state(manager, exp_id, data_id)
    simple_mode = _pipeline_flags()
    statuses: dict[str, str] = {}
    for step_id, _, _, deps in _visible_pipeline_steps():
        artifact = None if step_id == "smile" else artifacts.get(step_id)
        outdated = False
        if step_id == "smile":
            entry = state["steps"].get("smile")
            done = entry is not None
            if done and not simple_mode:
                current = input_fingerprint(manager, exp_id, data_id, "smile")
                if (
                    current is not None
                    and entry.get("input_hash")
                    and current != entry["input_hash"]
                ):
                    outdated = True
        else:
            done = artifact is not None
            if done and not simple_mode:
                entry = state["steps"].get(step_id)
                if entry:
                    current_input = input_fingerprint(manager, exp_id, data_id, step_id)
                    if (
                        current_input is not None
                        and entry.get("input_hash")
                        and current_input != entry["input_hash"]
                    ):
                        outdated = True
                    current_script = script_fingerprint(manager, exp_id, data_id, step_id)
                    if (
                        current_script is not None
                        and entry.get("script_hash")
                        and current_script != entry["script_hash"]
                    ):
                        outdated = True
                else:
                    upstream = _upstream_artifact(step_id, artifacts)
                    if upstream is not None and _mtime_ns(upstream) > _mtime_ns(artifact):
                        outdated = True
        failed_run = _last_run_for(manager, exp_id, data_id, _step_refs(step_id))
        if failed_run is not None and failed_run.status == "failed":
            statuses[step_id] = "FAILED"
        elif done and not outdated:
            statuses[step_id] = "SUCCESS"
        elif done:
            statuses[step_id] = "OUTDATED"
        elif all(statuses.get(dep) in ("SUCCESS", "OUTDATED") for dep in deps):
            statuses[step_id] = "READY"
        else:
            statuses[step_id] = "LOCKED"

    if not simple_mode:
        for step_id, _, _, deps in _visible_pipeline_steps():
            if statuses.get(step_id) == "SUCCESS" and any(
                statuses.get(dep) == "OUTDATED" for dep in deps
            ):
                statuses[step_id] = "OUTDATED"
    return statuses


def compute_step_statuses(manager: ProjectManager, exp_id: str) -> dict[str, str]:
    """Infer the status of each step according to product file, fingerprint verification and pre-
    dependency (supports OUTDATED). Experimental-level aggregation: when any node succeeds when
    there is multiple data, it means SUCCESS(Compatible with old behaviour/test).
    """
    nodes = _data_nodes(manager, exp_id)
    if not nodes:
        return {step_id: "LOCKED" for step_id, _, _, _ in _visible_pipeline_steps()}
    per_node = [_node_step_statuses(manager, exp_id, node) for node in nodes]
    statuses: dict[str, str] = {}
    for step_id, _, _, deps in _visible_pipeline_steps():
        verdicts = [node_status[step_id] for node_status in per_node]
        if "OUTDATED" in verdicts:
            statuses[step_id] = "OUTDATED"
        elif "SUCCESS" in verdicts:
            statuses[step_id] = "SUCCESS"
        elif all(statuses.get(dep) in ("SUCCESS", "OUTDATED") for dep in deps):
            statuses[step_id] = "READY"
        else:
            statuses[step_id] = "LOCKED"
    return statuses


def compute_data_step_statuses(
    manager: ProjectManager, exp_id: str, data_id: str
) -> dict[str, str]:
    """Calculate the step status according to a single data node (the intermediate processing page
    is displayed according to the selected data).
    """
    nodes = _data_nodes(manager, exp_id)
    node = next((n for n in nodes if getattr(n, "id", "") == data_id), None)
    if node is None:
        return compute_step_statuses(manager, exp_id)
    return _node_step_statuses(manager, exp_id, node)


def _outdated_reasons(
    statuses: dict[str, str],
    manager: ProjectManager,
    exp_id: str,
    data_id: str = "",
) -> dict[str, str]:
    """Generate reason for OUTDATED step (input/script changes, upstream expired, product
    behind).
    """
    reasons: dict[str, str] = {}
    nodes = _data_nodes(manager, exp_id)
    if data_id:
        nodes = [n for n in nodes if getattr(n, "id", "") == data_id]
    for step_id, _, _, deps in _visible_pipeline_steps():
        if statuses.get(step_id) != "OUTDATED":
            continue
        reason = ""
        for node in nodes:
            node_data_id = getattr(node, "id", exp_id)
            entry = load_pipeline_state(manager, exp_id, node_data_id)["steps"].get(step_id)
            if entry and entry.get("input_hash"):
                current = input_fingerprint(manager, exp_id, node_data_id, step_id)
                if current is not None and current != entry["input_hash"]:
                    reason = tr(
                        "The input has changed (upstream re-run or external modification), please "
                        "re-run",
                    )
                    break
                current_script = script_fingerprint(manager, exp_id, node_data_id, step_id)
                if (
                    current_script is not None
                    and entry.get("script_hash")
                    and current_script != entry["script_hash"]
                ):
                    reason = tr("The processing script has changed, please run it again")
                    break
        if not reason:
            stale_upstream = [STEP_LABEL[dep] for dep in deps if statuses.get(dep) == "OUTDATED"]
            if stale_upstream:
                reason = tr("The upstream step has expired: ") + "、".join(stale_upstream)
            else:
                reason = tr(
                    "The input or parameters changed; update this step to refresh the result",
                )
        reasons[step_id] = reason
    return reasons


def _lock_reasons(statuses: dict[str, str]) -> dict[str, str]:
    """Generate dependency hints for the LOCKED step (telling the user which prerequisites are
    missing).
    """
    reasons: dict[str, str] = {}
    for step_id, _, _, deps in _visible_pipeline_steps():
        if statuses.get(step_id) != "LOCKED":
            continue
        missing = [STEP_LABEL[dep] for dep in deps if statuses.get(dep) != "SUCCESS"]
        if missing:
            reasons[step_id] = tr("Prerequisite steps not completed: ") + "、".join(missing)
        else:
            reasons[step_id] = tr("Wait for the pre-product to be ready")
    return reasons


def _step_refs(step_id: str) -> tuple[str, ...]:
    """Steps -> Possible workflow refs (see gui.pipeline_state.STEP_RUN_REFS)."""
    return STEP_RUN_REFS.get(step_id, ())


def _last_run_for(manager: ProjectManager, exp_id: str, data_id: str, refs: tuple[str, ...]):
    """The latest run of the data under the specified step refs (strictly data_id attribution).
    0.2.199-patch29hz: The original accepted inputs are missing data_id old records. In multi-
    data experiments, an old failure will be counted on all data heads; the old project products
    will be regenerated, so there will be no rollback.
    """
    return manager.last_run_for_data(exp_id, data_id, refs)


def _format_params(params: dict) -> str:
    """Render recorded run parameters as readable text.

    Prefer peak_pick_report to the raw localization dictionary and avoid displaying the same
    information twice. Legacy records without a readable report retain the flat parameter
    rendering.
    """
    if not params:
        return ""
    report = params.get("peak_pick_report")
    report_text = ""
    if isinstance(report, str):
        report_text = report.strip()
    elif isinstance(report, (list, tuple)):
        report_text = "\n".join(str(item) for item in report if str(item).strip()).strip()
    if report_text:
        return report_text
    skip = {"peak_pick_report"}
    parts = [f"{key}={value}" for key, value in sorted(params.items()) if key not in skip]
    rendered = ", ".join(parts)
    return rendered


def _smile_step_report(params: dict, outputs: dict) -> list[str]:
    """Render a concise SMILE run report without exposing the raw ranking representation."""
    lines = [tr("== SMILE optimisation report ==")]
    combos = params.get("n_combos")
    if combos is not None:
        lines.append(tr("summary: compared {p0} parameter combinations", p0=combos))
    ranking = params.get("ranking")
    if isinstance(ranking, list) and ranking:
        lines.append(tr("◆ top candidates"))
        for row in ranking[:3]:
            if not isinstance(row, dict):
                continue
            lines.append(
                "   "
                + tr(
                    "Rank {p0}: nSigma {p1:g}, threshold {p2:g}, stable peaks {p3}",
                    p0=int(row.get("rank", len(lines))),
                    p1=float(row.get("nsigma", 0.0) or 0.0),
                    p2=float(row.get("thresh", 0.0) or 0.0),
                    p3=int(row.get("stable_count", 0) or 0),
                )
            )
    ranking_path = str(outputs.get("csv") or "")
    if ranking_path:
        lines.append(tr("ranking table: {p0}", p0=ranking_path))
    return lines


def _fid_step_report_lines(manager: ProjectManager, exp_id: str, data_id: str) -> list[str]:
    """Data-quality report of the Generate-FID step (reads that data's process/ records only).

    2026-09-23 (user request): what this step's log reports at its end must also show up in the
    step report of the middle panel - both use the same format
    (workflow.direct_diagnostics.fid_step_quality_report) and read diagnostics.json /
    field_drift.json / qc_audit.jsonl without re-running the diagnosis or touching the fid.
    """
    from workflow.direct_diagnostics import fid_step_quality_report

    try:
        work = manager.data_dir(exp_id, data_id, "process")
        raw = manager.data_dir(exp_id, data_id, "raw")
    except Exception:  # noqa: BLE001
        return [tr("(no data quality report record for this data)")]
    try:
        return fid_step_quality_report(work, [raw])
    except Exception as exc:  # noqa: BLE001
        return [tr("could not read the data quality report: {p0}", p0=exc)]


def _spectrum_param_report(params: dict, spectrum_path: str | None = None) -> str:
    """Generate spectrum parameter report (0.2.169-complement readable): ◆ processing parameter
    and optimisation; spectrum_path appended when readable ◆ final spectrum graph quality
    (shared with log end summary spectrum_quality_report_lines).

    2026-09-23 (user request): the data quality diagnosis belongs to the Generate-FID step and
    was removed from this report - that step's log and step report carry it now.
    """
    from workflow.optimization_report import (
        format_optimization_report,
        spectrum_quality_report_lines,
    )

    lines: list[str] = []

    lines.append(tr("◆ processing settings"))
    lines += format_optimization_report({k: v for k, v in params.items() if k != "diagnostics"})
    if spectrum_path:
        lines += spectrum_quality_report_lines(spectrum_path)
    return "\n".join(lines) if lines else tr(" (no parameter record)")


class _FlowLayout(QLayout):
    """Wrap step controls to the available width.

    Explicit add_break markers separate parameter and action sections while allowing each
    section to wrap further. Markers occupy no space and are excluded from count and itemAt.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []

        self._breaks: set[int] = set()
        self.setSpacing(6)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def add_break(self) -> None:
        """Append an explicit line break before subsequently added widgets."""
        self._breaks.add(len(self._items))

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            self._breaks = {
                (mark - 1 if mark > index else mark) for mark in self._breaks if mark != index
            }
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QRect, test_only: bool = False) -> int:
        m = self.contentsMargins()
        space = max(0, int(self.spacing()))
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        line_height = 0
        for position, item in enumerate(self._items):
            if position in self._breaks and line_height > 0:
                x = rect.x() + m.left()
                y += line_height + space
                line_height = 0
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            hint = item.sizeHint()
            next_x = x + hint.width()
            if line_height > 0 and next_x > rect.right() - m.right():
                x = rect.x() + m.left()
                y += line_height + space
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x + space
            line_height = max(line_height, hint.height())
        return y + line_height + m.bottom() - rect.y()


# ---------------------------------------------------------------------------

#


# ---------------------------------------------------------------------------


_INDIRECT_AXIS_ORDER: dict[int, tuple[str, ...]] = {2: ("F1",), 3: ("F2", "F1")}


_NEG_TOKEN = re.compile(r"(?<![-\w])-neg(?![-\w])")
_FT_LINE_TOKEN = re.compile(r"-fn\s+FT(?![-\w])", re.IGNORECASE)

_PS_LINE_TOKEN = re.compile(r"-fn\s+PS(?![-\w])", re.IGNORECASE)
_PS_P0_TOKEN = re.compile(r"(-p0\s+)([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")
_PS_P1_TOKEN = re.compile(r"(-p1\s+)([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")


def _pipeline_statements(lines: list[str]) -> list[list[int]]:
    """Cut the script into "pipeline statements": one top-level command plus its continuation
    lines (returns lists of line numbers).

    Blank lines and ``#`` comment lines are separators; a continuation line is either the
    previous line ending in ``\\``, or the current line starting with ``|``.
    """
    statements: list[list[int]] = []
    current: list[int] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not current:
            if not stripped or stripped.startswith("#"):
                continue
            current = [index]
            continue
        if lines[current[-1]].rstrip().endswith("\\") or stripped.startswith("|"):
            current.append(index)
            continue
        statements.append(current)
        current = [] if (not stripped or stripped.startswith("#")) else [index]
    if current:
        statements.append(current)
    return statements


def _is_ft_line(line: str) -> bool:
    """Is this line a ``| nmrPipe -fn FT`` line (comment lines do not count)."""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#") and bool(_FT_LINE_TOKEN.search(line))


def _indirect_ft_line_indexes(lines: list[str], ndim: int) -> list[int] | None:
    """Line numbers of the indirect-dimension FT lines (in F2 -> F1 order); None when the
    script layout cannot be recognised.

    Rule: the **first** FT line of the statement reading the time-domain ``.fid`` is the
    direct dimension; the remaining FT lines of that statement (after TP/ZTP) and the FT
    lines of **other** statements (reading back from a reconstructed plane / SMILE output)
    are all indirect. If the number of indirect FT lines does not match the dimensionality,
    do not guess -- return None (the caller warns the user).
    """
    expected = _INDIRECT_AXIS_ORDER.get(int(ndim))
    if not expected:
        return None
    indirect: list[int] = []
    for statement in _pipeline_statements(lines):
        time_domain = ".fid" in lines[statement[0]]
        seen_ft = False
        for line_no in statement:
            if not _is_ft_line(lines[line_no]):
                continue
            if time_domain and not seen_ft:
                seen_ft = True
                continue
            indirect.append(line_no)
    if len(indirect) != len(expected):
        return None
    return indirect


def _add_neg_token(line: str) -> str:
    """Append ``-neg`` to an FT line (unchanged if already present); inserted at the end of
    the parameters, before the continuation backslash.
    """
    if _NEG_TOKEN.search(line):
        return line
    body = line.rstrip()
    if body.endswith("\\"):
        return body[:-1].rstrip() + " -neg \\"
    return body + " -neg"


def _remove_neg_token(line: str) -> str:
    """Remove ``-neg`` from an FT line (unchanged if absent), cleaning up only the gap the
    token leaves behind.

    **Only** the space left by ``-neg`` itself is cleaned; the line's leading indentation and
    any other whitespace stay exactly as they are -- an earlier
    ``re.sub(r"[ \\t]{2,}", " ")`` collapsed **every** run of spaces inside the line and
    destroyed the script's indentation (continuation-line alignment the user had edited by
    hand).
    """
    if not _NEG_TOKEN.search(line):
        return line

    cleaned = re.sub(r"[ \t]*-neg(?![-\w])", "", line, count=1)
    return re.sub(r"[ \t]+$", "", cleaned)


def _neg_companion_ps_line(lines: list[str], ft_line_no: int) -> int | None:
    """Return the first PS line following this indirect FT in the same pipeline statement, or None.

    The generated indirect-axis sequence places its phase operation after its FT; do not search
    across a statement boundary into another axis.
    """
    for statement in _pipeline_statements(lines):
        if ft_line_no not in statement:
            continue
        for line_no in statement[statement.index(ft_line_no) + 1 :]:
            if _PS_LINE_TOKEN.search(lines[line_no]):
                return line_no
            if _is_ft_line(lines[line_no]):
                return None
        return None
    return None


def _conjugate_ps_line(line: str) -> str | None:
    """Transform the companion phase as p0' = (360 - p0) mod 360 and p1' = -p1.

    FT -neg conjugates indirect time-domain data, reversing frequency direction and phase sign.
    Compensate the phase when changing that option. Zero-order phase is periodic modulo 360
    degrees; first-order phase is negated without wrapping. Return None when the line has
    neither p0 nor p1 rather than inventing parameters.
    """
    if not (_PS_P0_TOKEN.search(line) or _PS_P1_TOKEN.search(line)):
        return None
    new = _PS_P0_TOKEN.sub(lambda m: f"{m.group(1)}{_wrap_p0(-float(m.group(2)))}", line, count=1)
    new = _PS_P1_TOKEN.sub(
        lambda m: f"{m.group(1)}{_format_phase(-float(m.group(2)))}", new, count=1
    )
    return new


def _wrap_p0(value: float) -> str:
    """Normalize negated zero-order phase to [0, 360): (360 - p0) mod 360."""
    return _format_phase(float(value) % 360.0)


def _format_phase(value: float) -> str:
    """Format phase values without redundant integer decimals or negative zero."""
    text = f"{value:.6g}"
    return "0" if text in ("-0", "-0.0") else text


def flip_indirect_ft_lines(
    script: str, *, ndim: int, flips: dict[str, bool]
) -> tuple[str, dict[str, bool]]:
    """Set the requested final -neg state for each indirect FT axis.

    On either transition, conjugate the corresponding PS phase using p0' = (360-p0) mod 360 and
    p1' = -p1. Repeated toggles remain reversible rather than accumulating phase drift.

    Return (new script, changed axis states). An unrecognized layout yields an empty state
    dictionary so callers can request script regeneration.
    """
    if not script or not flips:
        return script, {}
    lines = script.split("\n")
    indexes = _indirect_ft_line_indexes(lines, ndim)
    if indexes is None:
        return script, {}
    applied: dict[str, bool] = {}
    for axis, line_no in zip(_INDIRECT_AXIS_ORDER[int(ndim)], indexes):
        if axis not in flips:
            continue
        want = bool(flips[axis])
        if want == bool(_NEG_TOKEN.search(lines[line_no])):
            continue
        lines[line_no] = (
            _add_neg_token(lines[line_no]) if want else _remove_neg_token(lines[line_no])
        )

        ps_line_no = _neg_companion_ps_line(lines, line_no)
        if ps_line_no is not None:
            compensated = _conjugate_ps_line(lines[ps_line_no])
            if compensated is not None:
                lines[ps_line_no] = compensated
        applied[axis] = want
    return "\n".join(lines), applied


def indirect_neg_state(script: str, *, ndim: int) -> dict[str, bool]:
    """Whether each indirect dimension of the final script currently carries ``-neg``;
    empty dict when the layout cannot be recognised.
    """
    lines = script.split("\n")
    indexes = _indirect_ft_line_indexes(lines, ndim)
    if indexes is None:
        return {}
    return {
        axis: bool(_NEG_TOKEN.search(lines[line_no]))
        for axis, line_no in zip(_INDIRECT_AXIS_ORDER[int(ndim)], indexes)
    }


def _indirect_only_section(script: str) -> str | None:
    """3D NUS: cut out the "indirect-dimension processing from the retained reconstruction
    planes" section (step 3 of the final script).

    Only a statement that really reads back from ``nus3d_rc/...`` is cut (returns from that
    statement to the end of the script); when nothing can be cut it returns None -- the
    caller then falls back to re-running the whole script instead of silently running
    something else.
    """
    lines = script.split("\n")
    for statement in _pipeline_statements(lines):
        header = lines[statement[0]]
        if "xyz2pipe -in " in header and "nus3d_rc" in header:
            return "\n".join(lines[statement[0] :]) + "\n"
    return None


class PipelineStepRow(QWidget):
    """Single step line: status icon + name + description + run/artificial entrance + embedded
    details. Click on row to expand/Collapse details (input product, running record, parameter,
    script snapshot); LOCKED / OUTDATED / FAILED The reason is displayed in gray text; FAILED
    provides "View log" and "Retry".
    """

    run_requested = Signal(str)  # step_id

    rerun_final_requested = Signal(str, dict)
    manual_requested = Signal(str)
    show_spectrum_requested = Signal(str)
    ext_range_requested = Signal(str)

    segment_shift_requested = Signal(str)
    ref_spectrum_requested = Signal(str)
    clear_ref_requested = Signal(str)
    detail_toggled = Signal(str)
    view_log_requested = Signal(str)
    rank1_run_requested = Signal(str)

    def __init__(
        self, step_id: str, label: str, description: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)

        self.setObjectName("StepRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.step_id = step_id

        self._flip_ndim = 2
        self._rerun_widgets_visible = False
        self._indirect_neg: dict[str, bool] = {}

        self._indirect_nuclei: dict[str, str] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(0)

        header = QHBoxLayout()
        self.icon_label = QLabel()
        self.icon_label.setFixedWidth(24)
        header.addWidget(self.icon_label)
        header.addSpacing(4)
        text_box = QVBoxLayout()
        title_row = QHBoxLayout()
        self.name_label = QLabel(label)
        self.name_label.setStyleSheet("font-weight: bold;")
        title_row.addWidget(self.name_label)
        title_row.addStretch(1)
        self.status_label = QLabel("")
        title_row.addWidget(self.status_label)
        text_box.addLayout(title_row)
        self.desc_label = QLabel(description)
        self.desc_label.setStyleSheet(f"color: {TEXT_MUTED};")
        self.desc_label.setWordWrap(True)
        text_box.addWidget(self.desc_label)
        header.addLayout(text_box, 1)
        outer.addLayout(header)

        button_row = _FlowLayout()
        button_row.setContentsMargins(24, 0, 0, 0)

        self.ext_range_button = QPushButton(tr("direct dimension range"))
        self.ext_range_button.setToolTip(
            tr(
                'Set the direct-dimension extraction window (EXT -x1/-xn); "apply this range to '
                'the optimisation" is on by default and can be turned off; when unset the default '
                "(10.5-6.5) is "
                "used",
            )
        )
        self.ext_range_button.setVisible(self.step_id == "spectrum")
        self.ext_range_button.clicked.connect(lambda: self.ext_range_requested.emit(self.step_id))
        button_row.addWidget(self.ext_range_button)

        self.threshold_label = QLabel(tr("threshold (σ)"))
        self.threshold_label.setVisible(self.step_id == "peaks")
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setRange(30, 500)
        self.threshold_slider.setSingleStep(5)
        self.threshold_slider.setValue(350)
        self.threshold_slider.setFixedWidth(120)
        self.threshold_slider.setVisible(self.step_id == "peaks")
        self.threshold_spin = CommitDoubleSpinBox()
        self.threshold_spin.setRange(3.0, 1_000_000.0)
        self.threshold_spin.setSingleStep(0.5)
        self.threshold_spin.setDecimals(1)
        self.threshold_spin.setValue(35.0)
        self.threshold_spin.setVisible(self.step_id == "peaks")

        self._threshold_sync = False

        def _slider_to_spin(v: int) -> None:
            if self._threshold_sync:
                return
            self._threshold_sync = True
            try:
                self.threshold_spin.setValue(v / 10.0)
            finally:
                self._threshold_sync = False

        def _spin_to_slider(v: float) -> None:
            if self._threshold_sync:
                return
            self._threshold_sync = True
            try:
                self.threshold_slider.setValue(max(30, min(500, int(round(v * 10.0)))))
            finally:
                self._threshold_sync = False

        self.threshold_slider.valueChanged.connect(_slider_to_spin)
        self.threshold_spin.valueChanged.connect(_spin_to_slider)
        tip = tr(
            'Press Enter to confirm the threshold (σ); click "run / reprocess" to pick peaks again',
        )
        self.threshold_slider.setToolTip(tip)
        self.threshold_spin.setToolTip(tip)
        button_row.addWidget(self.threshold_label)
        button_row.addWidget(self.threshold_slider)
        button_row.addWidget(self.threshold_spin)

        self.grid_label = QLabel(tr("degree of optimisation"))
        self.grid_label.setVisible(self.step_id == "smile")
        self.grid_combo = QComboBox()
        for _n in (2, 3, 4, 5):
            self.grid_combo.addItem(f"{_n}x{_n}", _n)
        self.grid_combo.setCurrentIndex(2)
        self.grid_combo.setVisible(self.step_id == "smile")
        self.grid_combo.setToolTip(
            tr(
                "Optimisation depth n×n: scans n×n combinations of nSigma × threshold.\n2×2 = 4, "
                "3×3 = 9, 4×4 = 16, 5×5 = 25 combinations; the default is 4×4.\nA finer grid is "
                "slower (each combination does its own reconstruction and "
                "evaluation).",
            )
        )
        fit_combo_width(self.grid_combo)
        button_row.addWidget(self.grid_label)
        button_row.addWidget(self.grid_combo)

        self.smile_gap = QFrame()
        self.smile_gap.setFrameShape(QFrame.Shape.NoFrame)
        self.smile_gap.setFixedSize(12, 1)
        self.smile_gap.setVisible(self.step_id == "smile")
        button_row.addWidget(self.smile_gap)
        self.rank_label = QLabel(tr("sort"))
        self.rank_label.setVisible(self.step_id == "smile")
        self.rank_combo = QComboBox()

        self.rank_combo.addItem(tr("Pure peaks first (fewer false peaks)"), "true_peaks")
        self.rank_combo.addItem(tr("Consistency first (small residuals)"), "consistency")
        self.rank_combo.setVisible(self.step_id == "smile")
        self.rank_combo.setToolTip(
            tr(
                "Ranking criterion: decides which three of the 25 parameter combinations provide "
                'the\nfinal-run scripts.\n\n- Pure peaks first (default): ranks by "stable peaks '
                'minus suspected spurious peaks".\n  A stable peak is one that appears in most '
                "parameter combinations;\n  a suspected spurious peak appears in only a few.\n  "
                'Suited to "as few spurious peaks and as many true peaks as possible";\n  the '
                "number of peaks comes from SMILE's own low threshold (3 sigma),\n  and under this "
                "criterion every combination is reconstructed from all sampling points\n  (the "
                "final-spectrum criterion),\n  independently of the threshold used by the "
                "peak-picking step (35 sigma by default).\n\n- Consistency first: ranks by the "
                "residual on the held-out sampling points.\n  Under this criterion every "
                "combination is reconstructed from the held-out points:\n  the reconstruction is "
                "then compared with the measured values (a smaller residual is better,\n  and a "
                "correlation coefficient closer to 1 is better), independently of the peak "
                'count.\n  Suited to wanting trustworthy "shape and intensities" first;\n  when '
                "no hold-out table is available it falls back to ranking by fit "
                "residual.",
            )
        )
        fit_combo_width(self.rank_combo)
        button_row.addWidget(self.rank_label)
        button_row.addWidget(self.rank_combo)
        self.ref_button = QPushButton(tr("Reference spectrum"))
        self.ref_button.setToolTip(
            tr(
                "Use an existing peak table as a picking constraint: estimate an overall ppm "
                "offset, then keep matching peaks without changing the spectrum. Unreliable "
                "or ambiguous alignment keeps all peaks and reports a warning; fewer than five "
                "reference peaks use unshifted matching.",
            )
        )
        self.ref_button.setVisible(self.step_id == "peaks")
        self.ref_button.clicked.connect(lambda: self.ref_spectrum_requested.emit(self.step_id))

        if self.step_id == "peaks":
            button_row.add_break()
        button_row.addWidget(self.ref_button)
        self.ref_label = QLabel("")
        self.ref_label.setVisible(self.step_id == "peaks")
        self.ref_label.setStyleSheet("color: #16a085;")
        button_row.addWidget(self.ref_label)
        self.clear_ref_button = QPushButton(tr("Clear"))
        self.clear_ref_button.setVisible(False)
        self.clear_ref_button.setToolTip(tr("Clear reference spectrum constraints"))
        self.clear_ref_button.clicked.connect(lambda: self.clear_ref_requested.emit(self.step_id))
        button_row.addWidget(self.clear_ref_button)

        self.segment_shift_button = QPushButton(tr("Inter-part field drift"))
        self.segment_shift_button.setToolTip(
            tr(
                "Segmented data only: set a frequency offset (Hz) for parts 2..N (part 1 is the "
                'reference); "run" writes PS -rs into each part\'s fid.com according to these '
                "values. There is no automatic detection any more.",
            )
        )
        self.segment_shift_button.setVisible(False)
        self.segment_shift_button.clicked.connect(
            lambda: self.segment_shift_requested.emit(self.step_id)
        )
        button_row.addWidget(self.segment_shift_button)
        self.run_button = QPushButton(tr("Run"))
        self.run_button.setVisible(False)
        self.run_button.clicked.connect(lambda: self.run_requested.emit(self.step_id))
        button_row.addWidget(self.run_button)

        self.rerun_group = QFrame()
        self.rerun_group.setObjectName("RerunGroup")
        self.rerun_group.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.rerun_group.setStyleSheet(
            f"#RerunGroup {{ border: 1px solid {PANEL_BORDER}; "
            f"border-radius: 6px; background: {SURFACE_ALT}; }}"
        )
        self.rerun_group.setToolTip(
            tr(
                "Re-run the last generated final script without re-optimisation. The indirect flip "
                "next to it applies to this run only.",
            )
        )
        self.rerun_group.setVisible(False)
        _group_row = QHBoxLayout(self.rerun_group)
        _group_row.setContentsMargins(8, 3, 8, 3)
        _group_row.setSpacing(6)
        self.flip_group_label = QLabel(tr("Indirect flip"))
        self.flip_group_label.setStyleSheet(f"color: {TEXT_MUTED};")
        _group_row.addWidget(self.flip_group_label)
        self.rerun_final_button = QPushButton(tr("Re-run the final script"))
        self.rerun_final_button.setToolTip(
            tr(
                "Without re-optimisation, directly reuse the last generated final script and "
                "re-run "
                "it",
            )
        )
        self.rerun_final_button.setVisible(False)
        self.rerun_final_button.clicked.connect(self._on_rerun_final_clicked)

        self.flip_indirect_check = QCheckBox(tr("Indirect (F1)"))
        self.flip_indirect_check.setToolTip(
            tr(
                "Add FT -neg to the indirect dimension (F1) and re-run the final script; "
                "unchecking removes it again. Only the indirect-dimension FT line is changed.",
            )
        )
        self.flip_indirect_check.setVisible(False)
        self.flip_indirect_combo = QComboBox()

        self.flip_indirect_combo.addItem(tr("Indirect (F2)"), {"ft_neg_f2": True})
        self.flip_indirect_combo.addItem(tr("Indirect (F1)"), {"ft_neg_f1": True})
        self.flip_indirect_combo.addItem(tr("F1 and F2"), {"ft_neg_f1": True, "ft_neg_f2": True})

        self.flip_indirect_combo.setCurrentIndex(-1)
        self.flip_indirect_combo.setPlaceholderText(tr("no flip"))
        self.flip_indirect_combo.setToolTip(
            tr(
                "Flip the FT sign (FT -neg) of the chosen indirect dimension(s) and re-run the "
                "final script; choosing an already-flipped entry again removes the flip. Only the "
                "indirect-dimension FT line is changed.",
            )
        )
        self.flip_indirect_combo.setVisible(False)
        fit_combo_width(self.flip_indirect_combo)

        _flip_metrics = self.flip_indirect_combo.fontMetrics()
        self.flip_indirect_combo.setMinimumWidth(
            max(
                self.flip_indirect_combo.minimumWidth(),
                _flip_metrics.horizontalAdvance(self.flip_indirect_combo.placeholderText()) + 38,
            )
        )

        _group_row.addWidget(self.flip_indirect_check)
        _group_row.addWidget(self.flip_indirect_combo)
        _group_row.addWidget(self.rerun_final_button)
        button_row.addWidget(self.rerun_group)
        self.show_spectrum_button = QPushButton(tr("display spectrum"))
        self.show_spectrum_button.setToolTip(
            tr(
                "The spectrum panel on the right displays the final spectrum of the current data "
                "spectrum "
                "folder",
            )
        )
        self.show_spectrum_button.setVisible(False)
        self.show_spectrum_button.clicked.connect(
            lambda: self.show_spectrum_requested.emit(self.step_id)
        )

        if self.step_id == "spectrum":
            button_row.add_break()
        button_row.addWidget(self.show_spectrum_button)
        self.manual_button = QPushButton(tr("Manual"))
        self.manual_button.setToolTip(
            tr(
                "Script editor: open this step's script, edit it, then run it",
            )
        )
        self.manual_button.setVisible(False)
        self.manual_button.clicked.connect(lambda: self.manual_requested.emit(self.step_id))
        button_row.addWidget(self.manual_button)

        self.rank1_button = QPushButton(tr("Press Rank1 to rerun"))
        self.rank1_button.setToolTip(
            tr(
                "Use SMILE to scan the selected Rank1 parameter, rerun the final script, and use "
                "the result as the current "
                "spectrum",
            )
        )
        self.rank1_button.setVisible(False)
        self.rank1_button.clicked.connect(lambda: self.rank1_run_requested.emit(self.step_id))
        button_row.addWidget(self.rank1_button)
        outer.addLayout(button_row)

        self.reason_label = QLabel("")
        self.reason_label.setStyleSheet(f"color: {TEXT_MUTED};")
        self.reason_label.setWordWrap(True)
        self.reason_label.setVisible(False)
        outer.addWidget(self.reason_label)

        self.detail_frame = QFrame()
        self.detail_frame.setFrameShape(QFrame.Shape.StyledPanel)

        self.detail_frame.setStyleSheet(
            f"QFrame {{ background: {SURFACE_ALT}; border: 1px solid {PANEL_BORDER}; "
            "border-radius: 6px; }"
        )
        self.detail_frame.setVisible(False)
        detail_layout = QVBoxLayout(self.detail_frame)
        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_label.setStyleSheet(f"color: {TEXT_PRIMARY}; padding: 6px; border: none;")
        detail_layout.addWidget(self.detail_label)
        detail_buttons = QHBoxLayout()
        self.view_log_button = QPushButton(tr("View log"))
        self.view_log_button.setVisible(False)
        self.view_log_button.clicked.connect(lambda: self.view_log_requested.emit(self.step_id))
        detail_buttons.addWidget(self.view_log_button)
        self.retry_button = QPushButton(tr("Try again"))
        self.retry_button.setVisible(False)
        self.retry_button.clicked.connect(lambda: self.run_requested.emit(self.step_id))
        detail_buttons.addWidget(self.retry_button)
        detail_layout.addLayout(detail_buttons)
        outer.addWidget(self.detail_frame)

        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        """Click row (non-button area) Switch details to expand/close."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.detail_toggled.emit(self.step_id)
        super().mousePressEvent(event)

    def set_ext_override(self, text: str) -> None:
        """Update the "direct dimension range" button text (displays the current range when the
        value is set).
        """
        self.ext_range_button.setText(text)

    def get_threshold(self) -> float:
        """Peak picking step threshold (σ); non-peaks steps return default 35.0 (patch29hn)."""
        return self.threshold_spin.value() if self.step_id == "peaks" else 35.0

    def get_localization_method(self) -> str:
        """Return the fixed three-point parabolic method for compatibility with older callers."""
        return "parabolic"

    def set_ref_text(self, text: str) -> None:
        """Display the selected reference spectrum (0.2.199-patch29dl)."""
        self.ref_label.setText(text)
        self.clear_ref_button.setVisible(bool(text) and self.step_id == "peaks")

    def clear_ref_display(self) -> None:
        """Clear the reference spectrum display."""
        self.ref_label.setText("")
        self.clear_ref_button.setVisible(False)

    def set_detail(self, text: str, failed: bool = False) -> None:
        """Fill in the details text."""
        self.detail_label.setText(text)
        self.view_log_button.setVisible(failed)
        self.retry_button.setVisible(failed)

    def set_status(self, status: str, reason: str = "") -> None:
        icon = STATUS_ICON.get(status, "·")
        label = STATUS_TEXT.get(status, status)
        self.status_label.setText(f"{icon} {label}")

        _color = STATUS_COLORS.get(status, TEXT_MUTED)
        self.status_label.setStyleSheet(f"color: {_color}; font-weight: bold;")
        self.icon_label.setStyleSheet(f"color: {_color};")
        tooltip = tr("state: {p0}", p0=STATUS_TEXT.get(status, status))
        if reason:
            tooltip += f"\n{reason}"
        self.status_label.setToolTip(tooltip)

        if status in ("LOCKED", "OUTDATED", "FAILED"):
            self.reason_label.setText(reason or STATUS_TEXT.get(status, status))
            self.reason_label.setVisible(True)
        else:
            self.reason_label.setVisible(False)
        threshold_ok = status in ("READY", "SUCCESS", "OUTDATED", "FAILED")
        if self.step_id == "peaks":
            self.threshold_slider.setEnabled(threshold_ok)
            self.threshold_spin.setEnabled(threshold_ok)

        if status == "OUTDATED":
            self.run_button.setText(tr("Update results"))
            self.run_button.setVisible(True)
            self.run_button.setToolTip(
                tr(
                    "Input/parameter has changed, run again to update the results",
                )
            )
        elif status == "SUCCESS":
            if self.step_id == "spectrum":
                self.run_button.setText(tr("re-optimisation"))
                self.run_button.setToolTip(
                    tr(
                        "Re-execute full automatic processing (phase / parameter optimisation + "
                        "final "
                        "run)",
                    )
                )
                self._set_rerun_widgets_visible(True)
            else:
                self.run_button.setText(tr("Run again"))
                self._set_rerun_widgets_visible(False)
            self.run_button.setVisible(True)
            self.run_button.setToolTip(
                tr(
                    "Processed Complete; click to force reprocessing (downstream steps will be "
                    "marked as "
                    "expired)",
                )
            )
        else:
            self.run_button.setText(tr("run"))
            self.run_button.setVisible(status == "READY")
            self.run_button.setToolTip(tr("run current step"))
            self._set_rerun_widgets_visible(False)

        self.rank1_button.setVisible(status == "SUCCESS" and self.step_id == "smile")

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    def _set_rerun_widgets_visible(self, visible: bool) -> None:
        """Show/hide "Re-run the final script" and the indirect flip (one unit inside the same
        box) together.
        """
        self._rerun_widgets_visible = bool(visible)
        self.rerun_group.setVisible(bool(visible))
        self.rerun_final_button.setVisible(bool(visible))
        self._sync_flip_widget_visibility()

    def _sync_flip_widget_visibility(self) -> None:
        """Flip-control visibility: spectrum step + an existing final script; 2D checkbox, 3D
        drop-down, no entrance for 1D.
        """
        visible = self._rerun_widgets_visible and self.step_id == "spectrum"
        self.flip_indirect_check.setVisible(visible and self._flip_ndim == 2)
        self.flip_indirect_combo.setVisible(visible and self._flip_ndim >= 3)

    def set_indirect_dimension(self, ndim: int) -> None:
        """Pick the control's shape from the data dimensionality (2D checkbox / 3D three-entry
        drop-down).
        """
        self._flip_ndim = int(ndim or 2)
        self._sync_flip_widget_visibility()

    def set_indirect_nuclei(self, nuclei: dict[str, str] | None) -> None:
        """Label indirect flip controls using the experiment's F1/F2 nuclei.

        When metadata is absent or unreadable, an empty mapping restores logical F1/F2 labels.
        """
        self._indirect_nuclei = {
            str(axis): str(name or "").strip()
            for axis, name in (nuclei or {}).items()
            if str(name or "").strip()
        }
        self._apply_flip_labels()

    def _indirect_axis_text(self, axis: str) -> str:
        """Use the known nucleus as the axis label, otherwise its logical F-axis name."""
        return self._indirect_nuclei.get(axis, "") or axis

    def _apply_flip_labels(self) -> None:
        """Update indirect flip labels without changing ft_neg_f1/ft_neg_f2 payload keys or option
        order.
        """
        nuc_f1 = self._indirect_nuclei.get("F1", "")
        nuc_f2 = self._indirect_nuclei.get("F2", "")
        self.flip_indirect_check.setText(
            tr("Indirect ({p0})", p0=nuc_f1) if nuc_f1 else tr("Indirect (F1)")
        )
        self.flip_indirect_combo.setItemText(
            0, tr("Indirect ({p0})", p0=nuc_f2) if nuc_f2 else tr("Indirect (F2)")
        )
        self.flip_indirect_combo.setItemText(
            1, tr("Indirect ({p0})", p0=nuc_f1) if nuc_f1 else tr("Indirect (F1)")
        )
        if nuc_f1 or nuc_f2:
            self.flip_indirect_combo.setItemText(
                2,
                tr(
                    "{p0} and {p1}",
                    p0=self._indirect_axis_text("F1"),
                    p1=self._indirect_axis_text("F2"),
                ),
            )
        else:
            self.flip_indirect_combo.setItemText(2, tr("F1 and F2"))

        fit_combo_width(self.flip_indirect_combo)

    def set_indirect_neg_state(self, state: dict[str, bool] | None) -> None:
        """Fill the controls from the final script's current state (2D checkbox checked =
        indirect dimension F1 already carries ``-neg``).

        The 2D checkbox is itself a **state**: without filling it back in, the next ordinary
        re-run (only to update the direct-dimension range) would silently drop the previous
        flip. The 3D drop-down is a command and does not show the current state, but the flip
        conversion needs that state (see :meth:`_take_flip_sampling`).
        """
        self._indirect_neg = {str(axis): bool(neg) for axis, neg in (state or {}).items()}
        if self._flip_ndim < 3:
            self.flip_indirect_check.setChecked(bool(self._indirect_neg.get("F1", False)))

    def _take_flip_sampling(self) -> dict[str, bool]:
        """Current control choice -> sampling overrides (explicitly "should this axis finally
        carry ``-neg``").

        The keys use the official names ``ft_neg_f1``/``ft_neg_f2`` (same semantics as the
        global ``ft_neg``: they decide **directly** whether to add it, not an inverted
        auto-criterion).

        2D: the checkbox's checked state is the target state; 3D: the drop-down is a
        **command** (choosing an entry sets the dimensions it contains, choosing the same
        entry again sets them back to "no add" -- here the panel computes the **explicit
        target state** from the row's stored final-script state, so the script layer always
        receives "add/no add"), and the drop-down is reset to "nothing selected".
        """
        if self.step_id != "spectrum":
            return {}
        if self._flip_ndim >= 3:
            data = self.flip_indirect_combo.currentData()
            self.flip_indirect_combo.setCurrentIndex(-1)
            if not isinstance(data, dict):
                return {}
            return {
                key: not bool(self._indirect_neg.get(axis, False))
                for key, axis in (("ft_neg_f2", "F2"), ("ft_neg_f1", "F1"))
                if key in data
            }
        return {"ft_neg_f1": bool(self.flip_indirect_check.isChecked())}

    def _on_rerun_final_clicked(self) -> None:
        """'Re-run the final script': emit the indirect-flip choice together with the signal."""
        self.rerun_final_requested.emit(self.step_id, self._take_flip_sampling())


def _looks_like_memory_guard(exc: BaseException) -> bool:
    """Is this exception the memory guard's? (two producers, one wording each)"""
    text = str(exc)
    return (
        tr("The current memory cannot process this spectrum") in text
        or tr("which cannot be handled at the current memory level") in text
    )


class PipelinePanel(QWidget):
    """Pipeline Ribbon: Contextual Breadcrumbs + Next Step Tips + Step List."""

    log_message = Signal(str)
    log_scoped = Signal(str, str)
    memory_guard_requested = Signal(str)
    run_finished = Signal()
    run_target_finished = Signal(str, str)
    spectrum_results_changed = Signal(str, str)
    run_started = Signal(str, str)
    manual_open_requested = Signal(str)
    show_spectrum_requested = Signal(str)
    view_log_requested = Signal(str)
    progress_updated = Signal(str)
    batch_summary_requested = Signal(object)
    rank1_run_requested = Signal(str)

    def __init__(
        self,
        manager: ProjectManager | None = None,
        controller: ProcessingController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager or ProjectManager()
        self.controller = controller or ProcessingController()
        self._current_exp_id: str = ""
        self._selection_kind: str = ""
        self._current_data_id: str = ""
        self._rows: dict[str, PipelineStepRow] = {}

        self._final_ext: dict[tuple[str, str], tuple[str, str, bool]] = {}

        self._segment_shifts: dict[tuple[str, str], dict[int, float]] = {}

        self._spectrum_report_cache: dict[str, str] = {}
        self._running_targets: dict[tuple[str, str, str], int] = {}
        self._running_targets_lock = threading.Lock()

        self._ref_info: dict[tuple[str, str], dict] = {}

        self._threshold_by_data: dict[tuple[str, str], float] = {}

        self._threshold_custom_by_data: dict[tuple[str, str], bool] = {}

        self._nus_cache: dict[tuple[str, str], bool] = {}
        self._smile_step_visible = False
        self._ndim_cache: dict[tuple[str, str], int] = {}

        self._nucleus_cache: dict[tuple[str, str], str] = {}

        self._axis_nuclei_cache: dict[tuple[str, str], dict[str, str]] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        panel_title = QLabel(tr("Processing flow"))
        panel_title.setObjectName("PanelTitle")
        title_row.addWidget(panel_title)
        flow_hint = QLabel(tr("import -> FID -> spectrum -> peak selection"))
        flow_hint.setObjectName("PanelSubtitle")
        title_row.addWidget(flow_hint)
        title_row.addStretch(1)
        layout.addLayout(title_row)

        self.context_label = QLabel(tr("project not open"))
        self.context_label.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {TEXT_PRIMARY};"
        )
        layout.addWidget(self.context_label)

        self.next_label = QLabel("")
        self.next_label.setWordWrap(True)
        self.next_label.setStyleSheet("color: #16a085;")
        layout.addWidget(self.next_label)
        self.batch_progress_label = QLabel("")
        self.batch_progress_label.setVisible(False)
        self.batch_progress_label.setStyleSheet(
            "color: #16a085; font-weight: bold; padding: 2px 0;"
        )
        self.batch_progress_label.setWordWrap(True)
        layout.addWidget(self.batch_progress_label)
        self.progress_updated.connect(self._on_progress_updated)

        self.run_finished.connect(self._refresh_after_run)
        self.hint_bubble = QLabel("")
        self.hint_bubble.setVisible(False)
        self.hint_bubble.setWordWrap(True)
        self.hint_bubble.setStyleSheet(
            "background: #fef9e7; border: 1px solid #f5b041; color: #935116; padding: 4px 8px;"
        )
        layout.addWidget(self.hint_bubble)

        steps_box = QVBoxLayout()
        for step_id, label, description, _deps in _visible_pipeline_steps():
            row = PipelineStepRow(step_id, label, description)
            row.run_requested.connect(self._on_run_requested)
            row.rerun_final_requested.connect(self._on_rerun_final_requested)
            row.manual_requested.connect(self.manual_open_requested.emit)
            row.show_spectrum_requested.connect(self.show_spectrum_requested.emit)
            row.ext_range_requested.connect(self._on_ext_range_requested)
            row.segment_shift_requested.connect(self._on_segment_shift_requested)
            row.ref_spectrum_requested.connect(self._on_pick_reference)
            row.clear_ref_requested.connect(self._on_clear_reference)
            row.detail_toggled.connect(self._toggle_step_detail)
            row.view_log_requested.connect(self.view_log_requested.emit)
            row.rank1_run_requested.connect(self.rank1_run_requested.emit)
            steps_box.addWidget(row)
            self._rows[step_id] = row

        peaks_row = self._rows.get("peaks")
        if peaks_row is not None:
            peaks_row.threshold_spin.valueChanged.connect(self._store_current_threshold)
            peaks_row.threshold_spin.editingFinished.connect(self._mark_current_threshold_custom)
            peaks_row.threshold_slider.sliderReleased.connect(self._mark_current_threshold_custom)

        smile_row = self._rows.get("smile")
        if smile_row is not None:
            smile_row.grid_combo.currentIndexChanged.connect(self._store_smile_grid_size)
            smile_row.rank_combo.currentIndexChanged.connect(self._store_smile_rank_mode)
        steps_box.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.viewport().setAutoFillBackground(False)
        content = QWidget()
        content.setAutoFillBackground(False)
        content.setLayout(steps_box)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.refresh()

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    def _data_facts(self, exp_id: str, data_id: str) -> dict:
        """Current data fact (ndim / direct dimension nuclide / whether NUS). 0.2.199-patch29hz:
        Unify ProcessingController.data_facts; test avatars, etc. If this interface is not
        provided, press "Unknown" to downgrade (processed the same as read failure, no error
        will be thrown).
        """
        getter = getattr(self.controller, "data_facts", None)
        if getter is None:
            return {}
        try:
            return dict(getter(exp_id, data_id) or {})
        except Exception:  # noqa: BLE001
            return {}

    @property
    def current_data_id(self) -> str:
        """The currently selected sample data id (empty string if not selected)."""
        return str(self._current_data_id or "")

    def set_context(self, exp_id: str, data_id: str | None = None) -> None:
        """Compatible entry: Set context according to experiment type (data_id defaults to the
        first sample data).
        """
        self._selection_kind = "experiment" if exp_id else ""
        self._current_exp_id = exp_id or ""
        if data_id is not None:
            self._current_data_id = data_id
        self.refresh()

    def set_selection(self, kind: str, exp_id: str, data_id: str = "") -> None:
        """Refresh by tree selection type: project/experiment displays "unselected data";
        data/folder displays steps.
        """
        self._selection_kind = kind or ""
        self._current_exp_id = exp_id or ""
        self._current_data_id = data_id
        self.refresh()

    def current_experiment_id(self) -> str:
        return self._current_exp_id

    def _sync_reference_display(self) -> None:
        """Synchronize the "Reference:..." line display according to the currently selected data
        (0.2.199-patch29fx: Reference is isolated by data, switching data does not remain to
        other data).
        """
        row = self._rows.get("peaks")
        if row is None:
            return
        ref = (
            self._ref_info.get((self._current_exp_id, self._current_data_id))
            if self._current_data_id
            else None
        )
        if ref:
            row.set_ref_text(
                tr("refer to: {p0} ({p1} peak)", p0=ref["label"], p1=len(ref["peaks"]))
            )
        else:
            row.clear_ref_display()

    def _store_current_threshold(self, value: float) -> None:
        """The threshold is recorded into the current data immediately and persisted
        (0.2.199-patch29fz/patch29ga/patch29gc); does not override user's explicit custom tags.
        """
        if not (self._current_exp_id and self._current_data_id):
            return
        key = (self._current_exp_id, self._current_data_id)
        self._threshold_by_data[key] = float(value)
        custom = self._threshold_custom_by_data.get(key, False)
        try:
            from gui.per_data_records import update_ui_state

            update_ui_state(
                self.manager,
                self._current_exp_id,
                self._current_data_id,
                "peaks",
                self._peaks_ui_state(threshold=float(value), custom=custom),
            )
        except Exception:  # noqa: BLE001
            pass

    def _mark_current_threshold_custom(self) -> None:
        """User explicitly edits the threshold (Input completed/Slider lets go) to set a custom
        tag.
        """
        if not (self._current_exp_id and self._current_data_id):
            return
        key = (self._current_exp_id, self._current_data_id)
        self._threshold_custom_by_data[key] = True
        if key not in self._threshold_by_data:
            self._threshold_by_data[key] = 35.0
        try:
            from gui.per_data_records import update_ui_state

            update_ui_state(
                self.manager,
                self._current_exp_id,
                self._current_data_id,
                "peaks",
                self._peaks_ui_state(threshold=self._threshold_by_data[key], custom=True),
            )
        except Exception:  # noqa: BLE001
            pass

    def _peaks_ui_state(self, **extra: object) -> dict:
        """Return persistent peak-picking UI state; currently only the threshold is stored."""
        key = (self._current_exp_id, self._current_data_id)
        state: dict = {
            "threshold": float(self._threshold_by_data.get(key, 35.0)),
            "custom": bool(self._threshold_custom_by_data.get(key, False)),
        }
        state.update(extra)
        return state

    def _threshold_for(self, exp_id: str, data_id: str) -> float:
        """The data threshold: session cache takes priority, otherwise reads
        d_xxx/ui_state.json(patch29ga); the old default 15/25σ (not explicitly customized) is
        migrated to 35σ(patch29gc/patch29hn).
        """
        key = (exp_id, data_id)
        if key not in self._threshold_by_data:
            value = 35.0
            custom = False
            try:
                from gui.per_data_records import load_ui_state

                peaks_state = load_ui_state(self.manager, exp_id, data_id).get("peaks") or {}
                raw = peaks_state.get("threshold")
                custom = bool(peaks_state.get("custom", False))
                if raw is not None:
                    value = float(raw)

                if not custom and (abs(value - 15.0) < 1e-9 or abs(value - 25.0) < 1e-9):
                    value = 35.0
            except Exception:  # noqa: BLE001
                pass
            self._threshold_by_data[key] = value
            self._threshold_custom_by_data[key] = custom
        return self._threshold_by_data[key]

    def _store_smile_grid_size(self, *_args) -> None:
        """Write the SMILE optimisation degree of the current data into ui_state (same as the peak
        threshold).
        """
        row = self._rows.get("smile")
        if row is None or not (self._current_exp_id and self._current_data_id):
            return
        try:
            from gui.per_data_records import update_ui_state

            update_ui_state(
                self.manager,
                self._current_exp_id,
                self._current_data_id,
                "smile",
                {"grid_size": int(row.grid_combo.currentData() or 4)},
            )
        except Exception:  # noqa: BLE001
            pass

    def _store_smile_rank_mode(self, *_args) -> None:
        """Write the SMILE sorting caliber of the current data into ui_state."""
        row = self._rows.get("smile")
        if row is None or not (self._current_exp_id and self._current_data_id):
            return
        try:
            from gui.per_data_records import update_ui_state

            update_ui_state(
                self.manager,
                self._current_exp_id,
                self._current_data_id,
                "smile",
                {"rank_mode": str(row.rank_combo.currentData() or "true_peaks")},
            )
        except Exception:  # noqa: BLE001
            pass

    def _sync_smile_grid_size(self) -> None:
        """Displays SMILE degree of optimisation based on current data (read ui_state; default
        5x5).
        """
        row = self._rows.get("smile")
        if row is None or not (self._current_exp_id and self._current_data_id):
            return
        key = (self._current_exp_id, self._current_data_id)
        if key == getattr(self, "_grid_key", None):
            return
        size = 4
        try:
            from gui.per_data_records import load_ui_state

            size = int(
                (load_ui_state(self.manager, key[0], key[1]).get("smile") or {}).get("grid_size", 4)
            )
        except Exception:  # noqa: BLE001
            size = 5
        self._grid_key = key
        try:
            from gui.per_data_records import load_ui_state

            mode = str(
                (load_ui_state(self.manager, key[0], key[1]).get("smile") or {}).get(
                    "rank_mode", "true_peaks"
                )
            )
            r_index = row.rank_combo.findData(mode)
            if r_index >= 0:
                row.rank_combo.setCurrentIndex(r_index)
        except Exception:  # noqa: BLE001
            pass
        index = row.grid_combo.findData(max(2, min(5, size)))
        if index >= 0:
            row.grid_combo.setCurrentIndex(index)

    def _current_statuses(self) -> dict[str, str]:
        """The step status of the currently selected sample data; Sample data not selected/Old
        order sample data rollback experiment type aggregation.
        """
        self._sync_smile_grid_size()
        if self._current_data_id:
            return compute_data_step_statuses(
                self.manager, self._current_exp_id, self._current_data_id
            )
        return compute_step_statuses(self.manager, self._current_exp_id)

    def _data_is_nus(self, exp_id: str, data_id: str) -> bool:
        """Whether the current data is detected as NUS (only NUS displays SMILE optimisation,
        0.2.199-patch29gd). uncertain/If the read fails, press "No" NUS processing -- "Like NUS
        but with actual full sampling" is also hidden.
        """
        if not (exp_id and data_id):
            return False
        key = (exp_id, data_id)
        if key not in self._nus_cache:
            facts = self._data_facts(exp_id, data_id)
            self._nus_cache[key] = bool(facts.get("is_nus", False))
        return self._nus_cache[key]

    def _smile_supported(self, exp_id: str, data_id: str) -> bool:
        """Is SMILE optimisation available -- only **2D** NUS(user 2026-09-11). The SMILE
        optimisation of 3D NUS is not ideal for the time being, and the entrance is hidden first
        (non-NUS is also hidden, 0.2.199-patch29gd).
        """
        if not (exp_id and data_id):
            return False
        return self._data_is_nus(exp_id, data_id) and self._data_ndim(exp_id, data_id) == 2

    def _data_ndim(self, exp_id: str, data_id: str) -> int:
        """Current data dimension (failure to read will be treated as 2 and will not affect the
        2D/3D main process).
        """
        if not (exp_id and data_id):
            return 2
        key = (exp_id, data_id)
        if key not in self._ndim_cache:
            facts = self._data_facts(exp_id, data_id)
            self._ndim_cache[key] = int(facts.get("ndim", 2) or 2)
        return self._ndim_cache[key]

    def _data_direct_nucleus(self, exp_id: str, data_id: str) -> str:
        """Current data direct dimension nuclide (returns empty string if read fails, uses loose
        file for range verification).
        """
        if not (exp_id and data_id):
            return ""
        key = (exp_id, data_id)
        if key not in self._nucleus_cache:
            facts = self._data_facts(exp_id, data_id)
            self._nucleus_cache[key] = str(facts.get("direct_nucleus", "") or "")
        return self._nucleus_cache[key]

    def _data_axis_nuclei(self, exp_id: str, data_id: str) -> dict[str, str]:
        """Return the selected dataset's F1/F2/F3 nucleus mapping, or an empty mapping when
        unknown.
        """
        if not (exp_id and data_id):
            return {}
        key = (exp_id, data_id)
        if key not in self._axis_nuclei_cache:
            self._axis_nuclei_cache[key] = self._read_axis_nuclei(exp_id, data_id)
        return self._axis_nuclei_cache[key]

    def _read_axis_nuclei(self, exp_id: str, data_id: str) -> dict[str, str]:
        """Read axis nuclei from imported dataset.dimensions metadata.

        Share the viewer's nucleus inference: prefer observed frequency sf, then stored nucleus.
        Missing or corrupt metadata leaves nuclei unknown and controls use F-axis labels.
        """
        try:
            from viewer.axis_labels import nuclei_from_metadata

            payload = json.loads(
                self.manager.data_metadata_path(exp_id, data_id).read_text(encoding="utf-8")
            )
        except Exception:  # noqa: BLE001
            return {}
        nuclei = nuclei_from_metadata(payload) or []
        return {
            f"F{index + 1}": str(name)
            for index, name in enumerate(nuclei)
            if str(name or "").strip()
        }

    def _set_peaks_visible(self, visible: bool) -> None:
        """Display and hide peak selection step rows based on current data (1D does not require
        peak selection, patch29gj).
        """
        row = self._rows.get("peaks")
        if row is not None:
            row.setVisible(bool(visible))

    def _set_smile_visible(self, visible: bool) -> None:
        """Shows and hides SMILE optimisation step lines based on current data."""
        self._smile_step_visible = bool(visible)
        row = self._rows.get("smile")
        if row is not None:
            row.setVisible(self._smile_step_visible)

    def refresh(self) -> None:
        """Refresh contextual labels and step status."""
        project = self.manager.project
        if project is None or not self._current_exp_id:
            self.context_label.setText(tr("project not open"))
            self.next_label.setText("")
            for row in self._rows.values():
                row.set_status("LOCKED")
            self._set_smile_visible(False)
            self._set_peaks_visible(True)
            self._sync_reference_display()
            self._refresh_expanded_details()
            return
        if self._selection_kind in ("project", "experiment"):
            exp = project.experiment(self._current_exp_id)
            label = exp.title if exp is not None else project.name
            self.context_label.setText(tr("{p0} -- No data selected", p0=label))
            self.next_label.setText(
                tr(
                    "Please select the Data node on the left to view/run processing steps",
                )
            )
            for row in self._rows.values():
                row.set_status("LOCKED")
                row.manual_button.setVisible(False)
            self._set_smile_visible(False)
            self._set_peaks_visible(True)
            self._sync_reference_display()
            self._refresh_expanded_details()
            return
        exp = project.experiment(self._current_exp_id)
        exp_title = exp.title if exp is not None else self._current_exp_id
        group = (
            self.manager.group_of_data(self._current_exp_id, self._current_data_id)
            if self._current_data_id
            and self.manager is not None
            and self.manager.project is not None
            else None
        )
        context_text = f"{project.name} / {exp_title} ({self._current_exp_id})"
        if group is not None:
            context_text += tr(" [Group {p0}: {p1} data]", p0=group.id, p1=len(group.data_ids))
        self.context_label.setText(context_text)
        self._sync_reference_display()

        self._update_segment_shift_button()

        self._set_smile_visible(self._smile_supported(self._current_exp_id, self._current_data_id))
        self._set_peaks_visible(self._data_ndim(self._current_exp_id, self._current_data_id) != 1)
        statuses = self._current_statuses()

        peaks_row = self._rows.get("peaks")
        if peaks_row is not None:
            peaks_row.threshold_spin.setValue(
                self._threshold_for(self._current_exp_id, self._current_data_id)
            )

        outdated_next = next(
            (sid for sid, st in statuses.items() if sid != "smile" and st == "OUTDATED"),
            None,
        )
        next_step = next(
            (sid for sid, st in statuses.items() if sid != "smile" and st == "READY"),
            None,
        )
        smile_status = statuses.get("smile") if self._smile_step_visible else None
        if smile_status == "OUTDATED":
            optional_text = tr("SMILE optimisation (rerun)")
        elif smile_status == "READY":
            optional_text = tr("SMILE optimisation")
        else:
            optional_text = ""
        if outdated_next:
            text = tr("Next step: update {p0}", p0=STEP_LABEL[outdated_next])
        elif next_step:
            text = tr("Next step: {p0}", p0=STEP_LABEL[next_step])
        else:
            text = (
                tr("All steps completed")
                if any(st == "SUCCESS" for st in statuses.values())
                else tr("Wait for import sample data")
            )
        if optional_text:
            text = tr("Optional: {p0}|{p1}", p0=optional_text, p1=text)
        self.next_label.setText(text)
        reasons = _lock_reasons(statuses)
        outdated = _outdated_reasons(
            statuses,
            self.manager,
            self._current_exp_id,
            self._current_data_id,
        )
        for step_id, status in statuses.items():
            if (
                self._current_exp_id,
                self._current_data_id,
                step_id,
            ) in self._running_targets:
                status = "RUNNING"
            reason = reasons.get(step_id, "") or outdated.get(step_id, "")
            if status == "FAILED":
                run = _last_run_for(
                    self.manager,
                    self._current_exp_id,
                    self._current_data_id,
                    _step_refs(step_id),
                )
                if run is not None and run.message:
                    reason = run.message
            self._rows[step_id].set_status(status, reason)
            if step_id == "spectrum":
                self._sync_indirect_flip_control(status)

            if step_id == "fid":
                manual_visible = status == "SUCCESS"
            else:
                manual_visible = step_id not in ("smile", "peaks") and status != "LOCKED"
            self._rows[step_id].manual_button.setVisible(manual_visible)

            self._rows[step_id].show_spectrum_button.setVisible(
                step_id == "spectrum" and status == "SUCCESS"
            )
        self._update_ext_button()
        self._refresh_expanded_details()

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    def _on_ext_range_requested(self, step_id: str) -> None:
        """"Direct dimension range" button: Pop up the input dialog box and press the data to save
        the direct dimension range coverage.
        """
        if step_id != "spectrum":
            return
        exp_id = self._current_exp_id
        data_id = self._current_data_id
        if not (exp_id and data_id):
            return
        key = (exp_id, data_id)
        current = self._final_ext.get(key, ("", "", True))
        nucleus = self._data_direct_nucleus(exp_id, data_id)
        lo_default, hi_default = _default_window_ppm(nucleus)
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("direct dimension range"))
        form = QFormLayout(dialog)
        lo_edit = QLineEdit(str(current[0]) if current[0] else "")
        hi_edit = QLineEdit(str(current[1]) if current[1] else "")
        lo_edit.setPlaceholderText(lo_default)
        hi_edit.setPlaceholderText(hi_default)
        form.addRow(tr("High field end ppm (EXT -x1):"), lo_edit)
        form.addRow(tr("Low field end ppm (EXT -xn):"), hi_edit)
        tip = QLabel(
            tr(
                "Leave blank = use default window (direct dimension nuclide {p0});peaks outside "
                "the window will not appear in the final spectrum,\nthe direct-dimension linear "
                "phase p1 is renormalised automatically to the window "
                "width.",
                p0=nucleus or "unknown",
            )
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {TEXT_MUTED};")
        form.addRow(tip)
        apply_check = QCheckBox(tr("Apply this range to the optimisation process"))
        apply_check.setChecked(bool(current[2]))
        form.addRow(apply_check)
        apply_tip = QLabel(
            tr(
                "On by default: the optimisation (first-pass reconstruction / phase search, plus "
                "baseline / zero-fill / window evaluation)\nuses the specified range, matching the "
                "final spectrum and reducing SMILE memory;\nif the optimisation comes out poorly "
                "you can turn this off and optimise over the default wide "
                "range.",
            )
        )
        apply_tip.setWordWrap(True)
        apply_tip.setStyleSheet(f"color: {TEXT_MUTED};")
        form.addRow(apply_tip)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        lo = lo_edit.text().strip()
        hi = hi_edit.text().strip()
        apply_opt = apply_check.isChecked()

        from gui.dialogs import InfoDialog

        error = validate_ext_range(lo, hi, nucleus)
        if error:
            InfoDialog.show_info(self, tr("direct dimension range"), error)
            return
        if not lo and not hi:
            self._final_ext.pop(key, None)
            self.log_message.emit(
                tr("direct dimension range: {p0} Cleared, restore default window", p0=data_id)
            )
        else:
            self._final_ext[key] = (lo, hi, apply_opt)
            scope = tr("including optimisation") if apply_opt else tr("final run only")
            self.log_message.emit(
                tr(
                    "direct dimension range: {p0} set to {p1}-{p2} ppm ({p3})",
                    p0=data_id,
                    p1=lo or "default",
                    p2=hi or "default",
                    p3=scope,
                )
            )
        self._update_ext_button()

    def _update_ext_button(self) -> None:
        """Cover the update button copy and prompt word according to the direct dimension range of
        the current data.
        """
        row = self._rows.get("spectrum")
        if row is None:
            return
        over = self._final_ext.get((self._current_exp_id, self._current_data_id))
        if over and (over[0] or over[1]):
            lo = over[0] or tr("default")
            hi = over[1] or tr("default")
            scope = tr("including optimisation") if over[2] else tr("final run only")
            first_note = (
                tr(
                    "first-pass reconstruction / phase search and the optimisation evaluation "
                    "share the "
                    "window",
                )
                if over[2]
                else tr("the first pass of reconstruction / phase search keeps the original window")
            )
            row.set_ext_override(
                tr(
                    "direct dimension range {p0}/{p1} · {p2}",
                    p0=lo,
                    p1=hi,
                    p2=scope,
                )
            )
            row.ext_range_button.setToolTip(
                tr(
                    "direct-dimension window: {p0}-{p1} ppm (EXT -x1/-xn, {p2})\n{p3}; peaks "
                    "outside it do not enter the final spectrum,\np1 is renormalised to the window "
                    "width; each data set shows its own "
                    "settings",
                    p0=lo,
                    p1=hi,
                    p2=scope,
                    p3=first_note,
                )
            )
        else:
            row.set_ext_override(tr("direct dimension range"))
            row.ext_range_button.setToolTip(
                tr(
                    "Set the direct-dimension extraction window (EXT -x1/-xn). Use default if not "
                    'set (10.5-6.5 ppm);\n"Apply this range to the optimisation" is on by '
                    "default and can be turned off to optimise over the default wide 6.5-10.5 "
                    "range",
                )
            )

    def _spectrum_ext_params(self, data_id: str, *, exp_id: str | None = None) -> dict | None:
        """The direct dimension range of a certain data in the current experiment ->
        generate_spectrum params (None). apply_ext_to_opt: enabled by default, the range is also
        used for the optimisation process (first pass reconstruction / phase search and baseline
        / zero filling / window function evaluation); when closed, optimisation uses the default
        6.5-10.5 large range, and only the final run uses this range.
        """
        over = self._final_ext.get((exp_id or self._current_exp_id, data_id))
        if not over:
            return None
        ext_params: dict[str, str] = {"apply_ext_to_opt": "1" if over[2] else "0"}
        if over[0]:
            ext_params["final_ext_lo"] = over[0]
        if over[1]:
            ext_params["final_ext_hi"] = over[1]
        return ext_params

    def _segment_count(self, exp_id: str, data_id: str) -> int:
        """Return the segment count; an ordinary dataset's empty segments list yields zero."""
        try:
            entry = (
                self.manager.project.experiment(exp_id)
                if self.manager is not None and self.manager.project is not None
                else None
            )
        except Exception:  # noqa: BLE001
            return 0
        if entry is None:
            return 0
        for node in getattr(entry, "data", None) or []:
            if getattr(node, "id", "") != data_id:
                continue
            segs = getattr(node, "segments", None)
            if isinstance(segs, (list, tuple)):
                return len(segs)
            return 0
        return 0

    def _on_segment_shift_requested(self, step_id: str) -> None:
        """Collect one manual frequency shift in Hz per segment, with the first segment as
        reference.
        """
        if step_id != "fid":
            return
        exp_id = self._current_exp_id
        data_id = self._current_data_id
        if not (exp_id and data_id):
            return
        from gui.dialogs import InfoDialog

        parts = self._segment_count(exp_id, data_id)
        if parts < 2:
            InfoDialog.show_info(
                self,
                tr("Inter-part field drift"),
                tr(
                    "This data has no segments (needs 2 or more); inter-part field drift "
                    "correction does not apply.",
                ),
            )
            return
        key = (exp_id, data_id)
        current = self._segment_shifts.get(key) or {}
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Inter-part field drift"))
        form = QFormLayout(dialog)
        head = QLabel(
            tr(
                "Part 1 is the reference and is fixed at 0 Hz. Fill in an offset (Hz) for each of "
                'the other parts;\n"run" writes PS -rs into that part\'s fid.com and '
                "re-converts it. Leave blank (or 0) to leave a part untouched.\nThere is no "
                "automatic detection any more - these values are used exactly as entered.",
            )
        )
        head.setWordWrap(True)
        head.setStyleSheet(f"color: {TEXT_MUTED};")
        form.addRow(head)
        edits: dict[int, QLineEdit] = {}
        for index in range(2, parts + 1):
            edit = QLineEdit(str(current.get(index, "")))
            edit.setPlaceholderText("0")
            edits[index] = edit
            form.addRow(tr("Part {p0} offset (Hz):", p0=index), edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values: dict[int, float] = {}
        for index, edit in edits.items():
            text = edit.text().strip()
            if not text:
                continue
            try:
                value = float(text)
            except ValueError:
                InfoDialog.show_info(
                    self,
                    tr("Inter-part field drift"),
                    tr('Part {p0}: "{p1}" is not a number.', p0=index, p1=text),
                )
                return
            if not math.isfinite(value):
                InfoDialog.show_info(
                    self,
                    tr("Inter-part field drift"),
                    tr("Part {p0}: the offset must be a finite number.", p0=index),
                )
                return
            if value:
                values[index] = value
        if values:
            self._segment_shifts[key] = values
            self.log_message.emit(
                tr(
                    "Inter-part field drift: {p0} set - {p1} (part 1 is the reference, 0 Hz)",
                    p0=data_id,
                    p1=", ".join(
                        tr("part {p0}: {p1} Hz", p0=i, p1=f"{v:+.4f}")
                        for i, v in sorted(values.items())
                    ),
                )
            )
        else:
            self._segment_shifts.pop(key, None)
            self.log_message.emit(
                tr(
                    "Inter-part field drift: {p0} cleared - no part will be shifted",
                    p0=data_id,
                )
            )
        self._update_segment_shift_button()

    def _update_segment_shift_button(self) -> None:
        """Update the manual inter-segment drift button from segment count and configured shifts."""
        row = self._rows.get("fid")
        if row is None:
            return
        parts = self._segment_count(self._current_exp_id, self._current_data_id)
        row.segment_shift_button.setVisible(parts >= 2)
        if parts < 2:
            return
        values = self._segment_shifts.get((self._current_exp_id, self._current_data_id)) or {}
        if values:
            row.segment_shift_button.setText(
                tr("Inter-part field drift ({p0} part(s))", p0=len(values))
            )
            row.segment_shift_button.setToolTip(
                tr(
                    'Segmented data: {p0};\n"run" writes PS -rs into each part\'s fid.com. Part 1 '
                    "is the reference.",
                    p0=", ".join(
                        tr("part {p0}: {p1} Hz", p0=i, p1=f"{v:+.4f}")
                        for i, v in sorted(values.items())
                    ),
                )
            )
        else:
            row.segment_shift_button.setText(tr("Inter-part field drift"))
            row.segment_shift_button.setToolTip(
                tr(
                    "Segmented data only: set a frequency offset (Hz) for parts 2..N (part 1 is "
                    'the reference); "run" writes PS -rs into each part\'s fid.com according to '
                    "these values. There is no automatic detection any more.",
                )
            )

    def _segment_shift_params(self, data_id: str, *, exp_id: str | None = None) -> dict | None:
        """Return configured segment shifts for generate_fid, or None when absent.

        Cover every segment because the backend indexes the list by segment. The reference first
        segment is always zero; unspecified segments also remain unshifted.
        """
        target_exp_id = exp_id or self._current_exp_id
        values = self._segment_shifts.get((target_exp_id, data_id))
        if not values:
            return None
        parts = max(self._segment_count(target_exp_id, data_id), max(values))
        shifts = [0.0] * parts
        for index, value in values.items():
            if 1 <= index <= parts:
                shifts[index - 1] = float(value)
        return {"segment_shift_hz": shifts}

    def _final_script_path(self, data_id: str, *, exp_id: str | None = None) -> Path | None:
        """Locate an existing final-run script (uniform ``_process.com`` / NUS ``_nus.com``).

        Returns None when nothing is found (the caller prompts to optimise and generate one
        first). The final-run script names match how ``backend.nmrpipe_backend`` writes them.
        """
        target_exp_id = exp_id or self._current_exp_id
        if not (target_exp_id and data_id):
            return None
        work = self.manager.data_dir(target_exp_id, data_id, "process")
        for name in (
            f"{data_id}_process.com",
            f"{data_id}_nus.com",
            "process.com",
            "nus.com",
        ):
            candidate = work / name
            if candidate.is_file():
                return candidate
        return None

    def _sync_indirect_flip_control(self, status: str) -> None:
        """Sync indirect flip controls to dimensionality, nuclei and the current final script."""
        row = self._rows.get("spectrum")
        if row is None:
            return
        ndim = self._data_ndim(self._current_exp_id, self._current_data_id)
        row.set_indirect_dimension(ndim)

        row.set_indirect_nuclei(self._data_axis_nuclei(self._current_exp_id, self._current_data_id))
        if status != "SUCCESS":
            return
        script_path = self._final_script_path(self._current_data_id)
        if script_path is None:
            return
        try:
            content = script_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        row.set_indirect_neg_state(indirect_neg_state(content, ndim=ndim))

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    def run_step(self, step_id: str, data_id: str | None = None) -> None:
        """Run the specified step (data_id specified scope; Defaults to currently selected/first
        data).
        """
        if step_id not in self._rows:
            return
        if data_id is not None:
            self._current_data_id = data_id

        if step_id == "peaks":
            peaks_row = self._rows.get("peaks")
            if peaks_row is not None:
                peaks_row.threshold_spin.setValue(
                    self._threshold_for(self._current_exp_id, self._current_data_id)
                )
        row = self._rows[step_id]
        if not row.run_button.isHidden():
            self._on_run_requested(step_id)

    def show_first_import_hint(self) -> None:
        """Next step prompt after first import: Highlight the next runnable step + bubble and
        disappear after 8 seconds.
        """
        statuses = self._current_statuses()

        next_step = next(
            (sid for sid, st in statuses.items() if sid != "smile" and st in ("READY", "OUTDATED")),
            None,
        )
        if next_step is None:
            return
        row = self._rows.get(next_step)
        if row is None:
            return
        row.name_label.setStyleSheet("font-weight: bold; color: #16a085;")
        self.hint_bubble.setText(
            tr(
                'Sample data has been imported: the next step can be run "{p0}"',
                p0=STEP_LABEL.get(next_step, next_step),
            )
        )
        self.hint_bubble.setVisible(True)
        from qtcompat.QtCore import QTimer

        QTimer.singleShot(8000, self._clear_first_import_hint)

    def _clear_first_import_hint(self) -> None:
        self.hint_bubble.setVisible(False)
        for row in self._rows.values():
            row.name_label.setStyleSheet("font-weight: bold;")

    @staticmethod
    def _run_log_scope(exp_id: str, data_id: str, group_id: str = "") -> str:
        """Use group logs for whole-group jobs and dataset logs for independent member jobs."""
        from gui.log_panel import LogPanel

        return LogPanel.scope_key(
            "group" if group_id else "data",
            exp_id,
            data_id,
            group_id,
        )

    def _refresh_after_run(self) -> None:
        """Panel refresh after running (executed by the main thread via queue signal). The worker
        thread finally only emits run_finished, and no longer directly calls refresh(); under
        the SyncThread test, emit is a direct connection, and the behaviour remains unchanged.
        """
        self.refresh()

    def _start_guard_message(self, run_target: tuple[str, str, str], ndim: int) -> str:
        """Return an empty string when runnable; only distinct 2D datasets may process
        concurrently.
        """
        exp_id, data_id, _step_id = run_target
        with self._running_targets_lock:
            active = dict(self._running_targets)
        if not active:
            return ""
        if any((e, d) == (exp_id, data_id) for e, d, _ in active):
            return tr(
                "This data already has a task running; wait for it to finish before starting "
                "another step"
            )
        if ndim == 2 and all(active_ndim == 2 for active_ndim in active.values()):
            return ""
        return tr(
            "Only different 2D data can be processed in parallel; wait for the current 1D/3D "
            "task to finish"
        )

    def _register_running_target(self, run_target: tuple[str, str, str], ndim: int) -> bool:
        """Register a running target atomically; return False if concurrency state has changed."""
        exp_id, data_id, _step_id = run_target
        with self._running_targets_lock:
            active = self._running_targets
            if any((e, d) == (exp_id, data_id) for e, d, _ in active):
                return False
            if active and (ndim != 2 or any(value != 2 for value in active.values())):
                return False
            active[run_target] = ndim
        return True

    def _finish_running_target(self, run_target: tuple[str, str, str]) -> None:
        with self._running_targets_lock:
            self._running_targets.pop(run_target, None)

    def _on_progress_updated(self, text: str) -> None:
        """Batch progress label (main thread): empty text hidden."""
        self.batch_progress_label.setText(text)
        self.batch_progress_label.setVisible(bool(text))

    def _toggle_step_detail(self, step_id: str) -> None:
        """Click on the step row to expand/Collapse inline details panel."""
        row = self._rows.get(step_id)
        if row is None:
            return
        text, _params, failed = self._step_detail(step_id)
        row.set_detail(text, failed=failed)
        row.detail_frame.setVisible(row.detail_frame.isHidden())

    def _cached_spectrum_report(self, params: dict, spectrum_path: str) -> str:
        """Generate spectrum parameter report (0.2.199-patch12): According to spectrum file
        fingerprint cache (memory + disk record). spectrum_quality_report_lines will read the
        entire ft3 and evaluate the quality of the full spectrum. Each refresh will be executed
        in the main thread and it will be very stuck; when the spectrum file has not changed,
        the record will be read directly without re-reading the spectrum. Record file:
        {spectrum_path}.quality.json, fingerprint = mtime_ns+size+ parameter.
        """
        params_fp = hashlib.sha256(
            json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
        try:
            p = Path(spectrum_path)
            if p.is_file():
                st = p.stat()
                fp = f"{st.st_mtime_ns}|{st.st_size}"
                rec = Path(f"{spectrum_path}.quality.json")
            else:
                fp = "missing"
                rec = None
        except OSError:
            fp = "err"
            rec = None
        key = f"{spectrum_path}|{fp}|{params_fp}"
        cached = self._spectrum_report_cache.get(key)
        if cached is not None:
            return cached
        if rec is not None and rec.is_file():
            try:
                data = json.loads(rec.read_text(encoding="utf-8"))

                if data.get("fp") == fp and isinstance(data.get("text"), str):
                    self._spectrum_report_cache[key] = data["text"]
                    return data["text"]
            except (OSError, ValueError):
                pass

        return tr('(No report record; generate a report after re-running "Generate Spectrum")')

    def _step_detail(self, step_id: str) -> tuple[str, dict | None, bool]:
        """Build step details from conclusions and reports rather than internal run/snapshot
        fields.
        """
        exp_id = self._current_exp_id
        data_id = self._current_data_id
        if not (exp_id and data_id):
            return tr("No data selected"), None, False
        lines: list[str] = []
        params: dict | None = None
        failed = False
        artifact = None
        try:
            artifacts = _node_artifacts(self.manager, exp_id, data_id)
            artifact = artifacts.get(step_id)
        except Exception:  # noqa: BLE001
            pass
        run = _last_run_for(self.manager, exp_id, data_id, _step_refs(step_id))
        lines.append(tr("Data: {p0}", p0=data_id))
        if run is None:
            lines.append(tr("No run record for this step"))
        else:
            failed = run.status == "failed"
            status_text = tr("Failed") if failed else tr("Completed")
            status_icon = "×" if failed else "✓"
            lines.append(tr("{p0} Status: {p1}", p0=status_icon, p1=status_text))
            if failed and run.message:
                lines.append(tr("Problem: {p0}", p0=run.message))
            if step_id == "fid":
                lines += _fid_step_report_lines(self.manager, exp_id, data_id)
            elif step_id == "spectrum":
                if run.params:
                    params = dict(run.params)
                    lines.append(
                        self._cached_spectrum_report(
                            run.params,
                            str((run.outputs or {}).get("spectrum_path") or ""),
                        )
                    )
            elif step_id == "smile":
                lines += _smile_step_report(dict(run.params or {}), dict(run.outputs or {}))
            else:
                if run.params:
                    report = _format_params(run.params)
                    if report:
                        lines.append(report)
        if run is not None and step_id in ("fid", "spectrum"):
            from workflow.script_audit import script_change_report

            manual_report = "\n".join(
                script_change_report(
                    list((run.params or {}).get("manual_script_changes") or []), failed=failed
                )
            )
            if manual_report and manual_report not in "\n".join(lines):
                lines.append(manual_report)
        if artifact is not None:
            lines.append(tr("Result file: {p0}", p0=artifact))
        return "\n".join(lines) if lines else tr("No details"), params, failed

    def _refresh_expanded_details(self) -> None:
        """0.2.161: Expanded step details (parameter report, etc.) With data/Context switch
        refreshes immediately.
        """
        for step_id, row in self._rows.items():
            if not row.detail_frame.isHidden():
                text, _params, failed = self._step_detail(step_id)
                row.set_detail(text, failed=failed)

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    def _reference_candidates(self) -> list[tuple[str, str, str]]:
        """There is already a data list of peak tables in the project (display name, exp_id,
        data_id); exclude the current data itself (2026-09-04 user).
        """
        out: list[tuple[str, str, str]] = []
        if self.manager is None or self.manager.project is None:
            return out
        cur_exp = str(getattr(self, "_current_exp_id", "") or "")
        cur_data = str(getattr(self, "_current_data_id", "") or "")
        for exp in self.manager.project.experiments:
            for entry in getattr(exp, "data", []):
                data_id = str(getattr(entry, "id", "") or "")
                if not data_id:
                    continue

                if str(exp.id) == cur_exp and data_id == cur_data:
                    continue
                try:
                    peaks_dir = self.manager.data_dir(exp.id, data_id, "peaks")
                except Exception:  # noqa: BLE001
                    continue
                has = any(
                    (peaks_dir / f"{exp.id}-{data_id}{suffix}").is_file()
                    for suffix in (".list", ".csv")
                )
                if not has:
                    continue
                title = str(getattr(entry, "title", "") or "")
                name = f"{exp.id}/{data_id}"
                if title:
                    name += f" ({title})"
                out.append((name, exp.id, data_id))
        return out

    def _ref_nuclei_from_spectrum(self, spectrum_path: Path) -> list[str] | None:
        """Get the core name of each axis from the reference spectrum header (F order); fail/core
        agnostic return None.
        """
        symbols = {
            "H": "1H",
            "N": "15N",
            "C": "13C",
            "F": "19F",
            "P": "31P",
            "D": "2H",
        }
        full = {"1H", "2H", "13C", "15N", "19F", "31P", "23Na", "29Si"}
        try:
            if spectrum_path.suffix.lower() == ".ft3":
                from viewer.spectrum import Spectrum3D

                spec = Spectrum3D.load_from_ft3(spectrum_path, lazy=True)
            else:
                from viewer.spectrum import Spectrum

                spec = Spectrum.load_from_ft2(spectrum_path)
        except Exception:  # noqa: BLE001
            return None
        nuclei: list[str] = []
        for axis in getattr(spec, "axes", []):
            label = str(getattr(axis, "label", "") or "").strip()
            if len(label) > 1 and label[-1:].lower() in ("x", "y", "z"):
                label = label[:-1]
            nuc = symbols.get(label, label)
            nuclei.append(nuc if nuc in full else "")
        return nuclei if nuclei and all(nuclei) else None

    def _load_reference(self, exp_id: str, data_id: str) -> dict | None:
        """Load reference data peak table + core name; return None on failure."""
        from core.peaks.peak_table import load_peaks
        from gui.peaks_io import import_peaks_poky

        try:
            peaks_dir = self.manager.data_dir(exp_id, data_id, "peaks")
        except Exception:  # noqa: BLE001
            return None
        path: Path | None = None
        for suffix in (".list", ".csv"):
            cand = peaks_dir / f"{exp_id}-{data_id}{suffix}"
            if cand.is_file():
                path = cand
                break
        if path is None:
            return None
        data_entry = self.manager.data(exp_id, data_id)
        spectrum_path = str(getattr(data_entry, "spectrum_path", "") or "")
        nuclei = (
            self._ref_nuclei_from_spectrum(Path(spectrum_path))
            if spectrum_path and Path(spectrum_path).is_file()
            else None
        )
        if path.suffix.lower() == ".list":
            peaks = import_peaks_poky(path, nuclei=nuclei)
        else:
            peaks = load_peaks(path)
        if not peaks:
            return None

        if nuclei is None and "N_shift" in peaks[0] and "H_shift" in peaks[0]:
            nuclei = ["15N", "1H"]
        title = str(getattr(data_entry, "title", "") or "")
        label = f"{exp_id}/{data_id}"
        if title:
            label += f" ({title})"
        return {
            "label": label,
            "peaks": peaks,
            "nuclei": nuclei,
            "path": str(path),
        }

    def _on_pick_reference(self, step_id: str) -> None:
        """"Reference Spectrum" button: A drop-down pops up below the button, listing the data of
        existing peak files in the project (sorted by exp/data number). After selection, load it
        as a reference for peak selection (after alignment, eliminate peaks whose corresponding
        peaks cannot be found in the reference).
        """
        if self.manager is None or self.manager.project is None:
            return
        candidates = self._reference_candidates()
        if not candidates:
            from gui.dialogs import InfoDialog

            InfoDialog.show_info(
                self,
                tr(
                    "Reference spectrum",
                ),
                tr(
                    "There is no data with peak table in the project. Please peak pick some data "
                    "first",
                ),
            )
            return
        row = self._rows.get(step_id)
        anchor = row.ref_button if row is not None else self
        menu = QMenu(anchor)

        for name, ref_exp, ref_data in sorted(candidates, key=lambda item: (item[1], item[2])):
            action = menu.addAction(f"{name}")
            action.setData((ref_exp, ref_data))
        chosen = menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        if chosen is None:
            return
        ref_exp, ref_data = chosen.data()
        info = self._load_reference(ref_exp, ref_data)
        if info is None:
            from gui.dialogs import InfoDialog

            InfoDialog.show_info(
                self,
                tr(
                    "Reference spectrum",
                ),
                tr(
                    "Unable to load reference peak table: {p0}/{p1}",
                    p0=ref_exp,
                    p1=ref_data,
                ),
            )
            return
        if info.get("nuclei") is None and any("F1_shift" in p for p in info["peaks"]):
            from gui.dialogs import InfoDialog

            InfoDialog.show_info(
                self,
                tr("Reference spectrum"),
                tr(
                    "The reference peak table is 3D and the nucleus name cannot be determined (no "
                    "reference spectrum is loaded), the constraint will not take "
                    "effect",
                ),
            )
            return
        self._ref_info[(self._current_exp_id, self._current_data_id)] = info
        self._rows["peaks"].set_ref_text(
            tr("refer to: {p0} ({p1} peak)", p0=info["label"], p1=len(info["peaks"]))
        )

    def _on_clear_reference(self, step_id: str) -> None:
        """Clear reference spectrum constraints."""
        self._ref_info.pop((self._current_exp_id, self._current_data_id), None)
        row = self._rows.get("peaks")
        if row is not None:
            row.clear_ref_display()

    def _on_run_requested(self, step_id: str) -> None:
        if not self._current_exp_id:
            return
        entry = (
            self.manager.project.experiment(self._current_exp_id)
            if self.manager.project is not None
            else None
        )
        if entry is None:
            return

        if step_id == "smile" and not self._smile_supported(
            self._current_exp_id, self._current_data_id
        ):
            self.log_message.emit(
                tr("SMILE optimisation only works with 2D NUS data (not currently, step is hidden)")
            )
            return
        method_name = STEP_METHOD.get(step_id)
        method = getattr(self.controller, method_name, None) if method_name else None
        if method is None:
            self.log_message.emit(
                tr(
                    "{p0}: The backend interface needs to be implemented and cannot be run yet",
                    p0=STEP_LABEL.get(step_id, step_id),
                )
            )
            return

        if self._current_data_id:
            try:
                statuses = compute_data_step_statuses(
                    self.manager, self._current_exp_id, self._current_data_id
                )
                if statuses.get(step_id) == "LOCKED":
                    reasons = _lock_reasons(statuses)
                    self.log_message.emit(
                        tr(
                            "{p0} is waiting for {p1}",
                            p0=STEP_LABEL.get(step_id, step_id),
                            p1=reasons.get(step_id, "Previous step"),
                        )
                    )
                    return
            except Exception:  # noqa: BLE001
                pass
        target_exp_id = self._current_exp_id
        nodes = _data_nodes(self.manager, target_exp_id)
        if not nodes:
            self.log_scoped.emit(
                tr(
                    "{p0}: this experiment type has no sample data yet, please import sample data "
                    "first",
                    p0=STEP_LABEL.get(step_id, step_id),
                ),
                self._run_log_scope(target_exp_id, ""),
            )
            return
        data_node = next(
            (n for n in nodes if getattr(n, "id", "") == self._current_data_id),
            nodes[0],
        )
        target_data_id = getattr(data_node, "id", target_exp_id)
        run_target = (target_exp_id, target_data_id, step_id)
        target_ndim = self._data_ndim(target_exp_id, target_data_id)
        guard_message = self._start_guard_message(run_target, target_ndim)
        if guard_message:
            self.log_message.emit(guard_message)
            return
        if not self._register_running_target(run_target, target_ndim):
            self.log_message.emit(
                tr("The running task changed; please try starting this step again")
            )
            return
        peak_threshold = (
            self._threshold_for(target_exp_id, target_data_id) if step_id == "peaks" else 35.0
        )
        peak_ref = self._ref_info.get((target_exp_id, target_data_id))
        self._rows[step_id].set_status("RUNNING")

        def worker() -> None:
            run_scope = self._run_log_scope(target_exp_id, target_data_id, "")
            try:
                exp_id = target_exp_id

                self.run_started.emit(exp_id, target_data_id)

                self.log_scoped.emit(STEP_LOG_SEPARATOR, run_scope)
                self.log_scoped.emit(
                    tr(
                        "▶ {p0} — {p1}",
                        p0=STEP_LABEL.get(step_id, step_id),
                        p1=target_data_id,
                    ),
                    run_scope,
                )
                step_label = STEP_LABEL.get(step_id, step_id)
                try:
                    import inspect

                    kwargs: dict = {"exp_id": exp_id, "data_id": target_data_id}
                    if step_id == "fid":
                        seg_params = self._segment_shift_params(target_data_id, exp_id=exp_id)
                        if seg_params and "params" in inspect.signature(method).parameters:
                            kwargs["params"] = seg_params
                    if step_id == "spectrum":
                        ext_params = self._spectrum_ext_params(target_data_id, exp_id=exp_id)
                        if ext_params and "params" in inspect.signature(method).parameters:
                            kwargs["params"] = ext_params
                    if step_id == "peaks":
                        kwargs["sigma_multiplier"] = peak_threshold

                        if "localization_method" in inspect.signature(method).parameters:
                            kwargs["localization_method"] = "parabolic"
                        ref = peak_ref
                        if ref:
                            kwargs["ref_peaks"] = ref["peaks"]
                            kwargs["ref_nuclei"] = ref.get("nuclei")
                            kwargs["ref_name"] = str(ref.get("label", "")).split(" (")[0]

                            from gui.settings import load_settings

                            kwargs["tolerance_ppm"] = (
                                load_settings().get("alignment_tolerance_ppm") or None
                            )
                    if "progress" in inspect.signature(method).parameters:
                        kwargs["progress"] = lambda msg: self.log_scoped.emit(msg, run_scope)
                    result = method(data_node, **kwargs)
                    if step_id == "spectrum":
                        self.spectrum_results_changed.emit(target_exp_id, target_data_id)
                    if (
                        step_id == "peaks"
                        and isinstance(result, dict)
                        and result.get("status") == "success"
                    ):
                        self.show_spectrum_requested.emit(step_id)
                    message = result if isinstance(result, str) else str(result)
                    if message.strip():
                        if step_id == "peaks":
                            for line in message.splitlines():
                                self.log_scoped.emit(line, run_scope)
                        elif step_id in ("fid", "spectrum"):
                            self.log_scoped.emit(tr("Result file: {p0}", p0=message), run_scope)
                        else:
                            self.log_scoped.emit(message, run_scope)
                    self.log_scoped.emit(tr("✓ {p0} completed", p0=step_label), run_scope)
                except Exception as exc:  # noqa: BLE001
                    self.log_scoped.emit(
                        tr(
                            "× {p0} failed: {p1}",
                            p0=step_label,
                            p1=describe_exception(exc),
                        ),
                        run_scope,
                    )
                    if _looks_like_memory_guard(exc):
                        self.memory_guard_requested.emit(str(exc))
            except Exception as exc:  # noqa: BLE001
                self.log_scoped.emit(
                    tr("fail {p0}: {p1}", p0=STEP_LABEL.get(step_id, step_id), p1=exc),
                    run_scope,
                )
                if _looks_like_memory_guard(exc):
                    self.memory_guard_requested.emit(str(exc))
            finally:
                self._finish_running_target(run_target)
                self.run_target_finished.emit(target_exp_id, target_data_id)
                self.run_finished.emit()

        from backend.runtime import clear_cancel

        clear_cancel()
        threading.Thread(target=worker, daemon=True).start()

    def _on_rerun_final_requested(self, step_id: str, sampling: dict | None = None) -> None:
        """Edit direct-axis bounds and indirect flips in the existing final script, then rerun it.

        Uniform process.com and NUS nus.com already contain optimized phase, window and baseline
        parameters. Preserve unrelated operations while updating EXT bounds and indirect FT -neg
        with companion phase compensation.

        ft_neg_f1/ft_neg_f2 specify final states rather than toggle requests; legacy
        flip_f1/flip_f2 aliases are accepted. For 3D NUS, retained nus3d_rc input allows
        rerunning only the indirect part without repeating SMILE or direct-axis processing. 2D,
        2D NUS and 3D uniform rerun the complete final script.

        Keep the step running throughout script edits. On success register the new fingerprint,
        regenerate 3D projections and request spectrum reload.
        """
        if step_id != "spectrum":
            return
        exp_id = self._current_exp_id
        if not exp_id:
            return
        nodes = _data_nodes(self.manager, exp_id)
        if not nodes:
            self.log_scoped.emit(
                tr("The experiment type does not yet have sample data"),
                self._run_log_scope(exp_id, ""),
            )
            return
        data_node = next(
            (n for n in nodes if getattr(n, "id", "") == self._current_data_id),
            nodes[0],
        )
        data_id = getattr(data_node, "id", exp_id)
        run_target = (exp_id, data_id, step_id)
        ndim = self._data_ndim(exp_id, data_id)
        guard_message = self._start_guard_message(run_target, ndim)
        if guard_message:
            self.log_message.emit(guard_message)
            return
        if not self._register_running_target(run_target, ndim):
            self.log_message.emit(
                tr("The running task changed; please try starting this step again")
            )
            return
        flips: dict[str, bool] = {}
        for axis, keys in (
            ("F2", ("ft_neg_f2", "flip_f2")),
            ("F1", ("ft_neg_f1", "flip_f1")),
        ):
            for key in keys:
                if sampling and sampling.get(key) is not None:
                    flips[axis] = bool(sampling[key])
                    break
        self._rows[step_id].set_status("RUNNING")
        run_scope = self._run_log_scope(exp_id, data_id)
        self.log_scoped.emit(
            tr(
                "Start re-running the final script (only the direct dimension range / the indirect "
                "dimension flip is "
                "updated, the other parameters remain "
                "unchanged)",
            ),
            run_scope,
        )

        def worker() -> None:
            try:
                self.run_started.emit(exp_id, data_id)

                self.log_scoped.emit(STEP_LOG_SEPARATOR, run_scope)
                self.log_scoped.emit(
                    tr(
                        "start {p0}: {p1}",
                        p0=STEP_LABEL.get(step_id, step_id),
                        p1=data_id,
                    ),
                    run_scope,
                )

                work = self.manager.data_dir(exp_id, data_id, "process")
                script_path = self._final_script_path(data_id, exp_id=exp_id)
                if script_path is None:
                    self.log_scoped.emit(
                        tr(
                            'There is no reusable final script, please execute "re-optimisation" '
                            "to generate it "
                            "first",
                        ),
                        run_scope,
                    )
                    return
                content = script_path.read_text(encoding="utf-8", errors="replace")

                indirect_section = (
                    _indirect_only_section(content) if (flips and ndim >= 3) else None
                )
                if indirect_section is not None:
                    planes_dir = work / "nus3d_rc"
                    if not (planes_dir.is_dir() and any(planes_dir.glob("*.ft1"))):
                        indirect_section = None

                ext = self._spectrum_ext_params(data_id, exp_id=exp_id) or {}
                if ext and indirect_section is None:
                    lo = str(ext.get("final_ext_lo", ""))
                    hi = str(ext.get("final_ext_hi", ""))
                    if lo:
                        content = re.sub(
                            r"(-x1 )([0-9.]+)(ppm)?",
                            lambda m: f"{m.group(1)}{lo}" + (m.group(3) or ""),
                            content,
                        )
                    if hi:
                        content = re.sub(
                            r"(-xn )([0-9.]+)(ppm)?",
                            lambda m: f"{m.group(1)}{hi}" + (m.group(3) or ""),
                            content,
                        )
                    script_path.write_text(content, encoding="utf-8", newline="\n")
                    self.log_scoped.emit(
                        tr(
                            "direct dimension range updated: {p0}-{p1} ppm → {p2}",
                            p0=lo or "default",
                            p1=hi or "default",
                            p2=script_path.name,
                        ),
                        run_scope,
                    )
                elif ext and indirect_section is not None:
                    self.log_scoped.emit(
                        tr(
                            "The direct dimension range was not applied: this re-run reuses the "
                            'retained reconstruction planes — execute "re-optimisation" to apply '
                            "it",
                        ),
                        run_scope,
                    )

                if flips:
                    content, applied = flip_indirect_ft_lines(content, ndim=ndim, flips=flips)
                    if applied:
                        script_path.write_text(content, encoding="utf-8", newline="\n")
                        added = sorted(axis for axis, neg in applied.items() if neg)
                        removed = sorted(axis for axis, neg in applied.items() if not neg)
                        if added:
                            self.log_scoped.emit(
                                tr(
                                    "indirect dimension flip on the final script: {p0} -neg",
                                    p0=", ".join(added),
                                ),
                                run_scope,
                            )
                        if removed:
                            self.log_scoped.emit(
                                tr(
                                    "indirect dimension flip cancelled on the final script: "
                                    "{p0} -neg",
                                    p0=", ".join(removed),
                                ),
                                run_scope,
                            )
                    else:
                        self.log_scoped.emit(
                            tr(
                                "The indirect dimension FT line could not be located in the final "
                                'script; the flip was not applied — execute "re-optimisation" to '
                                "regenerate the script "
                                "first",
                            ),
                            run_scope,
                        )

                if indirect_section is not None:
                    run_key = f"{data_id}_nus_indirect.com"

                    content = _indirect_only_section(content) or content
                    self.log_scoped.emit(
                        tr(
                            "Indirect dimension flip: only the indirect dimension is re-run "
                            "from the retained reconstruction planes (SMILE and the direct "
                            "dimension are not re-run)",
                        ),
                        run_scope,
                    )
                else:
                    run_key = script_path.name

                from workflow.script_check import check_script

                for w in check_script(content, run_key):
                    self.log_scoped.emit(tr("⚠ script check: {p0}", p0=w), run_scope)

                result = self.controller.run_manual_spectrum(
                    data_node,
                    {run_key: content},
                    exp_id=exp_id,
                    data_id=data_id,
                    progress=lambda line: self.log_scoped.emit(f"[{run_key}] {line}", run_scope),
                )
                self.log_scoped.emit(
                    tr(
                        "Re-run the final script to complete {p0}: {p1}",
                        p0=data_id,
                        p1=result,
                    ),
                    run_scope,
                )

                try:
                    record_step_success(self.manager, exp_id, data_id, "spectrum")
                except Exception:  # noqa: BLE001
                    pass

                if ndim >= 3:
                    self._regenerate_3d_projections(exp_id, data_id, run_scope)

                self.spectrum_results_changed.emit(exp_id, data_id)
            except Exception as exc:  # noqa: BLE001
                self.log_scoped.emit(
                    tr("Re-run the final script failed: {p0}", p0=describe_exception(exc)),
                    run_scope,
                )
            finally:
                self._finish_running_target(run_target)
                self.run_target_finished.emit(exp_id, data_id)
                self.run_finished.emit()

        threading.Thread(target=worker, daemon=True).start()

    def _regenerate_3d_projections(self, exp_id: str, data_id: str, run_scope: str) -> None:
        """Regenerate independent 3D projection files after changing the final spectrum.

        Skip when the controller lacks the projection interface. Projection failures are logged
        without changing the successful final-script run result.
        """
        regenerate = getattr(self.controller, "regenerate_3d_projections", None)
        if regenerate is None:
            return
        try:
            result = regenerate(
                exp_id,
                data_id,
                progress=lambda line: self.log_scoped.emit(line, run_scope),
            )
        except Exception as exc:  # noqa: BLE001
            result = {"error": describe_exception(exc)}
        if not isinstance(result, dict):
            return
        if result.get("error"):
            self.log_scoped.emit(
                tr("3D projection regeneration failed: {p0}", p0=result["error"]),
                run_scope,
            )
            return
        names = ", ".join(sorted(Path(str(path)).name for path in result.values()))
        if names:
            self.log_scoped.emit(tr("3D projection regenerated: {p0}", p0=names), run_scope)
