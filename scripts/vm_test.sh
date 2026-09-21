#!/usr/bin/env bash
# Single entry point for the full VM test run: bytecode / pytest / ruff caches go to
# $HOME/.cache/nmrforge/vm-test-artifacts/,
# keeping the ~/NMRForge main directory clean (Architect, 2026-08-18).
#
# Usage:
#   bash scripts/vm_test.sh                  # full run
#   bash scripts/vm_test.sh tests/test_x.py  # one file (extra pytest args pass through)
#
# Environment overrides (used by the CI real-engine job; day-to-day use is unchanged):
#   NMRFORGE_TEST_ROOT      repository under test (default $HOME/NMRForge; CI points it
#                           at the checkout for the commit being tested)
#   NMRFORGE_TEST_PYTHON    interpreter (default $ROOT/nmrforge/bin/python, the VM venv)
#   NMRFORGE_TEST_ARTIFACTS log/cache directory (default $HOME/.cache/nmrforge/vm-test-artifacts)
set -euo pipefail

ART="${NMRFORGE_TEST_ARTIFACTS:-$HOME/.cache/nmrforge/vm-test-artifacts}"
REPO="${NMRFORGE_TEST_ROOT:-$HOME/NMRForge}"
PYTHON="${NMRFORGE_TEST_PYTHON:-$REPO/nmrforge/bin/python}"
mkdir -p "$ART/pycache" "$ART/ruff"
export PYTHONPYCACHEPREFIX="$ART/pycache"   # redirect __pycache__
export RUFF_CACHE_DIR="$ART/ruff"           # redirect the ruff cache
export QT_QPA_PLATFORM=offscreen
# A fresh basetemp per run (2026-09-20): the shared "$ART/pytest" used to be wiped by a
# second run (for example two windows validating on the VM at the same time), producing
# failures unrelated to the code -- tests/test_logging_setup.py, tests/test_nmrforge_api.py
# and tests/test_test_categories.py were all seen red when concurrent and green alone.
# Each run now gets its own directory, removed on exit; the parent stays clean.
BASE="$(mktemp -d "$ART/pytest-XXXXXXXX")"
cleanup() { rm -rf "$BASE"; }
trap cleanup EXIT
cd "$REPO"
if [ ! -x "$PYTHON" ]; then
  echo "interpreter not executable: $PYTHON (set NMRFORGE_TEST_PYTHON)" >&2
  exit 127
fi
"$PYTHON" -m pytest \
  -o addopts='' -p no:cacheprovider \
  --basetemp="$BASE" -q --tb=no "$@"
