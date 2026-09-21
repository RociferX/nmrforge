"""Drop-down box text margin guard: cannot be pressed into ellipses (0.2.199-patch29hz-Fix 14).
user 2026-09-11: "The content of the drop-down box with optimised degree and sorting has many
ellipses". Root cause: The sizeHint of QComboBox in the system/QSS style does not include the
drop-down arrow and padding, and only 2~3 px margin is left in the text area (measured
"Jingzhenfeng Priority" 60 px text / 62 px text area), there is a slight difference in font
rendering and it is truncated by Qt ellipses. Correction method: gui/theme.fit_combo_width Press
"Longest item text + arrow + margin" to set the minimum width; in this test, stick to "text area
>= text width + 8 px"."""

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
    """Text area width - the longest item text width (negative values will be truncated by
    ellipses)."""
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
    """Leave enough text margin for the "optimisation level/sort" drop-down box in the SMILE row."""
    row = PipelineStepRow("smile", "SMILE optimisation", "Optional", host)
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
    """The "Process to / optimisation to" drop-down box in the data group panel also does not leave
    an ellipsis."""
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
    """Fit_combo_width against air/Single item/All long terms are given >= Explicit minimum width
    of text width."""
    from ui_support.theme import fit_combo_width

    combo = QComboBox(host)
    fit_combo_width(combo)
    assert combo.minimumWidth() > 0
    combo.addItems(["Consistency first", "Pure peaks first"])
    fit_combo_width(combo)
    metrics = combo.fontMetrics()
    longest = max(metrics.horizontalAdvance(t) for t in ("Consistency first",
        "Pure peaks first"))
    assert combo.minimumWidth() >= longest + MIN_SLACK_PX
