"""Regression tests for common-axis cropping in the QC scoring helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest


def _load_tool():
    path = Path(__file__).resolve().parents[1] / "scripts" / "vm_qc_score.py"
    spec = importlib.util.spec_from_file_location("vm_qc_score", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _spectrum(nuclei: tuple[str, ...], *, offset: float = 0.0):
    ppm_by_nucleus = {
        "H": np.arange(16, dtype=float) + 1.0 + offset,
        "N": np.arange(16, dtype=float) + 100.0 + offset,
        "C": np.arange(16, dtype=float) + 40.0 + offset,
    }
    data = np.arange(16 ** len(nuclei), dtype=float).reshape((16,) * len(nuclei))
    data.flat[0] = -321.0
    return {
        "data": data,
        "ppm": [ppm_by_nucleus[nucleus] for nucleus in nuclei],
        "nuclei": list(nuclei),
    }


def _patch_reader(monkeypatch, spectra):
    reader_module = importlib.import_module("scripts.vm_four_path_figure")
    monkeypatch.setattr(reader_module, "_load", lambda path: spectra[Path(path).name])


@pytest.mark.parametrize(
    "nuclei",
    [("H", "N"), ("N", "H"), ("H", "N", "C"), ("C", "H", "N")],
)
def test_load_without_reference_preserves_axes_and_sign(monkeypatch, nuclei):
    tool = _load_tool()
    spectrum = _spectrum(nuclei)
    _patch_reader(monkeypatch, {"sample": spectrum})

    data, meta = tool._load(Path("sample"), None)

    assert data.shape == (16,) * len(nuclei)
    assert meta["nuclei"] == list(nuclei)
    assert np.min(data) == -321.0


def test_load_crops_each_axis_to_reference_window_by_nucleus(monkeypatch):
    tool = _load_tool()
    sample = _spectrum(("H", "N", "C"))
    reference = _spectrum(("C", "H", "N"), offset=4.0)
    _patch_reader(monkeypatch, {"sample": sample, "reference": reference})

    data, meta = tool._load(Path("sample"), None, Path("reference"))

    assert data.shape == (12, 12, 12)
    assert meta["shape"] == [12, 12, 12]
    assert meta["windows_ppm"] == {"H": [5.0, 16.0], "N": [104.0, 115.0], "C": [44.0, 55.0]}
    assert meta["comparison_input"] == "reference"


def test_explicit_h_window_intersects_common_window_and_keeps_negative_data(monkeypatch):
    tool = _load_tool()
    sample = _spectrum(("H", "N"))
    sample["data"][3, 2] = -321.0
    reference = _spectrum(("N", "H"), offset=2.0)
    _patch_reader(monkeypatch, {"sample": sample, "reference": reference})

    data, meta = tool._load(Path("sample"), (4.0, 12.0), Path("reference"))

    assert data.shape == (9, 14)
    assert meta["windows_ppm"]["H"] == [4.0, 12.0]
    assert meta["H_span_ppm"] == [4.0, 12.0]
    assert np.min(data) < 0


@pytest.mark.parametrize(
    "sample, reference, message",
    [
        (_spectrum(("H", "H")), None, "unique explicit nuclei"),
        (_spectrum(("N", "C")), None, "including 1H"),
        (_spectrum(("H", "N")), _spectrum(("H", "C")), "same unique nuclear axes"),
    ],
)
def test_load_rejects_ambiguous_or_incompatible_nuclei(
    monkeypatch, sample, reference, message
):
    tool = _load_tool()
    spectra = {"sample": sample}
    if reference is not None:
        spectra["reference"] = reference
    _patch_reader(monkeypatch, spectra)
    reference_path = Path("reference") if reference is not None else None

    with pytest.raises(SystemExit, match=message):
        tool._load(Path("sample"), None, reference_path)


def test_load_rejects_axis_with_fewer_than_eight_retained_points(monkeypatch):
    tool = _load_tool()
    sample = _spectrum(("H", "N"))
    reference = _spectrum(("N", "H"), offset=9.0)
    _patch_reader(monkeypatch, {"sample": sample, "reference": reference})

    with pytest.raises(SystemExit, match="common H window has fewer than eight points"):
        tool._load(Path("sample"), None, Path("reference"))


def test_load_rejects_non_finite_spectrum(monkeypatch):
    tool = _load_tool()
    sample = _spectrum(("H", "N"))
    sample["data"][0, 1] = np.inf
    _patch_reader(monkeypatch, {"sample": sample})

    with pytest.raises(SystemExit, match="non-finite"):
        tool._load(Path("sample"), None)


@pytest.mark.parametrize("window", ["nan,2", "1,inf", "2,1"])
def test_cli_rejects_non_finite_or_reversed_h_window(window, capsys):
    tool = _load_tool()

    with pytest.raises(SystemExit) as exc_info:
        tool.main(["--spectrum", "sample", "--h-window", window])

    assert exc_info.value.code == 2
    assert "finite increasing bounds" in capsys.readouterr().err
