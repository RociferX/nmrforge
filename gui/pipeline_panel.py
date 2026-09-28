"""Middle Pipeline panel: Displays processing steps and status around the current sample
data/experiment type. Five-step process (Contract v1.2 / G2B-002, including optional SMILE
optimisation): Import sample data -> Generate FID -> Generate spectrum (including SMILE
reconstruction) -> [SMILE optimisation, optional] -> Peak selection. Analysis (HSQC CSP) has
been pressed The user decided to delete (2026-09-12, REPORT-008). For the deletion scope and
recovery method, see docs/tasks/archive/2026-09-12-analysis-removal.md. - The step status is
inferred based on the pre-dependency and product file (LOCKED/READY/RUNNING/SUCCESS/FAILED); -
READY The step provides a "Run" button and is executed by the corresponding method of
ProcessingController; - LOCKED The step tooltip explains which pre-step is missing; - GUI The
layer does not directly touch Backend: the only outlet is ProcessingController."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from qtcompat.QtCore import QPoint, QRect, QSize, Qt
from qtcompat.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
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
    script_fingerprint,
)
from gui.processing import ProcessingController
from qtcompat import Signal
from ui_support.i18n import tr
from ui_support.theme import (
    PANEL_BORDER,
    STATUS_COLORS,
    SURFACE_ALT,
    TEXT_MUTED,
    TEXT_PRIMARY,
    fit_combo_width,
)

# Step definition: id / name / description / pre-step id list.
PIPELINE_STEPS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("fid", tr("Generate FID"), tr("Convert raw data to fid (backend bruker -AUTO/fid.com)"), ()),
    (
        "spectrum",
        tr(
        "Generate "
        "spectrum",
    ),
        tr(
        "Back-end processing generates spectra (automatically includes NUS SMILE "
        "reconstruction)",
    ),
        ("fid",),
    ),
    ("smile", tr(
        "SMILE optimisation",
    ),
        tr(
        "Optional: Reconstruct parameter scan ranking (does not automatically replace active "
        "spectrum; 2D only "
        "NUS)",
    ), ("spectrum",)),
    ("peaks", tr(
        "Peak picking",
    ),
        tr(
        "automatic peak detection with intensity/SNR "
        "estimation",
    ), ("spectrum",)),
)

# 2026-09-12 (REPORT-008): The analysis steps have been deleted (including workflow/analyze.py and
# report pages). Only this part of the step table is retained, GUI and the state machine cannot be
# filtered separately.
VISIBLE_PIPELINE_STEPS = PIPELINE_STEPS

def _visible_pipeline_steps():
    return VISIBLE_PIPELINE_STEPS

STEP_LABEL: dict[str, str] = {
    step_id: label
    for step_id, label, _, _ in _visible_pipeline_steps()
}

STATUS_TEXT = {
    "LOCKED": tr("Not ready"),
    "READY": tr("Can be run"),
    "RUNNING": tr("Running"),
    "SUCCESS": tr("Completed"),
    "FAILED": tr("Failed"),
    "OUTDATED": tr("Expired"),
}
STATUS_ICON = {
    "LOCKED": "🔒",
    "READY": "▶",
    "RUNNING": "…",
    "SUCCESS": "✓",
    "FAILED": "×",
    "OUTDATED": "!",
}

# Step -> ProcessingController method mapping (contract v1.2 §8.3).
STEP_METHOD: dict[str, str] = {
    "fid": "generate_fid",
    "spectrum": "generate_spectrum",
    "smile": "optimize_smile",
    "peaks": "pick_peaks",
}



# Direct dimension range input constraint (0.2.199-patch29eg; patch29hz is divided by nuclide). The
# original implementation hard-coded the range 0-20 ppm (1H caliber): 13C direct detection solid
# experiments (CANCO/CAN(CO)CA/CBCANCO/CCC/NCACX/NCOCX/CANH/NCACB, etc.) and 13C 1D direct dimension
# are 0-200 ppm, the original verification will directly reject the legal window.
_DIRECT_RANGE_PPM: dict[str, tuple[float, float]] = {
    "1H": (0.0, 20.0),
    "2H": (0.0, 20.0),
    "13C": (-20.0, 220.0),
    "15N": (0.0, 260.0),
    "31P": (-60.0, 120.0),
    "19F": (-300.0, 100.0),
}
_DIRECT_RANGE_FALLBACK = (-100.0, 320.0)
# Direct dimension window commonly used for each nuclide (only used for dialog box placeholder
# prompts, leave it blank to use the default processing).
_DEFAULT_WINDOW_PPM: dict[str, tuple[str, str]] = {
    "1H": ("10.5", "6.5"),
    "13C": ("70", "20"),
    "15N": ("130", "100"),
}


def _direct_dimension_range(nucleus: str) -> tuple[float, float]:
    """Direct dimension nuclide -> ppm range allowed for input (unknown nuclide gives loose
    range)."""
    return _DIRECT_RANGE_PPM.get(str(nucleus or "").strip(), _DIRECT_RANGE_FALLBACK)


def _default_window_ppm(nucleus: str) -> tuple[str, str]:
    """Direct dimension nuclide -> common window for placeholder prompts (leave blank for unknown
    nuclei)."""
    return _DEFAULT_WINDOW_PPM.get(str(nucleus or "").strip(), ("", ""))


def validate_ext_range(lo_text: str, hi_text: str, nucleus: str = "") -> str:
    """Verify the "direct dimension range" input: legitimate/Leave blank to return "", otherwise
    the error text will be returned to the user."""
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
            return (
                tr(
                "{p0} ppm must be within {p1:g}-{p2:g} ({p3}direct "
                "dimension)",
                p0=name,
                p1=lo_min,
                p2=hi_max,
                p3=tag,
            )
            )
    if lo_f is not None and hi_f is not None and lo_f <= hi_f:
        return tr(
            "The high field end ppm must be greater than the low field end ppm (such as "
            "8.5-7.5)",
        )
    return ""


def _data_nodes(manager: ProjectManager, exp_id: str) -> list:
    """Returns the Data node under the experiment. Backend returns directly to entry.data after
    landing at the DataEntry level (contract v1.2 §8.1); Current compatibility stage: The
    experiment itself is used as the data node under the single data model."""
    entry = manager.project.experiment(exp_id) if manager.project is not None else None
    if entry is None:
        return []
    return list(getattr(entry, "data", None) or [])



def _node_artifacts(
    manager: ProjectManager, exp_id: str, data_id: str
) -> dict[str, Path | None]:
    """The product path of each step of a single data node (schema 1.4 data-level layout)."""
    artifacts: dict[str, Path | None] = {
        'fid': None,
        'spectrum': None,
        'peaks': None,
    }
    data = manager.data(exp_id, data_id)
    fid_candidate = getattr(data, 'fid_path', '') or ''
    if fid_candidate:
        path = Path(fid_candidate)
        if not path.is_absolute():
            path = manager.root / path
        # 0.2.108: Segmentally merge FID into directory (process/merged/fid), file or directory are
        # regarded as products.
        if path.is_file() or path.is_dir():
            artifacts['fid'] = path
    if artifacts['fid'] is None:
        proc = manager.data_dir(exp_id, data_id, 'process')
        try:
            fids = sorted(proc.glob('*.fid'))
        except OSError:
            fids = []
        if fids:
            artifacts['fid'] = fids[0]
        else:
            merged_fid = proc / 'merged' / 'fid'
            if merged_fid.is_dir():
                artifacts['fid'] = merged_fid
    artifacts['spectrum'] = find_primary_spectrum(manager, exp_id, data_id)
    for suffix in ('.list', '.csv'):
        candidate = (
            manager.data_dir(exp_id, data_id, 'peaks')
            / f'{exp_id}-{data_id}{suffix}'
        )
        if candidate.is_file():
            artifacts['peaks'] = candidate
            break
    return artifacts


def _upstream_artifact(
    step_id: str, artifacts: dict[str, Path | None]
) -> Path | None:
    prev = {'spectrum': 'fid', 'peaks': 'spectrum'}.get(
        step_id
    )
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
    compared with the old and new product, and "expired" is not displayed."""
    try:
        from gui.settings import load_settings

        pipeline = load_settings().get("pipeline") or {}
    except Exception:  # noqa: BLE001 - Close by default when the setting is unreadable.
        pipeline = {}
    return bool(pipeline.get("simple_mode", False))


def _node_step_statuses(
    manager: ProjectManager, exp_id: str, node
) -> dict[str, str]:
    """Five-step status of a single data node: product + fingerprint verification (OUTDATED) + pre-
    dependency."""
    data_id = getattr(node, 'id', exp_id)
    artifacts = _node_artifacts(manager, exp_id, data_id)
    state = load_pipeline_state(manager, exp_id, data_id)
    simple_mode = _pipeline_flags()
    statuses: dict[str, str] = {}
    for step_id, _, _, deps in _visible_pipeline_steps():
        artifact = (
            None if step_id == 'smile' else artifacts.get(step_id)
        )
        outdated = False
        if step_id == 'smile':
            # Optional step: Complete after running (product reuse spectrum, fingerprint
            # verification input changes).
            entry = state['steps'].get('smile')
            done = entry is not None
            if done and not simple_mode:
                current = input_fingerprint(manager, exp_id, data_id, 'smile')
                if (
                    current is not None
                    and entry.get('input_hash')
                    and current != entry['input_hash']
                ):
                    outdated = True
        else:
            done = artifact is not None
            if done and not simple_mode:
                entry = state['steps'].get(step_id)
                if entry:
                    current_input = input_fingerprint(
                        manager, exp_id, data_id, step_id
                    )
                    if (
                        current_input is not None
                        and entry.get('input_hash')
                        and current_input != entry['input_hash']
                    ):
                        outdated = True
                    current_script = script_fingerprint(
                        manager, exp_id, data_id, step_id
                    )
                    if (
                        current_script is not None
                        and entry.get('script_hash')
                        and current_script != entry['script_hash']
                    ):
                        outdated = True
                else:
                    # Old data/No fingerprint status: Use the upstream product mtime heuristic.
                    upstream = _upstream_artifact(step_id, artifacts)
                    if upstream is not None and _mtime_ns(upstream) > _mtime_ns(
                        artifact
                    ):
                        outdated = True
        failed_run = _last_run_for(
            manager, exp_id, data_id, _step_refs(step_id)
        )
        if failed_run is not None and failed_run.status == 'failed':
            statuses[step_id] = 'FAILED'
        elif done and not outdated:
            statuses[step_id] = 'SUCCESS'
        elif done:
            statuses[step_id] = 'OUTDATED'
        elif all(statuses.get(dep) in ('SUCCESS', 'OUTDATED') for dep in deps):
            statuses[step_id] = 'READY'
        else:
            statuses[step_id] = 'LOCKED'
    # Upstream OUTDATED Propagation: Even if the fingerprint matches the downstream, it is
    # considered expired (no propagation when the switch is turned off).
    if not simple_mode:
        for step_id, _, _, deps in _visible_pipeline_steps():
            if statuses.get(step_id) == 'SUCCESS' and any(
                statuses.get(dep) == 'OUTDATED' for dep in deps
            ):
                statuses[step_id] = 'OUTDATED'
    return statuses


