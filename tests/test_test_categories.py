"""Phase 12: Test the integrity guard of the classification table (unit/integration/regression)."""

from __future__ import annotations

from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
IGNORED = {"conftest.py", "categories.py"}


def test_every_test_file_is_registered(test_categories) -> None:
    """New/Name change test file must be registered simultaneously, otherwise the classification
    table will silently miss it."""
    files = {p.name for p in TESTS_DIR.glob("*.py")} - IGNORED
    registered = set(test_categories.CATEGORIES)
    missing = sorted(files - registered)
    assert not missing, (
        "New test files must be registered to tests/categories.py: "
    ) + ", ".join(missing)
    extra = sorted(registered - files)
    assert not extra, (
        "There are redundant entries in categories.py (the file has been deleted/renamed): "
    ) + ", ".join(extra)


def test_categories_are_valid_and_non_empty(test_categories) -> None:
    assert set(test_categories.CATEGORIES.values()) <= set(test_categories.CATEGORY_MARKERS)
    for category in test_categories.CATEGORY_MARKERS:
        assert any(
            value == category for value in test_categories.CATEGORIES.values()
        ), f"without any {category} class test"


@pytest.mark.parametrize("category", ["unit", "integration", "regression"])
def test_category_markers_are_registered(category: str, pytestconfig) -> None:
    """Markers must be registered in pyproject, otherwise --strict-markers will report an error
    directly."""
    markers = pytestconfig.getini("markers")
    assert any(str(marker).startswith(f"{category}:") for marker in markers), markers


def test_unknown_file_defaults_to_integration(test_categories) -> None:
    """Unregistered files are conservatively processed as integration, and the above guard prompts
    are used to register them."""
    assert test_categories.category_of("test_brand_new.py") == "integration"
