"""Release-readiness regression tests.

These do not test NMR processing. They lock down the properties that make the repository
publishable, so that a later change cannot silently break them:

- the version has exactly one source;
- the README and the public docs do not point at files that do not exist (markdown links and
  plain-text `docs/...` references alike);
- no developer-specific absolute path leaks into the shipped runtime packages;
- the CI configuration is valid and still declares its jobs;
- the example scripts exist and compile;
- the licence state is explicit (either a LICENSE exists, or the "undecided" state is
  documented in LICENSE_OPTIONS.md and stated in the README);
- the third-party inventory still names the external engines;
- the changelog keeps an Unreleased section;
- no raw NMR binary data has been committed as a test fixture;
- the GUI asset referenced by the README is present.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

PUBLIC_DOCS = [
    "docs/getting-started.md",
    "docs/installation.md",
    "docs/gui.md",
    "docs/cli.md",
    "docs/python-api.md",
    "docs/processing-model.md",
    "docs/qc-system.md",
    "docs/peak-picking.md",
    "docs/batch-processing.md",
    "docs/troubleshooting.md",
    "docs/external-dependencies.md",
    "docs/faq.md",
]

ROOT_ARTIFACTS = [
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "THIRD_PARTY.md",
    "LICENSE_OPTIONS.md",
    "NOTICE",
    "CITATION.cff",
]

#: Process records that exist only in the private trunk: the public snapshot does not
#: publish them (the public-export rules live in the private tooling; owner decision
#: 2026-09-21 to slim the public repository). Public trees must not contain them.
INTERNAL_ARTIFACTS = [
    "CHANGELOG.md",
    "RELEASE_CHECKLIST_v0.9.0.md",
    "APPIMAGE_RELEASE_CHECKLIST.md",
    "PUBLIC_RELEASE_AUDIT.md",
]


def _private_trunk() -> bool:
    """True in the private trunk (it keeps docs/manager and .codex), False publicly."""
    return (ROOT / "docs" / "manager").is_dir() or (ROOT / ".codex").is_dir()

# Runtime package: The absolute path to the development machine must not appear.
RUNTIME_PACKAGES = ["core", "backend", "workflow", "gui", "viewer", "nmrforge_api"]

ABSOLUTE_PATH_PATTERNS = [
    re.compile(r"[A-Za-z]:\\\\?Users\\\\?"),          # C:\<user>\... / C:/Users/...
    re.compile(r"/home/(?!nmrforge\b)[A-Za-z0-9_.-]+/"),
    re.compile(r"/Users/[A-Za-z0-9_.-]+/"),          # macOS home
    re.compile(r"OneDrive", re.IGNORECASE),
    re.compile(r"~/Desktop"),
    re.compile(r"/mnt/[a-z]/"),
]

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_version_has_single_source() -> None:
    data = tomllib.loads(_read("pyproject.toml"))
    project = data["project"]

    assert project["dynamic"] == ["version"], "pyproject must not hardcode a version"
    assert "version" not in project, "pyproject must not carry a second version literal"
    assert data["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "core.__version__"}

    import core

    assert isinstance(core.__version__, str) and core.__version__.strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+([-.+].+)?", core.__version__), core.__version__


def test_public_documentation_exists() -> None:
    missing = [name for name in PUBLIC_DOCS + ROOT_ARTIFACTS if not (ROOT / name).is_file()]
    assert not missing, f"missing public artefacts: {missing}"
    if _private_trunk():
        absent = [name for name in INTERNAL_ARTIFACTS if not (ROOT / name).is_file()]
        assert not absent, f"private trunk is missing its process records: {absent}"
        return
    published = [name for name in INTERNAL_ARTIFACTS if (ROOT / name).is_file()]
    assert not published, f"public tree must not publish internal records: {published}"


def test_relative_links_in_public_docs_resolve() -> None:
    documents = [Path("README.md")] + [Path(n) for n in PUBLIC_DOCS]
    broken: list[str] = []
    for document in documents:
        text = (ROOT / document).read_text(encoding="utf-8")
        for target in LINK_RE.findall(text):
            target = target.strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if "<" in target or ">" in target:  # placeholder such as <this repository>
                continue
            relative = target.split("#", 1)[0]
            if not relative:
                continue
            resolved = (ROOT / document.parent / relative).resolve()
            if not resolved.exists():
                broken.append(f"{document} -> {target}")
    assert not broken, "broken relative links: " + "; ".join(broken)


#: Repository paths a public document names must really ship with the snapshot: documents
#: (`docs/xxx.md`) as well as scripts and assets (`scripts/xxx.py`, `tests/xxx.py`,
#: `nmrforge_data/xxx` ...). Plain text counts too - a reader can neither click nor find the file,
#: and the markdown-link checker cannot see that style.
#: 2026-09-22 review: architecture.md listed three private-trunk records; 2026-09-23 review: the
#: evidence page pointed its reproduction commands at three scripts that were never shipped - the
#: same defect class, so this now covers those directories instead of just docs/*.md.
PLAIN_DOC_REF_RE = re.compile(
    r"(?i)\b(?:docs|scripts|tests|nmrforge_data|packaging)/[A-Za-z0-9_./-]+"
    r"\.(?:md|py|sh|json|yaml|yml|toml|spec|txt|cff)"
)


def _shipped_paths(root: Path) -> set[str] | None:
    """Files the snapshot ships, from ``git ls-files`` (casefolded); ``None`` when git is absent.

    A bare ``Path.exists()`` mistakes a machine-local file for a shipped one - for example the
    run-time ``nmrforge_data/config/nmrforge.local.yaml`` (it is in ``.gitignore``): present on the
    developer machine, absent from CI's clean checkout, so the same commit passed on Windows and
    failed on Linux CI (hit for real on 2026-09-23). "Shipped" means "tracked by git".
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return {name.casefold() for name in done.stdout.split("\0") if name}


