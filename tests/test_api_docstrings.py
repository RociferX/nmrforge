"""Phase 19: Disclose the docstring integrity guard of API. The mission statement requires that API
be made public API and at least clearly write parameters / types / returns / raises / side
effects / examples, and name processing API, batch API, ``localize_peak``, QC API, sampling API.
The integrity of these **public entries** is locked here; internal private functions are not
included in this list (clearly not required by the mission statement)."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest

REQUIRED_SECTIONS = ("Parameters", "Returns", "Raises", "Side effects", "Examples")

PUBLIC_API: list[tuple[str, str]] = [
    # Processing API(External processing/Research entrance).
    ("nmrforge_api.session", "open_study"),
    ("nmrforge_api.session", "add_dataset"),
    ("nmrforge_api.reference", "build_reference"),
    ("nmrforge_api.reference", "ensure_reference_peaks"),
    ("nmrforge_api.peaks", "pick_reference_peaks"),
    ("nmrforge_api.peaks", "measure_peak_positions"),
    ("nmrforge_api.peaks", "detect_and_localize"),
    ("nmrforge_api.direct_range", "parse_direct_range"),
    ("nmrforge_api.study", "run_reference_study"),
    ("nmrforge_api.study", "run_combination_study"),
    ("nmrforge_api.study", "run_parameter_study"),
    ("nmrforge_api.sweep", "plan_sweep"),
    ("nmrforge_api.sweep", "run_sweep"),
    # batch API
    ("workflow.batch", "run_batch"),
    # Peak location.
    ("core.peaks.localize", "localize_peak"),
    # QC API
    ("core.qc.spectrum_quality", "evaluate"),
    ("core.audit.qc_audit", "read_audit"),
    ("workflow.direct_diagnostics", "run_direct_diagnostics"),
    ("workflow.direct_diagnostics", "run_fid_diagnostics_paths"),
    # sampling API
    ("core.experiment.sampling_detector", "detect"),
    ("core.data.nus_reader", "read_nuslist"),
    ("core.data.nus_reader", "indirect_grid_2d"),
    ("core.data.nus_reader", "scan_dense_2d"),
]

IDS = [f"{module}::{name}" for module, name in PUBLIC_API]


def _resolve(module: str, name: str):
    return getattr(importlib.import_module(module), name)


@pytest.mark.parametrize(("module", "name"), PUBLIC_API, ids=IDS)
def test_public_api_docstring_has_the_required_sections(module: str, name: str) -> None:
    doc = inspect.getdoc(_resolve(module, name)) or ""
    missing = [section for section in REQUIRED_SECTIONS if section not in doc]
    assert not missing, f"{module}.{name} The docstring is missing: {', '.join(missing)}"


@pytest.mark.parametrize(("module", "name"), PUBLIC_API, ids=IDS)
def test_public_api_docstring_names_real_parameters(module: str, name: str) -> None:
    """Parameters cannot be an empty shell: at least one real parameter name must be mentioned."""
    obj = _resolve(module, name)
    doc = inspect.getdoc(obj) or ""
    parameters = list(inspect.signature(obj).parameters)
    assert parameters, f"{module}.{name} No parameter, no need to check"
    assert any(param in doc for param in parameters), (
        f"{module}.{name} The docstring does not mention any real parameter name"
    )


ROOT = Path(__file__).resolve().parent.parent


def _column_block(text: str, marker: str) -> list[str]:
    """Remove the comma-separated column names in the first ```text code block after ``marker``."""
    block = text.split(marker, 1)[1]
    block = block.split("```text", 1)[1].split("```", 1)[0]
    return [name.strip() for name in block.replace("\n", " ").split(",") if name.strip()]


def test_peak_table_columns_match_the_docs() -> None:
    """The number and sequence of columns in the unified peak table are based on codes as the only
    source, and the documents must be aligned column by column (to prevent "19/20 column"
    drift)."""
    from nmrforge_api.peak_tables import PEAK_TABLE_COLUMNS

    expected = list(PEAK_TABLE_COLUMNS)
    guide = (ROOT / "docs" / "external-api" / "06-outputs-and-records.md").read_text(
        encoding="utf-8"
    )
    contract = (ROOT / "docs" / "API_CONTRACT.md").read_text(encoding="utf-8")

    assert _column_block(guide, "## 6.2 Unified peak table fields") == expected
    assert _column_block(contract, "### 11.4 Peak table field") == expected
    assert f"currently **{len(expected)} columns**" in guide, (
        "the API guide must state the current column count"
    )
