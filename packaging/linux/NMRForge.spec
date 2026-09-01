# NMRForge PyInstaller spec（Linux AppImage 构建）
# 路径相对本文件所在目录（packaging/linux/）；用法见 docs/packaging.md

a = Analysis(
    ["../../main.py"],
    pathex=["../.."],
    binaries=[],
    datas=[
        ("../../config", "config"),
        ("../../presets", "presets"),
        ("../../gui/assets", "gui/assets"),
    ],
    hiddenimports=["PyQt6.QtSvg"],
    hookspath=["hooks"],
    runtime_hooks=[],
    excludes=[],
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
