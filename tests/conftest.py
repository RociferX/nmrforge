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
