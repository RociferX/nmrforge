"""实验模板与先验：单一数据源 presets/*.yaml。

导入本包即从 presets/*.yaml 加载并注册全部模板(0.2.111 起,
替代逐模块 Python 模板)。
"""

from core.experiments.registry import load_presets  # noqa: F401

load_presets()
