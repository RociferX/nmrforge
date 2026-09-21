# FAQ

### Is nmrForge a replacement for NMRPipe?

No, and the design assumes it never will be. nmrForge decides *what* to run and records *why*,
then drives NMRPipe to do the processing. You need an NMRPipe installation.

### Do I need Python to use nmrForge?

Not for the AppImage: it bundles Python and Qt. For the source path you need Python 3.12+ and the
editable repository checkout.

### Can I run it on Windows or macOS?

The GUI and test suite run on Windows and Linux. macOS is currently untested and has no CI.
Both are available: a Linux AppImage (v0.11.0, interface language switched at run time) or a source install; on Windows use
the source install.

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

Yes - English is the interface's source language, so the menus, panel labels, statuses and
messages are English by default. The same build also speaks Chinese: the interface follows the
system language, or you can pin it with `NMRFORGE_LANG=zh` (the Chinese text lives in
`ui_support/locales/zh.json`). One code base, two languages; a Chinese edition of the
**documentation** is in `Chinese_version/`.

**You can also change it inside the GUI**: `Settings -> Software settings -> Interface
language`, choosing "Follow the system language / Chinese / English"; it is saved to the local
override config and takes effect after a restart. The resolution order is "explicit setting,
then the `NMRFORGE_LANG` environment variable, then the preference from the settings, then the
system locale (QLocale / `LANG` / `LC_ALL`), then the default of this tree". `NMRFORGE_LANG`
deliberately comes before the setting so that `NMRFORGE_LANG=zh ./NMRForge.AppImage` keeps
overriding a stale setting for one run - while `LANG=en_US.UTF-8` is only the system locale and
does **not** override the Chinese you picked in the settings.

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