def compute_step_statuses(manager: ProjectManager, exp_id: str) -> dict[str, str]:
    """Infer the status of each step according to product file, fingerprint verification and pre-
    dependency (supports OUTDATED). Experimental-level aggregation: when any node succeeds when
    there is multiple data, it means SUCCESS(Compatible with old behaviour/test)."""
    nodes = _data_nodes(manager, exp_id)
    if not nodes:
        return {step_id: 'LOCKED' for step_id, _, _, _ in _visible_pipeline_steps()}
    per_node = [_node_step_statuses(manager, exp_id, node) for node in nodes]
    statuses: dict[str, str] = {}
    for step_id, _, _, deps in _visible_pipeline_steps():
        verdicts = [node_status[step_id] for node_status in per_node]
        if 'OUTDATED' in verdicts:
            statuses[step_id] = 'OUTDATED'
        elif 'SUCCESS' in verdicts:
            statuses[step_id] = 'SUCCESS'
        elif all(statuses.get(dep) in ('SUCCESS', 'OUTDATED') for dep in deps):
            statuses[step_id] = 'READY'
        else:
            statuses[step_id] = 'LOCKED'
    return statuses


def compute_data_step_statuses(
    manager: ProjectManager, exp_id: str, data_id: str
) -> dict[str, str]:
    """Calculate the step status according to a single data node (the intermediate processing page
    is displayed according to the selected data)."""
    nodes = _data_nodes(manager, exp_id)
    node = next(
        (n for n in nodes if getattr(n, "id", "") == data_id), None
    )
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
    behind)."""
    reasons: dict[str, str] = {}
    nodes = _data_nodes(manager, exp_id)
    if data_id:
        nodes = [n for n in nodes if getattr(n, "id", "") == data_id]
    for step_id, _, _, deps in _visible_pipeline_steps():
        if statuses.get(step_id) != 'OUTDATED':
            continue
        reason = ''
        for node in nodes:
            # 0.2.199-patch29hz:Don't reuse/cover parameter data_id (After the original
            # implementation is overwritten, it is very easy to read the wrong data in subsequent
            # modifications).
            node_data_id = getattr(node, 'id', exp_id)
            entry = load_pipeline_state(
                manager, exp_id, node_data_id
            )['steps'].get(step_id)
            if entry and entry.get('input_hash'):
                current = input_fingerprint(
                    manager, exp_id, node_data_id, step_id
                )
                if current is not None and current != entry['input_hash']:
                    reason = (
                        tr(
                        "The input has changed (upstream re-run or external modification), please "
                        "re-run",
                    )
                    )
                    break
                current_script = script_fingerprint(
                    manager, exp_id, node_data_id, step_id
                )
                if (
                    current_script is not None
                    and entry.get('script_hash')
                    and current_script != entry['script_hash']
                ):
                    reason = tr("The processing script has changed, please run it again")
                    break
        if not reason:
            stale_upstream = [
                STEP_LABEL[dep] for dep in deps if statuses.get(dep) == 'OUTDATED'
            ]
            if stale_upstream:
                reason = tr("The upstream step has expired: ") + ','.join(stale_upstream)
            else:
                reason = (
                    tr(
                    "The input product is newer than the product of this step, please run "
                    "again",
                )
                )
        reasons[step_id] = reason
    return reasons


def _lock_reasons(statuses: dict[str, str]) -> dict[str, str]:
    """Generate dependency hints for the LOCKED step (telling the user which prerequisites are
    missing)."""
    reasons: dict[str, str] = {}
    for step_id, _, _, deps in _visible_pipeline_steps():
        if statuses.get(step_id) != "LOCKED":
            continue
        missing = [STEP_LABEL[dep] for dep in deps if statuses.get(dep) != "SUCCESS"]
        if missing:
            reasons[step_id] = tr("Prerequisite steps not completed: ") + ",".join(missing)
        else:
            reasons[step_id] = tr("Wait for the pre-product to be ready")
    return reasons


def _step_refs(step_id: str) -> tuple[str, ...]:
    """Steps -> Possible workflow refs (see gui.pipeline_state.STEP_RUN_REFS)."""
    return STEP_RUN_REFS.get(step_id, ())


def _last_run_for(
    manager: ProjectManager, exp_id: str, data_id: str, refs: tuple[str, ...]
):
    """The latest run of the data under the specified step refs (strictly data_id attribution).
    0.2.199-patch29hz: The original accepted inputs are missing data_id old records. In multi-
    data experiments, an old failure will be counted on all data heads; the old project products
    will be regenerated, so there will be no rollback."""
    return manager.last_run_for_data(exp_id, data_id, refs)


def _format_params(params: dict) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(params.items()))


def _fid_step_report_lines(
    manager: ProjectManager, exp_id: str, data_id: str
) -> list[str]:
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
    except Exception:  # noqa: BLE001 - an unresolvable path means "no record"
        return [tr("(no data quality report record for this data)")]
    try:
        return fid_step_quality_report(work, [raw])
    except Exception as exc:  # noqa: BLE001 - a broken report must not break the UI
        return [tr("could not read the data quality report: {p0}", p0=exc)]


def _spectrum_param_report(
    params: dict, spectrum_path: str | None = None
) -> str:
    """Generate spectrum parameter report (0.2.169-complement readable): ◆ processing parameter
    and optimisation; spectrum_path appended when readable ◆ final spectrum graph quality
    (shared with log end summary spectrum_quality_report_lines).

    2026-09-23 (user request): the data quality diagnosis belongs to the Generate-FID step and
    was removed from this report - that step's log and step report carry it now."""
    from workflow.optimization_report import (
        format_optimization_report,
        spectrum_quality_report_lines,
    )

    lines: list[str] = []
    # 2026-09-23 (user request): the data quality diagnosis is not here - that conclusion
    # belongs to the Generate-FID step (see _fid_step_report_lines / that step's log report).
    # 0.2.199-patch29ab: reporting order = processing parameter and optimisation -> final
    # spectrum image quality (the data quality diagnosis moved to the Generate-FID step).
    lines.append(tr("◆ Handle parameter and optimisation"))
    lines += format_optimization_report(
        {k: v for k, v in params.items() if k != "diagnostics"}
    )
    if spectrum_path:
        lines += spectrum_quality_report_lines(spectrum_path)
    return "\n".join(lines) if lines else tr(" (no parameter record)")


class _FlowLayout(QLayout):
    """Simple flow layout: Automatically wrap when the sub-item exceeds the available width
    (0.2.199-patch4). Used in the step row button area -- Up to 5 buttons after the spectrum
    generation is completed (direct dimension scope/again optimisation/rerun final
    script/display spectrum/manual), automatically wrap when a single line is too wide, without
    breaking the panel."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setSpacing(6)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
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
        # Patch29hz-fix 19: There should also be space between items.
        space = max(0, int(self.spacing()))
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        line_height = 0
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                # Hidden buttons do not occupy space (such as "direct dimension range" of non-
                # spectrum rows).
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
# Indirect-dimension flip (FT -neg): rewriting the final script text / reading its current state
#
# User 2026-09-25: the spectrum step's "indirect flip" only edits the **existing final
# script** (no re-optimisation), and the re-run reuses the 0.2.163-patch5 entrance. The only
# rewriting rule: add/remove ``-neg`` on the **indirect-dimension FT line** according to the
# choice; the direct-dimension FT, ``-alt``/``-real``/``-bruk``, EXT, PS, window functions,
# baseline and the SMILE direction flags (``-xNeg``/``-yNeg``) are never touched.
# ---------------------------------------------------------------------------

#: ndim -> order in which the indirect-dimension FT lines appear in the final script (matches
#: backend/script_generator.py's generate_process_script / generate_*_nus_script: after the
#: direct dimension, F2 then F1)
_INDIRECT_AXIS_ORDER: dict[int, tuple[str, ...]] = {2: ("F1",), 3: ("F2", "F1")}

#: stand-alone ``-neg`` token (does not match ``-xNeg``/``-nSigma`` and the like)
_NEG_TOKEN = re.compile(r"(?<![-\w])-neg(?![-\w])")
_FT_LINE_TOKEN = re.compile(r"-fn\s+FT(?![-\w])", re.IGNORECASE)


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
    return bool(stripped) and not stripped.startswith("#") and bool(
        _FT_LINE_TOKEN.search(line)
    )


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
                seen_ft = True  # the statement's first FT line = direct dimension
                continue
            indirect.append(line_no)
    if len(indirect) != len(expected):
        return None
    return indirect


def _add_neg_token(line: str) -> str:
    """Append ``-neg`` to an FT line (unchanged if already present); inserted at the end of
    the parameters, before the continuation backslash."""
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
    # Eat the whitespace immediately before the token too, so that no gap such as "FT  -alt"
    # or "FT \\" is left behind.
    cleaned = re.sub(r"[ \t]*-neg(?![-\w])", "", line, count=1)
    return re.sub(r"[ \t]+$", "", cleaned)


def flip_indirect_ft_lines(
    script: str, *, ndim: int, flips: dict[str, bool]
) -> tuple[str, dict[str, bool]]:
    """Edit the **indirect-dimension** FT lines according to ``flips`` (axis -> whether that
    axis should finally carry ``-neg``).

    Returns ``(new script, axes actually changed -> resulting state)``; when the layout
    cannot be recognised it returns an empty dict, on which the caller tells the user to
    "re-optimise to generate the script first".
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
            continue  # already in the target state
        lines[line_no] = (
            _add_neg_token(lines[line_no]) if want else _remove_neg_token(lines[line_no])
        )
        applied[axis] = want
    return "\n".join(lines), applied


def indirect_neg_state(script: str, *, ndim: int) -> dict[str, bool]:
    """Whether each indirect dimension of the final script currently carries ``-neg``;
    empty dict when the layout cannot be recognised."""
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
            return "\n".join(lines[statement[0]:]) + "\n"
    return None


