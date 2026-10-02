"""Phase 12: integrity guard for the test category table (unit / integration / regression)."""

from __future__ import annotations

from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
IGNORED = {"conftest.py", "categories.py"}


def test_every_test_file_is_registered(test_categories) -> None:
    """A new or renamed test file must be registered too, otherwise the category table
    silently misses it."""
    files = {p.name for p in TESTS_DIR.glob("*.py")} - IGNORED
    registered = set(test_categories.CATEGORIES)
    missing = sorted(files - registered)
    assert not missing, "新增测试文件必须登记到 tests/categories.py: " + ", ".join(missing)
    extra = sorted(registered - files)
    assert not extra, "categories.py 里有多余条目(文件已删除/改名): " + ", ".join(extra)


def test_categories_are_valid_and_non_empty(test_categories) -> None:
    assert set(test_categories.CATEGORIES.values()) <= set(test_categories.CATEGORY_MARKERS)
    for category in test_categories.CATEGORY_MARKERS:
        assert any(value == category for value in test_categories.CATEGORIES.values()), (
            f"没有任何 {category} 类测试"
        )


@pytest.mark.parametrize("category", ["unit", "integration", "regression"])
def test_category_markers_are_registered(category: str, pytestconfig) -> None:
    """Markers must be registered in pyproject, otherwise --strict-markers errors out."""
    markers = pytestconfig.getini("markers")
    assert any(str(marker).startswith(f"{category}:") for marker in markers), markers


def test_unknown_file_defaults_to_integration(test_categories) -> None:
    """Unregistered files conservatively count as integration; the guard above nudges you
    to register them."""
    assert test_categories.category_of("test_brand_new.py") == "integration"
