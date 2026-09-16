"""Tests for the licence tooling that the AppImage depends on.

The AppImage bundles Qt, so it distributes LGPL-covered libraries. Two scripts carry that
obligation: `check_third_party_licenses.py` (the shipped texts must be intact and the build must
stage them) and `audit_third_party.py` (the whole bundled set must be classified, so that a
strong-copyleft-only dependency cannot slip in unnoticed). Both are tested here, including the
classification logic that was wrong the first time it was written.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_third_party_licenses.py"
AUDITOR = ROOT / "scripts" / "audit_third_party.py"
LICENSE_DIR = ROOT / "packaging" / "linux" / "THIRD_PARTY_LICENSES"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- checker

def test_shipped_licence_texts_and_notice_are_consistent() -> None:
    done = subprocess.run(
        [sys.executable, str(CHECKER), "--quiet"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_licence_checker_detects_a_tampered_text(tmp_path: Path) -> None:
    """A truncated or replaced licence text must fail, or the check is decoration."""
    checker = _load(CHECKER, "checker_for_tamper_test")

    staging = tmp_path / "packaging" / "linux" / "THIRD_PARTY_LICENSES"
    staging.mkdir(parents=True)
    for name in ("PROVENANCE.txt", "NOTICE.md", "LGPL-3.0.txt", "GPL-3.0.txt"):
        shutil.copy(LICENSE_DIR / name, staging / name)
    build = tmp_path / "packaging" / "linux" / "build_appimage.sh"
    build.write_text(
        "THIRD_PARTY_LICENSES\ncheck_third_party_licenses.py\n--licenses\n", encoding="utf-8"
    )

    checker.ROOT = tmp_path
    checker.LICENSE_DIR = staging
    checker.PROVENANCE = staging / "PROVENANCE.txt"
    checker.NOTICE = staging / "NOTICE.md"

    assert checker.main(["--quiet"]) == 0

    (staging / "LGPL-3.0.txt").write_text("not the licence\n", encoding="utf-8")
    assert checker.main(["--quiet"]) == 1


def test_licence_checker_detects_a_removed_notice_statement(tmp_path: Path) -> None:
    checker = _load(CHECKER, "checker_for_notice_test")
    staging = tmp_path / "packaging" / "linux" / "THIRD_PARTY_LICENSES"
    staging.mkdir(parents=True)
    for name in ("PROVENANCE.txt", "LGPL-3.0.txt", "GPL-3.0.txt"):
        shutil.copy(LICENSE_DIR / name, staging / name)
    (staging / "NOTICE.md").write_text("nothing useful here\n", encoding="utf-8")
    (tmp_path / "packaging" / "linux" / "build_appimage.sh").write_text(
        "THIRD_PARTY_LICENSES\ncheck_third_party_licenses.py\n--licenses\n", encoding="utf-8"
    )

    checker.ROOT = tmp_path
    checker.LICENSE_DIR = staging
    checker.PROVENANCE = staging / "PROVENANCE.txt"
    checker.NOTICE = staging / "NOTICE.md"

    assert checker.main(["--quiet"]) == 1


# --------------------------------------------------------------------------- auditor

@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("MIT", "permissive"),
        ("BSD-3-Clause", "permissive"),
        ("Apache-2.0 OR BSD-2-Clause", "permissive"),
        # The Qt/PySide6 case: weak copyleft because the LGPL option can be used.
        ("LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only", "weak-copyleft"),
        ("LGPL-3.0-only", "weak-copyleft"),
        ("MPL-2.0", "weak-copyleft"),
        # Strong copyleft would constrain this project's own licence.
        ("GPL-3.0-only", "strong-copyleft"),
        ("AGPL-3.0-only", "strong-copyleft"),
        ("GPL-2.0-only OR GPL-3.0-only", "strong-copyleft"),
        # Vague metadata must not be silently treated as usable.
        ("", "unknown"),
        ("Dual License", "unclassified"),
    ],
)
def test_licence_option_classification(expression: str, expected: str) -> None:
    auditor = _load(AUDITOR, "auditor_for_classification_test")
    assert auditor._best_option(expression)[0] == expected


def test_best_option_prefers_the_usable_option_in_an_or_expression() -> None:
    auditor = _load(AUDITOR, "auditor_for_choice_test")
    klass, chosen = auditor._best_option("GPL-3.0-only OR LGPL-3.0-only")
    assert klass == "weak-copyleft"
    assert "LGPL" in chosen, chosen


def test_licence_lookup_prefers_a_precise_classifier_over_vague_free_text() -> None:
    """python-dateutil declares `License: Dual License` plus precise trove classifiers."""
    auditor = _load(AUDITOR, "auditor_for_lookup_test")
    text, source = auditor._licence_of(
        _FakeDistribution(
            {
                "License": "Dual License",
                "Classifier": [
                    "License :: OSI Approved :: BSD License",
                    "License :: OSI Approved :: Apache Software License",
                ],
            }
        )
    )
    assert source == "Classifier"
    assert "BSD" in text and "Apache" in text


class _FakeMetadata(dict):
    def get_all(self, key):
        return self.get(key, [])


class _FakeDistribution:
    def __init__(self, metadata_dict):
        self.metadata = _FakeMetadata(metadata_dict)


def test_audit_reports_no_strong_copyleft_dependency() -> None:
    """Run for real in this environment: the bundled set must not force a GPL project licence."""
    done = subprocess.run(
        [sys.executable, str(AUDITOR), "--quiet"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode in (0, 1), done.stdout + done.stderr
    assert "strong-copyleft only       : 0" in done.stdout, done.stdout
    assert "PySide6" in done.stdout, done.stdout
