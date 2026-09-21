"""Guard for the behaviour compatibility fingerprint and change levels (2026-09-19).

- a fingerprint that disagrees with the declaration -> failure (a changed behaviour tree
  means an updated declaration; silent behaviour changes are not allowed);
  - an inconsistent level (``behavior_changed``/``contract_changed`` without ``affected``, or
  ``same``/``additive`` with one) -> failure;
- ``level=same`` with changed code tokens -> failure (``same`` allows comments/wording only);
- a golden vector that cannot reproduce the declared hashes -> failure;
- the declaration module stays **pure data** and the contract snapshot matches the code.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from nmrforge_api import PEAK_TABLE_COLUMNS
from nmrforge_api.cli import main as cli_main
from nmrforge_api.compat import (
    AFFECTED_STEPS,
    BEHAVIOR_ROOTS,
    COMPAT_LEVELS,
    DECLARATION_MODULE,
    RECORD_SCHEMAS,
    UNVERIFIED_LEVEL,
    behavior_digest,
    behavior_sources,
    compat_manifest,
    compat_status,
    record_stamp,
    token_digest,
)
from nmrforge_api.conformance import GOLDEN_NAME, check_conformance

ROOT = Path(__file__).resolve().parent.parent


def test_behavior_digest_matches_the_declaration() -> None:
    """A changed behaviour tree means an updated declaration, or the level cannot be trusted."""
    status = compat_status()
    assert status["verified"], (
        "the behaviour fingerprint disagrees with the declaration -- update it as described in "
        "nmrforge_api/compat_declaration.py:\n"
        f"  measured behavior_digest = {status['behavior_digest']}\n"
        f"  declared digest          = {status['declared_digest']}\n"
        "hint: python scripts/update_compat_declaration.py --level <level>"
    )
    assert status["compat_level"] in COMPAT_LEVELS
    assert status["compat_level"] != UNVERIFIED_LEVEL


def test_declared_level_is_consistent() -> None:
    """The level and ``affected`` must be consistent (downstream re-runs by affected)."""
    status = compat_status()
    level = status["compat_level"]
    affected = list(status["affected"])
    unknown = [item for item in affected if item not in AFFECTED_STEPS]
    assert not unknown, f"unknown steps in affected: {unknown} (allowed: {AFFECTED_STEPS})"
    if level in ("behavior_changed", "contract_changed"):
        assert affected, f"level={level} must name affected (which downstream steps change)"
    else:
        assert not affected, f"level={level} must not name affected (downstream need not re-run)"


def test_same_level_requires_unchanged_code_tokens() -> None:
    """``same`` = comments/docstrings only: the code tokens must match the declaration."""
    status = compat_status()
    if status["compat_level"] != "same":
        pytest.skip(f"the current level is {status['compat_level']}, not applicable")
    assert status["declared_token_digest"] == status["token_digest"]


def test_declaration_module_is_data_only() -> None:
    """The declaration module allows only ``__future__``/``typing`` imports and constants."""
    path = ROOT / DECLARATION_MODULE
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            assert node.module in ("__future__", "typing"), node.module
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # the module docstring
        assert isinstance(node, (ast.Assign, ast.AnnAssign)), ast.dump(node)[:80]


def test_golden_vector_matches_the_declaration(tmp_path: Path) -> None:
    """Golden vector: a tiny synthetic spectrum + fixed parameters -> declared hashes."""
    result = check_conformance(workdir=tmp_path / "golden")
    assert result["name"] == GOLDEN_NAME
    assert result["items"]["spectrum_sha256"], "golden spectrum hash differs (behaviour changed)"
    assert result["items"]["peak_table_sha256"], (
        "golden peak-table hash differs (behaviour, column order or formatting changed)"
    )
    assert result["items"]["n_peaks"], "the golden vector detected a different number of peaks"


def test_manifest_contracts_match_the_code() -> None:
    """The manifest contract snapshot must match the code (columns/schema/codes)."""
    from nmrforge_api import errors as errors_module

    manifest = compat_manifest()
    assert manifest["schema"] == "nmrforge_api.compat.v1"
    contracts = manifest["contracts"]
    assert contracts["peak_table_columns"] == list(PEAK_TABLE_COLUMNS)
    assert contracts["records"] == dict(RECORD_SCHEMAS)
    assert contracts["error_codes"] == list(errors_module.__all__)
    assert "gaussian_fallback" in contracts["warning_codes"]
    assert "no_spectrum_change" in contracts["warning_codes"]
    # fingerprint coverage: the four code trees plus the shipped data
    sources = behavior_sources()
    for name in BEHAVIOR_ROOTS:
        assert name in sources
    assert manifest["defaults"]["threshold_semantics"] == "reference-locked"
    assert manifest["defaults"]["edge_margin"].endswith("×linewidth")


def test_resume_fingerprint_schema_matches_the_code() -> None:
    """The fingerprint schema id in the manifest must match the literal in sweep."""
    text = (ROOT / "nmrforge_api" / "sweep.py").read_text(encoding="utf-8")
    assert f'"{RECORD_SCHEMAS["resume_fingerprint"]}"' in text


def test_record_stamp_has_the_expected_keys() -> None:
    """The behaviour stamp written into artefacts: digest + level + affected + verified."""
    stamp = record_stamp()
    assert set(stamp) == {
        "behavior_digest",
        "token_digest",
        "compat_level",
        "compat_affected",
        "compat_verified",
    }
    assert stamp["behavior_digest"] == behavior_digest()
    assert stamp["token_digest"] == token_digest()
    assert stamp["compat_level"] in COMPAT_LEVELS


def test_cli_compat_prints_the_manifest(tmp_path: Path, capsys) -> None:
    """CLI ``compat [--out FILE]``: prints the manifest and can write it out."""
    assert cli_main(["compat"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "nmrforge_api.compat.v1"
    assert payload["behavior_digest"] == behavior_digest()

    out = tmp_path / "compat.json"
    assert cli_main(["compat", "--out", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["manifest_path"] == str(out)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["schema"] == "nmrforge_api.compat.v1"
    assert written["behavior_digest"] == behavior_digest()
    assert "manifest_path" not in written


def test_cli_compat_golden_flag_runs_the_vector(tmp_path: Path, capsys) -> None:
    """CLI ``compat --golden``: also runs the golden vector and reports the comparison."""
    assert cli_main(["compat", "--golden", "--workdir", str(tmp_path / "g")]) == 0
    payload = json.loads(capsys.readouterr().out)
    check = payload["golden_check"]
    assert check["name"] == GOLDEN_NAME
    assert check["match"] is True