def _ignored_paths(root: Path, targets: set[str]) -> set[str]:
    """Which of ``targets`` ``.gitignore`` excludes (generated / run-time / machine-local files).

    Naming one of these in a document is legitimate - the repository deliberately does not ship it,
    which is different from naming a file that neither exists nor should exist. ``--no-index``
    makes the decision depend on the rules alone, not on whether the file happens to be present
    right now, so the local run and CI agree.
    """
    if not targets:
        return set()
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--no-index", "-z", "--stdin"],
            input="\0".join(sorted(targets)),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return set()
    return {name.casefold() for name in done.stdout.split("\0") if name}


#: Resolve a repository path case-insensitively, one component at a time. Only a fallback for when
#: `_shipped_paths` cannot reach git: the regex is `(?i)` while Windows compares paths
#: case-insensitively and Linux does not, so a bare `Path.exists()` makes the same document pass on
#: Windows and fail on Linux CI (hit for real on 2026-09-23: `docs/packaging.md` wrote
#: `Packaging/linux/NMRForge.spec`).
def _path_resolves_case_insensitively(base: Path, target: str) -> bool:
    """Walk ``target`` from ``base`` one component at a time, ignoring case."""
    current = base
    for part in Path(target).parts:
        if not current.is_dir():
            return False
        found = next(
            (child for child in current.iterdir() if child.name.casefold() == part.casefold()),
            None,
        )
        if found is None:
            return False
        current = found
    return current.exists()


