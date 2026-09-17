"""Phase 19:公开 API 的 docstring 完整性守卫。

任务书要求公开 API 至少写清楚 parameters / types / returns / raises / side effects /
examples,并点名 processing API、batch API、``localize_peak``、QC API、sampling API。
这里锁定这些**公开入口**的完整性;内部私有函数不在此列(任务书明确不要求)。
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest

REQUIRED_SECTIONS = ("Parameters", "Returns", "Raises", "Side effects", "Examples")

PUBLIC_API: list[tuple[str, str]] = [
    # processing API(对外处理/研究入口)
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
    # 峰定位
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
    """Parameters 不能是空壳:至少要提到一个真实参数名。"""
    obj = _resolve(module, name)
    doc = inspect.getdoc(obj) or ""
    parameters = list(inspect.signature(obj).parameters)
    assert parameters, f"{module}.{name} 没有参数,无需检查"
    assert any(param in doc for param in parameters), (
        f"{module}.{name} 的 docstring 没有提到任何真实参数名"
    )


ROOT = Path(__file__).resolve().parent.parent


def _column_block(text: str, marker: str) -> list[str]:
    """取出 ``marker`` 之后第一个 ```text 代码块里的逗号分隔列名。"""
    block = text.split(marker, 1)[1]
    block = block.split("```text", 1)[1].split("```", 1)[0]
    return [name.strip() for name in block.replace("\n", " ").split(",") if name.strip()]


def test_peak_table_columns_match_the_docs() -> None:
    """统一峰表的列数与列序以代码为唯一来源,文档必须逐列对齐(防「19/20 列」漂移)。"""
    from nmrforge_api.peak_tables import PEAK_TABLE_COLUMNS

    expected = list(PEAK_TABLE_COLUMNS)
    guide = (ROOT / "docs" / "external-api" / "06-outputs-and-records.md").read_text(
        encoding="utf-8"
    )
    contract = (ROOT / "docs" / "API_CONTRACT.md").read_text(encoding="utf-8")

    assert _column_block(guide, "## 6.2 统一峰表字段") == expected
    assert _column_block(contract, "### 11.4 峰表字段") == expected
    assert f"当前 **{len(expected)} 列**" in guide, "API 使用指南必须写明当前列数"
