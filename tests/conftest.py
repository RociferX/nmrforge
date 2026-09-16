"""共享 fixtures。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.data.internal_data_model import (
    AxisRole,
    Dimension,
    Experiment,
    Sampling,
    SamplingMode,
)


@pytest.fixture
def hsqc_experiment() -> Experiment:
    """构造一个 2D HSQC 风格的最小 Experiment（骨架阶段用）。"""
    return Experiment(
        dataset_id="exp_001",
        source_path=Path("/fake/bruker/1"),
        ndim=2,
        dimensions=[
            Dimension(logical_axis="F1", nucleus="15N", td=128, role=AxisRole.INDIRECT),
            Dimension(logical_axis="F2", nucleus="1H", td=1024, role=AxisRole.DIRECT),
        ],
        sampling=Sampling(mode=SamplingMode.UNIFORM),
    )


FIXTURES_BRUKER = Path(__file__).parent / "fixtures" / "bruker"


@pytest.fixture
def bruker_dir(tmp_path: Path) -> Path:
    """Bruker 测试数据集 fixture 目录(每次测试给一份副本)。

    直接链接共享 fixture 会让文件硬链接数累积到 NTFS 上限(1024)导致
    os.link 失败;副本保证链接建在每测试独立 inode 上(0.2.162-补13)。"""
    copy = tmp_path / "bruker"
    if not copy.exists():
        shutil.copytree(FIXTURES_BRUKER, copy)
    return copy


@pytest.fixture(autouse=True)
def _clear_cancel_between_tests() -> None:
    """0.2.199-补29hg:每测试开始前清后端取消标志,避免上一个测试(如 GUI
    停止按钮)泄漏 _CANCEL 到下个处理/相位搜索测试造成「任务已取消」误报。"""
    from backend.runtime import clear_cancel

    clear_cancel()
    yield


@pytest.fixture(scope="session", autouse=True)
def _close_gui_windows_at_session_end() -> None:
    """会话结束前关闭所有残留顶层窗口并处理事件。

    offscreen 平台下,残留的顶层窗口(含 0.2.194 恢复的导入/组间分析
    下拉 Tool 窗口)在解释器退出时销毁顺序不定,会间歇触发 Qt 访问冲突
    (0xC0000005);显式收尾关闭可消除该抖动。
    """
    yield
    from qtcompat.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for widget in list(app.topLevelWidgets()):
        try:
            widget.close()
        except RuntimeError:  # pragma: no cover - 已销毁
            pass
    app.processEvents()
