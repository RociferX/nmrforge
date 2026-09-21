"""2026-09-10 Full project review and repair regression (0.2.199-patch29hz). Coverage: 1. Direct
dimension range verification is divided into direct dimension nuclide bins (originally hard-
coded 0-20 ppm, 13C direct detection is rejected); 2. Product scanning excludes 3D projection
(original *.ft2 fallback will treat the projection as the main spectrum); 3. Deleted/Recycle bin
data is no longer written back by the interface "resurrection"; 4. spectrum file Double-click:
1D (.ft1) should also be displayed on the right side instead of opening the directory where it
is located."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from qtcompat.QtCore import Qt
from qtcompat.QtWidgets import QApplication, QTreeWidgetItem

from core.project import ProjectManager  # noqa: E402
from core.project.artifacts import is_projection_spectrum_file  # noqa: E402
from gui.per_data_records import (  # noqa: E402
    data_is_trashed,
    ui_state_path,
    update_ui_state,
)
from gui.pipeline_panel import (  # noqa: E402
    _node_artifacts,
    validate_ext_range,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def host(qapp: QApplication):
    """Control host: The entire test is destroyed to avoid remaining top-level controls (Qt crashes
    at the end)."""
    from qtcompat.QtWidgets import QWidget

    widget = QWidget()
    yield widget
    widget.deleteLater()
    QApplication.processEvents()


def _project(tmp_path: Path, title: str = "demo"):
    manager = ProjectManager.create_project(tmp_path / title, title)
    exp = manager.create_experiment("HSQC")
    data = manager.import_data(exp.id, "/fake/bruker/1")
    return manager, exp, data


# -------------------------------------------------------------------------- direct dimension range.


def test_ext_range_1h_stays_strict() -> None:
    assert validate_ext_range("10.5", "6.5", "1H") == ""
    assert validate_ext_range("", "", "1H") == ""
    assert validate_ext_range("60", "20", "1H") != ""       # 1H Excludes 60 ppm.
    # The high field end must be larger than the low field end.
    assert validate_ext_range("6.5", "10.5", "1H") != ""
    assert validate_ext_range("abc", "", "1H") != ""


def test_ext_range_13c_direct_is_accepted() -> None:
    """13C direct detection (solid CANCO/NCACX, etc.) direct dimension in 0-200 ppm."""
    assert validate_ext_range("70", "20", "13C") == ""
    assert validate_ext_range("", "20", "13C") == ""
    assert validate_ext_range("250", "20", "13C") != ""


def test_ext_range_unknown_nucleus_uses_wide_fallback() -> None:
    assert validate_ext_range("70", "20", "") == ""
    assert validate_ext_range("200", "20", "13C") == ""


# --------------------------------------------------------------------- Product Scan.


def test_projection_names_are_recognised() -> None:
    assert is_projection_spectrum_file("d_001_15N-1H.ft2", "d_001") is True
    assert is_projection_spectrum_file("d_001_proj_F1.ft2", "d_001") is True
    assert is_projection_spectrum_file("d_001.ft3", "d_001") is False
    # Old naming <exp>-<data> is not a projection.
    assert is_projection_spectrum_file("exp_001-d_001.ft2", "d_001") is False


def test_node_artifacts_ignores_projection_only(tmp_path: Path) -> None:
    """"Generate spectrum" cannot be considered completed when only the file is projected."""
    manager, exp, data = _project(tmp_path, "proj_art")
    spectra = manager.data_dir(exp.id, data.id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / f"{data.id}_15N-1H.ft2").write_bytes(b"projection")

    artifacts = _node_artifacts(manager, exp.id, data.id)
    assert artifacts["spectrum"] is None

    main = spectra / f"{data.id}.ft3"
    main.write_bytes(b"main")
    artifacts = _node_artifacts(manager, exp.id, data.id)
    assert artifacts["spectrum"] == main


# ------------------------------------------------------------------ Recycle Bin Guard.


def test_ui_state_not_written_for_trashed_data(tmp_path: Path) -> None:
    manager, exp, data = _project(tmp_path, "trash_write")
    data.trashed = True
    assert data_is_trashed(manager, exp.id, data.id) is True
    assert ui_state_path(manager, exp.id, data.id).exists() is False

    written = update_ui_state(
        manager, exp.id, data.id, "peaks", {"threshold": 40.0}
    )
    assert written is False
    # Key: Deleted data directory must not be rebuilt (otherwise recover_trashed will misjudge
    # resurrection).
    assert manager.data_base(exp.id, data.id).exists() is False


def test_ui_state_still_written_for_active_data(tmp_path: Path) -> None:
    manager, exp, data = _project(tmp_path, "trash_write_ok")
    written = update_ui_state(
        manager, exp.id, data.id, "peaks", {"threshold": 30.0}
    )
    assert written is True
    assert ui_state_path(manager, exp.id, data.id).is_file()


def test_recover_trashed_ignores_ui_records_only(tmp_path: Path) -> None:
    """Only interface records (report/log.txt, ui_state.json) are not considered "real products"."""
    manager, exp, data = _project(tmp_path, "trash_recover")
    base = manager.data_base(exp.id, data.id)
    (base / "report").mkdir(parents=True, exist_ok=True)
    (base / "report" / "log.txt").write_text("log", encoding="utf-8")
    (base / "ui_state.json").write_text("{}", encoding="utf-8")
    data.trashed = True
    manager.save()

    assert manager.recover_trashed() == 0
    assert data.trashed is True

    (base / "raw").mkdir(exist_ok=True)
    assert manager.recover_trashed() == 1
    assert data.trashed is False


# --------------------------------------------------------------------- tree double-click spectrum.


def test_double_click_ft1_opens_spectrum(
    tmp_path: Path, qapp: QApplication, host
) -> None:
    from gui.project_tree import ProjectTreePanel

    manager, exp, data = _project(tmp_path, "ft1_click")
    spectra = manager.data_dir(exp.id, data.id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    ft1 = spectra / f"{data.id}.ft1"
    ft1.write_bytes(b"1d")

    panel = ProjectTreePanel(manager, parent=host)
    opened: list[str] = []
    panel.open_spectrum_requested.connect(opened.append)
    item = QTreeWidgetItem()
    item.setData(
        0,
        Qt.ItemDataRole.UserRole,
        {
            "kind": "file",
            "folder": "spectra",
            "name": ft1.name,
            "exp_id": exp.id,
            "data_id": data.id,
        },
    )
    panel._on_double_clicked(item, 0)
    assert opened and opened[0].endswith(f"{data.id}.ft1")
