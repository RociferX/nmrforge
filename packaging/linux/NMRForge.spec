# NMRForge PyInstaller spec (Linux AppImage build)
# Paths are relative to this file's directory (packaging/linux/); usage in docs/packaging.md

import os

# 2026-09-21: the UI strings go through a runtime language layer, so only **one** AppImage is
# built; this also drops the Qt modules the application does not use (it needs QtCore/QtGui/
# QtWidgets, plus QtSvg, pyqtgraph and the platform/image plugins). Excluding a module only
# affects artefact size: if a new feature needs one of them (a 3D OpenGL view, chart widgets),
# delete it from the list below and rebuild - PyInstaller never fails because of an exclude.
# Verify on a clean machine before releasing (the release checklist).
_EXCLUDED_QT_MODULES = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtHelp",
    "PySide6.QtHttpServer",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetworkAuth",
    "PySide6.QtNfc",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickTest",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    # QtTest must not be excluded: qtcompat/__init__.py imports PySide6.QtTest
    # unconditionally (measured on the build VM, 2026-09-21)
    "PySide6.QtUiTools",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
]

a = Analysis(
    ["../../main.py"],
    pathex=["../.."],
    binaries=[],
    datas=[
        # shipped data (default config and experiment templates) keeps the nmrforge_data/
        # shape inside the artefact, exactly as in an installed copy
        ("../../nmrforge_data/config", "nmrforge_data/config"),
        ("../../nmrforge_data/presets", "nmrforge_data/presets"),
        ("../../gui/assets", "gui/assets"),
        # language catalogues: the UI-string lookup tables (zh.json etc.), located at run time by
        # ui_support/i18n.py
        ("../../ui_support/locales", "ui_support/locales"),
    ],
    hiddenimports=["PySide6.QtSvg"],
    hookspath=[os.path.abspath(os.path.join(SPECPATH, "hooks"))],
    runtime_hooks=[],
    excludes=list(_EXCLUDED_QT_MODULES),
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NMRForge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="NMRForge",
)
