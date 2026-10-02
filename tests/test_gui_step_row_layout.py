"""Step-row layout: spacing between items + a gap between the two SMILE groups
(0.2.199-patch29hz-fix19).

User 2026-09-11: "the optimisation degree and rank are too close together, and there is no gap
between them" -- root cause: the custom flow layout `_FlowLayout` declares `setSpacing(6)` but only
uses it when **wrapping**, so items sit flush against each other (0 px) and the two groups have no
extra gap.
"""

from __future__ import annotations

import pytest
from qtcompat.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from gui.pipeline_panel import PipelineStepRow, _FlowLayout
from ui_support.theme import apply_dark_theme


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    apply_dark_theme(app)
    yield app


@pytest.fixture
def host(qapp: QApplication):
    widget = QWidget()
    yield widget
    widget.deleteLater()
    QApplication.processEvents()


def _hosted_row(host: QWidget, step: str) -> PipelineStepRow:
    """Place the step row into the host layout (otherwise the row width does not follow the host and
    every measurement is taken in the wrapped state)."""
    lay = host.layout() or QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    row = PipelineStepRow(step, step, "说明", host)
    lay.addWidget(row)
    return row


def test_flow_layout_applies_item_spacing(qapp: QApplication, host: QWidget) -> None:
    """The flow layout must put spacing between items (spacing is not only used for wrapping to a
    new line)."""
    flow = _FlowLayout(host)
    buttons = [QPushButton(f"b{i}", host) for i in range(3)]
    for button in buttons:
        flow.addWidget(button)
    host.resize(400, 100)
    host.show()
    qapp.processEvents()

    for left, right in zip(buttons, buttons[1:]):
        gap = right.x() - (left.x() + left.width())
        assert gap >= 4, (left.text(), right.text(), gap)


def test_smile_groups_are_separated(qapp: QApplication, host: QWidget) -> None:
    """Leave a clear gap between the "optimisation degree" group and the "rank" group, while keeping
    each group compact."""
    row = _hosted_row(host, "smile")
    host.resize(420, 240)
    host.show()
    qapp.processEvents()

    inner = row.grid_combo.x() - (row.grid_label.x() + row.grid_label.width())
    same_line = row.rank_label.y() == row.grid_label.y()
    if same_line:
        between = row.rank_label.x() - (row.grid_combo.x() + row.grid_combo.width())
    else:  # a very narrow viewport wraps the line -- then look at the vertical gap between rows
        between = row.rank_label.y() - (row.grid_label.y() + row.grid_label.height())

    assert inner >= 4, inner
    assert between >= 12, (inner, between, same_line)
    assert between > inner, (inner, between)


def test_smile_gap_not_visible_for_other_steps(qapp: QApplication, host: QWidget) -> None:
    """The gap widget only takes up space in the SMILE row; other step rows are unaffected."""
    for step in ("fid", "spectrum", "peaks"):
        row = PipelineStepRow(step, step, "x", host)
        assert row.smile_gap.isHidden() is True, step

    smile = PipelineStepRow("smile", "SMILE", "x", host)
    assert smile.smile_gap.isHidden() is False


# ---------------------------------------------------------------------------
# Geometry guard (0.2.199-patch29hz-fix20): every step row x multiple widths
# ---------------------------------------------------------------------------

_STEPS = ("project", "fid", "spectrum", "smile", "peaks")
_WIDTHS = (320, 380, 440, 560, 760)


def _visible_children(row: PipelineStepRow) -> list:
    return [
        child
        for child in row.findChildren(QWidget)
        if child.parent() is row and child.isVisible() and child.width() > 0
    ]


def _audit_row(row: PipelineStepRow, *, min_gap: int = 4) -> list[str]:
    """Return the list of geometry problems: overlap / insufficient spacing on a line / wider than
    the visible width / squeezed into a narrow strip."""
    problems: list[str] = []
    kids = _visible_children(row)
    for child in kids:
        if child.x() < 0 or child.x() + child.width() > row.width() + 1:
            problems.append(
                f"{type(child).__name__} 超出行宽 x={child.x()} w={child.width()} "
                f"row_w={row.width()}"
            )
    for i, left in enumerate(kids):
        for right in kids[i + 1 :]:
            lrect = left.geometry()
            rrect = right.geometry()
            if lrect.intersects(rrect):
                problems.append(
                    f"{type(left).__name__}{lrect} 与 {type(right).__name__}{rrect} 重叠"
                )
                continue
            same_line = abs(left.y() - right.y()) <= 2
            if not same_line:
                continue
            a, b = sorted((lrect, rrect), key=lambda r: r.x())
            gap = b.x() - (a.x() + a.width())
            if 0 <= gap < min_gap:
                problems.append(
                    f"同行间距 {gap}px < {min_gap}px:"
                    f"{type(left).__name__} 与 {type(right).__name__}"
                )
    return problems


@pytest.mark.parametrize("width", _WIDTHS)
def test_step_rows_have_no_geometry_problems(qapp: QApplication, host: QWidget, width: int) -> None:
    """Every step row at common widths: widgets do not overlap, in-line spacing >= 4px, and nothing
    exceeds the row width."""
    problems: list[str] = []
    for step in _STEPS:
        row = _hosted_row(host, step)
        host.resize(width, 420)
        host.show()
        qapp.processEvents()
        problems += [f"[{step}/{width}px] {p}" for p in _audit_row(row)]
        host.layout().removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        qapp.processEvents()
    assert not problems, problems


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------


def test_peaks_row_keeps_run_and_reference_on_one_line(qapp: QApplication, host: QWidget) -> None:
    "Regression coverage: test peaks row keeps run and reference on one line."
    row = _hosted_row(host, "peaks")
    row.set_status("SUCCESS")
    host.resize(562, 320)
    host.show()
    qapp.processEvents()

    assert not row.run_button.isHidden()
    assert not row.ref_button.isHidden()
    assert row.ref_button.y() == row.run_button.y(), (
        row.ref_button.geometry(),
        row.run_button.geometry(),
    )

    assert row.threshold_slider.y() < row.ref_button.y()

    for width in (420, 320, 240):
        host.resize(width, 320)
        qapp.processEvents()
        assert row.ref_button.y() == row.run_button.y(), width

    assert not _audit_row(row), _audit_row(row)


def test_flow_layout_break_starts_a_new_line(qapp: QApplication, host: QWidget) -> None:
    "Regression coverage: test flow layout break starts a new line."
    flow = _FlowLayout(host)
    first = QPushButton("first", host)
    second = QPushButton("second", host)
    flow.addWidget(first)
    flow.add_break()
    flow.addWidget(second)
    host.resize(600, 120)
    host.show()
    qapp.processEvents()

    assert flow.count() == 2
    assert flow.itemAt(0).widget() is first
    assert flow.itemAt(1).widget() is second
    assert second.y() > first.y()
    assert second.x() == first.x()
