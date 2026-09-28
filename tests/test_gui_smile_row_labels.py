"""SMILE row "rank / optimisation degree" drop-down copy: the option names point out the difference
and the hover text explains both conventions clearly.

User 2026-09-11: "the hover text for rank does not explain the two options clearly enough".
"""

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
    row = PipelineStepRow("smile", "SMILE 优化", "可选", host)
    yield row
    host.deleteLater()
    QApplication.processEvents()


def test_rank_items_name_the_difference(smile_row: PipelineStepRow) -> None:
    """The option display text points out the difference; the value written to ui_state is unchanged
    (true_peaks/consistency)."""
    combo = smile_row.rank_combo

    assert [combo.itemData(i) for i in range(combo.count())] == [
        "true_peaks",
        "consistency",
    ]
    texts = [combo.itemText(i) for i in range(combo.count())]
    assert "少伪峰" in texts[0]  # net true peaks first: suppress spurious peaks
    assert "残差小" in texts[1]  # consistency first: held-out residual


def test_rank_tooltip_explains_both_modes(smile_row: PipelineStepRow) -> None:
    """The hover text explains both conventions separately: how each is computed, what it looks at,
    and when to use it."""
    tip = smile_row.rank_combo.toolTip()

    assert "净真峰优先" in tip and "一致性优先" in tip
    # Net true peaks: stable peak - suspected spurious peak (both terms must be defined)
    assert "稳定峰" in tip and "疑伪峰" in tip
    # Consistency: held-out sample-point residual + correlation coefficient (quantifiable)
    assert "留出" in tip and "残差" in tip and "相关系数" in tip
    # The candidate-evaluation threshold is independent of the peak-picking step
    # (0.2.199-patch29hz-revision 16): the text must spell this out and not mislead
    assert "3σ" in tip and "35σ" in tip
    assert "无关" in tip or "独立" in tip
    assert "「峰挑选」步骤的阈值(σ)决定" not in tip
    # Readable as multiple lines: not one long line of text, and no over-long lines
    lines = tip.splitlines()
    assert len([ln for ln in lines if ln.strip()]) >= 8
    assert all(len(ln) <= 40 for ln in lines)


def test_grid_tooltip_explains_group_count(smile_row: PipelineStepRow) -> None:
    """The "optimisation degree" hover text gives the exact group count and the speed trade-off
    (it may not say "≈")."""
    tip = smile_row.grid_combo.toolTip()

    assert "n×n" in tip
    assert "组" in tip
    # The group count is exactly n×n (measured 2x2=4, 3x3=9, 4x4=16, 5x5=25); never "about"
    assert "≈" not in tip and "约" not in tip
    for text in ("2x2=4", "3x3=9", "4x4=16", "5x5=25"):
        assert text in tip
    assert "默认 4x4" in tip
    assert all(len(ln) <= 40 for ln in tip.splitlines())
