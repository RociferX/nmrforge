"""临时:量「优化程度」与「排序」两组之间的水平间隙(修复后)。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from gui.pipeline_panel import PipelineStepRow
from gui.theme import apply_dark_theme

app = QApplication([])
apply_dark_theme(app)

for step in ("smile", "peaks"):
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    row = PipelineStepRow(step, step, "x", host)
    lay.addWidget(row)
    host.resize(420, 200)
    host.show()
    app.processEvents()
    if step == "smile":
        gap = row.rank_label.x() - (row.grid_combo.x() + row.grid_combo.width())
        print(
            f"[{step}] grid_combo x={row.grid_combo.x()} w={row.grid_combo.width()} | "
            f"gap(间隔)= {gap} | 间隔控件 x={row.smile_gap.x()} w={row.smile_gap.width()} "
            f"hidden={row.smile_gap.isHidden()} | rank_label x={row.rank_label.x()}"
        )
    else:
        print(f"[{step}] 间隔控件 hidden={row.smile_gap.isHidden()}(非 SMILE 行应隐藏)")
    host.deleteLater()
    app.processEvents()
