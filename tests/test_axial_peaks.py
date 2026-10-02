"""Synthetic contract tests for conservative axial peak filtering.

These small arrays exercise local numerical/API behavior; they do not establish
that the heuristic identifies axial artifacts in experimental spectra.
"""

from pathlib import Path

import numpy as np
import pytest

import core.peaks.axial as axial
from core.data.internal_data_model import AxisRole, Dimension, Experiment, ExperimentType
from core.peaks.axial import filter_axial_peaks
from core.qc.peak_detection import Peak


def _experiment(ndim=2, *, mode=4, name=None, confidence=0.9, nuclei=None):
    if ndim == 2:
        dims = [
            Dimension(
                "F1",
                "15N",
                sf=1000,
                sw=320,
                acquisition_mode="States" if mode == 4 else str(mode),
                role=AxisRole.INDIRECT,
            ),
            Dimension("F2", "1H", sf=600, sw=4800, role=AxisRole.DIRECT),
        ]
        name = name or "HSQC"
        block_axis = 1
    else:
        dims = [
            Dimension(
                "F1",
                "15N",
                sf=100,
                sw=320,
                acquisition_mode="States" if mode == 4 else str(mode),
                role=AxisRole.INDIRECT,
            ),
            Dimension(
                "F2",
                "13C",
                sf=150,
                sw=480,
                acquisition_mode="States" if mode == 4 else str(mode),
                role=AxisRole.INDIRECT,
            ),
            Dimension("F3", "1H", sf=600, sw=4800, role=AxisRole.DIRECT),
        ]
        name = name or "HNCO"
        block_axis = 1
    if nuclei:
        for dim, nucleus in zip(dims, nuclei):
            dim.nucleus = nucleus
    exp = Experiment(
        "synthetic",
        Path("."),
        ndim=ndim,
        dimensions=dims,
        experiment_type=ExperimentType(name, confidence),
        acquisition_parameters={f"acqu{ndim - block_axis + 1}s": {"FnMODE": mode}},
    )
    return exp


def _case(ndim=2, *, mode=4, order=None, name=None, confidence=0.9):
    exp = _experiment(ndim, mode=mode, name=name, confidence=confidence)
    order = order or ([2, 1] if ndim == 2 else [3, 1, 2])
    # Storage axes follow FDDIMORDER: last listed dimension is storage axis 0.
    shape_by_logical = {
        d.logical_axis: (40 if d.role is AxisRole.DIRECT else 32) for d in exp.dimensions
    }
    shape = tuple(shape_by_logical[f"F{logical}"] for logical in reversed(order))
    data = np.zeros(shape, dtype=float)
    axes = []
    for axis, size in enumerate(shape):
        axes.append(np.arange(size, dtype=float) * 0.01)
    dic = {"FDDIMORDER": order}
    for dim in exp.dimensions:
        logical = int(dim.logical_axis[1:])
        dic[f"FDF{logical}OBS"] = dim.sf
        storage = ndim - 1 - order.index(logical)
        # Complete original sweep mapping: step * point count == SW / OBS.
        axes[storage] = np.arange(shape[storage], dtype=float) * (dim.sw / dim.sf / shape[storage])
    return exp, data, dic, axes


def _add_ridge(data, axis, direct_axis, direct_positions, other_positions=None):
    other_positions = other_positions or {}
    peaks = []
    for j, direct in enumerate(direct_positions):
        pos = [0] * data.ndim
        pos[axis] = 0
        pos[direct_axis] = direct
        for other_axis, vals in other_positions.items():
            pos[other_axis] = vals[j % len(vals)]
        data[tuple(pos)] = 10
        if pos[axis] + 1 < data.shape[axis]:
            neighbor = pos.copy()
            neighbor[axis] += 1
            data[tuple(neighbor)] = 5
        peaks.append(Peak(position=tuple(float(x) for x in pos), height=10))
    return peaks


def _run(exp, data, dic, axes, peaks, *, pulseprogram=""):
    # Synthetic datasets intentionally have no pulseprogram unless a test needs
    # to inject one; the module's parser reads that path from the experiment.
    exp.source_path = Path(pulseprogram) if pulseprogram else Path(".")
    return filter_axial_peaks(
        data,
        peaks,
        exp,
        dic=dic,
        axes_ppm=axes,
        linewidth_hz_by_nucleus={"15N": 30, "13C": 30},
    )


@pytest.mark.parametrize("count", range(1, 6))
def test_fewer_than_six_edge_peaks_are_kept(count):
    exp, data, dic, axes = _case()
    peaks = _add_ridge(data, 0, 1, [3 + 5 * i for i in range(count)])
    kept, audit = _run(exp, data, dic, axes, peaks)
    assert kept == peaks
    assert audit["rejected"] == 0


@pytest.mark.parametrize("mode", range(1, 7))
def test_supported_fnmode_removes_only_final_edge_ridge(mode):
    exp, data, dic, axes = _case(mode=mode)
    positions = [3, 8, 13, 18, 23, 28]
    edge = _add_ridge(data, 0, 1, positions)
    # A nearby off-axis true peak and an interior carrier remain untouched.
    true_near = Peak(position=(1.0, 11.0))
    true_inside = Peak(position=(16.0, 20.0))
    data[1, 11] = data[16, 20] = 8
    kept, audit = _run(exp, data, dic, axes, edge + [true_near, true_inside])
    assert kept == [true_near, true_inside]
    assert audit["rejected"] == 6


