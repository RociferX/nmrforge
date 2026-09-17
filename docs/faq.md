# FAQ

### Is nmrForge a replacement for NMRPipe?

No, and the design assumes it never will be. nmrForge decides *what* to run and records *why*,
then drives NMRPipe to do the processing. You need an NMRPipe installation.

### Do I need Python to use nmrForge?

Yes for v0.9.0, which is a source-only release: install Python 3.12+ and use the editable
repository checkout. A future AppImage may bundle Python and Qt, but no binary is distributed
with this release.

### Can I run it on Windows or macOS?

The GUI and test suite run on Windows and Linux. macOS is currently untested and has no CI.
v0.9.0 is installed from source; a Linux AppImage is deferred.

### Does nmrForge send my data anywhere?

No. There is no telemetry, upload or account requirement. Processing is local, and the
application itself makes no network calls. Development or future packaging may use network
access to install dependencies or fetch an AppImage runtime.

### Does it modify my raw data?

Not on purpose, and never silently. Where acquisition headers must be adjusted (for example to
recompute the sampling grid after bad points were removed), the original is backed up as `.bak`
on first modification, and work is done on copies rather than in-place. Automatic bad-point
*repair* is optional; detection is unconditional, and findings are reported with indices and
metrics before any decision is made. Deleting a project in the GUI moves it to the operating
system trash rather than erasing it.

### Does it handle non-uniform sampling?

Yes, for 2D and 3D, using SMILE. 3D NUS processing works; the 3D SMILE parameter-optimisation UI
is currently hidden, and the SMILE sweep/"re-run by rank" controls are exposed for 2D NUS only.

### Why is batch processing 2D-only?

Because 3D (especially 3D NUS/SMILE) memory risk on target machines was not resolved, and a
process that can take a host down is not something to run unattended. Non-2D datasets are skipped
with a reason rather than processed with an approximation. Batch also only applies the group
configuration; it does not pick per-dataset parameters, so differences between outputs reflect
the data.

### Why does it refuse to process when sampling is "uncertain"?

Because uniform and NUS change the meaning of every processing parameter. Guessing there produces
a spectrum that looks processed and is quietly wrong. Resolve the metadata conflict first; the
log states which rule fired.

### Is there an English interface?

Not yet. The GUI is Chinese-only today; the code, the documentation, the CLI output and the
scripting API are English. An English UI translation is a planned improvement.

### What happened to the HSQC CSP analysis features?

They were removed in September 2026 by decision, and the removal (including how to recover the
code from git history) is recorded in
the CHANGELOG entry for the 2026-09-12 analysis removal.
Downstream CSP analysis is expected to be done by a separate project that consumes
`nmrforge_api` output.

### How is the version number managed?

`core/__init__.py` defines `__version__`, and that is the only place it exists. `pyproject.toml`
reads it dynamically, the AppImage build script reads it, and run records store it - so the
version in the application, the package and the provenance record cannot drift apart.

### How do I cite nmrForge?

Citation metadata is being finalised in [CITATION.cff](../CITATION.cff); the author list and
affiliation are not fixed yet. Until a release with a DOI exists, cite the repository URL. If you
publish results, also cite NMRPipe and SMILE - see [THIRD_PARTY.md](../THIRD_PARTY.md).

### Can I use nmrForge commercially?

The licence has not been chosen, so the repository is currently "all rights reserved" and you
should not redistribute it. The GUI now depends on PySide6 (whose options include LGPL-3.0) instead
of PyQt6 (GPL-3.0-only), so a permissive licence is possible in principle - but it still has to be
chosen, and the third-party audit has to be rerun first. See
[LICENSE_OPTIONS.md](../LICENSE_OPTIONS.md).

### How do I know a published result is reproducible from nmrForge?

Each run records the resolved parameters, the scripts used, the software version, the external
tool versions and the warnings, and the generated scripts and spectra are kept. That is the
mechanism; it is not a claim that every historical run was recorded perfectly. See
[processing-model.md](processing-model.md) and
[external-api/06-outputs-and-records.md](external-api/06-outputs-and-records.md).