class PipelineStepRow(QWidget):
    """Single step line: status icon + name + description + run/artificial entrance + embedded
    details. Click on row to expand/Collapse details (input product, running record, parameter,
    script snapshot); LOCKED / OUTDATED / FAILED The reason is displayed in gray text; FAILED
    provides "View log" and "Retry"."""

    run_requested = Signal(str)  # step_id
    # step_id + sampling overrides (indirect flip: {"flip_f1"/"flip_f2": whether the target
    # should carry -neg})
    rerun_final_requested = Signal(str, dict)
    # Open the script editor (existing scripts are preferred).
    manual_requested = Signal(str)
    # Display the spectrum after generating the spectrum.
    show_spectrum_requested = Signal(str)
    ext_range_requested = Signal(str)  # step_id:Set the final run direct dimension range.
    ref_spectrum_requested = Signal(str)  # step_id:Select reference spectrum (peak picking).
    clear_ref_requested = Signal(str)  # step_id:Clear reference spectrum constraints.
    detail_toggled = Signal(str)  # step_id:Click on row to toggle details.
    view_log_requested = Signal(str)  # step_id:Locate the log panel.
    rank1_run_requested = Signal(str)  # step_id:Press SMILE to scan Rank1 and rerun final spectrum.
    localization_changed = Signal(str)  # Changes in peak positioning methods (peaks step).

    def __init__(
        self, step_id: str, label: str, description: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        # 0.2.199-patch29hz-Repair 2: Thin line separation at the bottom of step row + hover
        # highlight.
        self.setObjectName("StepRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.step_id = step_id
        # Indirect-dimension flip controls (2026-09-25): 2D checkbox / 3D drop-down; the state
        # stays in sync with the final script.
        self._flip_ndim = 2
        self._rerun_widgets_visible = False
        self._indirect_neg: dict[str, bool] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(0)

        header = QHBoxLayout()
        self.icon_label = QLabel()
        self.icon_label.setFixedWidth(24)
        header.addWidget(self.icon_label)
        # Patch29hz-fix 20: status icon and title are not stuck together (geometry guard).
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
        # 0.2.163-patch5:Button title/A separate line below the description (no longer squeezed on
        # the right); 0.2.199-patch4: Automatically wrap lines when there are many buttons (such as
        # spectrum steps 5) to avoid lines that are too wide.
        button_row = _FlowLayout()
        button_row.setContentsMargins(24, 0, 0, 0)
        # 0.2.162-patch15: Generate spectrum "direct dimension range" button before running (only
        # effective in final run).
        self.ext_range_button = QPushButton(tr("direct dimension range"))
        self.ext_range_button.setToolTip(
            tr(
                "Set the direct-dimension extraction window (EXT -x1/-xn); \"apply this range to "
                "the optimisation\" is on by default and can be turned off; when unset the default "
                "(10.5-6.5) is "
                "used",
            )
        )
        self.ext_range_button.setVisible(self.step_id == "spectrum")
        self.ext_range_button.clicked.connect(
            lambda: self.ext_range_requested.emit(self.step_id)
        )
        button_row.addWidget(self.ext_range_button)
        # 0.2.199-patch29ar/patch29au/patch29bo/patch29cm/patch29cn: Peak selection threshold bar
        # (3.0–50.0 σ, default 35; the input box has no upper limit, the slider only reaches 50);
        # patch29au: Adjustment only updates the value, click "run/reprocess" to re-select peaks.
        self.threshold_label = QLabel(tr("threshold (σ)"))
        self.threshold_label.setVisible(self.step_id == "peaks")
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setRange(30, 500)
        self.threshold_slider.setValue(350)
        self.threshold_slider.setFixedWidth(120)
        self.threshold_slider.setVisible(self.step_id == "peaks")
        self.threshold_spin = QDoubleSpinBox()
        # There is no upper limit on the input value (patch29cn).
        self.threshold_spin.setRange(3.0, 1_000_000.0)
        self.threshold_spin.setSingleStep(0.5)
        self.threshold_spin.setDecimals(1)
        self.threshold_spin.setValue(35.0)
        self.threshold_spin.setVisible(self.step_id == "peaks")
        # Linkage protection: When the input exceeds the upper limit of the slider (50σ), the slider
        # stops at 500 and does not write back to overwrite the input value.
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
                self.threshold_slider.setValue(
                    max(30, min(500, int(round(v * 10.0))))
                )
            finally:
                self._threshold_sync = False

        self.threshold_slider.valueChanged.connect(_slider_to_spin)
        self.threshold_spin.valueChanged.connect(_spin_to_slider)
        tip = (
            tr(
            "Adjust peak picking threshold (σ); click \"run / reprocess\" and press the new "
            "threshold to peak picking "
            "again",
        )
        )
        self.threshold_slider.setToolTip(tip)
        self.threshold_spin.setToolTip(tip)
        button_row.addWidget(self.threshold_label)
        button_row.addWidget(self.threshold_slider)
        button_row.addWidget(self.threshold_spin)
        # 2026-09-13 (user request): Peak positioning method -- Parabola (default, existing
        # algorithm)/2D Gaussian fitting. Gaussian only 2D: This item is disabled and a clear prompt
        # is displayed when it is not 2D.
        self.localization_label = QLabel(tr("peak localization"))
        self.localization_label.setVisible(self.step_id == "peaks")
        self.localization_combo = QComboBox()
        self.localization_combo.addItem(tr("parabolic line (default)"), "parabolic")
        self.localization_combo.addItem(tr("2D Gaussian fitting"), "gaussian")
        self.localization_combo.setCurrentIndex(0)
        self.localization_combo.setVisible(self.step_id == "peaks")
        self.localization_combo.setToolTip(self._localization_tip(True))
        self._localization_sync = False
        self.localization_combo.currentIndexChanged.connect(
            self._on_localization_changed
        )
        button_row.addWidget(self.localization_label)
        button_row.addWidget(self.localization_combo)
        # 0.2.199-patch29dl(user): Reference spectrum -- When selecting peaks, only keep peaks that
        # match the reference peak table 0.2.199-patch29hz - Modify 4: SMILE optimisation degree
        # 2x2..5x5 (remember according to data).
        self.grid_label = QLabel(tr("degree of optimisation"))
        self.grid_label.setVisible(self.step_id == "smile")
        self.grid_combo = QComboBox()
        for _n in (2, 3, 4, 5):
            self.grid_combo.addItem(f"{_n}x{_n}", _n)
        self.grid_combo.setCurrentIndex(2)  # Default 4x4(0.2.199-patch29hz-fix 5).
        self.grid_combo.setVisible(self.step_id == "smile")
        self.grid_combo.setToolTip(
            tr(
                "Optimisation depth n×n: scans n×n combinations of nSigma × threshold.\n2×2 = 4, "
                "3×3 = 9, 4×4 = 16, 5×5 = 25 combinations; the default is 4×4.\nA finer grid is "
                "slower (each combination does its own reconstruction and "
                "evaluation).",
            )
        )
        fit_combo_width(self.grid_combo)  # Fix ellipses (0.2.199-patch29hz-fix 14).
        button_row.addWidget(self.grid_label)
        button_row.addWidget(self.grid_combo)
        # 0.2.199-patch29hz - Modification 19 (user): Leave a gap between the two groups (previously
        # there was only 6px for the fluid layout, and the two drop-down boxes looked crowded
        # together); use a visibility-controlled gap control, and other steps will not be affected.
        self.smile_gap = QFrame()
        self.smile_gap.setFrameShape(QFrame.Shape.NoFrame)
        self.smile_gap.setFixedSize(12, 1)
        self.smile_gap.setVisible(self.step_id == "smile")
        button_row.addWidget(self.smile_gap)
        self.rank_label = QLabel(tr("sort"))
        self.rank_label.setVisible(self.step_id == "smile")
        self.rank_combo = QComboBox()
        # 0.2.199-patch29hz-Repair 15(user): The option name points out the difference + the hover
        # description makes the two calibers clear.
        self.rank_combo.addItem(tr("Pure peaks first (fewer false peaks)"), "true_peaks")
        self.rank_combo.addItem(tr("Consistency first (small residuals)"), "consistency")
        self.rank_combo.setVisible(self.step_id == "smile")
        self.rank_combo.setToolTip(
            tr(
                "Ranking criterion: decides which three of the 25 parameter combinations provide "
                "the\nfinal-run scripts.\n\n- Pure peaks first (default): ranks by \"stable peaks "
                "minus suspected spurious peaks\".\n  A stable peak is one that appears in most "
                "parameter combinations;\n  a suspected spurious peak appears in only a few.\n  "
                "Suited to \"as few spurious peaks and as many true peaks as possible\";\n  the "
                "number of peaks comes from SMILE's own low threshold (3 sigma),\n  and under this "
                "criterion every combination is reconstructed from all sampling points\n  (the "
                "final-spectrum criterion),\n  independently of the threshold used by the "
                "peak-picking step (35 sigma by default).\n\n- Consistency first: ranks by the "
                "residual on the held-out sampling points.\n  Under this criterion every "
                "combination is reconstructed from the held-out points:\n  the reconstruction is "
                "then compared with the measured values (a smaller residual is better,\n  and a "
                "correlation coefficient closer to 1 is better), independently of the peak "
                "count.\n  Suited to wanting trustworthy \"shape and intensities\" first;\n  when "
                "no hold-out table is available it falls back to ranking by fit "
                "residual.",
            )
        )
        fit_combo_width(self.rank_combo)  # Fix ellipses (0.2.199-patch29hz-fix 14).
        button_row.addWidget(self.rank_label)
        button_row.addWidget(self.rank_combo)
        self.ref_button = QPushButton(tr("Reference spectrum"))
        self.ref_button.setToolTip(
                tr(
                "Select a reference spectrum (any data with an existing peak table): During peak "
                "picking, only peaks that match the reference peak table are "
                "retained",
            )
        )
        self.ref_button.setVisible(self.step_id == "peaks")
        self.ref_button.clicked.connect(
            lambda: self.ref_spectrum_requested.emit(self.step_id)
        )
        button_row.addWidget(self.ref_button)
        self.ref_label = QLabel("")
        self.ref_label.setVisible(self.step_id == "peaks")
        self.ref_label.setStyleSheet("color: #16a085;")
        button_row.addWidget(self.ref_label)
        self.clear_ref_button = QPushButton(tr("Clear"))
        self.clear_ref_button.setVisible(False)
        self.clear_ref_button.setToolTip(tr("Clear reference spectrum constraints"))
        self.clear_ref_button.clicked.connect(
            lambda: self.clear_ref_requested.emit(self.step_id)
        )
        button_row.addWidget(self.clear_ref_button)
        self.run_button = QPushButton(tr("run"))
        self.run_button.setVisible(False)
        self.run_button.clicked.connect(lambda: self.run_requested.emit(self.step_id))
        button_row.addWidget(self.run_button)
        # 0.2.163-patch5: "Rerun the final script" when the spectrum is completed (reuse existing
        # scripts, without optimisation).
        # 2026-09-25 (user, second UI wording): the indirect flip and "Re-run the final script"
        # are **one thing** (the flip only applies to this re-run), so both go into the same
        # bordered little box with the flip control on the **left** (read as "flip -> re-run"),
        # and the box never takes up space in other step rows.
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
        # Indirect-dimension flip (FT -neg): 2D uses a checkbox (checked = the final script's
        # indirect dimension F1 already carries -neg), 3D uses a three-entry drop-down (indirect
        # F2 / indirect F1 / F1 and F2); shown/hidden under the same condition as
        # rerun_final_button.
        self.flip_indirect_check = QCheckBox(tr("Indirect (F1)"))
        self.flip_indirect_check.setToolTip(
            tr(
                "Add FT -neg to the indirect dimension (F1) and re-run the final script; "
                "unchecking removes it again. Only the indirect-dimension FT line is changed.",
            )
        )
        self.flip_indirect_check.setVisible(False)
        self.flip_indirect_combo = QComboBox()
        # Option value = the per-axis keys to decide **directly** (official names
        # ft_neg_f1/ft_neg_f2, same semantics as the global ft_neg; flip_f* are legacy aliases
        # that new code does not use)
        self.flip_indirect_combo.addItem(tr("Indirect (F2)"), {"ft_neg_f2": True})
        self.flip_indirect_combo.addItem(tr("Indirect (F1)"), {"ft_neg_f1": True})
        self.flip_indirect_combo.addItem(
            tr("F1 and F2"), {"ft_neg_f1": True, "ft_neg_f2": True}
        )
        # When nothing is selected (-1) nothing happens: the drop-down is a **command**
        # (choosing an entry sets the dimensions it contains to "add", and choosing the same
        # entry again sets them back to "no add"), then it resets; the empty state shows
        # "no flip".
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
        # The placeholder text's width is not part of fit_combo_width's rule (it only measures
        # the menu entries), so top it up separately, lest the placeholder be truncated with an
        # ellipsis while nothing is selected (same cause as 0.2.199-patch29hz-revision 14).
        _flip_metrics = self.flip_indirect_combo.fontMetrics()
        self.flip_indirect_combo.setMinimumWidth(
            max(
                self.flip_indirect_combo.minimumWidth(),
                _flip_metrics.horizontalAdvance(
                    self.flip_indirect_combo.placeholderText()
                )
                + 38,
            )
        )
        # Order inside the group: title -> flip control -> re-run button (read as
        # "flip -> re-run")
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
        button_row.addWidget(self.show_spectrum_button)
        # 0.2.199-patch29hz - Modification 3: SMILE optimisation only produces the ranking list +
        # the top three scripts (Plan B), and only uses the Rank1 script after user confirmation to
        # actually produce the score.
        self.rank1_button = QPushButton(tr("Press Rank1 to rerun"))
        self.rank1_button.setToolTip(
                tr(
                "Use SMILE to scan the selected Rank1 parameter, rerun the final script, and use "
                "the result as the current "
                "spectrum",
            )
        )
        self.rank1_button.setVisible(False)
        self.rank1_button.clicked.connect(
            lambda: self.rank1_run_requested.emit(self.step_id)
        )
        button_row.addWidget(self.rank1_button)
        self.manual_button = QPushButton(tr("Artificial"))
        self.manual_button.setToolTip(
            tr(
            "Script editor: Open the script generated in this step, you can directly modify it and "
            "run "
            "it",
        )
        )
        self.manual_button.setVisible(False)
        self.manual_button.clicked.connect(
            lambda: self.manual_requested.emit(self.step_id)
        )
        button_row.addWidget(self.manual_button)
        outer.addLayout(button_row)

        self.reason_label = QLabel("")
        self.reason_label.setStyleSheet(f"color: {TEXT_MUTED};")
        self.reason_label.setWordWrap(True)
        self.reason_label.setVisible(False)
        outer.addWidget(self.reason_label)

        self.detail_frame = QFrame()
        self.detail_frame.setFrameShape(QFrame.Shape.StyledPanel)
        # Explicit light background: QFrame will turn black under dark system themes, and gray text
        # cannot be seen clearly.
        self.detail_frame.setStyleSheet(
            "QFrame { background: #ffffff; border: 1px solid #d5d8dc; "
            "border-radius: 4px; }"
        )
        self.detail_frame.setVisible(False)
        detail_layout = QVBoxLayout(self.detail_frame)
        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("color: #222;")
        detail_layout.addWidget(self.detail_label)
        detail_buttons = QHBoxLayout()
        self.view_log_button = QPushButton(tr("View log"))
        self.view_log_button.setVisible(False)
        self.view_log_button.clicked.connect(
            lambda: self.view_log_requested.emit(self.step_id)
        )
        detail_buttons.addWidget(self.view_log_button)
        self.retry_button = QPushButton(tr("Try again"))
        self.retry_button.setVisible(False)
        self.retry_button.clicked.connect(
            lambda: self.run_requested.emit(self.step_id)
        )
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
        value is set)."""
        self.ext_range_button.setText(text)

    def get_threshold(self) -> float:
        """Peak picking step threshold (σ); non-peaks steps return default 35.0 (patch29hn)."""
        return self.threshold_spin.value() if self.step_id == "peaks" else 35.0

    def _localization_tip(self, supported: bool) -> str:
        """Instructions for peak positioning drop-down/Prompts not supported(user requirements:
        must be clearly informed)."""
        base = (
            tr(
                "Peak localisation method:\nparabolic = the existing 3-point parabola vertex "
                "(default, behaviour unchanged)\n2D Gaussian fit = fits an unrotated 2D Gaussian "
                "near the candidate peak, giving centre / linewidth / amplitude\nROI and "
                "parameters live in config peaks.localization (physical ppm "
                "width)",
            )
        )
        if not supported:
            from core.peaks.localize import GAUSSIAN_UNSUPPORTED_MESSAGE

            return base + "\n" + GAUSSIAN_UNSUPPORTED_MESSAGE + (
                tr(
                "(The current spectrum is not "
                "available)",
            )
            )
        return base

    def get_localization_method(self) -> str:
        """Current peak location method; non-peaks steps return default parabolic."""
        if self.step_id != "peaks":
            return "parabolic"
        return str(self.localization_combo.currentData() or "parabolic")

    def set_localization_method(self, method: str) -> None:
        """Peak positioning method for programmatically restoring certain data (without triggering
        persistence callbacks)."""
        index = self.localization_combo.findData(str(method or "parabolic"))
        self._localization_sync = True
        try:
            self.localization_combo.setCurrentIndex(max(0, index))
        finally:
            self._localization_sync = False

    def set_localization_supported(self, supported: bool) -> None:
        """Non-2D disable Gaussian option (user requirement: not allowed to be called on non-2D)."""
        item_index = self.localization_combo.findData("gaussian")
        if item_index >= 0:
            item = self.localization_combo.model().item(item_index)
            if item is not None:
                item.setEnabled(bool(supported))
        if not supported and self.get_localization_method() == "gaussian":
            self.set_localization_method("parabolic")
        self.localization_combo.setToolTip(self._localization_tip(bool(supported)))

    def _on_localization_changed(self, *_args) -> None:
        if self._localization_sync or self.step_id != "peaks":
            return
        self.localization_changed.emit(self.get_localization_method())

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
        # Status colour (0.2.199-patch29hz-revision 2): icons and text are colored according to
        # status, and you can know success or failure at a glance.
        _color = STATUS_COLORS.get(status, TEXT_MUTED)
        self.status_label.setStyleSheet(
            f"color: {_color}; font-weight: bold;"
        )
        self.icon_label.setStyleSheet(f"color: {_color};")
        tooltip = tr("state: {p0}", p0=STATUS_TEXT.get(status, status))
        if reason:
            tooltip += f"\n{reason}"
        self.status_label.setToolTip(tooltip)
        # The reason is displayed directly: LOCKED/OUTDATED/FAILED gray text (not only tooltip).
        if status in ("LOCKED", "OUTDATED", "FAILED"):
            self.reason_label.setText(reason or STATUS_TEXT.get(status, status))
            self.reason_label.setVisible(True)
        else:
            self.reason_label.setVisible(False)
        threshold_ok = status in ("READY", "SUCCESS", "OUTDATED", "FAILED")
        if self.step_id == "peaks":
            self.threshold_slider.setEnabled(threshold_ok)
            self.threshold_spin.setEnabled(threshold_ok)
            self.localization_combo.setEnabled(threshold_ok)
        if status == "OUTDATED":
            self.run_button.setText(tr("run again"))
            self.run_button.setVisible(True)
            self.run_button.setToolTip(
                tr(
                "Input/parameter has changed, run again to update the "
                "results",
            )
            )
        elif status == "SUCCESS":
            if self.step_id == "spectrum":
                # 0.2.163-patch5: Reprocess the two buttons -- Re-optimisation / Re-run the final
                # script.
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
                self.run_button.setText(tr("reprocess"))
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
        # SMILE The "Rerun by Rank1" entrance will be provided only after the scan is completed
        # (SUCCESS).
        self.rank1_button.setVisible(
            status == "SUCCESS" and self.step_id == "smile"
        )

    # ------------------------------------------------------------------
    # Indirect-dimension flip (FT -neg) controls (user 2026-09-25)
    # ------------------------------------------------------------------
    def _set_rerun_widgets_visible(self, visible: bool) -> None:
        """Show/hide "Re-run the final script" and the indirect flip (one unit inside the same
        box) together."""
        self._rerun_widgets_visible = bool(visible)
        self.rerun_group.setVisible(bool(visible))
        self.rerun_final_button.setVisible(bool(visible))
        self._sync_flip_widget_visibility()

    def _sync_flip_widget_visibility(self) -> None:
        """Flip-control visibility: spectrum step + an existing final script; 2D checkbox, 3D
        drop-down, no entrance for 1D."""
        visible = self._rerun_widgets_visible and self.step_id == "spectrum"
        self.flip_indirect_check.setVisible(visible and self._flip_ndim == 2)
        self.flip_indirect_combo.setVisible(visible and self._flip_ndim >= 3)

    def set_indirect_dimension(self, ndim: int) -> None:
        """Pick the control's shape from the data dimensionality (2D checkbox / 3D three-entry
        drop-down)."""
        self._flip_ndim = int(ndim or 2)
        self._sync_flip_widget_visibility()

    def set_indirect_neg_state(self, state: dict[str, bool] | None) -> None:
        """Fill the controls from the final script's current state (2D checkbox checked =
        indirect dimension F1 already carries ``-neg``).

        The 2D checkbox is itself a **state**: without filling it back in, the next ordinary
        re-run (only to update the direct-dimension range) would silently drop the previous
        flip. The 3D drop-down is a command and does not show the current state, but the flip
        conversion needs that state (see :meth:`_take_flip_sampling`).
        """
        self._indirect_neg = {
            str(axis): bool(neg) for axis, neg in (state or {}).items()
        }
        if self._flip_ndim < 3:
            self.flip_indirect_check.setChecked(
                bool(self._indirect_neg.get("F1", False))
            )

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
    log_scoped = Signal(str, str)  # (message, scope):Run log by data/group scope(0.2.199-patch29d).
    memory_guard_requested = Signal(str)  # 0.2.112:SMILE Insufficient memory pop-up window.
    run_finished = Signal()
    # A certain data starts to be processed, and the status on the left shows running.
    run_started = Signal(str, str)
    manual_open_requested = Signal(str)  # step_id:Open the manual processing dialog box.
    show_spectrum_requested = Signal(str)  # step_id:Display spectrum.
    view_log_requested = Signal(str)  # step_id:Locate the log panel.
    progress_updated = Signal(str)  # Batch progress text (main thread updates labels).
    batch_summary_requested = Signal(object)  # Batch summary dict.
    rank1_run_requested = Signal(str)  # SMILE Rank1 Rerun.

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
        # Display steps; project/experiment displays prompts.
        self._selection_kind: str = ""
        self._current_data_id: str = ""
        self._rows: dict[str, PipelineStepRow] = {}
        # 0.2.162-patch15:(exp_id, data_id) -> (end run ext_lo, end run ext_hi).
        self._final_ext: dict[tuple[str, str], tuple[str, str, bool]] = {}
        # 0.2.199-patch12: Generate a spectrum parameter report based on the spectrum file
        # fingerprint cache to avoid refreshing the main thread every time and re-reading large ft3
        # calculations resulting in lag; the running flag prevents continuous clicks and repeated
        # starts.
        self._spectrum_report_cache: dict[str, str] = {}
        self._run_active: bool = False
        # 0.2.199-patch29dl/patch29fx: Reference spectrum constraints are isolated by data --
        # (exp_id, data_id) -> {label, peaks, nuclei, path}; when switching data, the reference is
        # not passed to other data processing interfaces (user 2026-09-04).
        self._ref_info: dict[tuple[str, str], dict] = {}
        # 0.2.199-patch29fz(user): The peak selection threshold is isolated by data -- (exp_id,
        # data_id) -> σ; switch the respective threshold for data recovery.
        self._threshold_by_data: dict[tuple[str, str], float] = {}
        # 0.2.199-patch29gc: Explicit custom tag (default values still respect user settings after
        # migration).
        self._threshold_custom_by_data: dict[tuple[str, str], bool] = {}
        # The peak positioning method is based on data cache (ui_state persistence, the same
        # partition as the threshold).
        self._localization_by_data: dict[tuple[str, str], str] = {}
        # 0.2.199-patch29gd(user): Non-NUS (including misjudgment as NUS but actual full sampling)
        # does not display SMILE optimisation -- (exp_id, data_id) -> Whether NUS, check cache once.
        self._nus_cache: dict[tuple[str, str], bool] = {}
        self._smile_step_visible = False
        self._ndim_cache: dict[tuple[str, str], int] = {}
        # 0.2.199-patch29hz: direct dimension nuclide cache (direct dimension range
        # check/Placeholder reminder).
        self._nucleus_cache: dict[tuple[str, str], str] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # 0.2.199-patch29hz-Fix 2: title bar (current context as subtitle).
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
        # 0.2.199-patch29c:run_finished Emit from the working thread, connect back to the main
        # thread through the queue, refresh -- the old code directly self.refresh() cross-thread
        # touch the control in the finally of the working thread, trigger QBasicTimer::start error
        # and get stuck.
        self.run_finished.connect(self._refresh_after_run)
        self.hint_bubble = QLabel("")
        self.hint_bubble.setVisible(False)
        self.hint_bubble.setWordWrap(True)
        self.hint_bubble.setStyleSheet(
            "background: #fef9e7; border: 1px solid #f5b041; "
            "color: #935116; padding: 4px 8px;"
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
            row.ref_spectrum_requested.connect(self._on_pick_reference)
            row.clear_ref_requested.connect(self._on_clear_reference)
            row.detail_toggled.connect(self._toggle_step_detail)
            row.view_log_requested.connect(self.view_log_requested.emit)
            row.rank1_run_requested.connect(self.rank1_run_requested.emit)
            steps_box.addWidget(row)
            self._rows[step_id] = row
        # 0.2.199-patch29fz/patch29gc(user): The current data will be recorded every time the
        # threshold is adjusted; End of editing/The slider release mark is Explicitly customized by
        # user (programmed recovery does not count).
        peaks_row = self._rows.get("peaks")
        if peaks_row is not None:
            peaks_row.threshold_spin.valueChanged.connect(
                self._store_current_threshold
            )
            peaks_row.threshold_spin.editingFinished.connect(
                self._mark_current_threshold_custom
            )
            peaks_row.threshold_slider.sliderReleased.connect(
                self._mark_current_threshold_custom
            )
            # 2026-09-13 (user requirement): Every time the peak positioning method is changed, the
            # data will be dropped ui_state.
            peaks_row.localization_changed.connect(
                self._store_current_localization
            )
        # 0.2.199-patch29hz-Revision 4:SMILE The degree of optimisation is recorded according to
        # data ui_state.
        smile_row = self._rows.get("smile")
        if smile_row is not None:
            smile_row.grid_combo.currentIndexChanged.connect(
                self._store_smile_grid_size
            )
            smile_row.rank_combo.currentIndexChanged.connect(
                self._store_smile_rank_mode
            )
        steps_box.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # 0.2.199-patch29hz-Fix 2: The scrolling area does not use the background colour (otherwise
        # the central partition is a different colour from other partitions).
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.viewport().setAutoFillBackground(False)
        content = QWidget()
        content.setAutoFillBackground(False)
        content.setLayout(steps_box)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.refresh()

    # ------------------------------------------------------------------
    # Context.
    # ------------------------------------------------------------------
    def _data_facts(self, exp_id: str, data_id: str) -> dict:
        """Current data fact (ndim / direct dimension nuclide / whether NUS). 0.2.199-patch29hz:
        Unify ProcessingController.data_facts; test avatars, etc. If this interface is not
        provided, press "Unknown" to downgrade (processed the same as read failure, no error
        will be thrown)."""
        getter = getattr(self.controller, "data_facts", None)
        if getter is None:
            return {}
        try:
            return dict(getter(exp_id, data_id) or {})
        except Exception:  # noqa: BLE001 - Read failure is handled as unknown.
            return {}
    @property
    def current_data_id(self) -> str:
        """The currently selected sample data id (empty string if not selected)."""
        return str(self._current_data_id or "")

    def set_context(self, exp_id: str, data_id: str | None = None) -> None:
        """Compatible entry: Set context according to experiment type (data_id defaults to the
        first sample data)."""
        self._selection_kind = "experiment" if exp_id else ""
        self._current_exp_id = exp_id or ""
        if data_id is not None:
            self._current_data_id = data_id
        self.refresh()

    def set_selection(self, kind: str, exp_id: str, data_id: str = "") -> None:
        """Refresh by tree selection type: project/experiment displays "unselected data";
        data/folder displays steps."""
        self._selection_kind = kind or ""
        self._current_exp_id = exp_id or ""
        self._current_data_id = data_id
        self.refresh()

    def current_experiment_id(self) -> str:
        return self._current_exp_id

    def _sync_reference_display(self) -> None:
        """Synchronize the "Reference:..." line display according to the currently selected data
        (0.2.199-patch29fx: Reference is isolated by data, switching data does not remain to
        other data)."""
        row = self._rows.get("peaks")
        if row is None:
            return
        ref = (
            self._ref_info.get(
                (self._current_exp_id, self._current_data_id)
            )
            if self._current_data_id
            else None
        )
        if ref:
            row.set_ref_text(
                tr("refer to: {p0} ({p1} peak)", p0=ref['label'], p1=len(ref['peaks']))
            )
        else:
            row.clear_ref_display()

    def _store_current_threshold(self, value: float) -> None:
        """The threshold is recorded into the current data immediately and persisted
        (0.2.199-patch29fz/patch29ga/patch29gc); does not override user's explicit custom tags."""
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
        except Exception:  # noqa: BLE001 - Persistence failure does not block adjustment.
            pass

    def _mark_current_threshold_custom(self) -> None:
        """User explicitly edits the threshold (Input completed/Slider lets go) to set a custom
        tag."""
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
                self._peaks_ui_state(
                    threshold=self._threshold_by_data[key], custom=True
                ),
            )
        except Exception:  # noqa: BLE001 - Persistence failure does not block.
            pass

    def _peaks_ui_state(self, **extra: object) -> dict:
        """Peaks partition UI status (threshold + peak positioning method are written together to
        avoid overwriting each other)."""
        key = (self._current_exp_id, self._current_data_id)
        state: dict = {
            "threshold": float(self._threshold_by_data.get(key, 35.0)),
            "custom": bool(self._threshold_custom_by_data.get(key, False)),
            # When switching data, threshold_spin.setValue will send valueChanged first; here, the
            # cache/persistent value must be read according to the current data, and the old value
            # of the previous data in the control cannot be read.
            "localization_method": self._localization_for(*key),
        }
        state.update(extra)
        return state

    def _store_current_localization(self, *_args) -> None:
        """The peak positioning method writes ui_state as data (same partition as the threshold,
        merged before writing)."""
        row = self._rows.get("peaks")
        if row is None or not (self._current_exp_id and self._current_data_id):
            return
        method = row.get_localization_method()
        self._localization_by_data[
            (self._current_exp_id, self._current_data_id)
        ] = method
        try:
            from gui.per_data_records import update_ui_state

            update_ui_state(
                self.manager,
                self._current_exp_id,
                self._current_data_id,
                "peaks",
                self._peaks_ui_state(localization_method=method),
            )
        except Exception:  # noqa: BLE001 - Persistence failure does not block adjustment.
            pass

    def _localization_for(self, exp_id: str, data_id: str) -> str:
        """Peak positioning method for this data (session cache takes precedence, otherwise
        ui_state; default parabolic)."""
        key = (exp_id, data_id)
        if key not in self._localization_by_data:
            value = "parabolic"
            try:
                from core.peaks.localize import normalize_localization_method
                from gui.per_data_records import load_ui_state

                state = (
                    load_ui_state(self.manager, exp_id, data_id).get("peaks")
                    or {}
                )
                raw = str(state.get("localization_method", "") or "").strip()
                if raw:
                    value = normalize_localization_method(raw)
            except Exception:  # noqa: BLE001 - If the read fails, use the default.
                value = "parabolic"
            self._localization_by_data[key] = value
        return self._localization_by_data[key]

    def _threshold_for(self, exp_id: str, data_id: str) -> float:
        """The data threshold: session cache takes priority, otherwise reads
        d_xxx/ui_state.json(patch29ga); the old default 15/25σ (not explicitly customized) is
        migrated to 35σ(patch29gc/patch29hn)."""
        key = (exp_id, data_id)
        if key not in self._threshold_by_data:
            value = 35.0
            custom = False
            try:
                from gui.per_data_records import load_ui_state

                peaks_state = (
                    load_ui_state(self.manager, exp_id, data_id)
                    .get("peaks")
                    or {}
                )
                raw = peaks_state.get("threshold")
                custom = bool(peaks_state.get("custom", False))
                if raw is not None:
                    value = float(raw)
                # The old version default 15/25σ (not explicitly customized) will be migrated to the
                # new default 35σ.
                if not custom and (
                    abs(value - 15.0) < 1e-9 or abs(value - 25.0) < 1e-9
                ):
                    value = 35.0
            except Exception:  # noqa: BLE001 - If the read fails, use the default.
                pass
            self._threshold_by_data[key] = value
            self._threshold_custom_by_data[key] = custom
        return self._threshold_by_data[key]

    def _store_smile_grid_size(self, *_args) -> None:
        """Write the SMILE optimisation degree of the current data into ui_state (same as the peak
        threshold)."""
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
        except Exception:  # noqa: BLE001 - Persistence failure does not block.
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
        except Exception:  # noqa: BLE001 - Persistence failure does not block.
            pass

    def _sync_smile_grid_size(self) -> None:
        """Displays SMILE degree of optimisation based on current data (read ui_state; default
        5x5)."""
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
                (load_ui_state(self.manager, key[0], key[1]).get("smile") or {})
                .get("grid_size", 4)
            )
        except Exception:  # noqa: BLE001 - If the read fails, use the default.
            size = 5
        self._grid_key = key
        try:
            from gui.per_data_records import load_ui_state

            mode = str(
                (load_ui_state(self.manager, key[0], key[1]).get("smile") or {})
                .get("rank_mode", "true_peaks")
            )
            r_index = row.rank_combo.findData(mode)
            if r_index >= 0:
                row.rank_combo.setCurrentIndex(r_index)
        except Exception:  # noqa: BLE001 - If the read fails, use the default.
            pass
        index = row.grid_combo.findData(max(2, min(5, size)))
        if index >= 0:
            row.grid_combo.setCurrentIndex(index)

    def _current_statuses(self) -> dict[str, str]:
        """The step status of the currently selected sample data; Sample data not selected/Old
        order sample data rollback experiment type aggregation."""
        self._sync_smile_grid_size()
        if self._current_data_id:
            return compute_data_step_statuses(
                self.manager, self._current_exp_id, self._current_data_id
            )
        return compute_step_statuses(self.manager, self._current_exp_id)

    def _data_is_nus(self, exp_id: str, data_id: str) -> bool:
        """Whether the current data is detected as NUS (only NUS displays SMILE optimisation,
        0.2.199-patch29gd). uncertain/If the read fails, press "No" NUS processing -- "Like NUS
        but with actual full sampling" is also hidden."""
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
        (non-NUS is also hidden, 0.2.199-patch29gd)."""
        if not (exp_id and data_id):
            return False
        return self._data_is_nus(exp_id, data_id) and self._data_ndim(exp_id, data_id) == 2


    def _data_ndim(self, exp_id: str, data_id: str) -> int:
        """Current data dimension (failure to read will be treated as 2 and will not affect the
        2D/3D main process)."""
        if not (exp_id and data_id):
            return 2
        key = (exp_id, data_id)
        if key not in self._ndim_cache:
            facts = self._data_facts(exp_id, data_id)
            self._ndim_cache[key] = int(facts.get("ndim", 2) or 2)
        return self._ndim_cache[key]

    def _data_direct_nucleus(self, exp_id: str, data_id: str) -> str:
        """Current data direct dimension nuclide (returns empty string if read fails, uses loose
        file for range verification)."""
        if not (exp_id and data_id):
            return ""
        key = (exp_id, data_id)
        if key not in self._nucleus_cache:
            facts = self._data_facts(exp_id, data_id)
            self._nucleus_cache[key] = str(
                facts.get("direct_nucleus", "") or ""
            )
        return self._nucleus_cache[key]

    def _set_peaks_visible(self, visible: bool) -> None:
        """Display and hide peak selection step rows based on current data (1D does not require
        peak selection, patch29gj)."""
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
                "Please select the Data node on the left to view/run processing "
                "steps",
            )
            )
            for row in self._rows.values():
                row.set_status("LOCKED")
                row.manual_button.setVisible(False)  # Unselected data does not display labor.
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
        context_text = (
            f"{project.name} / {exp_title} ({self._current_exp_id})"
        )
        if group is not None:
            context_text += tr(" [Group {p0}: {p1} data]", p0=group.id, p1=len(group.data_ids))
        self.context_label.setText(context_text)
        self._sync_reference_display()
        # 0.2.199-patch29gd:SMILE optimisation. Only NUS is shown (not NUS/uncertain hidden); repair
        # 21(user): Only 2D NUS -- 3D NUS's SMILE optimisation is temporarily hidden.
        self._set_smile_visible(
            self._smile_supported(self._current_exp_id, self._current_data_id)
        )
        self._set_peaks_visible(
            self._data_ndim(self._current_exp_id, self._current_data_id) != 1
        )
        statuses = self._current_statuses()
        # 0.2.199-patch29fz(user): Thresholds are isolated by data -- Refresh to restore the current
        # data thresholds.
        peaks_row = self._rows.get("peaks")
        if peaks_row is not None:
            peaks_row.threshold_spin.setValue(
                self._threshold_for(
                    self._current_exp_id, self._current_data_id
                )
            )
            # 2026-09-13 (user request): Restoring peak positioning method according to data;
            # Gaussian is only available in 2D.
            peaks_row.set_localization_method(
                self._localization_for(
                    self._current_exp_id, self._current_data_id
                )
            )
            peaks_row.set_localization_supported(
                self._data_ndim(
                    self._current_exp_id, self._current_data_id
                )
                == 2
            )
        # Sample data layer does not prompt/Show import steps (Importing actions belonging to the
        # experiment type layer) 0.2.199-patch29as:SMILE optimisation is an optional step -- Not
        # done/No use after expiration "Next step", which displays "optional" separately; "Next
        # step" always points to the real required step.
        outdated_next = next(
            (
                sid
                for sid, st in statuses.items()
                if sid != "smile" and st == "OUTDATED"
            ),
            None,
        )
        next_step = next(
            (
                sid
                for sid, st in statuses.items()
                if sid != "smile" and st == "READY"
            ),
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
            text = tr("Next step: rerun {p0}", p0=STEP_LABEL[outdated_next])
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
                # 2026-09-25: the indirect-dimension flip control takes its shape from the data
                # dimensionality and is filled back from the final script's current state.
                self._sync_indirect_flip_control(status)
            # Importing sample data is an automated step, with no manual entry; the remaining
            # processing steps remain manual; 0.2.163-patch14: No manual button is provided when the
            # previous step is not completed (LOCKED) -- The next run entry is not given until the
            # previous step is completed (consistent with the automatic "Run" button)
            # 0.2.199-patch29dl(user): No manual script (automatic detection) for peak selection, no
            # manual button is displayed 0.2.199-patch29dm(user): Generate FID Must be processed
            # automatically (SUCCESS) before the manual button appears -- Manually read only the
            # generated fid.com and no longer trigger automatic conversion.
            if step_id == "fid":
                manual_visible = status == "SUCCESS"
            else:
                manual_visible = (
                    step_id not in ("smile", "peaks") and status != "LOCKED"
                )
            self._rows[step_id].manual_button.setVisible(manual_visible)
            # 0.2.88: After the spectrum generation is completed, the "Show spectrum" button appears
            # (the spectrum will no longer be automatically displayed).
            self._rows[step_id].show_spectrum_button.setVisible(
                step_id == "spectrum" and status == "SUCCESS"
            )
        self._update_ext_button()
        self._refresh_expanded_details()

    # ------------------------------------------------------------------
    # Final run direct dimension range(0.2.162-patch15).
    # ------------------------------------------------------------------
    def _on_ext_range_requested(self, step_id: str) -> None:
        """"Direct dimension range" button: Pop up the input dialog box and press the data to save
        the direct dimension range coverage."""
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
                p0=nucleus or 'unknown',
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
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        lo = lo_edit.text().strip()
        hi = hi_edit.text().strip()
        apply_opt = apply_check.isChecked()
        # 0.2.199-patch29eg/patch29hz: Input constraints are divided into direct dimension nuclides
        # (1H 0-20, 13C/15N/31P/19F respective ranges), the high field end must be greater than the
        # low field end.
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
                    "direct dimension range: {p0} set to {p1}-{p2} ppm "
                    "({p3})",
                    p0=data_id,
                    p1=lo or 'default',
                    p2=hi or 'default',
                    p3=scope,
                )
            )
        self._update_ext_button()

    def _update_ext_button(self) -> None:
        """Cover the update button copy and prompt word according to the direct dimension range of
        the current data."""
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
            row.set_ext_override(tr(
                "direct dimension range {p0}/{p1} · "
                "{p2}",
                p0=lo,
                p1=hi,
                p2=scope,
            ))
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
                    "set (10.5-6.5 ppm);\n\"Apply this range to the optimisation\" is on by "
                    "default and can be turned off to optimise over the default wide 6.5-10.5 "
                    "range",
                )
            )

    def _spectrum_ext_params(self, data_id: str) -> dict | None:
        """The direct dimension range of a certain data in the current experiment ->
        generate_spectrum params (None). apply_ext_to_opt: enabled by default, the range is also
        used for the optimisation process (first pass reconstruction / phase search and baseline
        / zero filling / window function evaluation); when closed, optimisation uses the default
        6.5-10.5 large range, and only the final run uses this range."""
        over = self._final_ext.get((self._current_exp_id, data_id))
        if not over:
            return None
        ext_params: dict[str, str] = {"apply_ext_to_opt": "1" if over[2] else "0"}
        if over[0]:
            ext_params["final_ext_lo"] = over[0]
        if over[1]:
            ext_params["final_ext_hi"] = over[1]
        return ext_params

    def _final_script_path(self, data_id: str) -> Path | None:
        """Locate an existing final-run script (uniform ``_process.com`` / NUS ``_nus.com``).

        Returns None when nothing is found (the caller prompts to optimise and generate one
        first). The final-run script names match how ``backend.nmrpipe_backend`` writes them.
        """
        if not (self._current_exp_id and data_id):
            return None
        work = self.manager.data_dir(self._current_exp_id, data_id, "process")
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
        """Sync the spectrum step's indirect-flip control (shape by dimensionality, state by the
        final script's current state)."""
        row = self._rows.get("spectrum")
        if row is None:
            return
        ndim = self._data_ndim(self._current_exp_id, self._current_data_id)
        row.set_indirect_dimension(ndim)
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
    # Run.
    # ------------------------------------------------------------------
    def run_step(self, step_id: str, data_id: str | None = None) -> None:
        """Run the specified step (data_id specified scope; Defaults to currently selected/first
        data)."""
        if step_id not in self._rows:
            return
        if data_id is not None:
            self._current_data_id = data_id
        # 0.2.199-patch29fz: This data threshold is also restored when switching data
        # programmatically.
        if step_id == "peaks":
            peaks_row = self._rows.get("peaks")
            if peaks_row is not None:
                peaks_row.threshold_spin.setValue(
                    self._threshold_for(
                        self._current_exp_id, self._current_data_id
                    )
                )
        row = self._rows[step_id]
        if not row.run_button.isHidden():
            self._on_run_requested(step_id)

    def show_first_import_hint(self) -> None:
        """Next step prompt after first import: Highlight the next runnable step + bubble and
        disappear after 8 seconds."""
        statuses = self._current_statuses()
        # 0.2.199-patch29as: optional SMILE optimisation does not occupy the "next step" bubble.
        next_step = next(
            (
                sid
                for sid, st in statuses.items()
                if sid != "smile" and st in ("READY", "OUTDATED")
            ),
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
                "Sample data has been imported: the next step can be run "
                "\"{p0}\"",
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
    def _run_log_scope(
        exp_id: str, data_id: str, group_id: str = ""
    ) -> str:
        """Run log scope key: a group log is shared within the group, and each individual data is
        independent."""
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
        the SyncThread test, emit is a direct connection, and the behaviour remains unchanged."""
        self._run_active = False
        self.refresh()

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

    def _cached_spectrum_report(
        self, params: dict, spectrum_path: str
    ) -> str:
        """Generate spectrum parameter report (0.2.199-patch12): According to spectrum file
        fingerprint cache (memory + disk record). spectrum_quality_report_lines will read the
        entire ft3 and evaluate the quality of the full spectrum. Each refresh will be executed
        in the main thread and it will be very stuck; when the spectrum file has not changed,
        the record will be read directly without re-reading the spectrum. Record file:
        {spectrum_path}.quality.json, fingerprint = mtime_ns+size+ parameter."""
        params_fp = hashlib.sha256(
            json.dumps(params, sort_keys=True, ensure_ascii=False).encode(
                "utf-8"
            )
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
                # 2026-09-23: the authoritative fingerprint is the spectrum file itself
                # (mtime_ns+size). The report was computed for this spectrum; parameter identity
                # only decides which run's text the record holds. Requiring params_fp to match
                # byte for byte would misjudge "spectrum unchanged, parameter object rendered
                # slightly differently" as no record, and what the user sees is the "Generate
                # Spectrum" step showing "no report record" forever.
                if data.get("fp") == fp and isinstance(data.get("text"), str):
                    self._spectrum_report_cache[key] = data["text"]
                    return data["text"]
            except (OSError, ValueError):
                pass
        # 0.2.199-patch29e: The report only displays the content recorded during the last generation
        # (the working thread writes {spectrum}.quality.json); no records are generated on-site
        # (reading the entire spectrum is neither lag nor real processing report), prompting to re-
        # run "Generate Spectrum".
        return tr("(No report record; generate a report after re-running \"Generate Spectrum\")")

    def _step_detail(self, step_id: str) -> tuple[str, dict | None, bool]:
        """Build step details (enter/product/Recently run/ parameter / script snapshot); return
        (text, params, failed)."""
        exp_id = self._current_exp_id
        data_id = self._current_data_id
        if not (exp_id and data_id):
            return tr("No data selected"), None, False
        lines: list[str] = []
        params: dict | None = None
        failed = False
        try:
            artifacts = _node_artifacts(self.manager, exp_id, data_id)
            artifact = artifacts.get(step_id)
            if artifact is not None:
                lines.append(tr("product: {p0}", p0=artifact))
        except Exception:  # noqa: BLE001 - Product parsing failure ignores.
            pass
        run = _last_run_for(self.manager, exp_id, data_id, _step_refs(step_id))
        if run is None:
            lines.append(tr("run records: none"))
        else:
            failed = run.status == "failed"
            lines.append(tr(
                "run: {p0} [{p1}] "
                "{p2}",
                p0=run.run_id,
                p1=run.status,
                p2=run.workflow_ref,
            ))
            if run.message:
                lines.append(tr("information: {p0}", p0=run.message))
            if step_id == "fid":
                # 2026-09-23 (user request): whatever the step log ended with is shown here
                # (the data quality diagnosis belongs to this step; the spectrum step report
                # no longer carries that section)
                lines.append(tr("data quality report (from the Generate-FID step):"))
                lines += _fid_step_report_lines(self.manager, exp_id, data_id)
            elif step_id == "spectrum":
                # 0.2.155: Simplification -- only display readable parameter reports when generating
                # spectrum, and no longer dump internal details such as original parameter/script
                # snapshots.
                if run.params:
                    params = dict(run.params)
                    lines.append(
                        tr(
                        "parameter report (generated spectrum actual effective "
                        "parameters):",
                    )
                    )
                    lines.append(
                        self._cached_spectrum_report(
                            run.params,
                            str((run.outputs or {}).get("spectrum_path") or ""),
                        )
                    )
            else:
                if run.outputs:
                    outs = " | ".join(f"{k}={v}" for k, v in run.outputs.items())
                    lines.append(tr("Output: {p0}", p0=outs))
                if run.snapshot_dir:
                    lines.append(tr("Snapshot directory: {p0}", p0=run.snapshot_dir))
                if run.scripts:
                    lines.append(tr("script snapshot: {p0}", p0=','.join(run.scripts)))
                if run.params:
                    lines.append(tr("parameter: {p0}", p0=_format_params(run.params)))
        return "\n".join(lines) if lines else tr("No details"), params, failed

    def _refresh_expanded_details(self) -> None:
        """0.2.161: Expanded step details (parameter report, etc.) With data/Context switch
        refreshes immediately."""
        for step_id, row in self._rows.items():
            if not row.detail_frame.isHidden():
                text, _params, failed = self._step_detail(step_id)
                row.set_detail(text, failed=failed)

    # ------------------------------------------------------------------
    # Reference spectrum constraints (0.2.199-patch29dl, user).
    # ------------------------------------------------------------------
    def _reference_candidates(self) -> list[tuple[str, str, str]]:
        """There is already a data list of peak tables in the project (display name, exp_id,
        data_id); exclude the current data itself (2026-09-04 user)."""
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
                # The reference spectrum cannot be itself (same as exp + data exclusion).
                if str(exp.id) == cur_exp and data_id == cur_data:
                    continue
                try:
                    peaks_dir = self.manager.data_dir(exp.id, data_id, "peaks")
                except Exception:  # noqa: BLE001 - Single data exception skip.
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
        agnostic return None."""
        symbols = {
            "H": "1H", "N": "15N", "C": "13C",
            "F": "19F", "P": "31P", "D": "2H",
        }
        full = {"1H", "2H", "13C", "15N", "19F", "31P", "23Na", "29Si"}
        try:
            if spectrum_path.suffix.lower() == ".ft3":
                from viewer.spectrum import Spectrum3D

                spec = Spectrum3D.load_from_ft3(spectrum_path, lazy=True)
            else:
                from viewer.spectrum import Spectrum

                spec = Spectrum.load_from_ft2(spectrum_path)
        except Exception:  # noqa: BLE001 - Fallback None if spectrum reading fails.
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
        # 2D row key name inference kernel when no spectrum is available (HSQC refer to common
        # scenarios).
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
        peaks cannot be found in the reference)."""
        if self.manager is None or self.manager.project is None:
            return
        candidates = self._reference_candidates()
        if not candidates:
            from gui.dialogs import InfoDialog

            InfoDialog.show_info(
                self, tr(
                    "Reference "
                    "spectrum",
                ), (
                    tr(
                    "There is no data with peak table in the project. Please peak pick some data "
                    "first",
                )
                )
            )
            return
        row = self._rows.get(step_id)
        anchor = row.ref_button if row is not None else self
        menu = QMenu(anchor)
        # Sort by data number (exp id, data id natural order is d_001 < d_002).
        for name, ref_exp, ref_data in sorted(
            candidates, key=lambda item: (item[1], item[2])
        ):
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
                self, tr(
                    "Reference "
                    "spectrum",
                ), (
                    tr(
                    "Unable to load reference peak table: "
                    "{p0}/{p1}",
                    p0=ref_exp,
                    p1=ref_data,
                )
                )
            )
            return
        if info.get("nuclei") is None and any(
            "F1_shift" in p for p in info["peaks"]
        ):
            from gui.dialogs import InfoDialog

            InfoDialog.show_info(
                self,
                tr("Reference spectrum"),
                (
                    tr(
                    "The reference peak table is 3D and the nucleus name cannot be determined (no "
                    "reference spectrum is loaded), the constraint will not take "
                    "effect",
                )
                ),
            )
            return
        self._ref_info[
            (self._current_exp_id, self._current_data_id)
        ] = info
        self._rows["peaks"].set_ref_text(
            tr("refer to: {p0} ({p1} peak)", p0=info['label'], p1=len(info['peaks']))
        )

    def _on_clear_reference(self, step_id: str) -> None:
        """Clear reference spectrum constraints."""
        self._ref_info.pop(
            (self._current_exp_id, self._current_data_id), None
        )
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
        # 0.2.199-patch29gd/Revision 21: Only 2D NUS provides SMILE optimisation (defense
        # programmatic entry).
        if step_id == "smile" and not self._smile_supported(
            self._current_exp_id, self._current_data_id
        ):
            self.log_message.emit(
                tr("SMILE optimisation only works with 2D NUS data (not currently, step is hidden)")
            )
            return
        if self._run_active:
            self.log_message.emit(
                tr(
                    "There is already a task running, please wait until it is completed and try "
                    "again",
                )
            )
            return
        method_name = STEP_METHOD.get(step_id)
        method = getattr(self.controller, method_name, None) if method_name else None
        if method is None:
            self.log_message.emit(
                    tr(
                    "{p0}: The backend interface needs to be implemented and cannot be run "
                    "yet",
                    p0=STEP_LABEL.get(step_id, step_id),
                )
            )
            return
        # 0.2.163-patch14: Refuse to run when the pre-steps are not completed (LOCKED) (peak
        # picking/Analysis etc. All subsequent steps are consistent; the button is hidden, here is
        # the defense check of the programmatic entrance).
        if self._current_data_id:
            try:
                statuses = compute_data_step_statuses(
                    self.manager, self._current_exp_id, self._current_data_id
                )
                if statuses.get(step_id) == "LOCKED":
                    reasons = _lock_reasons(statuses)
                    self.log_message.emit(
                        tr(
                            "{p0}: prerequisite steps are not complete,please finish "
                            "{p1}",
                            p0=STEP_LABEL.get(step_id, step_id),
                            p1=reasons.get(step_id, 'Previous step'),
                        )
                    )
                    return
            # Failure in status determination does not block the original process.
            except Exception:  # noqa: BLE001 -
                pass
        self._rows[step_id].set_status("RUNNING")

        def worker() -> None:
            try:
                nodes = _data_nodes(self.manager, self._current_exp_id)
                if not nodes:
                    self.log_scoped.emit(
                        tr(
                            "{p0}: this experiment type has no sample data yet, please import "
                            "sample data "
                            "first",
                            p0=STEP_LABEL.get(step_id, step_id),
                        ),
                        self._run_log_scope(self._current_exp_id, ""),
                    )
                    return
                data_node = next(
                    (n for n in nodes if getattr(n, "id", "") == self._current_data_id),
                    nodes[0],
                )
                exp_id = self._current_exp_id
                target_data_id = getattr(data_node, "id", exp_id)
                # 0.2.199-patch5: Processing starts, the data in the tree on the left shows
                # "Running".
                self.run_started.emit(exp_id, target_data_id)
                # 0.2.199-patch29gv: Single data in the group still runs independently in the panel
                # (not automatically converted to the entire group); the entire group of batch
                # processing is triggered by "Sequential optimisation/processing by reference" on
                # the data group page (GroupBatchPanel).
                run_scope = self._run_log_scope(exp_id, target_data_id, "")
                self.log_scoped.emit(
                    tr("start {p0}: {p1}", p0=STEP_LABEL.get(step_id, step_id), p1=entry.id),
                    run_scope,
                )
                step_label = STEP_LABEL.get(step_id, step_id)
                try:
                    import inspect

                    kwargs: dict = {"exp_id": exp_id, "data_id": target_data_id}
                    if step_id == "spectrum":
                        ext_params = self._spectrum_ext_params(target_data_id)
                        if ext_params and "params" in inspect.signature(method).parameters:
                            kwargs["params"] = ext_params
                    if step_id == "peaks":
                        kwargs["sigma_multiplier"] = self._rows[
                            step_id
                        ].get_threshold()
                        # 2026-09-13 (user requirement): Peak positioning method (GUI only passes
                        # the method name, the fitting mathematics is in core.peaks.localize).
                        if (
                            "localization_method"
                            in inspect.signature(method).parameters
                        ):
                            kwargs["localization_method"] = self._rows[
                                step_id
                            ].get_localization_method()
                        ref = self._ref_info.get(
                            (exp_id, target_data_id)
                        )
                        if ref:
                            kwargs["ref_peaks"] = ref["peaks"]
                            kwargs["ref_nuclei"] = ref.get("nuclei")
                            kwargs["ref_name"] = str(
                                ref.get("label", "")
                            ).split(" (")[0]
                            # 0.2.199-patch29fx: Tolerance can be changed in software settings (same
                            # place as line width).
                            from gui.settings import load_settings

                            kwargs["tolerance_ppm"] = (
                                load_settings().get(
                                    "alignment_tolerance_ppm"
                                )
                                or None
                            )
                    if "progress" in inspect.signature(method).parameters:
                        # 0.2.199-patch29ec: The progress message does not have the step name prefix
                        # (start/Finish/fail mark is reserved, and the specific progress is
                        # expressed by the backend message itself).
                        kwargs["progress"] = lambda msg: self.log_scoped.emit(
                            msg, run_scope
                        )
                    result = method(data_node, **kwargs)
                    if (
                        step_id == "peaks"
                        and isinstance(result, dict)
                        and result.get("status") == "success"
                    ):
                        # 0.2.199-patch29ar: After the peak selection is completed, the spectrum
                        # will be displayed immediately and the peaks will be displayed.
                        self.show_spectrum_requested.emit(step_id)
                    message = result if isinstance(result, str) else str(result)
                    self.log_scoped.emit(tr(
                        "Finish {p0}: "
                        "{p1}",
                        p0=step_label,
                        p1=message,
                    ), run_scope)
                except Exception as exc:  # noqa: BLE001 - Single data failure.
                    self.log_scoped.emit(
                        tr("fail {p0}: {p1}", p0=step_label, p1=describe_exception(exc)),
                        run_scope,
                    )
                    if _looks_like_memory_guard(exc):
                        self.memory_guard_requested.emit(str(exc))
            except Exception as exc:  # noqa: BLE001 - Unified error return UI.
                self.log_scoped.emit(
                    tr("fail {p0}: {p1}", p0=STEP_LABEL.get(step_id, step_id), p1=exc),
                    run_scope,
                )
                if _looks_like_memory_guard(exc):
                    self.memory_guard_requested.emit(str(exc))
            finally:
                self._run_active = False
                self.run_finished.emit()

        import threading

        # 0.2.199-patch6: Clear the last cancellation flag before starting a new task.
        from backend.runtime import clear_cancel

        clear_cancel()
        self._run_active = True
        threading.Thread(target=worker, daemon=True).start()

    def _on_rerun_final_requested(
        self, step_id: str, sampling: dict | None = None
    ) -> None:
        """"Rerun the final script": change the direct dimension range / the indirect flip
        directly on the existing final script and run it.

        The final script (uniform {data_id}_process.com / NUS {data_id}_nus.com) already
        contains every optimised parameter (phase/window/baseline and so on); after the user
        has changed the direct dimension range or picked an indirect flip, only the matching
        lines (EXT's -x1/-xn; the indirect-dimension FT line's -neg) are changed before
        running -- no other parameter is touched, and the spectrum does not change by being
        re-rendered.

        Sampling overrides (user 2026-09-25): ``ft_neg_f1``/``ft_neg_f2`` inside ``sampling``
        are the **target states** (True = that indirect dimension should carry ``-neg``,
        False = it should not; they decide **directly**, they are not an inversion), supplied
        by the spectrum step's flip control (``flip_f1``/``flip_f2`` are legacy aliases and
        are accepted too); they only affect the indirect-dimension FT lines. For 3D NUS the
        indirect-dimension input (``nus3d_rc``) is kept by the pipeline cleanup, so a flip
        re-run only runs the indirect-dimension section and does not re-run SMILE and the
        direct dimension; 2D (including 2D NUS) and 3D uniform have no such intermediate, so
        the whole script is re-run.
        """
        if step_id != "spectrum":
            return
        if self._run_active:
            self.log_message.emit(
                tr(
                    "There is already a task running, please wait until it is completed and try "
                    "again",
                )
            )
            return
        exp_id = self._current_exp_id
        if not exp_id:
            return
        flips: dict[str, bool] = {}
        for axis, keys in (
            ("F2", ("ft_neg_f2", "flip_f2")),
            ("F1", ("ft_neg_f1", "flip_f1")),
        ):
            for key in keys:  # official names first; flip_f* are legacy aliases
                if sampling and sampling.get(key) is not None:
                    flips[axis] = bool(sampling[key])
                    break
        self._rows[step_id].set_status("RUNNING")
        self.log_message.emit(tr(
            "Start re-running the final script (only the direct dimension range / the indirect "
            "dimension flip is "
            "updated, the other parameters remain "
            "unchanged)",
        ))

        def worker() -> None:
            try:
                nodes = _data_nodes(self.manager, exp_id)
                if not nodes:
                    self.log_scoped.emit(
                        tr(
                            "The experiment type does not yet have sample "
                            "data",
                        ), self._run_log_scope(exp_id, "")
                    )
                    return
                data_node = next(
                    (n for n in nodes if getattr(n, "id", "") == self._current_data_id),
                    nodes[0],
                )
                data_id = getattr(data_node, "id", exp_id)
                run_scope = self._run_log_scope(exp_id, data_id)
                # 0.2.199-patch5: Processing starts, the data in the tree on the left shows
                # "Running".
                self.run_started.emit(exp_id, data_id)
                # Locate the existing final script (uniform/NUS). If it cannot be found, it will
                # prompt optimisation to generate it first.
                work = self.manager.data_dir(exp_id, data_id, "process")
                script_path = self._final_script_path(data_id)
                if script_path is None:
                    self.log_scoped.emit(
                        tr(
                            "There is no reusable final script, please execute \"re-optimisation\" "
                            "to generate it "
                            "first",
                        ),
                        run_scope,
                    )
                    return
                ndim = self._data_ndim(exp_id, data_id)
                content = script_path.read_text(encoding="utf-8", errors="replace")
                # 3D NUS: the indirect-dimension input (nus3d_rc) is retained, so a flip re-run
                # only runs the indirect-dimension section.
                indirect_section = (
                    _indirect_only_section(content)
                    if (flips and ndim >= 3)
                    else None
                )
                if indirect_section is not None:
                    planes_dir = work / "nus3d_rc"
                    if not (planes_dir.is_dir() and any(planes_dir.glob("*.ft1"))):
                        indirect_section = None
                # Apply the direct dimension range of user's latest setting: replace -x1/-xn of
                # the EXT line (when only the indirect section runs, EXT is left alone: the
                # direct dimension is already baked into the retained reconstruction planes).
                ext = self._spectrum_ext_params(data_id) or {}
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
                            "direct dimension range updated: {p0}-{p1} ppm → "
                            "{p2}",
                            p0=lo or 'default',
                            p1=hi or 'default',
                            p2=script_path.name,
                        ),
                        run_scope,
                    )
                elif ext and indirect_section is not None:
                    self.log_scoped.emit(
                        tr(
                            "The direct dimension range was not applied: this re-run reuses the "
                            "retained reconstruction planes — execute \"re-optimisation\" to apply "
                            "it",
                        ),
                        run_scope,
                    )
                # Indirect-dimension flip: only change the matching indirect FT line to
                # carry/not carry -neg.
                if flips:
                    content, applied = flip_indirect_ft_lines(
                        content, ndim=ndim, flips=flips
                    )
                    if applied:
                        script_path.write_text(
                            content, encoding="utf-8", newline="\n"
                        )
                        added = sorted(
                            axis for axis, neg in applied.items() if neg
                        )
                        removed = sorted(
                            axis for axis, neg in applied.items() if not neg
                        )
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
                                "script; the flip was not applied — execute \"re-optimisation\" to "
                                "regenerate the script "
                                "first",
                            ),
                            run_scope,
                        )
                # The script to run: for a 3D NUS flip only the indirect-dimension section runs
                # (under a new script name, keeping the full final script).
                if indirect_section is not None:
                    run_key = f"{data_id}_nus_indirect.com"
                    # Cut from the **already flipped** script, otherwise the run would use the
                    # copy without -neg.
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
                # 0.2.199-patch29h: Detect common errors before running after modification (in
                # worker, log prompt).
                from workflow.script_check import check_script

                for w in check_script(content, run_key):
                    self.log_scoped.emit(tr("⚠ script check: {p0}", p0=w), run_scope)
                # Run the modified final script and return the spectrum to its original position;
                # forward the script output in real time.
                result = self.controller.run_manual_spectrum(
                    data_node,
                    {run_key: content},
                    exp_id=exp_id,
                    data_id=data_id,
                    progress=lambda line: self.log_scoped.emit(
                        f"[{run_key}] {line}", run_scope
                    ),
                )
                self.log_scoped.emit(
                    tr(
                        "Re-run the final script to complete {p0}: "
                        "{p1}",
                        p0=data_id,
                        p1=result,
                    ), run_scope
                )
            except Exception as exc:  # noqa: BLE001 - Unified error return UI.
                self.log_scoped.emit(
                    tr("Re-run the final script failed: {p0}", p0=describe_exception(exc)),
                    run_scope,
                )
            finally:
                self._run_active = False
                self.run_finished.emit()

        import threading

        self._run_active = True
        threading.Thread(target=worker, daemon=True).start()
