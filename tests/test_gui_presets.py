"""presets 数据类型模板数据完整性测试:全部 YAML 可解析、字段与化学位移先验合法。"""

from __future__ import annotations

from pathlib import Path

import yaml

PRESETS_DIR = Path(__file__).resolve().parent.parent / "presets"


def _presets() -> list[tuple[str, dict]]:
    items: list[tuple[str, dict]] = []
    for path in sorted(PRESETS_DIR.glob("*.yaml")):
        items.append(
            (path.name, yaml.safe_load(path.read_text(encoding="utf-8")))
        )
    return items


def test_all_presets_parse_and_required_fields() -> None:
    """全部预设可解析且含 name/ndim;非 generic 模板含核与化学位移先验。"""
    presets = _presets()
    assert presets, "presets 目录不应为空"
    for name, data in presets:
        assert isinstance(data, dict) and data.get("name"), name
        ndim = (data.get("constraints") or {}).get("ndim")
        assert ndim in (1, 2, 3), f"{name}: ndim={ndim}"
        generic = str(data.get("name", "")).lower().startswith("generic")
        if not generic:
            assert data.get("direct_nucleus"), f"{name}: 缺 direct_nucleus"
            assert data.get("priors"), f"{name}: 缺 priors"


def test_presets_priors_are_valid_ranges() -> None:
    """priors 均为 [lo, hi] 且 lo <= hi。"""
    for name, data in _presets():
        for nucleus, rng in (data.get("priors") or {}).items():
            assert isinstance(rng, list) and len(rng) == 2, f"{name}: {nucleus}"
            assert rng[0] <= rng[1], f"{name}: {nucleus} {rng}"


def test_common_types_present() -> None:
    """常用 1D/2D/3D 谱预设齐全。"""
    names = {data["name"] for _, data in _presets()}
    for expected in (
        "HSQC",
        "HMQC",
        "HMBC",
        "COSY",
        "TOCSY",
        "NOESY",
        "ROESY",
        "HNCA",
        "HNCACB",
        "CBCA(CO)NH",
        "HNCO",
        "HNHA",
        "HSQC-TOCSY-13C",
        "HSQC-TOCSY-15N",
        "HCCH-COSY",
        "TOCSY-HSQC-15N",
        "HCACO",
        "HMQC-31P",
        "HMBC-31P",
        "HSQC-19F",
        "REDOR",
        "1H-1D",
        "13C-1D",
        "31P-1D",
        "19F-1D",
    ):
        assert expected in names, f"缺少常用预设 {expected}"
