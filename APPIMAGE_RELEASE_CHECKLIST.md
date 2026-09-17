# AppImage release checklist

Status: **DEFERRED. No AppImage is included in the 0.9.0 source release.**

The nmrForge source is licensed under Apache-2.0. A future AppImage will also distribute
PySide6/Qt and other third-party binaries. Those components retain their own licences; choosing
Apache-2.0 for nmrForge does not replace or weaken their terms. PySide6 provides an LGPL-3.0
option, so binary distribution has additional notice, licence-text, source/relink and verification
requirements. The exact obligations must be reviewed against the versions actually bundled.

The repository keeps the build recipe and compliance scaffolding so binary work can resume without
changing the source-release boundary:

- `packaging/linux/build_appimage.sh` and `NMRForge.spec` define the build;
- `packaging/linux/THIRD_PARTY_LICENSES/` holds the current LGPL/GPL texts, provenance and notice;
- `scripts/check_third_party_licenses.py` verifies those files;
- `scripts/audit_third_party.py` inventories the installed dependency set;
- `AppRun --licenses` exposes notices from a built artefact;
- the build supports replacement PySide6/Shiboken wheels for the documented relink route.

These files are preparation, not evidence that a current binary is ready. Before publishing any
AppImage, complete every item below against the final binary commit:

- [ ] Build from a clean checkout of the intended release tag.
- [ ] Save the exact Python and package versions used to build it.
- [ ] Rerun the dependency licence audit in that build environment.
- [ ] Confirm every bundled library's applicable licence and required notices/source offer.
- [ ] Review the Qt/PySide6 LGPL distribution and replacement/relink mechanism.
- [ ] Confirm the licence texts, notices, provenance and build information are inside the artefact.
- [ ] Verify `--licenses`, normal launch, desktop install/removal and the no-desktop option.
- [ ] Test the final artefact on a clean supported Linux machine other than the build host.
- [ ] Confirm the artefact version and embedded commit match the release tag.
- [ ] Record checksums and attach the AppImage only after binary release approval.

Until this checklist is complete, documentation and releases must describe installation from
source and must not offer an AppImage download.
