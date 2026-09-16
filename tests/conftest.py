"""共享 fixtures 与测试分类打标(Phase 12)。

测试分类的**单一来源**是 ``tests/categories.py``;这里按文件名给每个用例打上
``unit`` / ``integration`` / ``regression`` 标记,于是 ``pytest -m unit`` 等可以选子集。
"""

from __future__ import annotations

import importlib.util
import shutil
import types
from pathlib import Path

import pytest

from core.data.internal_data_model import (
    AxisRole,
    Dimension,
    Experiment,
    Sampling,
    SamplingMode,
)


def _load_test_categories() -> types.ModuleType:
    """加载分类表(``tests/categories.py``);用 importlib 而不 import,避免测试目录入 sys.path。"""
    spec = importlib.util.spec_from_file_location(
        "nmrforge_test_categories", Path(__file__).with_name("categories.py")
    )
    if spec is None or spec.loader is None:  # pragma: no cover - 文件必然存在
        raise RuntimeError("找不到 tests/categories.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TEST_CATEGORIES = _load_test_categories()
category_of = TEST_CATEGORIES.category_of


@pytest.fixture(scope="session")
def test_categories() -> types.ModuleType:
    """分类表(Phase 12 单一来源),供完整性守卫测试读取。"""
    return TEST_CATEGORIES


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Phase 12:按 ``tests/categories.py`` 给每个用例打类别标记。

    标记在 conftest 里统一加,避免 100+ 个测试文件各写一行 ``pytestmark``
    (分类表是单一来源,``tests/test_test_categories.py`` 保证不漏登记)。
    """
    for item in items:
        marker = category_of(Path(str(item.fspath)).name)
        item.add_marker(getattr(pytest.mark, marker))


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

NMRPIPE_FID_FDSIZE = 1024
NMRPIPE_FID_SPECNUM = 120


@pytest.fixture(scope="session")
def nmrpipe_fid_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """真实 NMRPipe 2D 单文件 fid 布局的模板(不依赖任何开发机文件)。

    真实转换产物是「2048 字节参数头 + 每迹实部块 / 虚部块」:头里
    ``FDDIMCOUNT=2``、``FDSIZE``=直接维复数点数、``FDSPECNUM``=迹数、
    ``FDQUADFLAG=0``。关键细节:nmrglue 只有在 ``FDF2QUADFLAG=0`` 时才把
    ``(specnum, 2*fdsize)`` 的实型数据解回 ``(specnum, fdsize)`` 复型;
    保持 ``create_empty_dic()`` 的默认值 1 会读成实型,而
    ``workflow.direct_diagnostics._read_fid_raw`` 的二维分支要求复型
    ——这正是此前合成模板始终不被接受的原因(与解析器无关)。

    数据用 complex64 交给 ``ng.pipe.write``:它按实部/虚部块展开成
    「2048 字节头 + float32」,与真实文件逐字节同布局(尺寸取小是为了
    测试速度,布局与 862 迹的真实文件一致)。
    """
    import nmrglue as ng
    import numpy as np

    path = tmp_path_factory.mktemp("nmrpipe_fid") / "template.fid"
    fdsize, specnum = NMRPIPE_FID_FDSIZE, NMRPIPE_FID_SPECNUM
    dic = ng.pipe.create_empty_dic()
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = fdsize
    dic["FDSPECNUM"] = specnum
    dic["FDQUADFLAG"] = 0
    dic["FDF2QUADFLAG"] = 0
    rng = np.random.default_rng(20260916)
    t = np.arange(fdsize, dtype=float)
    signal = np.exp(-t / 150.0) * np.exp(2j * np.pi * 0.12 * t)
    data = np.tile(signal, (specnum, 1)) * rng.uniform(0.5, 2.0, (specnum, 1))
    data = data + rng.normal(0.0, 0.02, (specnum, fdsize))
    data = data + 1j * rng.normal(0.0, 0.02, (specnum, fdsize))
    ng.pipe.write(str(path), dic, data.astype(np.complex64), overwrite=True)

    # 模板必须真的能被诊断解析器接受,否则夹具本身就是错的(此前合成写法两次失败)
    from workflow.direct_diagnostics import _read_fid_raw

    parsed = _read_fid_raw(path)
    assert parsed is not None, "合成 fid 模板必须能被 _read_fid_raw 解析(真实布局)"
    _data, got_fdsize, got_specnum, header = parsed
    assert (got_fdsize, got_specnum) == (fdsize, specnum)
    assert header == 2048, header
    return path



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
