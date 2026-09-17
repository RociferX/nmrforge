# Security policy

## Reporting a vulnerability

Please **do not** open a public issue for a security problem.

Report it privately through GitHub's "Report a vulnerability" feature on the repository's Security
tab (Security Advisories). If that is not available, contact the maintainer directly:

The repository maintainer is **@RociferX** on GitHub. Use GitHub's private vulnerability
reporting (<https://github.com/RociferX/nmrforge/security/advisories/new>) — it is the preferred
channel and keeps the report private without publishing an email address. If the repository is not
yet public, or advisories are unavailable, contact @RociferX through GitHub and ask for a private
channel before sharing details.

Please include: what you found, how to reproduce it, the affected version, and what an attacker
could achieve.

## What counts as a security issue here

nmrForge is a local desktop and command-line application that processes files you point it at.
The realistic classes of issue are:

- **Path handling**: writing outside the project or work directory, following a symbolic link into
  an unintended location, or overwriting a file it should not.
- **Command execution**: constructing a shell command from dataset-controlled values in a way
  that allows injection. Processing runs C-shell scripts through `csh`; the argument handling
  around that boundary is security-relevant.
- **Unsafe data handling**: something that damages raw acquisition data rather than working on a
  copy, or that removes data without a backup.
- **Dependency issues**: a vulnerable version pinned in `pyproject.toml` or a compromised
  update path.

## What is not a security issue

- A wrong scientific result, a bad phase, or a failed reconstruction. Those are bugs - please use
  the data-processing issue template.
- Files you process being readable by other accounts on a shared machine; that is operating
  system configuration, not this application.
- The licence status of the project. See [LICENSE_OPTIONS.md](LICENSE_OPTIONS.md).

## Please do not put secrets in a public issue

Do not paste credentials, tokens, SSH keys, private repository URLs, internal hostnames, or
machine paths containing a user name. If a report requires that context, say so and a private
channel will be arranged.

## Data privacy

nmrForge processes data locally and makes no network calls of its own. It has no telemetry and no
account system. What you process stays on your machine, with one caveat worth stating explicitly:
processed spectra, scripts and run records are written into the study/project directories you
chose, which may be a shared or backed-up location. Review those directories before sharing them.

## Supported versions

Only the latest release is supported with security fixes:

| Version | Supported |
| --- | --- |
| latest release | yes |
| `master` | yes (development) |
| older releases | no |

## Status of this file

This policy was written during public-release preparation (2026-09). The reporting channel is
**GitHub private vulnerability reporting** on <https://github.com/RociferX/nmrforge>, with the
maintainer (@RociferX) as the fallback contact; no email address is published. See
[PUBLIC_RELEASE_AUDIT.md](PUBLIC_RELEASE_AUDIT.md) G.5.