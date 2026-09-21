"""NMRForge program entry. Follow the NMRFlow development process: venv (nmrforge/) is
automatically created and the project is installed during the first run in the development
state, and then switched to the venv interpreter to execute the real entry logic;
AppImage/PyInstaller is started directly in the frozen state."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_DIR = PROJECT_ROOT / "nmrforge"
VENV_PYTHON = VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _is_frozen() -> bool:
    """Whether to run in PyInstaller frozen environment (AppImage)."""
    return bool(getattr(sys, "frozen", False))


def _bootstrap_venv() -> None:
    """First run: Create venv and install this project (reuse directly later)."""
    if VENV_PYTHON.exists():
        return
    print(
        "No virtual environment detected, creating nmrforge/ and installing dependencies "
        "(first run will take a few minutes)..."
    )
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])
    subprocess.check_call([str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip"])
    subprocess.check_call([str(VENV_PYTHON), "-m", "pip", "install", "-e", str(PROJECT_ROOT)])


def main() -> int:
    """Launch the Qt main window (GUI is built on core.project)."""
    from core.logging_setup import configure_logging

    # Phase 22: Entrance unified configuration log (level NMRFORGE_LOG_LEVEL, default WARNING, write
    # stderr).
    configure_logging()
    from gui.main_window import MainWindow

    return MainWindow.run()


if __name__ == "__main__":
    if not _is_frozen() and "--inside-venv" not in sys.argv:
        _bootstrap_venv()
        if os.path.abspath(sys.executable) != os.path.abspath(str(VENV_PYTHON)):
            os.execv(
                str(VENV_PYTHON), [str(VENV_PYTHON), str(__file__), "--inside-venv"]
            )
    raise SystemExit(main())
