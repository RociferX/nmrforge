"""步骤行布局:项间留间距 + SMILE 两组之间留间隔(0.2.199-补29hz-修19)。

用户 2026-09-11:「优化程度和排序靠的太近,之间的间隔也没有」——
根因:自定义流式布局 `_FlowLayout` 声明了 `setSpacing(6)` 却只在**换行**时用到,
项与项之间是紧贴的(0 px);两组之间也没有额外间隔。
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from gui.pipeline_panel import PipelineStepRow, _FlowLayout
from gui.theme import apply_dark_theme


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
    """把步骤行放进宿主布局(否则行宽不会跟随宿主,量出来的都是换行态)。"""
    lay = host.layout() or QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    row = PipelineStepRow(step, step, "说明", host)
    lay.addWidget(row)
    return row


def test_flow_layout_applies_item_spacing(qapp: QApplication, host: QWidget) -> None:
    """流式布局的项与项之间要有间距(不是只有换行时才用 spacing)。"""
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
    """「优化程度」组与「排序」组之间留出明显间隔;组内保持紧凑。"""
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
    else:  # 视口很窄时会换行——这时看行与行之间的纵向间隔
        between = row.rank_label.y() - (
            row.grid_label.y() + row.grid_label.height()
        )

    assert inner >= 4, inner
    assert between >= 12, (inner, between, same_line)
    assert between > inner, (inner, between)


def test_smile_gap_not_visible_for_other_steps(
    qapp: QApplication, host: QWidget
) -> None:
    """间隔控件只在 SMILE 行占位,其它步骤行不受影响。"""
    for step in ("fid", "spectrum", "peaks"):
        row = PipelineStepRow(step, step, "x", host)
        assert row.smile_gap.isHidden() is True, step

    smile = PipelineStepRow("smile", "SMILE", "x", host)
    assert smile.smile_gap.isHidden() is False

# ---------------------------------------------------------------------------
# 几何守卫(0.2.199-补29hz-修20):所有步骤行 × 多种宽度
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
    """返回几何问题列表:重叠 / 同行间距不足 / 超出可视宽度 / 被压成窄条。"""
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
def test_step_rows_have_no_geometry_problems(
    qapp: QApplication, host: QWidget, width: int
) -> None:
    """所有步骤行在常见宽度下:控件不重叠、同行间距≥4px、不超出行宽。"""
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
