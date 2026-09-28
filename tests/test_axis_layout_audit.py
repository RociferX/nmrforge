"""Phase-optimisation dimension mapping audit regressions (0.2.199-patch29).

Measured baseline (sampleB + manually sliced sampleJ):
- NMRPipe single-file 3D output layout = (F2, F1, F3), 2D = (F1, F2);
- baseline optimisation reads the output-layout spectrum file, so it must use file_axis_index
  (the old code used the internal-convention axis_index, which swaps F1/F2 in 3D and picked
  F1's config from F2 data);
- NUS reconstruction plane 3D stack = (F1 time, F2 time, F3), the F1 window acts on axis 0;
- header SW lookup must match by nucleus label (header FDF1=15N/FDF2=1H/FDF3=13C, unlike the
  logical axis numbers F2/F3/F1).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from core.data.internal_data_model import (
    AxisRole,
    Dimension,
    Experiment,
    Sampling,
    SamplingMode,
)
from core.processing.axes import axis_index, file_axis_index
from workflow.baseline_optimize import optimize_baseline
from workflow.window_optimize import _axis_sw, _load_recon_planes, _nus_axis_map


def _exp_3d() -> Experiment:
    return Experiment(
        dataset_id="exp3d",
        source_path=Path("/fake/3d"),
        ndim=3,
        dimensions=[
            Dimension(logical_axis="F3", nucleus="1H", td=600, role=AxisRole.DIRECT),
            Dimension(logical_axis="F2", nucleus="15N", td=30, role=AxisRole.INDIRECT),
            Dimension(logical_axis="F1", nucleus="13C", td=75, role=AxisRole.INDIRECT),
        ],
        sampling=Sampling(mode=SamplingMode.NUS),
    )


def _write_ft3(path: Path, data: np.ndarray) -> None:
    from nmrglue.fileio import pipe

    dic = {k: "0" for k in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 3
    dic["FDPIPEFLAG"] = 1  # 3D data stream in one file: find_shape needs PIPE set to return 3D
    dic["FDSIZE"] = float(data.shape[2])  # x (fastest)
    dic["FDSPECNUM"] = float(data.shape[1])  # y
    dic["FDF3SIZE"] = float(data.shape[0])  # z (slowest)
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDF3QUADFLAG"] = 1
    for prefix in ("FDF1", "FDF2", "FDF3"):
        dic[prefix + "SW"] = "6000.0"
        dic[prefix + "OBS"] = "600.0"
        dic[prefix + "CAR"] = "4.7"
        dic[prefix + "ORIG"] = "1000.0"
    pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)


def test_file_axis_index_matches_output_layout() -> None:
    """Production layout mapping: 2D (F1,F2); 3D (F2,F1,F3)."""
    assert file_axis_index("F1", 2) == 0
    assert file_axis_index("F2", 2) == 1
    assert file_axis_index("F2", 3) == 0
    assert file_axis_index("F1", 3) == 1
    assert file_axis_index("F3", 3) == 2
    # the internal convention swaps F1/F2 relative to the production layout in 3D -- the very
    # root cause of the old baseline optimisation scoring the wrong axis
    assert axis_index("F1", 3) == 0
    assert axis_index("F2", 3) == 1


def test_baseline_optimize_3d_uses_output_layout(tmp_path: Path) -> None:
    """3D output layout (F2,F1,F3): with drift along F2 (axis 0), F2 is selected for correction
    and F1 stays off.

    The old code used the internal-convention axis_index (F1→0, F2→1), charging F2's drift to
    F1 (the F2 key got the flat data from axis 1 → wrongly stayed off).
    """
    rng = np.random.default_rng(7)
    # production layout (F2=16, F1=20, F3=3): the short F3 axis has no baseline problem;
    # drift runs only along F2 (axis 0); F1 (axis 1) has no drift
    spec = np.zeros((16, 20, 3))
    spec += np.linspace(-40.0, 40.0, 16)[:, np.newaxis, np.newaxis]
    for i, j in ((3, 5), (9, 12), (12, 4), (5, 15)):
        spec[i, j, 0] += 3000.0
        spec[i, j + 1, 0] += 1500.0
    spec += rng.normal(0.0, 0.3, spec.shape)
    ft3 = tmp_path / "spec.ft3"
    _write_ft3(ft3, spec)
    result = optimize_baseline(_exp_3d(), ft3)
    assert result.baseline["F2"]["enabled"] is True, result.logs
    assert result.baseline["F1"]["enabled"] is False, result.logs
    assert result.baseline["F3"]["enabled"] is False, result.logs


def test_nus_axis_map_3d_matches_recon_stack() -> None:
    """3D reconstruction plane stack (F1 time, F2 time, F3) → F1=0, F2=1."""
    assert _nus_axis_map(_exp_3d()) == {"F1": 0, "F2": 1}


def test_axis_sw_matches_header_label(tmp_path: Path) -> None:
    """Header FDF1=15N/FDF2=1H/FDF3=13C: take SW from the nucleus label, not the logical axis
    number."""
    import nmrglue as ng

    dic = ng.pipe.create_empty_dic()
    dic["FDF1LABEL"] = "15N"
    dic["FDF1SW"] = 2189.14
    dic["FDF2LABEL"] = "1H"
    dic["FDF2SW"] = 8196.72
    dic["FDF3LABEL"] = "13C"
    dic["FDF3SW"] = 11312.22
    exp = _exp_3d()
    assert abs(_axis_sw(dic, "F2", exp) - 2189.14) < 1e-2  # 15N(float32)
    assert abs(_axis_sw(dic, "F1", exp) - 11312.22) < 1e-2  # 13C
    assert abs(_axis_sw(dic, "F3", exp) - 8196.72) < 1e-2  # 1H


def _write_2d_recon(path: Path, data: np.ndarray) -> None:
    """Write a real 2D recon.ft1 layout file (F2 frequency, F1 time complex on the last axis).

    Matches the VM measurement (recon.ft1 produced from sampleF): FDF2QUADFLAG=1,
    FDTRANSPOSED=1, FDQUADFLAG=0, stored real as (F2, 2xF1) (real block + imaginary block),
    which nmrglue reads back as (F2, F1) complex.
    """
    import nmrglue as ng

    dic = ng.pipe.create_empty_dic()
    dic.update(
        {
            "FDDIMCOUNT": 2.0,
            "FDPIPEFLAG": 0.0,
            "FDSIZE": float(data.shape[1]),
            "FDSPECNUM": float(data.shape[0]),
            "FDQUADFLAG": 0.0,
            "FDF1QUADFLAG": 0.0,
            "FDF2QUADFLAG": 1.0,
            "FDTRANSPOSED": 1.0,
            "FDF1LABEL": "15N",
            "FDF1SW": 2920.0,
            "FDF2LABEL": "1H",
            "FDF2SW": 19230.77,
        }
    )
    ng.pipe.write(str(path), dic, data.astype(np.complex64), overwrite=True)


def test_load_recon_planes_2d_keeps_complex_shape(tmp_path: Path) -> None:
    """2D recon.ft1 layout (F2 frequency, F1 time) complex: neither reader halves the direct
    dimension.

    0.2.199-patch29b evidence: nmrglue already returns complex for a real 2D recon, so
    read_pipe_complex splitting axis 0 unconditionally halved the direct dimension (old bug).
    """
    rng = np.random.default_rng(9)
    data = rng.normal(size=(16, 8)) + 1j * rng.normal(size=(16, 8))
    recon = tmp_path / "nus2d"
    recon.mkdir()
    _write_2d_recon(recon / "recon.ft1", data)
    exp = _exp_3d()
    exp.ndim = 2
    exp.dimensions = exp.dimensions[1:]
    planes_w, _dic = _load_recon_planes(tmp_path, exp)
    assert planes_w.shape == (16, 8)
    assert np.iscomplexobj(planes_w)
    from workflow.phase_routes import _load_recon_planes as routes_load

    planes_r = routes_load(exp, tmp_path)
    assert planes_r.shape == (16, 8)
    # content matches the written complex data (not the mis-split (8, 8))
    assert np.allclose(np.abs(planes_w), np.abs(data), atol=1e-5)


def test_load_recon_planes_3d_raw_and_count(tmp_path: Path) -> None:
    """3D planes: stacked real as-is (no hypercomplex unpacking) + stale files truncated via
    FDFILECOUNT."""
    import nmrglue as ng

    work = tmp_path
    plane_dir = work / "nus3d_rc"
    plane_dir.mkdir()
    rng = np.random.default_rng(3)
    n0, n1 = 8, 5
    for i in range(1, 7):  # 4 valid + 2 stale
        arr = rng.normal(size=(n0, n1)).astype(np.float32)
        dic = ng.pipe.create_empty_dic()
        dic.update(
            {
                "FDDIMCOUNT": 3.0,
                "FDPIPEFLAG": 0.0,
                "FDSIZE": float(n1),
                "FDSPECNUM": float(n0),
                "FDQUADFLAG": 1.0,
                "FDF1QUADFLAG": 1.0,
                "FDF2QUADFLAG": 1.0,
                "FDFILECOUNT": 4.0,
                "FDF1LABEL": "15N",
                "FDF1SW": 2189.14,
                "FDF2LABEL": "1H",
                "FDF2SW": 8196.72,
                "FDF3LABEL": "13C",
                "FDF3SW": 11312.22,
            }
        )
        ng.pipe.write(
            str(plane_dir / f"test{i:04d}.ft1"), dic, arr, overwrite=True
        )
    loaded, dic = _load_recon_planes(work, _exp_3d())
    assert loaded is not None
    assert loaded.shape == (n0, n1, 4), loaded.shape  # only 4 read, no unpacking
    assert not np.iscomplexobj(loaded)
    assert abs(float(dic["FDF3SW"]) - 11312.22) < 1e-2
