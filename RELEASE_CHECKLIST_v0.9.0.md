# Release checklist - v0.9.0 (first public version)

Status: **prepared, not executed.** No tag, no GitHub release, no Zenodo record and no DOI has
been created. This checklist is the gate between the current private repository and a public
v0.9.0.

Working notes:

- The version currently in `core/__init__.py` is `0.2.199`. The first public version is planned as
  **0.9.0** (explicitly not 1.0.0: the scripting API is not frozen). Bumping the version is a
  one-line change in `core/__init__.py`, because that is the single source of truth - but make it
  as part of the release, not before, so the version never overstates readiness.
- Release artefacts: the Linux AppImage is the supported distribution
  (`packaging/linux/build_appimage.sh`). A wheel is not a supported distribution path (PACK-015).

## 1. Legal and ownership

- [ ] **IP ownership confirmed.** Who owns the copyright, and does the institute/laboratory claim
      it? Funding-agreement redistribution conditions checked.
- [ ] **Author list, order and affiliation confirmed**, and each author agrees to be listed.
- [x] **LICENSE selected and committed (2026-09-16): source Apache-2.0, LGPL only for the bundled
      Qt/PySide6.** Root `LICENSE` holds the Apache-2.0 text plus a project notice ("Copyright 2026
      NMRForge contributors"); `pyproject.toml` declares `Apache-2.0` with the Apache classifier;
      README (EN/ZH), `CITATION.cff`, `.zenodo.json`, `THIRD_PARTY.md` and
      [LICENSE_OPTIONS.md](LICENSE_OPTIONS.md) state the same, and the guard test fails if the
      project's own licence claims LGPL or if the AppImage stops shipping the LGPL text. The PyQt6
      GPL-3.0-only obstacle had already been removed by the PySide6 migration.
- [x] **Third-party licences checked** against the actual installed versions, including what is
      bundled into the AppImage: `scripts/audit_third_party.py` (28 permissive, 4 weak copyleft,
      0 strong-copyleft-only on 2026-09-16) with the full inventory in
      [THIRD_PARTY.md](THIRD_PARTY.md) section 7.
- [x] **LGPL texts and notice shipped**: `packaging/linux/THIRD_PARTY_LICENSES/` is hash-checked by
      `scripts/check_third_party_licenses.py`, copied into the AppDir by the build script, and
      readable from the built AppImage with `--licenses`.
- [x] **Replace/relink route documented and supported** (`PYSIDE6_REQUIREMENT` et al. in
      `build_appimage.sh`, see `NOTICE.md`).
- [ ] **LGPL obligations reviewed by the IP owner** - the notice states the mechanism, not legal
      advice, and Qt's licensing FAQ is the authority on the relink obligation for a single-file
      AppImage.
- [ ] **`SECURITY.md` has a real private contact** (currently a placeholder).
- [ ] **`CODE_OF_CONDUCT.md` has a real reporting contact** (currently a placeholder).
- [ ] **`CITATION.cff` placeholders replaced** with real authors, repository URL and licence.

## 2. Security and privacy

- [ ] Security audit clear: no credentials, tokens, SSH keys or private URLs in the working tree.
- [ ] Git-history audit reviewed (nothing sensitive remains in earlier commits).
- [ ] **Unpublished identifiers reviewed.** The following are currently present in tracked files
      and must be accepted, sanitised or removed before the repository is public:
      - the uncertainty-study paths and the real sample identifier `sampleA.fid`
        in `docs/proposals/external-api/001-parameter-sweep-api.md` and
        `docs/tasks/2026-09-14-combination-independent-picking.md`;
      - laboratory dataset shorthand (`sampleA`, `sampleB`, `sampleC`, `sampleM`, ...) in
        `CHANGELOG.md`, several `docs/` files, and comments in `backend/script_generator.py`,
        `core/experiment/acquisition_mode_detector.py`, `core/processing/axes.py`;
      - the internal development host reference in `.codex/AGENTS.md`, `docs/GIT_WORKFLOW.md`
        and `docs/HANDOVER.md`.
- [ ] **Internal management docs reviewed** for public suitability (`docs/manager/`, `docs/tasks/`,
      `docs/AGENT_PROMPTS.md`, `docs/HANDOVER.md`, `docs/PROJECT_STATUS.md`, `.codex/`).
- [ ] **Example data publishable**: `examples/` and `tests/fixtures/` contain only synthetic or
      tiny header-only data.
- [ ] No raw NMR data anywhere in the repository or its history.

## 3. Code and packaging

- [ ] Version set to 0.9.0 in `core/__init__.py` (single source).
- [ ] `python -m pytest -q` green on the release commit.
- [ ] `tests/test_release_readiness.py` green.
- [ ] `python -m ruff check .` green.
- [ ] `python -m pip install -e ".[test]"` works from a clean clone.
- [x] AppImage builds from the release commit and starts (2026-09-16, user's Linux VM):
      `BUILD_EXIT=0`, `NMRForge-0.2.199-x86_64.AppImage` (~134 MB); smoke test with an isolated
      `HOME` passed: `--licenses` prints the shipped LGPL/GPL texts, NOTICE, PROVENANCE and
      BUILD_INFO; the application starts and stays in its event loop; the desktop entry is
      installed and `--remove-desktop` removes it. Remaining nuance: this was the build host,
      not a freshly provisioned third machine - a distribution test on an untouched machine is
      still worth doing before announcing.
- [ ] AppImage smoke test: launches, `--remove-desktop` works, `NMRFORGE_NO_DESKTOP=1` works.
- [ ] GUI launches (`python main.py`); CLI launches (`python -m nmrforge_api --help`).
- [ ] Core API imports without Qt (`import core, nmrforge_api`).
- [ ] Example workflow runs (`examples/make_synthetic_dataset.py` + `examples/quickstart.py`).
- [ ] README quick start is reproducible exactly as written.

## 4. CI

- [ ] CI workflow valid and green on `master`.
- [ ] Test matrix green (Python 3.12 and 3.13).
- [ ] Packaging-contract test green.
- [ ] Branch protection enabled after the repository exists (require CI to pass).
- [ ] README status badges added **after** the repository URL exists (a badge pointing at a
      non-existent repository is worse than no badge).

## 5. Documentation

- [ ] README first screen answers: what it is, what problem it solves, what it needs, what it can
      do, how to install, how to run in five minutes.
- [ ] Public docs under `docs/` match the shipped behaviour.
- [ ] CHANGELOG `[Unreleased]` section converted to `[0.9.0] - <date>`.
- [ ] GUI screenshots present, clean, and free of unpublished data, sample names, user names and
      laboratory paths.
- [ ] Known limitations stated rather than omitted (batch is 2D-only; 3D SMILE UI hidden;
      NMSPipe required; GUI is Chinese-only).

## 6. Publication (all manual, by the owner)

- [ ] GitHub repository created by the owner.
- [ ] **Visibility chosen by the owner** (this preparation never changes visibility).
- [ ] Remote added and `master` pushed by the owner.
- [ ] Release tag `v0.9.0` created and the GitHub release published.
- [ ] AppImage attached to the release.
- [ ] Zenodo connected / record created, DOI obtained, and `CITATION.cff` + `.zenodo.json`
      updated with the real DOI and licence.
- [ ] Announcement (if any) does not claim performance or accuracy that no benchmark supports.

## Sign-off

| Item | Owner decision | Date |
| --- | --- | --- |
| IP ownership | | |
| Author list | | |
| Licence | | |
| Visibility | | |
| Release approval | | |