def test_public_docs_do_not_reference_documents_that_are_not_shipped() -> None:
    """Every repository path a public document names must really ship - plain text included."""
    public = ROOT / "publish" if _private_trunk() else ROOT
    if not public.is_dir():
        pytest.skip("the public snapshot is not present (VM/CI)")
    shipped = _shipped_paths(public)
    references: list[tuple[str, str]] = []  # (document, as written)
    for path in sorted(public.rglob("*.md")):
        if PRIVATE_SCAN_SKIP & set(path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # markdown-link targets are covered by test_relative_links_in_public_docs_resolve
        body = LINK_RE.sub(" ", text)
        references += [
            (path.relative_to(public).as_posix(), match.group(0))
            for match in PLAIN_DOC_REF_RE.finditer(body)
        ]
    if shipped is None:  # no git: fall back to a case-insensitive filesystem lookup
        missing = [
            (where, target)
            for where, target in references
            if not _path_resolves_case_insensitively(public, target)
            and not _path_resolves_case_insensitively((public / where).parent, target)
        ]
    else:
        missing = [
            (where, target) for where, target in references if target.casefold() not in shipped
        ]
        ignored = _ignored_paths(public, {target for _, target in missing})
        missing = [
            (where, target) for where, target in missing if target.casefold() not in ignored
        ]
    offenders = [f"{where} -> {target}" for where, target in missing]
    assert not offenders, (
        "public docs reference files that the snapshot does not ship: "
        + "; ".join(sorted(set(offenders))[:8])
    )


#: Real sample names, developer-machine and VM paths: the public tree (including the comments
#: and docstrings of scripts, packaging and tests) must never carry them. Until 2026-09-22 the
#: check only covered the runtime packages' .py files and missed exactly these places.
def _marker(*parts: bytes) -> bytes:
    """Assemble a marker - this test file itself lives in the public tree, so the markers
    must not appear verbatim here."""
    return b"".join(parts)


PRIVATE_MARKERS = (
    # public data is cited by its BMRB timedomain entry id, so sample names are no longer a
    # redaction term (2026-09-22, owner's call)

    _marker(b"/home/", b"nmr"),
    _marker(b"nmrforge-test-", b"artifacts"),
    _marker(b"~/NMRForge/", b"nmrforge"),
    _marker(b"C:\\", b"Users\\"),
    _marker(b"user", b"1"),
    _marker(b"dev@nmrforge", b".local"),
    _marker(b"127.0.0", b".1"),
    _marker(b"l", b"zj"),
)
#: Directories the scan skips: repository internals, build output, caches
PRIVATE_SCAN_SKIP = {
    ".git",
    "build",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".pytest_tmp",
    ".mypy_cache",
    ".tox",
    "htmlcov",
}


def test_public_tree_carries_no_private_markers() -> None:
    """The public tree must not carry real sample names, developer-machine or VM paths."""
    public = ROOT / "publish" if _private_trunk() else ROOT
    if not public.is_dir():
        pytest.skip("the public tree is not present (VM/CI)")
    offenders: list[str] = []
    for path in sorted(public.rglob("*")):
        if not path.is_file() or PRIVATE_SCAN_SKIP & set(path.parts):
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:  # an unreadable file is still a finding, not a crash
            offenders.append(
                f"{path.relative_to(public).as_posix()} <- unreadable ({exc.__class__.__name__})"
            )
            continue
        for marker in PRIVATE_MARKERS:
            if marker in data:
                offenders.append(f"{path.relative_to(public).as_posix()} <- {marker.decode()}")
    assert not offenders, "private markers in the public tree: " + "; ".join(offenders[:8])


def test_no_developer_absolute_paths_in_runtime_packages() -> None:
    offenders: list[str] = []
    for package in RUNTIME_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), start=1):
                for pattern in ABSOLUTE_PATH_PATTERNS:
                    if pattern.search(line):
                        offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")
    assert not offenders, "developer-specific paths in shipped code: " + "; ".join(offenders)


def test_ci_workflow_is_valid_and_declares_jobs() -> None:
    if not (ROOT / ".github").is_dir():
        pytest.skip("a mirror tree has no .github of its own; the repository root carries it")
    yaml = pytest.importorskip("yaml")
    workflow_path = ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow_path.is_file()
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    assert "jobs" in workflow
    jobs = workflow["jobs"]
    for job in ("static", "tests", "release-readiness", "wheel"):
        assert job in jobs, f"CI job missing: {job}"
    matrix = jobs["tests"]["strategy"]["matrix"]["python-version"]
    assert "3.12" in matrix and len(matrix) >= 2, matrix


