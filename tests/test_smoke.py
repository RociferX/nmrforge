"""骨架冒烟测试：包可导入、内置模板已注册。"""

from __future__ import annotations

import core.data.internal_data_model as model
import core.experiments  # noqa: F401  导入即注册内置模板


def test_package_importable() -> None:
    assert model.SamplingMode.NUS.value == "nus"


def test_builtin_templates_registered() -> None:
    from core.experiments.registry import REGISTRY

    names = {t.name for t in REGISTRY.values()}
    assert {"HSQC", "HNCA", "HNCO", "HNCACB", "CBCA(CO)NH", "Generic2D", "Generic3D"} <= names
