"""Presets data type template data integrity test: all YAML can be parsed, fields and chemical
shifts are legal a priori."""

from __future__ import annotations

import yaml

from core.app_paths import resource_path

#: the same lookup the product uses (data package nmrforge_data/presets; identical when
#: installed and inside the AppImage)
PRESETS_DIR = resource_path("presets")


def _presets() -> list[tuple[str, dict]]:
    items: list[tuple[str, dict]] = []
    for path in sorted(PRESETS_DIR.glob("*.yaml")):
        items.append(
            (path.name, yaml.safe_load(path.read_text(encoding="utf-8")))
        )
    return items


def test_all_presets_parse_and_required_fields() -> None:
    """All defaults are analytic and contain name/ndim; non-generic templates contain kernel and
    chemical shift priors."""
    presets = _presets()
    assert presets, "presets directory should not be empty"
    for name, data in presets:
        assert isinstance(data, dict) and data.get("name"), name
        ndim = (data.get("constraints") or {}).get("ndim")
        assert ndim in (1, 2, 3), f"{name}: ndim={ndim}"
        generic = str(data.get("name", "")).lower().startswith("generic")
        if not generic:
            assert data.get("direct_nucleus"), f"{name}: missing direct_nucleus"
            assert data.get("priors"), f"{name}: Missing priors"


def test_presets_priors_are_valid_ranges() -> None:
    """The priors are all [lo, hi] and lo <= hi."""
    for name, data in _presets():
        for nucleus, rng in (data.get("priors") or {}).items():
            assert isinstance(rng, list) and len(rng) == 2, f"{name}: {nucleus}"
            assert rng[0] <= rng[1], f"{name}: {nucleus} {rng}"


def test_common_types_present() -> None:
    """Commonly used 1D/2D/3D spectrum presets are complete."""
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
        assert expected in names, f"Missing common presets {expected}"
