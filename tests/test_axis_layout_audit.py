"""Phase optimisation dimension parsing audit regression test (0.2.199-patch29). Empirical
benchmark (sampleB + sampleJ manual slicing): - NMRPipe single file 3D output layout = (F2, F1,
F3),2D = (F1, F2); - baseline optimisation reads the output layout spectrum file, must use
file_axis_index (old code uses internal convention axis_index, under 3D F1/F2 interchange, F2
data will be given to F1 to select configuration); - NUS reconstruction plane 3D stacking =
(when F1, when F2, F3), F1 window action axis 0; - Head SW The axis taken must match the core
label (head FDF1=15N/FDF2=1H/FDF3=13C, which is different from the logical axis number
F2/F3/F1)."""

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
    dic["FDPIPEFLAG"] = 1  # 3D Data stream single file:find_shape requires PIPE mark to return 3D.
    dic["FDSIZE"] = float(data.shape[2])  # x(Fastest).
    dic["FDSPECNUM"] = float(data.shape[1])  # y
    dic["FDF3SIZE"] = float(data.shape[0])  # z(Slowest).
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
    """Production layout mapping: 2D (F1, F2); 3D (F2, F1, F3)."""
    assert file_axis_index("F1", 2) == 0
    assert file_axis_index("F2", 2) == 1
    assert file_axis_index("F2", 3) == 0
    assert file_axis_index("F1", 3) == 1
    assert file_axis_index("F3", 3) == 2
    # Internally agreed 3D is the opposite of production layout F1/F2 (this is the source of the
    # wrong axis of the old baseline optimisation).
    assert axis_index("F1", 3) == 0
    assert axis_index("F2", 3) == 1


def test_baseline_optimize_3d_uses_output_layout(tmp_path: Path) -> None:
    """3D output layout (F2, F1, F3): When drifting along F2 (axis 0), F2 is selected for
    correction, and F1 remains off. The old code uses the internal convention axis_index(F1 ->
    0, F2 -> 1), which will calculate the drift of F2 to the head of F1 (the F2 key gets the
    flat data of axis 1 -> is kept off by mistake)."""
    rng = np.random.default_rng(7)
    # Production layout (F2=16, F1=20, F3=3): The short axis of F3 is considered to have no baseline
    # problem; the drift is only along F2 (axis 0); there is no drift on F1 (axis 1).
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
    """3D reconstruction plane stacking (when F1, when F2, F3) -> F1=0,F2=1."""
    assert _nus_axis_map(_exp_3d()) == {"F1": 0, "F2": 1}


def test_axis_sw_matches_header_label(tmp_path: Path) -> None:
    """Head FDF1=15N/FDF2=1H/FDF3=13C: Take SW according to the core label, not according to the
    logical axis number."""
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
    """Write the real 2D recon.ft1 layout file (F2 frequency, F1 when the replica is on the last
    axis). Consistent with VM measured (recon.ft1 made by sampleF): FDF2QUADFLAG=1,
    FDTRANSPOSED=1, FDQUADFLAG=0, the real data is stored as (F2, 2 x F1) (real part block +
    imaginary part block), nmrglue reads back (F2, F1) complex."""
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
    """2D recon.ft1 layout (F2 frequency, F1 time) replication: neither reader cuts the direct
    dimension. 0.2.199-patch29b Empirical evidence: nmrglue directly returns the replication for
    the real 2D recon, read_pipe_complex unconditionally removing axis 0 will cut the direct
    dimension in half (old bug)."""
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
    # The content is consistent with the written copy data (not the wrong one (8, 8)).
    assert np.allclose(np.abs(planes_w), np.abs(data), atol=1e-5)


def test_load_recon_planes_3d_raw_and_count(tmp_path: Path) -> None:
    """3D Plane: As-is real stack (no unpacking hypercomplex) + truncate stale file by
    FDFILECOUNT."""
    import nmrglue as ng

    work = tmp_path
    plane_dir = work / "nus3d_rc"
    plane_dir.mkdir()
    rng = np.random.default_rng(3)
    n0, n1 = 8, 5
    for i in range(1, 7):  # 4 Valid + 2 stale.
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
    assert loaded.shape == (n0, n1, 4), loaded.shape  # Read only 4, do not unpack.
    assert not np.iscomplexobj(loaded)
    assert abs(float(dic["FDF3SW"]) - 11312.22) < 1e-2