@pytest.mark.parametrize("mode", [0, 7])
def test_unknown_fnmode_is_retained(mode):
    exp, data, dic, axes = _case(mode=mode)
    peaks = _add_ridge(data, 0, 1, [3, 8, 13, 18, 23, 28])
    kept, _ = _run(exp, data, dic, axes, peaks)
    assert kept == peaks


def test_conflicting_pulse_family_is_retained(monkeypatch):
    exp, data, dic, axes = _case(mode=4)
    peaks = _add_ridge(data, 0, 1, [3, 8, 13, 18, 23, 28])
    monkeypatch.setattr(axial, "_family", lambda *_args: "F1EA")
    kept, _ = _run(exp, data, dic, axes, peaks)
    assert kept == peaks


def test_unknown_template_or_low_confidence_is_retained():
    for name, confidence in [("not-a-template", 0.9), ("HSQC", 0.59)]:
        exp, data, dic, axes = _case(name=name, confidence=confidence)
        peaks = _add_ridge(data, 0, 1, [3, 8, 13, 18, 23, 28])
        kept, _ = _run(exp, data, dic, axes, peaks)
        assert kept == peaks


def test_incomplete_original_sweep_mapping_keeps_peaks():
    exp, data, dic, axes = _case()
    peaks = _add_ridge(data, 0, 1, [3, 8, 13, 18, 23, 28])
    exp.dimensions[0].sw = 0
    kept, audit = _run(exp, data, dic, axes, peaks)
    assert kept == peaks
    assert audit["rejected"] == 0


def test_three_dimensional_reordered_axes_are_independently_checked():
    exp, data, dic, axes = _case(3, order=[3, 1, 2])
    # F1 maps to storage axis 1, F2 maps to storage axis 0; F3 is direct axis 2.
    f1 = _add_ridge(data, 1, 2, [3, 8, 13, 18, 23, 28], {0: [5, 8, 11, 14, 17, 20]})
    f2 = _add_ridge(data, 0, 2, [4, 9, 14, 19, 24, 29], {1: [5, 8, 11, 14, 17, 20]})
    kept, audit = _run(exp, data, dic, axes, f1 + f2)
    assert kept == []
    assert audit["rejected"] == 12
    assert {item["storage_axis"] for item in audit["axes"]} == {0, 1}


def test_peak_sign_and_amplitude_do_not_change_edge_decision():
    decisions = []
    for scale, sign in [(1, 1), (100, -1)]:
        exp, data, dic, axes = _case()
        peaks = _add_ridge(data, 0, 1, [3, 8, 13, 18, 23, 28])
        data *= scale * sign
        decisions.append(_run(exp, data, dic, axes, peaks)[1]["rejected"])
    assert decisions == [6, 6]


def test_direct_zero_filling_does_not_change_independence_gate():
    decisions = []
    for factor in (1, 2, 4):
        exp, base, dic, axes = _case()
        data = np.zeros((base.shape[0], base.shape[1] * factor))
        axes[1] = (
            np.arange(data.shape[1]) * exp.dimensions[1].sw / exp.dimensions[1].sf / data.shape[1]
        )
        peaks = _add_ridge(data, 0, 1, [i * factor for i in (3, 5, 7, 9, 11, 13)])
        decisions.append(_run(exp, data, dic, axes, peaks)[1]["rejected"])
    assert decisions == [6, 6, 6]


def test_axial_alignment_tolerance_is_a_physical_width():
    widths = []
    for factor in (1, 2, 4):
        exp, base, dic, axes = _case()
        data = np.zeros((base.shape[0] * factor, base.shape[1]))
        axes[0] = (
            np.arange(data.shape[0]) * exp.dimensions[0].sw / exp.dimensions[0].sf / data.shape[0]
        )
        peaks = _add_ridge(data, 0, 1, [3, 8, 13, 18, 23, 28])
        audit = _run(exp, data, dic, axes, peaks)[1]
        widths.append(audit["axes"][0]["alignment_tolerance_ppm"])
        assert audit["identity"] == "heuristic_not_artifact_proof"
    assert widths == pytest.approx([0.003] * 3)


@pytest.mark.parametrize(
    "changes",
    [
        {"FDF1FTSIZE": 64},
        {"FDF1X1": 8},
        {"FDF1FTSIZE": 32, "FDF1XN": 24},
    ],
)
def test_extraction_header_is_respected_even_without_adjusted_sweep(changes):
    exp, data, dic, axes = _case()
    peaks = _add_ridge(data, 0, 1, [3, 8, 13, 18, 23, 28])
    dic.update(changes)
    kept, audit = _run(exp, data, dic, axes, peaks)
    assert kept == peaks
    assert audit["rejected"] == 0


@pytest.mark.parametrize("field", ["confidence", "sf", "sw", "logical_axis"])
def test_corrupt_metadata_keeps_peaks(field):
    exp, data, dic, axes = _case()
    peaks = _add_ridge(data, 0, 1, [3, 8, 13, 18, 23, 28])
    if field == "confidence":
        exp.experiment_type.confidence = float("nan")
    elif field == "logical_axis":
        exp.dimensions[0].logical_axis = "unmapped"
    else:
        setattr(exp.dimensions[0], field, float("nan"))
    kept, audit = _run(exp, data, dic, axes, peaks)
    assert kept == peaks
    assert audit["rejected"] == 0
