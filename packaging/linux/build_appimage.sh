#!/usr/bin/env bash
# Build the NMRForge AppImage (run on a Linux build host/VM; see docs/packaging.md)
# Requires: python3.12+, pip, appimagetool (https://github.com/AppImage/appimagetool/releases)
#
# Licence (LGPL) notes: the AppImage bundles PySide6/Qt, which counts as "distributing LGPL libraries", so
# the licence texts and notices must ship with the artefact, and recipients must be able to replace/relink the library. This script therefore:
#   - verifies the texts and hashes in packaging/linux/THIRD_PARTY_LICENSES before building;
#   - copies that directory into the AppDir (usr/share/doc/NMRForge/third-party/), so the artefact can be extracted;
#   - writes BUILD_INFO.txt recording version/commit/dependency versions as the pointer to the "corresponding source";
#   - supports rebuilding after replacing the default libraries with a self-built PySide6/shiboken6 (the LGPL replaceability mechanism):
#       PYSIDE6_REQUIREMENT="PySide6 @ file:///path/to/PySide6-....whl" \
#       EXTRA_PIP_ARGS="--find-links /path/to/wheels" \
#       bash packaging/linux/build_appimage.sh
#   see packaging/linux/THIRD_PARTY_LICENSES/NOTICE.md for details.
set -euo pipefail
cd "$(dirname "$0")/../.."

# Replacement libraries (empty = the versions declared in pyproject.toml)
PYSIDE6_REQUIREMENT="${PYSIDE6_REQUIREMENT:-}"
SHIBOKEN6_REQUIREMENT="${SHIBOKEN6_REQUIREMENT:-}"
EXTRA_PIP_ARGS="${EXTRA_PIP_ARGS:-}"
LICENSE_DIR="packaging/linux/THIRD_PARTY_LICENSES"

APP=NMRForge
ARCH="$(uname -m)"
# 2026-09-21 (user): the UI strings go through a runtime language layer, so **one** artefact is
# built. The language is decided at run time (system locale -> NMRFORGE_LANG/NMRFORGE_LANGUAGE ->
# this tree's ui_support/locales/default.json); there is no -en suffix and no edition field, and
# Chinese and English users download the same file.
# When building from a tarball (no .git), pass the commit in via NMRFORGE_BUILD_COMMIT
BUILD_COMMIT="${NMRFORGE_BUILD_COMMIT:-$(git rev-parse HEAD 2>/dev/null || echo unknown)}"
VERSION="$("${PYTHON:-python3}" -c 'from core import __version__; print(__version__)')"
# the default language this tree declares (public English tree = en, private trunk = zh); the
# system locale / NMRFORGE_LANG can still override it
DEFAULT_LANG="$("${PYTHON:-python3}" -c 'import json,pathlib; print(json.loads(pathlib.Path("ui_support/locales/default.json").read_text(encoding="utf-8"))["language"])' 2>/dev/null || echo unknown)"
BUILD_DIR="build/appimage"
APPDIR="$BUILD_DIR/$APP.AppDir"

# 0) licence-text integrity (missing or edited texts fail the build, so an artefact cannot silently omit a licence)
"${PYTHON:-python3}" scripts/check_third_party_licenses.py

rm -rf "$BUILD_DIR"
mkdir -p \
  "$APPDIR/usr/bin" \
  "$APPDIR/usr/share/applications" \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# 0.5) the machine-local config must not enter the artefact:
#      nmrforge_data/config/nmrforge.local.yaml is a
#      .gitignore'd, per-machine file and can carry the builder's absolute paths (for example
#      the data root configured on the build machine), which would hand third parties
#      information about the builder. It is moved aside for the build and restored after it,
#      so the NMRForge.spec datas cannot copy it into the AppDir. (At runtime the settings
#      live in ~/.config/NMRForge/, so this only decides whether the file ships.)
LOCAL_CFG="nmrforge_data/config/nmrforge.local.yaml"
LOCAL_CFG_STASH=""
restore_local_cfg() {
  if [ -n "$LOCAL_CFG_STASH" ] && [ -e "$LOCAL_CFG_STASH" ]; then
    mv "$LOCAL_CFG_STASH" "$LOCAL_CFG"
  fi
}
if [ -e "$LOCAL_CFG" ]; then
  LOCAL_CFG_STASH="$BUILD_DIR/nmrforge.local.yaml.stash"
  mv "$LOCAL_CFG" "$LOCAL_CFG_STASH"
  trap restore_local_cfg EXIT
  echo "the machine-local $LOCAL_CFG is moved out of the build tree and restored after"
fi


