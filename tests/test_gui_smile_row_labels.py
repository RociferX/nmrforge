"""SMILE Line "Sort / optimisation Level" drop-down box copy: The option name points out the
difference, and the hover description explains the two calibers. user 2026-09-11: "The hover
text description of sorting does not explain the two options clearly enough."."""

from __future__ import annotations

import pytest
from qtcompat.QtWidgets import QApplication, QWidget

from gui.pipeline_panel import PipelineStepRow
from ui_support.theme import apply_dark_theme


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    apply_dark_theme(app)
    yield app


@pytest.fixture
def smile_row(qapp: QApplication):
    host = QWidget()
    row = PipelineStepRow("smile", "SMILE optimisation", "Optional", host)
    yield row
    host.deleteLater()
    QApplication.processEvents()


def test_rank_items_name_the_difference(smile_row: PipelineStepRow) -> None:
    """Option display text highlights the difference; the value written as ui_state remains
    unchanged (true_peaks/consistency)."""
    combo = smile_row.rank_combo

    assert [combo.itemData(i) for i in range(combo.count())] == [
        "true_peaks",
        "consistency",
    ]
    texts = [combo.itemText(i) for i in range(combo.count())]
    assert "Pure peaks first" in texts[0]  # Pure-peak priority: suppress spurious peaks.
    assert "Consistency first" in texts[1]  # Consistency first: rank by residual.


def test_rank_tooltip_explains_both_modes(smile_row: PipelineStepRow) -> None:
    """The hover description explains the two calibers respectively: how to calculate, what to look
    at, and when to use it."""
    tip = smile_row.rank_combo.toolTip()

    assert "Pure peaks first" in tip and "Consistency first" in tip
    # Pure peaks: stable peak minus spurious peak (both terms must be defined).
    assert "stable peak" in tip and "suspected spurious peak" in tip
    # Consistency: held-out residual + correlation coefficient (both quantifiable).
    assert "held-out" in tip and "residual" in tip and "correlation coefficient" in tip
    # The candidate evaluation threshold is independent of the peak selection step
    # (0.2.199-patch29hz-revision 16): it must be made clear in the description and cannot be
    # misleading.
    assert "3 sigma" in tip and "35 sigma" in tip
    assert "irrelevant" in tip or "independent" in tip
    assert "Determination of threshold (σ) in the \"Peak Picking\" step" not in tip
    # Multi-line readable: not one long line of text; and no extra-long lines.
    lines = tip.splitlines()
    assert len([ln for ln in lines if ln.strip()]) >= 8
    assert all(len(ln) <= 100 for ln in lines)


def test_grid_tooltip_explains_group_count(smile_row: PipelineStepRow) -> None:
    """The "optimisation degree" hover description gives the exact relationship between the number
    of sets and speed (cannot write "≈")."""
    tip = smile_row.grid_combo.toolTip()

    assert "n×n" in tip
    assert "combination" in tip
    # The number of sets is exact n x n (measured 2x2=4, 3x3=9, 4x4=16, 5x5=25), don’t make an
    # appointment.
    assert "≈" not in tip and "about" not in tip
    for text in ("2×2 = 4", "3×3 = 9", "4×4 = 16", "5×5 = 25"):
        assert text in tip
    assert "default is 4×4" in tip
    assert all(len(ln) <= 100 for ln in tip.splitlines())
