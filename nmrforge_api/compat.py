"""Behaviour compatibility fingerprint and change classification (downstream, 2026-09-19).

Downstream runs ensembles under frozen rules and needs a machine-readable answer to
"which behaviour produced these numbers, and do they have to be re-run?". An unchanged
interface name does not mean unchanged behaviour (the exclusive window and the two repairs
of the ratio denominator all left the API surface alone while changing the numbers), so the
judgement is turned into a fact:

- ``behavior_digest``: SHA-256 over the **file content** of ``core/ + backend/ +
  workflow/ + nmrforge_api/`` and the shipped data
  (``nmrforge_data/config/nmrforge.yaml``, ``nmrforge_data/presets/``); line endings are
  normalised, so one commit has the same fingerprint on
  Windows and Linux; ``tests/``, ``docs/`` and the user's local configuration take no
  part;
- ``token_digest``: the same ``.py`` files normalised through the AST (**comments and
  docstrings stripped**) -- the two language editions differ only in comments/docstrings,
  so this one agrees across both and answers "is this the same code?";
- ``compat_level``: ``same | additive | behavior_changed | contract_changed``, plus
  ``unverified`` (the working tree changed code without updating the declaration -- the
  value cannot be trusted);
- ``affected``: the downstream steps a behaviour change touches (see
  :data:`AFFECTED_STEPS`);
- the declaration file ``nmrforge_api/compat_declaration.py`` (**a pure data module**,
  deliberately excluded from the digest: otherwise "declare a new level" would itself
  change the digest, which is self-contradictory);
- the guard ``tests/test_compat.py``: a digest that disagrees with the declaration ->
  failure; an inconsistent level -> failure; ``level=same`` with changed code tokens ->
  failure; golden-vector hashes that do not match -> failure.

At run time ``run.json`` / ``records/reference.json`` / ``records/manifest.json`` carry
:func:`record_stamp` (``behavior_digest`` / ``token_digest`` / ``compat_level`` /
``compat_affected`` / ``compat_verified``) next to ``versions``, so the artefact itself
says which behaviour produced it.
"""

from __future__ import annotations

import ast
import copy
import functools
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from core.project.manager import atomic_write_text
from core.version import git_commit_dirty, software_commit, software_version
from ui_support.i18n import tr

#: manifest schema (downstream parses it by this version)
COMPAT_SCHEMA = "nmrforge_api.compat.v1"
#: declaration-file schema
DECLARATION_SCHEMA = "nmrforge_api.compat.declaration.v1"

#: code trees that take part in ``behavior_digest`` (relative to the repo root)
BEHAVIOR_ROOTS: tuple[str, ...] = ("core", "backend", "workflow", "nmrforge_api")
#: behaviour-bearing data shipped with the package (changes with the code on install)
BEHAVIOR_EXTRA: tuple[str, ...] = (
    "nmrforge_data/config/nmrforge.yaml",
    "nmrforge_data/presets",
)
#: declaration file: a pure data module, **deliberately excluded** from the digest
DECLARATION_MODULE = "nmrforge_api/compat_declaration.py"

#: change levels (downstream only has to re-run for behavior_changed / contract_changed)
COMPAT_LEVELS: tuple[str, ...] = (
    "same",
    "additive",
    "behavior_changed",
    "contract_changed",
)
#: value when code changed without updating the declaration (untrusted; never in old artefacts)
UNVERIFIED_LEVEL = "unverified"

#: allowed downstream steps for ``affected``: a behaviour change must name them
AFFECTED_STEPS: tuple[str, ...] = (
    "reference",
    "processing",
    "sweep_detection",
    "localization",
    "records",
    "api_surface",
    "cli",
    # the combined quality score/decision (consumed by optimisation ordering and
    # reports; added 2026-09-20)
    "qc",
)

