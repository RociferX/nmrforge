"""Poky peak-table association replacement test (import replaces it; save writes .list)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager
from gui.spectrum_panel import SpectrumPanel


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
        "Peak_ID,H_shift,N_shift,Intensity,SN,label\n1,8.0,115.0,100,20,OLD\n",
        encoding="utf-8",
    )
    manager.save()
    return manager, exp_id, data_id


def test_import_poky_replaces_association_then_save_writes_list(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Poky import only replaces the peak table association (no file overwrite); when saving, the
    .list peak file is written."""
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

    # Import: replace the association, do not overwrite the old CSV
    panel._on_import_poky()
    old_csv = manager.data_dir(exp_id, data_id, "peaks") / f"{exp_id}-{data_id}.csv"
    assert "OLD" in old_csv.read_text(encoding="utf-8")
    assert any("替换当前峰表关联" in text for text in shown)
    assert panel.peak_table.rowCount() == 2

    # Save: write the Poky .list peak file and register a manual_peaks run
    panel._on_save_peaks()
    list_path = manager.data_dir(exp_id, data_id, "peaks") / f"{exp_id}-{data_id}.list"
    assert list_path.is_file()
    content = list_path.read_text(encoding="utf-8")
    assert "G1" in content and "A2" in content and "OLD" not in content
    runs = [r for r in manager.project.workflow_runs if r.workflow_ref == "manual_peaks"]
    assert len(runs) == 1 and runs[0].status == "success"
    assert runs[0].outputs["peaks"] == str(list_path)
    panel.close()


def test_projection_file_hides_peak_ui(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29db: projection file hides peak UI, no peak association/operations."""
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
    # _load_peaks (the caller's unified entry) also remains empty under a projection and is not
    # associated with the peak table
    panel._load_peaks(proj)
    assert panel._peaks == []
    assert panel.peak_table.rowCount() == 0
    panel.close()


def test_peak_table_lazy_assignment_widgets(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.199-patch29dc: Assignment editors are created on demand; peak tables with thousands of
    rows no longer stall."""
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
    # Widgets are created only for visible rows +/- a buffer, far fewer than the row count
    assert widget_count <= 60
    table_peaks = panel._table_peaks()
    # Visible rows (with widgets): label is normalized by segment (2D two segments -> G1-?);
    # rows outside the viewport (no widget) fall back to the raw label in the item text
    assert table_peaks[0]["label"] == "G1-?"
    assert table_peaks[-1]["label"] == "G120"
    panel.close()


def test_3d_peak_table_columns_use_nucleus_names(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.199-patch29df: 3D peak-table columns show nucleus names (F1_shift -> N/H/C)."""
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
    # 0.2.199-patch29dj: column names come from the loaded 3D spectrum's axis labels (no more
    # metadata fallback); bind a synthetic N,H,C 3D spectrum first, then fill the peak table
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
    # 0.2.199-patch29dk: peak table column order matches the external .list convention
    # (w1=15N/w2=13C/w3=1H)
    shift_cols = [h for h in headers if h.endswith("_shift")]
    assert shift_cols == ["N_shift", "C_shift", "H_shift"]
    assert "F1_shift" not in headers and "F2_shift" not in headers
    panel.close()


def _manager_for_snr(tmp_path: Path, name: str, peak_text: str, suffix: str):
    "Regression coverage:  manager for snr."
    manager = ProjectManager.create_project(tmp_path / name, "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    exp_id, data_id = entry.id, data.id
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    spectrum = spectra / f"{exp_id}-{data_id}.ft2"
    spectrum.write_bytes(b"x")
    peaks_dir = manager.data_dir(exp_id, data_id, "peaks")
    peaks_dir.mkdir(parents=True, exist_ok=True)
    peak_path = peaks_dir / f"{exp_id}-{data_id}{suffix}"
    peak_path.write_text(peak_text, encoding="utf-8")
    manager.save()
    return manager, exp_id, data_id, spectrum, peak_path


def test_peak_table_sn_column_shows_snr(tmp_path: Path, qapp: QApplication) -> None:
    "Regression coverage: test peak table sn column shows snr."
    manager, exp_id, data_id, spectrum, _peak_path = _manager_for_snr(
        tmp_path,
        "proj_snr",
        "Peak_ID,H_shift,N_shift,Intensity,SNR,label\n"
        "1,8.0,115.0,100,20.53,G1\n"
        "2,7.5,118.0,80,,A2\n"
        "3,7.0,120.0,60,NaN,C3\n",
        ".csv",
    )
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    panel._load_peaks(spectrum)
    assert panel.peak_table.rowCount() == 3
    sn_col = panel._peak_keys.index("SN")
    assert panel.peak_table.item(0, sn_col).text() == "20.5"
    assert panel.peak_table.item(1, sn_col).text() == ""
    assert panel.peak_table.item(2, sn_col).text() == ""

    assert panel._peaks[0]["SN"] == "20.53"
    panel.close()


def test_peak_table_sn_column_accepts_legacy_sn_column(tmp_path: Path, qapp: QApplication) -> None:
    "Regression coverage: test peak table sn column accepts legacy sn column."
    manager, exp_id, data_id, spectrum, _peak_path = _manager_for_snr(
        tmp_path,
        "proj_sn",
        "Peak_ID,H_shift,N_shift,Intensity,SN,label\n1,8.0,115.0,100,3,OLD\n",
        ".csv",
    )
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    panel._load_peaks(spectrum)
    sn_col = panel._peak_keys.index("SN")
    assert panel.peak_table.item(0, sn_col).text() == "3.0"
    panel.close()


def test_peak_table_sn_column_empty_for_poky_list(tmp_path: Path, qapp: QApplication) -> None:
    "Regression coverage: test peak table sn column empty for poky list."
    manager, exp_id, data_id, spectrum, _peak_path = _manager_for_snr(
        tmp_path,
        "proj_list_sn",
        "Assignment w1 w2 Data Height Volume\nG1 118.0 8.2 0 100.0 100.0\n",
        ".list",
    )
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    panel._load_peaks(spectrum)
    assert panel.peak_table.rowCount() == 1
    sn_col = panel._peak_keys.index("SN")
    assert panel.peak_table.item(0, sn_col).text() == ""
    panel.close()
