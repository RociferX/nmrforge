# Source release checklist - v0.9.0

Status: **source release candidate ready locally; external publication not executed.**

This checklist covers the Git source repository only. No AppImage, wheel, DOI or Zenodo record is
part of this release. Binary preparation is intentionally separate in
[APPIMAGE_RELEASE_CHECKLIST.md](APPIMAGE_RELEASE_CHECKLIST.md).

## Source release boundary

- [x] Version is `0.9.0` from the single source `core.__version__`.
- [x] nmrForge source licence is Apache-2.0 and the copyright holder is recorded.
- [x] PySide6/Qt and every other dependency retain their own licences.
- [x] The AppImage is deferred and documentation does not advertise a binary download.
- [x] No wheel or non-editable pip install is presented as supported.
- [x] NMRPipe, SMILE and research data are not bundled.
- [x] Author identity and public repository URL are populated; DOI and affiliation may be added later.

## Privacy, history and repository shape

- [x] `publish/` is the filtered real history on branch `main`, with only the public author identity.
- [x] Internal management material is excluded from every public revision.
- [x] The current tree and every Git object are checked for the release's sensitive patterns.
- [x] No raw NMR data, credentials, private keys or private remote is included.
- [x] The public repository has no remote, tag or release configured locally.

## Engineering gates

- [ ] Full pytest suite passes on the final public commit.
- [ ] Ruff passes on the final public commit.
- [ ] Release-readiness tests pass on the final public commit.
- [ ] Source install succeeds from a clean clone on Python 3.12 and 3.13.
- [ ] Hosted GitHub CI passes after the first push.
- [x] Core/API import without Qt, CLI help and the synthetic quickstart work locally.
- [x] The existing NMRPipe development-host regression is recorded separately from hosted CI.

## Owner actions for first publication

- [ ] Confirm the repository visibility and create the empty GitHub repository.
- [ ] Push `publish/` branch `main` without force.
- [ ] Confirm the first hosted CI matrix is green.
- [ ] Enable branch protection requiring CI.
- [ ] Review the rendered README, licence, citation and security pages on GitHub.
- [ ] Create the `v0.9.0` source tag/release only after those checks.
- [ ] Do not attach an AppImage; link its deferred checklist if users ask about binaries.

## GitHub repository settings

```text
Name:         nmrforge
Description:  Automation, parameter optimisation and quality control for Bruker multidimensional
              NMR data (NMRPipe / SMILE), with reproducible run records.
Default:      main
Topics:       nmr, nmrpipe, bruker, nmr-spectroscopy, non-uniform-sampling, smile,
              peak-picking, quality-control, scientific-software, python, pyside6
```

The local checks can establish a release candidate. The hosted CI checkbox necessarily remains
open until the first external push; it is a post-push publication gate rather than a reason to
publish binaries.