#: record schema ids (a change of field set or meaning = contract_changed)
RECORD_SCHEMAS: dict[str, str] = {
    "run": "nmrforge_api.run.v1",
    "reference": "nmrforge_api.reference.v1",
    "manifest": "nmrforge_api.manifest.v1",
    "resume_fingerprint": "nmrforge_api.resume.v2",
}

_SKIP_DIRS = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)
_SKIP_SUFFIXES = frozenset({".pyc", ".pyo"})


def repo_root() -> Path:
    """Repo root (the parent of ``nmrforge_api/``; the artefact root when packaged)."""
    return Path(__file__).resolve().parent.parent


def behavior_sources() -> list[str]:
    """Paths that take part in the fingerprint (relative; published so downstream can check)."""
    return [*BEHAVIOR_ROOTS, *BEHAVIOR_EXTRA]


def _iter_behavior_files(root: Path) -> list[tuple[str, Path]]:
    """Behaviour files as ``[(rel, abs)]`` (sorted; declaration and caches excluded)."""
    found: dict[str, Path] = {}
    targets: list[Path] = []
    for name in BEHAVIOR_ROOTS:
        base = root / name
        if base.is_dir():
            targets.append(base)
    for name in BEHAVIOR_EXTRA:
        base = root / name
        if base.is_dir():
            targets.append(base)
        elif base.is_file():
            targets.append(base)
    for base in targets:
        candidates = [base] if base.is_file() else list(base.rglob("*"))
        for path in candidates:
            if not path.is_file():
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.suffix in _SKIP_SUFFIXES:
                continue
            rel = path.relative_to(root).as_posix()
            if rel == DECLARATION_MODULE:
                continue
            found[rel] = path
    return sorted(found.items())


def _normalized_bytes(path: Path) -> bytes:
    """File bytes with line endings normalised (CRLF/CR -> LF).

    The same commit may be checked out with different line endings on Windows and Linux
    (``.gitattributes`` only forces LF for ``*.py``); the fingerprint is a **content**
    fingerprint and must not be skewed by the checkout encoding.
    """
    raw = path.read_bytes()
    if b"\r" in raw:
        return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return raw


def behavior_digest(root: Path | None = None) -> str:
    """Content fingerprint of the behaviour tree (path + SHA-256 per file, endings normalised)."""
    base = Path(root) if root is not None else repo_root()
    digest = hashlib.sha256()
    for rel, path in _iter_behavior_files(base):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(_normalized_bytes(path)).digest())
    return digest.hexdigest()


_DOC_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _strip_docstrings(tree: ast.AST) -> None:
    """Drop the first string statement of a module/class/function in place."""
    for node in ast.walk(tree):
        if not isinstance(node, _DOC_OWNERS) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node.body = node.body[1:] or [ast.Pass()]


def _dump_ast(tree: ast.AST) -> str:
    """Canonical AST text for ``token_digest``.

    From Python 3.13 on, ``ast.dump`` omits empty optional fields/sequences by default
    (``show_empty=False``) while 3.12 prints them, so the very same source produced two
    different token fingerprints and the 3.12/3.13 CI matrix went red. Ask for the empty
    fields explicitly wherever the interpreter supports it, so one code base keeps one
    fingerprint.
    """
    if sys.version_info >= (3, 13):
        return ast.dump(
            tree, annotate_fields=True, include_attributes=False, show_empty=True
        )
    return ast.dump(tree, annotate_fields=True, include_attributes=False)


def token_digest(root: Path | None = None) -> str:
    """Code fingerprint: AST text with comments/docstrings stripped (edition-comparable)."""
    base = Path(root) if root is not None else repo_root()
    digest = hashlib.sha256()
    for rel, path in _iter_behavior_files(base):
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:  # pragma: no cover
            raise RuntimeError(tr("cannot normalise {p0}: {p1}", p0=rel, p1=exc)) from exc
        _strip_docstrings(tree)
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_dump_ast(tree).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def declaration() -> dict[str, Any]:
    """Read the declaration module (pure data; missing fields fall back to defaults)."""
    from nmrforge_api import compat_declaration as module

    data = dict(getattr(module, "DECLARATION", {}) or {})
    data.setdefault("schema", DECLARATION_SCHEMA)
    data.setdefault("compat_level", "")
    data.setdefault("affected", [])
    data.setdefault("digest", "")
    data.setdefault("token_digest", "")
    return data