# 1) venv + dependencies (with replacement libraries installed first, so they satisfy the PySide6 requirement)
"${PYTHON:-python3}" -m venv "$BUILD_DIR/venv"
"$BUILD_DIR/venv/bin/pip" install --upgrade pip
if [ -n "$PYSIDE6_REQUIREMENT" ] || [ -n "$SHIBOKEN6_REQUIREMENT" ]; then
  echo "using substituted Qt bindings: ${PYSIDE6_REQUIREMENT:-'(default PySide6)'} ${SHIBOKEN6_REQUIREMENT:-''}"
  # shellcheck disable=SC2086
  "$BUILD_DIR/venv/bin/pip" install $EXTRA_PIP_ARGS \
    ${SHIBOKEN6_REQUIREMENT:+"$SHIBOKEN6_REQUIREMENT"} \
    ${PYSIDE6_REQUIREMENT:+"$PYSIDE6_REQUIREMENT"}
fi
# shellcheck disable=SC2086
"$BUILD_DIR/venv/bin/pip" install $EXTRA_PIP_ARGS -e .
"$BUILD_DIR/venv/bin/pip" install pyinstaller
echo "=== versions actually used by the artefact ==="
"$BUILD_DIR/venv/bin/pip" list --format=freeze | grep -Ei '^(PySide6|shiboken6|pyqtgraph|numpy)=' || true

# 2) PyInstaller packaging (collects the PySide6 plugins automatically)
"$BUILD_DIR/venv/bin/pyinstaller" --clean --noconfirm \
  --distpath "$BUILD_DIR/dist" packaging/linux/NMRForge.spec
cp -a "$BUILD_DIR/dist/$APP/." "$APPDIR/usr/bin/"

# 2.2) runtime-resource self-check: a missing resource fails the build here instead of on the
# user's machine (user, 2026-09-21)
# PyInstaller 6 puts the datas inside a contents directory (default _internal/, which is what
# sys._MEIPASS points at at run time), so locate that first; the flat layout is accepted too.
BUNDLE_DIR="$APPDIR/usr/bin"
if [ -d "$BUNDLE_DIR/_internal" ]; then
    BUNDLE_DIR="$BUNDLE_DIR/_internal"
fi
for resource in nmrforge_data/config nmrforge_data/presets nmrforge_data/tutorial gui/assets ui_support/locales; do
    if [ ! -e "$BUNDLE_DIR/$resource" ]; then
        echo "build failed: the artefact is missing the runtime resource $resource (PyInstaller datas did not apply)" >&2
        exit 1
    fi
