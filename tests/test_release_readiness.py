"""Release-readiness regression tests.

These do not test NMR processing. They lock down the properties that make the repository
publishable, so that a later change cannot silently break them:

- the version has exactly one source;
- the README and the public docs do not point at files that do not exist;
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

import re
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
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "THIRD_PARTY.md",
    "LICENSE_OPTIONS.md",
    "CITATION.cff",
    "RELEASE_CHECKLIST_v0.9.0.md",
    "APPIMAGE_RELEASE_CHECKLIST.md",
    "PUBLIC_RELEASE_AUDIT.md",
]

# 运行期包:不得出现开发机绝对路径。
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
    yaml = pytest.importorskip("yaml")
    workflow_path = ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow_path.is_file()
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    assert "jobs" in workflow
    jobs = workflow["jobs"]
    for job in ("static", "tests", "release-readiness"):
        assert job in jobs, f"CI job missing: {job}"
    matrix = jobs["tests"]["strategy"]["matrix"]["python-version"]
    assert "3.12" in matrix and len(matrix) >= 2, matrix


def test_github_templates_exist() -> None:
    templates = ROOT / ".github" / "ISSUE_TEMPLATE"
    assert (templates / "bug_report.yml").is_file()
    assert (templates / "feature_request.yml").is_file()
    assert (templates / "data_processing_problem.yml").is_file()
    assert (templates / "config.yml").is_file()
    assert (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").is_file()


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
    assert "Apache-2.0" in text, "LICENSE must state the SPDX identifier it is released under"
    grant = text.split("=====")[0]
    assert "SPDX-License-Identifier: Apache-2.0" in grant
    assert "SPDX-License-Identifier: LGPL" not in grant, (
        "the project's own licence must not be LGPL: LGPL applies only to bundled third-party "
        "libraries (see packaging/linux/THIRD_PARTY_LICENSES/NOTICE.md)"
    )
    assert "under the terms of the GNU Lesser General Public License" not in grant, (
        "the licence grant must not be phrased as an LGPL grant"
    )
    declared = str(tomllib.loads(_read("pyproject.toml"))["project"].get("license", ""))
    assert "Apache-2.0" in declared, "pyproject.toml must declare the same licence as LICENSE"
    assert "Apache-2.0" in readme, "README.md must state the licence that applies to the source"
    assert "LGPL" in readme, (
        "README.md must state that LGPL applies to the bundled Qt/PySide6 in the AppImage"
    )
    assert "LICENSE_OPTIONS.md" in readme, "README.md must link the licence reasoning"
    bundled = ROOT / "packaging" / "linux" / "THIRD_PARTY_LICENSES" / "LGPL-3.0.txt"
    assert bundled.is_file(), (
        "the AppImage must keep shipping the LGPL text for the Qt/PySide6 libraries it bundles"
    )


def test_third_party_inventory_names_external_engines() -> None:
    text = _read("THIRD_PARTY.md")
    for name in ("NMRPipe", "SMILE", "PySide6", "nmrglue"):
        assert name in text, f"THIRD_PARTY.md does not mention {name}"


def test_changelog_keeps_an_unreleased_section() -> None:
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


def test_source_release_keeps_appimage_as_deferred_distribution() -> None:
    """Prevent a future edit from advertising unverified binaries as this release."""
    checklist = _read("APPIMAGE_RELEASE_CHECKLIST.md")
    assert "DEFERRED" in checklist
    assert "Apache-2.0" in checklist and "LGPL-3.0" in checklist
    assert "APPIMAGE_RELEASE_CHECKLIST.md" in _read("RELEASE_CHECKLIST_v0.9.0.md")
    assert "AppImage is not included" in _read("README.md")
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