def _declared_golden() -> dict[str, Any]:
    golden = declaration().get("golden") or {}
    return dict(golden) if isinstance(golden, dict) else {}


@functools.lru_cache(maxsize=1)
def _status_cached() -> dict[str, Any]:
    """Computed once per process (two tree-wide fingerprints); copy it before returning."""
    declared = declaration()
    digest = behavior_digest()
    tokens = token_digest()
    declared_digest = str(declared.get("digest", "") or "")
    verified = bool(declared_digest) and declared_digest == digest
    declared_level = str(declared.get("compat_level", "") or "")
    return {
        "schema": COMPAT_SCHEMA,
        "behavior_digest": digest,
        "token_digest": tokens,
        "declared_digest": declared_digest,
        "declared_token_digest": str(declared.get("token_digest", "") or ""),
        "declared_level": declared_level,
        "compat_level": declared_level if verified else UNVERIFIED_LEVEL,
        "affected": list(declared.get("affected") or []) if verified else [],
        "verified": verified,
        "declared_updated": str(declared.get("updated", "") or ""),
        "note": str(declared.get("note", "") or ""),
        "software_version": software_version(),
        "software_commit": software_commit(),
        "git_dirty": bool(git_commit_dirty()),
    }


def compat_status() -> dict[str, Any]:
    """Current fingerprints plus the level (``compat_level=unverified`` when not verified)."""
    return copy.deepcopy(_status_cached())


def reset_status_cache() -> None:
    """Clear the in-process cache (tests / long-running processes that changed the working tree)."""
    _status_cached.cache_clear()


def record_stamp() -> dict[str, Any]:
    """The behaviour stamp written into ``run.json`` / ``reference.json`` / ``manifest.json``."""
    status = _status_cached()
    return {
        "behavior_digest": status["behavior_digest"],
        "token_digest": status["token_digest"],
        "compat_level": status["compat_level"],
        "compat_affected": list(status["affected"]),
        "compat_verified": bool(status["verified"]),
    }


def default_snapshot() -> dict[str, Any]:
    """Built-in default snapshot (**independent of config**, identical on every machine)."""
    from core.peaks.axis_units import EDGE_MARGIN_LINEWIDTH_FACTOR
    from core.peaks.localize import (
        DEFAULT_GAUSSIAN_MAX_NFEV,
        DEFAULT_GAUSSIAN_ROI_F1_PPM,
        DEFAULT_GAUSSIAN_ROI_F2_PPM,
        DEFAULT_GAUSSIAN_ROI_MAX_POINTS,
        DEFAULT_LOCALIZATION_METHOD,
    )
    from nmrforge_api.peaks import DEFAULT_DETECTION_SIGMA

    return {
        "window": {
            "default_type": "sine_bell",
            "note": tr("when the base gives no window.<axis>.type, sine_bell is rendered"),
        },
        "baseline": {"default_mode": "auto"},
        "zero_fill": {"default_multiple": 1},
        "localization": {
            "default_method": DEFAULT_LOCALIZATION_METHOD,
            "gaussian_roi_f1_ppm": DEFAULT_GAUSSIAN_ROI_F1_PPM,
            "gaussian_roi_f2_ppm": DEFAULT_GAUSSIAN_ROI_F2_PPM,
            "gaussian_roi_max_points": DEFAULT_GAUSSIAN_ROI_MAX_POINTS,
            "gaussian_max_nfev": DEFAULT_GAUSSIAN_MAX_NFEV,
            # strategy when the target list is written per condition and a condition has no rows
            "targets_on_missing": "error",
        },
        # mirror key of localization.*: downstream reads defaults.gaussian_roi from the spec
        "gaussian_roi": {
            "f1_ppm": DEFAULT_GAUSSIAN_ROI_F1_PPM,
            "f2_ppm": DEFAULT_GAUSSIAN_ROI_F2_PPM,
            "max_points": DEFAULT_GAUSSIAN_ROI_MAX_POINTS,
        },
        "edge_margin": f"{EDGE_MARGIN_LINEWIDTH_FACTOR:g}×linewidth",
        "detection_sigma": float(DEFAULT_DETECTION_SIGMA),
        "threshold_semantics": "reference-locked",
    }


