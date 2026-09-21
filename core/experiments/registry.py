"""Experiment template registry (ExperimentTemplate, framework §43).

A template only supplies priors/constraints/expected behaviour; the optimiser decides the
concrete parameters. Single source of truth presets/*.yaml (since 0.2.111): from_yaml plus
load_presets register the display name and the file stem, so case and naming differences
such as Generic still resolve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExperimentTemplate:
    name: str
    phase_sensitive: bool = True
    direct_nucleus: str = "1H"
    indirect_nuclei: list[str] = field(default_factory=list)
    expected_peak_mode: str = "absorption"
    # peak sign convention: uniform = signal peaks share one sign (HSQC/CBCA(CO)NH and
    # similar); mixed = both signs coexist (HNCACB and similar, inverted 13Ca/13Cb). The
    # phase search uses it as an early constraint.
    peak_sign: str = "uniform"
    # chemical-shift-region sign priors: {nucleus: {region: {ppm: [lo, hi], sign: +-1}}}.
    # Used to disambiguate the absolute +-180 degree sign of mixed experiments (e.g. Ca/Cb of
    # HNCACB on the 13C axis).
    peak_sign_regions: dict[str, Any] = field(default_factory=dict)
    display_orientation: str = ""
    priors: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    processing_hints: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path) -> ExperimentTemplate:
        """Load a template from presets/*.yaml (single source of truth, 0.2.111)."""
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(
            name=str(data.get("name") or path.stem),
            phase_sensitive=bool(data.get("phase_sensitive", True)),
            direct_nucleus=str(data.get("direct_nucleus") or ""),
            indirect_nuclei=list(data.get("indirect_nuclei") or []),
            expected_peak_mode=data.get("expected_peak_mode") or "absorption",
            peak_sign=str(data.get("peak_sign") or "uniform"),
            peak_sign_regions=dict(data.get("peak_sign_regions") or {}),
            display_orientation=str(data.get("display_orientation") or ""),
            priors=dict(data.get("priors") or {}),
            constraints=dict(data.get("constraints") or {}),
            processing_hints=dict(data.get("processing_hints") or {}),
        )


REGISTRY: dict[str, ExperimentTemplate] = {}


def get(name: str) -> ExperimentTemplate | None:
    return REGISTRY.get(name)


def load_presets(presets_dir: Path | str | None = None) -> int:
    """Load every template from presets/*.yaml (single source of truth).

    Registered both under the template display name and under the YAML file stem (so naming
    differences such as Generic still resolve); returns the number loaded. Called automatically
    when core.experiments is imported.
    """
    if presets_dir is None:
        from core.app_paths import resource_path

        presets_dir = resource_path("presets")
    REGISTRY.clear()
    count = 0
    for path in sorted(Path(presets_dir).glob("*.yaml")):
        tpl = ExperimentTemplate.from_yaml(path)
        REGISTRY[tpl.name] = tpl
        REGISTRY[path.stem] = tpl
        count += 1
    return count
