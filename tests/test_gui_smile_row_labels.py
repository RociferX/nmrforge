"""SMILE 行「排序 / 优化程度」下拉框文案:选项名点明差别、悬停说明讲清两种口径。

用户 2026-09-11:「排序的悬停文本说明对两个选项的解释不够清楚」。
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from gui.pipeline_panel import PipelineStepRow
from gui.theme import apply_dark_theme


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
    """选项显示文本点明差别;写入 ui_state 的值不变(true_peaks/consistency)。"""
    combo = smile_row.rank_combo

    assert [combo.itemData(i) for i in range(combo.count())] == [
        "true_peaks",
        "consistency",
    ]
    texts = [combo.itemText(i) for i in range(combo.count())]
    assert "少伪峰" in texts[0]  # 净真峰优先:压伪峰
    assert "残差小" in texts[1]  # 一致性优先:留出残差


def test_rank_tooltip_explains_both_modes(smile_row: PipelineStepRow) -> None:
    """悬停说明分别解释两种口径:怎么算、看什么、什么时候用。"""
    tip = smile_row.rank_combo.toolTip()

    assert "净真峰优先" in tip and "一致性优先" in tip
    # 净真峰:稳定峰 − 疑伪峰(两词定义都要在)
    assert "稳定峰" in tip and "疑伪峰" in tip
    # 一致性:留出采样点残差 + 相关系数(可量化)
    assert "留出" in tip and "残差" in tip and "相关系数" in tip
    # 候选评估阈值独立于选峰步骤(0.2.199-补29hz-修16):说明里要讲清,不能误导
    assert "3σ" in tip and "35σ" in tip
    assert "无关" in tip or "独立" in tip
    assert "「峰挑选」步骤的阈值(σ)决定" not in tip
    # 多行可读:不是一行长文本;且没有超长行
    lines = tip.splitlines()
    assert len([ln for ln in lines if ln.strip()]) >= 8
    assert all(len(ln) <= 40 for ln in lines)


def test_grid_tooltip_explains_group_count(smile_row: PipelineStepRow) -> None:
    """「优化程度」悬停说明给出组数与快慢关系。"""
    tip = smile_row.grid_combo.toolTip()

    assert "n×n" in tip
    assert "组" in tip
