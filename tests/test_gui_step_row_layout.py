"""Step row layout: Leave space between items + SMILE Leave space between two groups
(0.2.199-patch29hz-Revision 19). user 2026-09-11: "The optimisation degree and sorting are too
close, and there is no gap between them" -- Root cause: Custom flow layout `_FlowLayout`
declares `setSpacing(6)` but only uses it when **line wrapping**, and the items are close to
each other (0 px); there is no extra space between the two groups."""

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
    """Put the step rows into the host layout (otherwise the row width will not follow the host,
    and the measured values will be in line breaks)."""
    lay = host.layout() or QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    row = PipelineStepRow(step, step, "illustrate", host)
    lay.addWidget(row)
    return row


def test_flow_layout_applies_item_spacing(qapp: QApplication, host: QWidget) -> None:
    """There should be spacing between items in fluid layout (spacing is not used only for line
    breaks)."""
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
    """Leave a clear gap between the "optimisation degree" group and the "sorting" group; keep the
    groups compact."""
    row = _hosted_row(host, "smile")
    host.resize(420, 240)
    host.show()
    qapp.processEvents()

    inner = row.grid_combo.x() - (row.grid_label.x() + row.grid_label.width())
    same_line = row.rank_label.y() == row.grid_label.y()
    if same_line:
        between = row.rank_label.x() - (
            row.grid_combo.x() + row.grid_combo.width()
        )
    # When the viewport is very narrow, the line will wrap -- At this time, look at the vertical
    # spacing between lines.
    else:
        between = row.rank_label.y() - (
            row.grid_label.y() + row.grid_label.height()
        )

    assert inner >= 4, inner
    assert between >= 12, (inner, between, same_line)
    assert between > inner, (inner, between)


def test_smile_gap_not_visible_for_other_steps(
    qapp: QApplication, host: QWidget
) -> None:
    """The interval control only takes up space in the SMILE row, and other step rows are not
    affected."""
    for step in ("fid", "spectrum", "peaks"):
        row = PipelineStepRow(step, step, "x", host)
        assert row.smile_gap.isHidden() is True, step

    smile = PipelineStepRow("smile", "SMILE", "x", host)
    assert smile.smile_gap.isHidden() is False

# ---------------------------------------------------------------------------
# Geometry Guard (0.2.199-patch29hz - fix 20): all step rows x multiple widths.
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
    """Return to the list of geometric problems: Overlapping / Insufficient line spacing /
    Exceeding the visual width / Squeezed into narrow strips."""
    problems: list[str] = []
    kids = _visible_children(row)
    for child in kids:
        if child.x() < 0 or child.x() + child.width() > row.width() + 1:
            problems.append(
                f"{type(child).__name__} Exceeds line width x={child.x()} w={child.width()} "
                f"row_w={row.width()}"
            )
    for i, left in enumerate(kids):
        for right in kids[i + 1 :]:
            lrect = left.geometry()
            rrect = right.geometry()
            if lrect.intersects(rrect):
                problems.append(
                    f"{type(left).__name__}{lrect} and {type(right).__name__}{rrect} overlapping"
                )
                continue
            same_line = abs(left.y() - right.y()) <= 2
            if not same_line:
                continue
            a, b = sorted((lrect, rrect), key=lambda r: r.x())
            gap = b.x() - (a.x() + a.width())
            if 0 <= gap < min_gap:
                problems.append(
                    f"peer spacing {gap}px < {min_gap}px:"
                    f"{type(left).__name__} and {type(right).__name__}"
                )
    return problems


@pytest.mark.parametrize("width", _WIDTHS)
def test_step_rows_have_no_geometry_problems(
    qapp: QApplication, host: QWidget, width: int
) -> None:
    """All step rows are under common width: controls do not overlap, row spacing >= 4px, and do
    not exceed the line width."""
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
