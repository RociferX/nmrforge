"""Poky peak table association replacement test (import replacement association, save and
write.list)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager
from gui.spectrum_panel import SpectrumPanel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _manager_with_peaks(tmp_path: Path):
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    exp_id, data_id = entry.id, data.id
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / f"{exp_id}-{data_id}.ft2").write_bytes(b"x")
    peaks = manager.data_dir(exp_id, data_id, "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / f"{exp_id}-{data_id}.csv").write_text(
        "Peak_ID,H_shift,N_shift,Intensity,SN,label\n"
        "1,8.0,115.0,100,20,OLD\n",
        encoding="utf-8",
    )
    manager.save()
    return manager, exp_id, data_id


def test_import_poky_replaces_association_then_save_writes_list(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Poky import only replaces the peak table association (does not overwrite file); when saving,
    write.list peak file."""
    manager, exp_id, data_id = _manager_with_peaks(tmp_path)
    list_file = tmp_path / "new.list"
    list_file.write_text(
        "Assignment w1 w2 Data Height Volume\n"
        "G1 118.0 8.2 0 100.0 100.0\n"
        "A2 120.0 7.5 0 80.0 80.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "qtcompat.QtWidgets.QFileDialog.getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(list_file), "")),
    )
    shown: list[str] = []
    monkeypatch.setattr(
        "gui.spectrum_panel.InfoDialog.show_info",
        staticmethod(lambda parent, title, text: shown.append(text)),
    )
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)

    # Import: Replace association, do not overwrite old CSV.
    panel._on_import_poky()
    old_csv = (
        manager.data_dir(exp_id, data_id, "peaks") / f"{exp_id}-{data_id}.csv"
    )
    assert "OLD" in old_csv.read_text(encoding="utf-8")
    assert any("current peak table association" in text for text in shown)
    assert panel.peak_table.rowCount() == 2

    # Save: Write Poky.list peak file and register manual_peaks to run.
    panel._on_save_peaks()
    list_path = (
        manager.data_dir(exp_id, data_id, "peaks") / f"{exp_id}-{data_id}.list"
    )
    assert list_path.is_file()
    content = list_path.read_text(encoding="utf-8")
    assert "G1" in content and "A2" in content and "OLD" not in content
    runs = [
        r
        for r in manager.project.workflow_runs
        if r.workflow_ref == "manual_peaks"
    ]
    assert len(runs) == 1 and runs[0].status == "success"
    assert runs[0].outputs["peaks"] == str(list_path)
    panel.close()


def test_projection_file_hides_peak_ui(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29db: Open projection file to hide peaks UI,No peak correlation/peak
    operation."""
    import numpy as np

    from viewer.spectrum import Spectrum, SpectrumAxis

    manager, exp_id, data_id = _manager_with_peaks(tmp_path)
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    proj = spectra / f"{data_id}_15N-1H.ft2"
    proj.write_bytes(b"x")
    manager.save()
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    assert panel._is_projection_name(proj.name) is True
    fake = Spectrum(
        np.zeros((8, 8), dtype=float),
        [
            SpectrumAxis("15N", 8, 6000.0, 600.0, 118.0),
            SpectrumAxis("1H", 8, 6000.0, 600.0, 4.7),
        ],
    )
    monkeypatch.setattr(panel, "_load_projection_ft2", lambda path: fake)
    assert panel.open_spectrum(proj) is True
    assert panel._projection_active is True
    assert panel._peaks == []
    assert panel.peak_table.rowCount() == 0
    assert panel.peak_table.isVisible() is False
    assert panel.peak_toolbar_widget.isVisible() is False
    # _load_peaks (unified entry for the caller) also remains empty under projection and is not
    # associated with the peak table.
    panel._load_peaks(proj)
    assert panel._peaks == []
    assert panel.peak_table.rowCount() == 0
    panel.close()


def test_peak_table_lazy_assignment_widgets(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.199-patch29dc: Assignment editing components are created on demand, and peak tables with
    thousands of rows are no longer stuck."""
    manager, exp_id, data_id = _manager_with_peaks(tmp_path)
    peaks = [
        {
            "Peak_ID": i + 1,
            "H_shift": 8.0,
            "N_shift": 115.0,
            "Intensity": 1,
            "SN": 1,
            "label": f"G{i + 1}",
        }
        for i in range(120)
    ]
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    panel._peaks = peaks
    panel._populate_peak_table()
    label_col = panel._peak_keys.index("label")
    widget_count = sum(
        1
        for row in range(panel.peak_table.rowCount())
        if panel.peak_table.cellWidget(row, label_col) is not None
    )
    # Create components only for visible rows +/- buffers, much less than the total number of rows.
    assert widget_count <= 60
    table_peaks = panel._table_peaks()
    # Visual row (with components) label normalized by segment (2D two segments -> G1-?); viewport
    # outer row (without components) fallback item text original label.
    assert table_peaks[0]["label"] == "G1-?"
    assert table_peaks[-1]["label"] == "G120"
    panel.close()


def test_3d_peak_table_columns_use_nucleus_names(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.199-patch29df: The 3D peak table column name displays the corresponding nuclei name
    (F1_shift -> N/H/C)."""
    import json

    manager, exp_id, data_id = _manager_with_peaks(tmp_path)
    meta = manager.data_metadata_path(exp_id, data_id)
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(
        json.dumps(
            {
                "dataset": {
                    "dimensions": [
                        {"logical_axis": "F1", "sf": 60.8, "nucleus": "15N"},
                        {"logical_axis": "F2", "sf": 600.13, "nucleus": "1H"},
                        {"logical_axis": "F3", "sf": 150.9, "nucleus": "13C"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    # 0.2.199-patch29dj: The column name comes from the axis label of the loaded 3D spectrum (no
    # longer using metadata), first bind and synthesize the N, H, C 3D spectrum and then fill the
    # peak table.
    import numpy as np

    from viewer.spectrum import Spectrum3D, SpectrumAxis

    panel._spectrum3d_panel._spectrum3d = Spectrum3D(
        np.zeros((4, 4, 4)),
        [
            SpectrumAxis("N", 4, 1703.0, 81.1, 117.0, 117.0 * 81.1),
            SpectrumAxis("H", 4, 6000.0, 600.1, 4.7, 4.7 * 600.1),
            SpectrumAxis("C", 4, 3000.0, 150.9, 45.0, 45.0 * 150.9),
        ],
        source="x.ft3",
    )
    panel._peaks = [
        {
            "Peak_ID": 1,
            "F1_shift": 118.0,
            "F2_shift": 8.2,
            "F3_shift": 45.0,
            "Intensity": 1,
            "SN": 1,
            "label": "",
        }
    ]
    panel._populate_peak_table()
    headers = [
        panel.peak_table.horizontalHeaderItem(i).text()
        for i in range(panel.peak_table.columnCount())
    ]
    # 0.2.199-patch29dk: The peak table sequence is consistent with the external.list convention
    # (w1=15N/w2=13C/w3=1H).
    shift_cols = [h for h in headers if h.endswith("_shift")]
    assert shift_cols == ["N_shift", "C_shift", "H_shift"]
    assert "F1_shift" not in headers and "F2_shift" not in headers
    panel.close()


