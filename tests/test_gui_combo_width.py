"""Combo box text slack guard: it must not be squeezed into an ellipsis (0.2.199-patch29hz-fix14).

User 2026-09-11: "the optimisation-level and ranking combo boxes show lots of ellipses".
Root cause: under the system/QSS style the QComboBox sizeHint excludes the drop-down arrow
and the padding, leaving only 2~3 px of slack in the text area (measured: "pure peaks
first" 60 px of text / 62 px text area), so a slight font-rendering difference makes Qt
elide it. Fix: gui/theme.fit_combo_width sets a minimum width from "longest item text +
arrow + slack"; this test holds "text area >= text width + 8 px".
"""

from __future__ import annotations

import pytest
from qtcompat.QtWidgets import QApplication, QComboBox, QStyle, QStyleOptionComboBox, QWidget

from gui.group_panel import GroupBatchPanel
from gui.pipeline_panel import PipelineStepRow
from ui_support.theme import apply_dark_theme

MIN_SLACK_PX = 8


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


def _text_slack(combo: QComboBox) -> int:
    """Text area width minus the longest item text width (negative means it gets elided)."""
    metrics = combo.fontMetrics()
    option = QStyleOptionComboBox()
    option.initFrom(combo)
    option.currentText = combo.currentText()
    field = combo.style().subControlRect(
        QStyle.ComplexControl.CC_ComboBox,
        option,
        QStyle.SubControl.SC_ComboBoxEditField,
        combo,
    )
    longest = max(
        (combo.itemText(i) for i in range(combo.count())),
        key=lambda t: metrics.horizontalAdvance(t),
        default="",
    )
    return field.width() - metrics.horizontalAdvance(longest)


def test_smile_row_combo_text_not_elided(host: QWidget) -> None:
    """The SMILE row's "optimisation level / ranking" combo boxes keep enough text slack."""
    row = PipelineStepRow("smile", "SMILE 优化", "可选", host)
    host.resize(360, 220)
    host.show()
    QApplication.processEvents()

    for combo in (row.grid_combo, row.rank_combo):
        assert combo.count() > 0
        assert _text_slack(combo) >= MIN_SLACK_PX, (
            combo.currentText(),
            combo.width(),
            combo.minimumWidth(),
        )


def test_group_panel_combo_text_not_elided(host: QWidget) -> None:
    """The data group panel's "process to / optimise through" combos show no ellipsis either."""
    panel = GroupBatchPanel(host)
    host.resize(360, 520)
    host.show()
    panel.show()
    QApplication.processEvents()

    for attr in ("stop_combo", "opt_stop_combo"):
        combo = getattr(panel, attr)
        assert combo.count() > 0
        assert _text_slack(combo) >= MIN_SLACK_PX, (attr, combo.width())


def test_fit_combo_width_sets_minimum(qapp: QApplication, host: QWidget) -> None:
    """fit_combo_width gives an explicit minimum width >= the text width for empty, single
    and long items."""
    from ui_support.theme import fit_combo_width

    combo = QComboBox(host)
    fit_combo_width(combo)
    assert combo.minimumWidth() > 0
    combo.addItems(["一致性优先", "净真峰优先"])
    fit_combo_width(combo)
    metrics = combo.fontMetrics()
    longest = max(metrics.horizontalAdvance(t) for t in ("一致性优先", "净真峰优先"))
    assert combo.minimumWidth() >= longest + MIN_SLACK_PX
