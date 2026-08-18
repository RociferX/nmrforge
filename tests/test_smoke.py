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


def test_presets_single_source_all_registered() -> None:
    """presets/*.yaml 为唯一数据源:全部模板名与 stem 别名可解析。"""
    import yaml

    from core.app_paths import resource_path
    from core.experiments.registry import REGISTRY

    presets_dir = resource_path("presets")
    yaml_names = {
        yaml.safe_load(p.read_text(encoding="utf-8"))["name"]
        for p in presets_dir.glob("*.yaml")
    }
    names = {t.name for t in REGISTRY.values()}
    assert yaml_names <= names
    assert REGISTRY.get("generic_2d") is not None
    assert REGISTRY.get("generic_3d") is not None
    assert REGISTRY.get("hsqc") is not None
    assert REGISTRY.get("hncacb") is not None


def test_classifier_pulprog_names_in_registry() -> None:
    """分类器 pulprog 名单必须在模板注册表内(防漂移)。"""
    from core.experiment.experiment_classifier import _PULPROG_TYPES
    from core.experiments.registry import REGISTRY

    for _keyword, name in _PULPROG_TYPES:
        assert name in REGISTRY, name