def contract_snapshot() -> dict[str, Any]:
    """Contract snapshot: peak-table column order, record schema ids, error and warning codes."""
    import inspect

    from nmrforge_api import errors as errors_module
    from nmrforge_api import study as study_module
    from nmrforge_api import sweep as sweep_module
    from nmrforge_api.peak_tables import PEAK_TABLE_COLUMNS

    warnings: set[str] = set()
    for module in (sweep_module, study_module):
        for name, value in inspect.getmembers(module):
            if name.startswith("WARN_") and isinstance(value, str):
                warnings.add(value)
    return {
        "peak_table_columns": list(PEAK_TABLE_COLUMNS),
        "records": dict(RECORD_SCHEMAS),
        "error_codes": [
            str(name) for name in getattr(errors_module, "__all__", [])
        ],
        "warning_codes": sorted(warnings),
    }


def compat_manifest() -> dict[str, Any]:
    """The machine-readable compatibility manifest (``nmrforge_api.compat.v1``).

    Returns
    -------
    dict[str, Any]
        ``software_*``, both fingerprints, ``compat_level`` / ``affected`` / ``verified``,
        ``contracts`` (column order / record schema / error and warning codes),
        ``defaults`` (built-in defaults), ``golden`` (the declared golden-vector hashes)
        and ``declaration`` (declaration metadata).
    """
    status = _status_cached()
    declared = declaration()
    return {
        "schema": COMPAT_SCHEMA,
        "software_version": status["software_version"],
        "software_commit": status["software_commit"],
        "git_dirty": status["git_dirty"],
        "behavior_sources": behavior_sources(),
        "behavior_digest": status["behavior_digest"],
        "token_digest": status["token_digest"],
        "declared_digest": status["declared_digest"],
        "compat_level": status["compat_level"],
        "declared_level": status["declared_level"],
        "affected": list(status["affected"]),
        "verified": bool(status["verified"]),
        "contracts": contract_snapshot(),
        "defaults": default_snapshot(),
        "golden": _declared_golden(),
        "declaration": {
            "schema": declared.get("schema", DECLARATION_SCHEMA),
            "updated": declared.get("updated", ""),
            "note": declared.get("note", ""),
            "module": DECLARATION_MODULE,
        },
    }


def write_compat_manifest(path: Path | str) -> Path:
    """Write the manifest as JSON (UTF-8, trailing newline).

    Parameters
    ----------
    path : Path | str
        target file; parent directories are created.

    Returns
    -------
    Path
        the written path.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        target,
        json.dumps(compat_manifest(), ensure_ascii=False, indent=2) + "\n",
    )
    return target


__all__ = [
    "AFFECTED_STEPS",
    "BEHAVIOR_EXTRA",
    "BEHAVIOR_ROOTS",
    "COMPAT_LEVELS",
    "COMPAT_SCHEMA",
    "DECLARATION_MODULE",
    "DECLARATION_SCHEMA",
    "RECORD_SCHEMAS",
    "UNVERIFIED_LEVEL",
    "behavior_digest",
    "behavior_sources",
    "compat_manifest",
    "compat_status",
    "contract_snapshot",
    "declaration",
    "default_snapshot",
    "record_stamp",
    "repo_root",
    "reset_status_cache",
    "token_digest",
    "write_compat_manifest",
]
