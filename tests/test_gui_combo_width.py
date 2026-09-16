"""下拉框文本余量守卫:不能被压到省略号(0.2.199-补29hz-修14)。

用户 2026-09-11:「优化程度和排序的下拉框内容很多省略号」。
根因:系统/QSS 样式下 QComboBox 的 sizeHint 不含下拉箭头与内边距,文本区只剩
2~3 px 余量(实测「净真峰优先」60 px 文字 / 62 px 文本区),字体渲染稍有差异
就被 Qt 省略号截断。修法:gui/theme.fit_combo_width 按「最长项文字 + 箭头 +
余量」设最小宽度;本测试守住「文本区 ≥ 文字宽 + 8 px」。
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
    """文本区宽度 - 最长项文字宽(负值即会被省略号截断)。"""
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
    """SMILE 行的「优化程度 / 排序」下拉框留够文字余量。"""
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
    """数据组面板的「处理到 / 依次优化到」下拉框同样不留省略号。"""
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
    """fit_combo_width 对空/单项/长项都给出 ≥ 文字宽的显式最小宽度。"""
    from ui_support.theme import fit_combo_width

    combo = QComboBox(host)
    fit_combo_width(combo)
    assert combo.minimumWidth() > 0
    combo.addItems(["一致性优先", "净真峰优先"])
    fit_combo_width(combo)
    metrics = combo.fontMetrics()
    longest = max(metrics.horizontalAdvance(t) for t in ("一致性优先", "净真峰优先"))
    assert combo.minimumWidth() >= longest + MIN_SLACK_PX
