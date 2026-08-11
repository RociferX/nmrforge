"""Bruker 参数解析（acqus / acqu2s / acqu3s）。

要求：
- 支持跨行数组、引号/尖括号剥离（TopSpin 4 间接维参数在 acqu2s/acqu3s）；
- 支持别名与缺失参数，不因某个参数缺失而崩溃；
- 只负责「读出原始参数」，语义判断交给上层。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def parse_param_file(path: Path) -> dict[str, Any]:
    """解析单个参数文件，返回键值字典。"""
    raise NotImplementedError("Phase 1: 实现 acqus/acqu2s/acqu3s 解析")


def parse_dataset_params(dataset_dir: Path) -> dict[str, Any]:
    """解析整个数据集的参数（acqus + acqu2s + acqu3s，分维存放）。"""
    raise NotImplementedError("Phase 1: 实现数据集参数解析")