def test_github_templates_exist() -> None:
    if not (ROOT / ".github").is_dir():
        pytest.skip("a mirror tree has no .github of its own; the repository root carries it")
    templates = ROOT / ".github" / "ISSUE_TEMPLATE"
    assert (templates / "bug_report.yml").is_file()
    assert (templates / "feature_request.yml").is_file()
    assert (templates / "data_processing_problem.yml").is_file()
    assert (templates / "config.yml").is_file()
    assert (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").is_file()


def test_wheel_ships_every_fingerprinted_file() -> None:
    """behavior_digest covers every file under the four behaviour trees, so an installed copy
    must be able to reproduce the same fingerprint.

    Drop one of them (typically a package README) and the wheel computes a digest that
    disagrees with ``nmrforge_api/compat_declaration.py``: ``compat_verified`` is then
    permanently false, which makes the engine stamp useless downstream. The
    translation-tooling lists (``source.json`` / ``converted.json``) are the opposite case -
    development-only, never shipped.
    """
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    packaged = data["tool"]["setuptools"]["package-data"]
    excluded = data["tool"]["setuptools"].get("exclude-package-data", {})

    missing: list[str] = []
    for package in ("core", "backend", "workflow", "nmrforge_api"):
        for path in sorted((ROOT / package).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix in (".py", ".pyc", ".pyo"):
                continue  # the code ships by itself, no declaration needed
            relative = path.relative_to(ROOT / package).as_posix()
            declared = packaged.get(package, ())
            dropped = excluded.get(package, ())
            if any(fnmatch.fnmatch(relative, pattern) for pattern in dropped):
                missing.append(f"{package}/{relative} (excluded from package-data)")
            elif not any(fnmatch.fnmatch(relative, pattern) for pattern in declared):
                missing.append(f"{package}/{relative}")
    assert not missing, (
        "files the behaviour fingerprint covers are missing from the wheel "
        "(the installed behavior_digest would disagree with the declaration): " + "; ".join(missing)
    )
    tooling = {"source.json", "converted.json"}
    locales = packaged.get("ui_support", ())
    undeclared = [
        path.name
        for path in sorted((ROOT / "ui_support" / "locales").glob("*.json"))
        if path.name not in tooling
        and not any(fnmatch.fnmatch(f"locales/{path.name}", pattern) for pattern in locales)
    ]
    assert not undeclared, "runtime language packs missing from the wheel: " + ", ".join(undeclared)
    for name in sorted(tooling):
        assert not any(
            fnmatch.fnmatch(f"locales/{name}", pattern) for pattern in locales
        ), f"translation-tooling list locales/{name} must not ship"


def test_example_scripts_compile_and_expose_main() -> None:
    examples = sorted((ROOT / "examples").glob("*.py"))
    assert examples, "examples/ has no scripts"
    for path in examples:
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")
        assert "def main(" in source, f"{path.name} has no main()"


def test_licence_state_is_explicit() -> None:
    licence_file = next(
        (ROOT / name for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")
         if (ROOT / name).is_file()),
        None,
    )
    options = _read("LICENSE_OPTIONS.md")
    readme = _read("README.md")
    assert "LGPL" in options, "LICENSE_OPTIONS.md must record the LGPL situation"
    if licence_file is None:
        assert "PySide6" in options, "LICENSE_OPTIONS.md must record the Qt binding dependency"
        assert "No licence has been chosen" in readme, (
            "while no LICENSE exists, README.md must state that the licence is undecided"
        )
        return

    # A licence was chosen: the places that state it must not drift apart, and the
    # LGPL must stay confined to the packaged distribution (its Qt/PySide6 libraries).
    text = licence_file.read_text(encoding="utf-8")
    assert "Apache License" in text and "Version 2.0, January 2004" in text, (
        "LICENSE must contain the full Apache-2.0 text, not just a one-line reference"
    )
    # Since 2026-09-21 LICENSE is the verbatim Apache-2.0 text and nothing else; the copyright
    # line, the SPDX identifier and the scope notes live in NOTICE, so that licence-detection
    # tools see a clean Apache-2.0 file (before the split, GitHub reported NOASSERTION).
    notice = _read("NOTICE")
    assert "SPDX-License-Identifier: Apache-2.0" in notice, (
        "NOTICE must carry the SPDX identifier the source is released under"
    )
    assert "Apache-2.0" in notice, "NOTICE must name the licence of the source"
    assert "SPDX-License-Identifier: LGPL" not in notice, (
        "the project's own licence must not be LGPL: LGPL applies only to bundled third-party "
        "libraries (see packaging/linux/THIRD_PARTY_LICENSES/NOTICE.md)"
    )
    assert "under the terms of the GNU Lesser General Public License" not in notice, (
        "the licence grant must not be phrased as an LGPL grant"
    )
    declared = str(tomllib.loads(_read("pyproject.toml"))["project"].get("license", ""))
    assert "Apache-2.0" in declared, "pyproject.toml must declare the same licence as LICENSE"
    assert "Apache-2.0" in readme, "README.md must state the licence that applies to the source"
    assert "LGPL" in readme, (
        "README.md must state that LGPL applies to the bundled Qt/PySide6 in the AppImage"
    )
    assert "LICENSE_OPTIONS.md" in readme, "README.md must link the licence reasoning"
    assert "NOTICE" in readme, "README.md must point at NOTICE for the copyright line"
    bundled = ROOT / "packaging" / "linux" / "THIRD_PARTY_LICENSES" / "LGPL-3.0.txt"
    assert bundled.is_file(), (
        "the AppImage must keep shipping the LGPL text for the Qt/PySide6 libraries it bundles"
    )


def test_citation_metadata_matches_the_version() -> None:
    """CITATION.cff must track core.__version__; the release recipe bumps them together."""
    import core

    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(_read("CITATION.cff"))
    assert data["version"] == core.__version__, (
        f"CITATION.cff says {data['version']}, core.__version__ is {core.__version__}"
    )
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(data["date-released"])), data["date-released"]
    assert data["license"] == "Apache-2.0"
    assert data["repository-code"] == "https://github.com/RociferX/nmrforge"


def test_third_party_inventory_names_external_engines() -> None:
    text = _read("THIRD_PARTY.md")
    for name in ("NMRPipe", "SMILE", "PySide6", "nmrglue"):
        assert name in text, f"THIRD_PARTY.md does not mention {name}"


def test_changelog_keeps_an_unreleased_section() -> None:
    if not (ROOT / "CHANGELOG.md").is_file():
        pytest.skip("the public snapshot does not publish the internal CHANGELOG")
    text = _read("CHANGELOG.md")
    assert "## [Unreleased]" in text
    for section in ("### Added", "### Changed", "### Fixed"):
        assert section in text, f"CHANGELOG.md is missing {section}"


def test_no_raw_nmr_binary_data_in_repository() -> None:
    """Fixtures must stay tiny text headers; raw datasets belong outside the repository."""
    violations: list[str] = []
    for path in sorted((ROOT / "tests" / "fixtures").rglob("*")):
        if not path.is_file():
            continue
        if path.stat().st_size > 64 * 1024:
            violations.append(f"{path.relative_to(ROOT)} is {path.stat().st_size} bytes")
        if path.name in {"ser", "fid"} or path.suffix.lower() in {".ft2", ".ft3", ".ucsf", ".zip"}:
            violations.append(f"{path.relative_to(ROOT)} looks like raw data")
    assert not violations, "raw data committed as fixture: " + "; ".join(violations)


def test_readme_gui_asset_exists() -> None:
    assert (ROOT / "gui" / "assets" / "nmrforge.png").is_file()
    assert (ROOT / "packaging" / "linux" / "icons" / "nmrforge.png").is_file()


def test_examples_are_referenced_by_the_readme() -> None:
    readme = _read("README.md")
    assert "examples/quickstart.py" in readme
    assert "examples/make_synthetic_dataset.py" in readme

def test_run_provenance_records_the_git_commit_when_available() -> None:
    """A run record must be traceable to the code state that produced it."""
    from core.version import git_commit, tool_versions

    versions = tool_versions()
    commit = git_commit()
    if commit:
        assert versions["git_commit"] == commit
        assert versions["git_commit_dirty"] in {"0", "1"}
        assert len(commit) == 40
    else:
        # Frozen (AppImage) or no git: the key is absent rather than "unknown".
        assert "git_commit" not in versions
        assert "git_commit_dirty" not in versions


def test_benchmark_output_schema_columns_are_documented() -> None:
    if not (ROOT / "benchmarks").is_dir():
        pytest.skip("benchmarks/ is not shipped in this tree (public repository)")
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "nmrforge_benchmarks", ROOT / "benchmarks" / "run_benchmarks.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    readme = _read("benchmarks/README.md")
    for column in module.COLUMNS:
        assert column in readme, f"benchmarks/README.md does not document column {column}"
    # The framework must not ship results.
    assert not (ROOT / "benchmark_results.csv").exists()


@pytest.mark.parametrize("mode", ["2d", "3d", "all"])
@pytest.mark.parametrize("explicit_root", [False, True])
def test_zero_fill_validation_cli_routes_output_and_datasets(
    tmp_path, monkeypatch, mode, explicit_root,
) -> None:
    """Exercise main(), not just --help: missing Namespace fields must be caught."""
    from scripts import vm_validate_zero_fill as script

    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(script, "run_2d", lambda dataset, root: calls.append(("2d", dataset, root)))
    monkeypatch.setattr(script, "run_3d", lambda dataset, root: calls.append(("3d", dataset, root)))
    args = [mode, "--dataset-2d", "input2d", "--dataset-3d", "input3d"]
    output = Path("chosen-output") if explicit_root else Path("outputs/zero-fill-validation")
    if explicit_root:
        args += ["--root", str(output)]
    assert script.main(args) == 0
    assert output.is_dir()
    modes = ["2d", "3d"] if mode == "all" else [mode]
    assert calls == [(m, Path("input" + m), output) for m in modes]


def test_benchmark_results_snapshot_is_documented() -> None:
    """RESULTS.md is a dated snapshot: it must state its scope and point at the real run."""
    if not (ROOT / "benchmarks").is_dir():
        pytest.skip("benchmarks/ is not shipped in this tree (public repository)")
    text = _read("benchmarks/RESULTS.md")
    assert "run_benchmarks.py" in text and "--repeats" in text
    assert "skipped_no_engine" in text and "skipped_engine_available_not_measured" in text
    assert "vm_realdata_report.py" in text, (
        "the snapshot must point at the script that measures the engine-dependent rows"
    )


def test_release_documents_the_appimage_and_its_language_switch() -> None:
    """One AppImage, language chosen at run time; the README and the gate record say so."""
    # the version has exactly one source; the gate record (private) must name the artefact
    found = re.search(r'__version__ = "([^"]+)"', _read("core/__init__.py"))
    assert found is not None
    released = found.group(1)
    if _private_trunk():
        checklist = _read("APPIMAGE_RELEASE_CHECKLIST.md")
        assert "Apache-2.0" in checklist and "LGPL-3.0" in checklist
        assert "APPIMAGE_RELEASE_CHECKLIST.md" in _read("RELEASE_CHECKLIST_v0.9.0.md")
        assert f"NMRForge-{released}-x86_64.AppImage" in checklist
        # 2026-09-21: a single artefact, the edition suffix is gone for good
        assert "单产物" in checklist
        script = _read("packaging/linux/build_appimage.sh")
        assert "APPIMAGE_SUFFIX" not in script and "APPIMAGE_EDITION" not in script
    # the README points at the release page instead of claiming there is no binary
    release_note = _read("README.md")
    assert "AppImage" in release_note
    assert "releases" in release_note.lower()
    claims = "\n".join(
        _read(name) for name in (
            "README.md", "docs/getting-started.md", "docs/installation.md",
            "docs/gui.md", "docs/faq.md", "docs/cli.md",
        )
    )
    stale_claims = (
        "AppImage (recommended)",
        "supported distribution is a single-file Linux AppImage",
        "users: the supported distribution",
    )
    for stale in stale_claims:
        assert stale not in claims
