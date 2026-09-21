# Pull request

## What changed

<!-- A short description of the change, in terms of behaviour. -->

## Why

<!-- The problem this solves. Link an issue if there is one: "Closes #123". -->

## How it was verified

<!--
Which tests did you run? If the change touches processing, state which of the four paths it was
checked against (2D/3D x uniform/NUS). If it needs a real NMRPipe run, say what was run and where.
-->

- [ ] `python -m pytest -q` passes
- [ ] `python -m ruff check .` passes
- [ ] Tested with a real dataset (describe below), or not applicable

Test command(s) and result:

```text

```

## Backward compatibility

<!--
Does this change the meaning of existing parameters, project files, peak tables, study records
or the scripting API? If yes, say what breaks and how a user should migrate. If a reference
spectrum must be rebuilt, say so.
-->

## Screenshots (GUI changes only)

<!--
Screenshots must not contain unpublished data, sample names, user names or laboratory paths.
-->

## Checklist

- [ ] No secrets, credentials or machine-specific absolute paths were added
- [ ] No raw or unpublished NMR data was added to the repository
- [ ] `CHANGELOG.md` updated if the change is user-visible
- [ ] Documentation updated (including the public docs under `docs/`) where behaviour changed
- [ ] Third-party dependency changes are reflected in `THIRD_PARTY.md`