done
for catalogue in "$BUNDLE_DIR/ui_support/locales"/*.json; do
    [ -f "$catalogue" ] || { echo "build failed: the language catalogue directory is empty" >&2; exit 1; }
done
echo "runtime-resource self-check passed: $BUNDLE_DIR ($(ls "$BUNDLE_DIR/nmrforge_data/presets"/*.yaml | wc -l) presets)"

# 2.5) licence texts, notices and build info are packed into the artefact too
DOC_DIR="$APPDIR/usr/share/doc/$APP"
mkdir -p "$DOC_DIR/third-party"
cp -a "$LICENSE_DIR"/. "$DOC_DIR/third-party/"
{
  echo "application : $APP"
  echo "version     : $VERSION"
  echo "languages   : en, zh (runtime: system locale, then NMRFORGE_LANG)"
  echo "default lang: $DEFAULT_LANG"
  echo "commit      : $BUILD_COMMIT"
  echo "worktree    : $(git status --porcelain --untracked-files=no 2>/dev/null | wc -l) modified tracked file(s)"
  echo "built       : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "build host  : $(uname -srm)"
  echo "python      : $("$BUILD_DIR/venv/bin/python" -V)"
  echo "packages    :"
  "$BUILD_DIR/venv/bin/pip" list --format=freeze | grep -Ei '^(PySide6|PySide6_Essentials|PySide6_Addons|shiboken6|pyqtgraph|numpy|scipy|matplotlib|nmrglue)=' | sed 's/^/  /'
} > "$DOC_DIR/BUILD_INFO.txt"
# the dist-info/licenses of every wheel packed into _internal is kept too (licence texts of the remaining dependencies)


# 2.6) frozen start-up smoke (2026-09-21): the resource self-check only proves the files are in
#      the artefact, not that it runs. Start the packaged executable offscreen and require it to
#      survive 20 s (exit code 124 from timeout); a crash or a missing module fails immediately
#      and prints the log. A dedicated HOME keeps the build machine clean.
#      (Excluding PySide6.QtTest was found this way: every file was present, yet qtcompat hit
#      ModuleNotFoundError as soon as it imported it.)
SMOKE_HOME="$BUILD_DIR/smoke-home"
mkdir -p "$SMOKE_HOME"
set +e
HOME="$SMOKE_HOME" QT_QPA_PLATFORM=offscreen NMRFORGE_NO_DESKTOP=1 \
    timeout 20 "$APPDIR/usr/bin/NMRForge" > "$BUILD_DIR/frozen-smoke.log" 2>&1
SMOKE_STATUS=$?
set -e
if [ "$SMOKE_STATUS" -ne 124 ]; then
    echo "build failed: the frozen artefact did not survive the start-up smoke (exit $SMOKE_STATUS, expected 124 from timeout)" >&2
    tail -20 "$BUILD_DIR/frozen-smoke.log" >&2
    exit 1
fi
echo "frozen start-up smoke passed (offscreen, running after 20 s)"

# 3) desktop entry and icon
install -m 0644 packaging/linux/NMRForge.desktop "$APPDIR/usr/share/applications/"
install -m 0644 packaging/linux/icons/nmrforge.png \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps/"
cp "$APPDIR/usr/share/applications/NMRForge.desktop" "$APPDIR/NMRForge.desktop"
cp "$APPDIR/usr/share/icons/hicolor/256x256/apps/nmrforge.png" "$APPDIR/nmrforge.png"

# 3.5) AppRun entry point (appimagetool does not generate it; the AppImage starts the program through it)
cat > "$APPDIR/AppRun" <<'APPRUN_EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"

# Desktop-integration self-install/removal (0.2.199-patch29ew):
# - on first run / after a move, installs the desktop entry and icon into ~/.local/share, with Exec/TryExec pointing at
#   the AppImage's real path (the menu entry hides itself once the AppImage file is deleted);
# - running --remove-desktop (or --uninstall-desktop) removes the desktop entry and icon;
# - setting NMRFORGE_NO_DESKTOP=1 skips the self-install.
if [ "$1" = "--licenses" ] || [ "$1" = "--third-party-licenses" ]; then
    DOC="$HERE/usr/share/doc/NMRForge"
    echo "NMRForge third-party licences and build information"
    echo "--------------------------------------------------"
    echo "This AppImage bundles Qt and PySide6, used under the LGPL-3.0 option."
    echo "Full texts, the notice, and the build provenance:"
    echo "  $DOC/third-party/LGPL-3.0.txt"
    echo "  $DOC/third-party/GPL-3.0.txt   (LGPL-3.0 incorporates GPL-3.0 by reference)"
    echo "  $DOC/third-party/NOTICE.md"
    echo "  $DOC/third-party/PROVENANCE.txt"
    echo "  $DOC/BUILD_INFO.txt"
    echo
    echo "How to replace or relink the LGPL libraries: see NOTICE.md"
    echo "--- NOTICE.md ---"
    cat "$DOC/third-party/NOTICE.md" 2>/dev/null || echo "(NOTICE.md missing from this build)"
    exit 0
fi

if [ "$1" = "--remove-desktop" ] || [ "$1" = "--uninstall-desktop" ]; then
    rm -f "$HOME/.local/share/applications/nmrforge.desktop"
    rm -f "$HOME/.local/share/icons/hicolor/256x256/apps/nmrforge.png"
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
    fi
    echo "removed the NMRForge desktop entry and icon (project data is untouched; deleting the AppImage file completes the uninstall)"
    exit 0
fi

if [ -z "${NMRFORGE_NO_DESKTOP:-}" ] && [ -n "${APPIMAGE:-}" ]; then
    APPIMG_PATH="$(readlink -f "$APPIMAGE")"
    DESKTOP_DIR="$HOME/.local/share/applications"
    ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
    DESKTOP_FILE="$DESKTOP_DIR/nmrforge.desktop"
    if [ ! -f "$DESKTOP_FILE" ] || ! grep -qF "Exec=$APPIMG_PATH" "$DESKTOP_FILE"; then
        mkdir -p "$DESKTOP_DIR" "$ICON_DIR"
        sed -e "s|^Exec=.*|Exec=\"$APPIMG_PATH\"|" \
            -e "1a TryExec=$APPIMG_PATH" \
            "$HERE/NMRForge.desktop" > "$DESKTOP_FILE"
        cp -f "$HERE/nmrforge.png" "$ICON_DIR/nmrforge.png"
        if command -v update-desktop-database >/dev/null 2>&1; then
            update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
        fi
        if command -v gtk-update-icon-cache >/dev/null 2>&1; then
            gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
        fi
    fi
fi

exec "$HERE/usr/bin/NMRForge" "$@"
APPRUN_EOF
chmod +x "$APPDIR/AppRun"

# 4) appimagetool builds the AppImage (prefers a locally cached runtime over downloading it every time)
RUNTIME_FILE="${RUNTIME_FILE:-$HOME/.cache/nmrforge-appimage/runtime-${ARCH}}"
APPIMAGE_TOOL_ARGS=()
if [ -f "$RUNTIME_FILE" ]; then
    APPIMAGE_TOOL_ARGS+=(--runtime-file "$RUNTIME_FILE")
fi
appimagetool "${APPIMAGE_TOOL_ARGS[@]}" \
  "$APPDIR" "$BUILD_DIR/${APP}-${VERSION}-${ARCH}.AppImage"
echo "artefact: $BUILD_DIR/${APP}-${VERSION}-${ARCH}.AppImage"
echo "=== licence files inside the artefact ==="
find "$APPDIR/usr/share/doc/$APP" -maxdepth 2 -type f | sed "s|$APPDIR||"

