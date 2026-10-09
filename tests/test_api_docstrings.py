"""Phase 19: docstring completeness guard for the public API.

The task specification requires a public API to spell out at least parameters / types /
returns / raises / side effects / examples, and names the processing API, the batch API,
``localize_peak``, the QC API and the sampling API. This locks down the completeness of
those **public entry points**; internal private functions are out of scope (the task
specification explicitly does not require them).
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest

REQUIRED_SECTIONS = ("Parameters", "Returns", "Raises", "Side effects", "Examples")

PUBLIC_API: list[tuple[str, str]] = [
    # processing API (external processing / research entry points)
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
    # peak localization
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
    assert not missing, f"{module}.{name} 的 docstring 缺少: {', '.join(missing)}"


@pytest.mark.parametrize(("module", "name"), PUBLIC_API, ids=IDS)
def test_public_api_docstring_names_real_parameters(module: str, name: str) -> None:
    """The Parameters section must not be an empty shell: it must name at least one real
    parameter."""
    obj = _resolve(module, name)
    doc = inspect.getdoc(obj) or ""
    parameters = list(inspect.signature(obj).parameters)
    assert parameters, f"{module}.{name} 没有参数,无需检查"
    assert any(param in doc for param in parameters), (
        f"{module}.{name} 的 docstring 没有提到任何真实参数名"
    )


ROOT = Path(__file__).resolve().parent.parent


def _column_block(text: str, *markers: str) -> list[str]:
    """Return the comma-separated column names from the first ```text block after the
    **present** heading among ``markers``.

    The two trees document in different languages (private Chinese / public English), so
    each heading is accepted in either spelling.
    """
    marker = next((m for m in markers if m in text), None)
    assert marker, f"文档里找不到任何标题: {markers}"
    block = text.split(marker, 1)[1]
    block = block.split("```text", 1)[1].split("```", 1)[0]
    return [name.strip() for name in block.replace("\n", " ").split(",") if name.strip()]


def test_peak_table_columns_match_the_docs() -> None:
    """The code is the single source for the column count and column order of the unified peak
    table; the docs must align column by column (to prevent "19/20 columns" drift)."""
    from nmrforge_api.peak_tables import PEAK_TABLE_COLUMNS

    expected = list(PEAK_TABLE_COLUMNS)
    guide = (ROOT / "docs" / "external-api" / "06-outputs-and-records.md").read_text(
        encoding="utf-8"
    )
    contract = (ROOT / "docs" / "API_CONTRACT.md").read_text(encoding="utf-8")

    assert (
        _column_block(guide, "## 6.2 统一峰表字段", "## 6.2 Unified peak table fields") == expected
    )
    contract_markers = ("### 11.4 峰表字段", "### 11.4 Peak table field")
    if any(marker in contract for marker in contract_markers):
        assert _column_block(contract, *contract_markers) == expected
    else:
        assert f"{len(expected)} 列" in contract or f"{len(expected)} columns" in contract
        assert "external-api/06-outputs-and-records.md" in contract
    assert (
        f"当前 **{len(expected)} 列**" in guide or f"currently **{len(expected)} columns**" in guide
    ), "API 使用指南必须写明当前列数"
