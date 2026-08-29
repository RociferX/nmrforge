"""峰表模型测试:save/load 往返、Poky 导出/导入、2D/3D、占位(G2B-005)。"""

from __future__ import annotations

from pathlib import Path

from core.peaks import (
    PeakTable,
    export_peaks_poky,
    import_peaks_poky,
    load_peaks,
    normalize_poky_label,
    poky_label_is_valid,
    save_peaks,
)


def test_save_load_roundtrip(tmp_path: Path) -> None:
    """0.2.199-补29ar:峰文件即 Poky .list(无 SN/CSV 列)。"""
    path = tmp_path / "peaks.list"
    peaks = [
        {
            "Peak_ID": 1, "H_shift": 8.464, "N_shift": 118.5,
            "Intensity": 500.0, "label": "H1",
        },
        {
            "Peak_ID": 2, "H_shift": 7.2, "N_shift": 120.1,
            "Intensity": 350.0, "label": "",
        },
    ]
    save_peaks(path, peaks)
    loaded = load_peaks(path)
    assert len(loaded) == 2
    assert loaded[0]["Peak_ID"] == 1  # 数字 ID(行序)
    assert loaded[1]["Peak_ID"] == 2
    assert loaded[0]["H_shift"] == 8.464
    assert loaded[0]["N_shift"] == 118.5
    assert loaded[0]["Intensity"] == 500.0
    assert loaded[1]["label"] == "?-?"


def test_save_peaks_missing_columns_filled(tmp_path: Path) -> None:
    path = tmp_path / "peaks.list"
    save_peaks(path, [{"Peak_ID": 1, "H_shift": 8.0, "N_shift": 118.0}])
    loaded = load_peaks(path)
    assert loaded[0]["Peak_ID"] == 1
    assert loaded[0]["H_shift"] == 8.0
    assert loaded[0]["Intensity"] == 0.0  # 缺强度 → 0.0


def test_save_peaks_auto_detect_3d(tmp_path: Path) -> None:
    path = tmp_path / "peaks3d.list"
    save_peaks(
        path,
        [
            {
                "Peak_ID": 1,
                "F1_shift": 120.0,
                "F2_shift": 30.0,
                "F3_shift": 8.5,
                "Intensity": 100.0,
            }
        ],
    )
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert first == "Assignment w1 w2 w3 Data Height Volume"
    loaded = load_peaks(path)
    assert loaded[0]["F3_shift"] == 8.5


def test_export_poky_2d_format(tmp_path: Path) -> None:
    path = tmp_path / "peaks.list"
    export_peaks_poky(
        path,
        [{"N_shift": 118.5, "H_shift": 4.703, "Intensity": 500.0}],
        ndim=2,
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "Assignment w1 w2 Data Height Volume"
    # 未命名 ?-?;位移 %.3f;Data/Volume=0;Height %.3g;双空格
    assert lines[1] == "?-?  118.500  4.703  0  500  0"


def test_export_poky_3d_format(tmp_path: Path) -> None:
    path = tmp_path / "peaks3d.list"
    export_peaks_poky(
        path,
        [
            {
                "F1_shift": 120.0,
                "F2_shift": 30.0,
                "F3_shift": 8.5,
                "Intensity": 1234.5,
                "label": "Gly_HN",
            }
        ],
        ndim=3,
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "Assignment w1 w2 w3 Data Height Volume"
    assert lines[1] == "Gly_HN  120.000  30.000  8.500  0  1.23e+03  0"


def test_import_poky_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "peaks.list"
    original = [
        {"label": "H1", "N_shift": 118.5, "H_shift": 4.703, "Intensity": 500.0},
        {"label": "", "N_shift": 120.1, "H_shift": 7.2, "Intensity": 3.5},
    ]
    export_peaks_poky(path, original, ndim=2)
    imported = import_peaks_poky(path)
    assert len(imported) == 2
    assert imported[0]["label"] == "H1"
    assert imported[0]["N_shift"] == 118.5
    assert imported[0]["H_shift"] == 4.703
    assert imported[0]["Intensity"] == 500.0
    assert imported[1]["label"] == "?-?"
    assert imported[1]["Intensity"] == 3.5


def test_import_poky_3d(tmp_path: Path) -> None:
    path = tmp_path / "peaks3d.list"
    export_peaks_poky(
        path,
        [{"F1_shift": 120.0, "F2_shift": 30.0, "F3_shift": 8.5, "Intensity": 100.0}],
        ndim=3,
    )
    imported = import_peaks_poky(path)
    assert imported[0]["F3_shift"] == 8.5
    assert imported[0]["Intensity"] == 100.0


def test_peak_table_add_remove() -> None:
    table = PeakTable(experiment_id="exp_001", params={"min_snr": 3.0})
    peak_id = table.add({"H_shift": 8.0, "N_shift": 118.0})
    assert peak_id == 1
    table.add({"H_shift": 7.0, "N_shift": 120.0})
    assert len(table.rows) == 2
    table.remove(1)
    assert len(table.rows) == 1
    assert table.rows[0]["Peak_ID"] == 2


def test_pick_peaks_columns_match_poky(tmp_path: Path) -> None:
    """0.2.199-补29ar:pick_peaks 输出 Poky .list(契约 §6:峰文件即 .list)。"""
    import numpy as np
    from scipy.ndimage import gaussian_filter

    from core.project import ProjectManager
    from workflow.pick_peaks import pick_peaks

    def _write_ft2(path: Path, data: np.ndarray) -> None:
        from nmrglue.fileio import pipe

        dic = {k: "0" for k in pipe.fdata_dic}
        dic["FDMAGIC"] = 9.2330230000000007e14
        dic["FDDIMCOUNT"] = 2
        dic["FDSIZE"] = data.shape[1]
        dic["FDSPECNUM"] = data.shape[0]
        dic["FDQUADFLAG"] = 1
        dic["FDF1QUADFLAG"] = 1
        dic["FDF2QUADFLAG"] = 1
        for prefix in ("FDF1", "FDF2"):
            dic[prefix + "SW"] = "6000.0"
            dic[prefix + "OBS"] = "600.0"
            dic[prefix + "CAR"] = "4.7"
            dic[prefix + "ORIG"] = "1000.0"
        pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)

    spec = np.zeros((64, 128))
    spec[20, 40] = 500.0
    spec = gaussian_filter(spec, sigma=1.5)
    ft2 = tmp_path / "out.ft2"
    _write_ft2(ft2, spec)
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")
    manager.set_data_spectrum(entry.id, data.id, str(ft2))
    result = pick_peaks(manager, entry.id, data.id)
    assert Path(result["peak_path"]).suffix == ".list"
    first = Path(result["peak_path"]).read_text(encoding="utf-8").splitlines()[0]
    assert first == "Assignment w1 w2 Data Height Volume"


def test_save_peaks_extra_columns_ignored_in_list(tmp_path: Path) -> None:
    """0.2.199-补29ar:Poky .list 无附加列(extra_columns 兼容忽略)。"""
    path = tmp_path / "peaks_rel.list"
    save_peaks(
        path,
        [
            {
                "Peak_ID": 1,
                "H_shift": 8.0,
                "N_shift": 118.0,
                "Reliability(%)": 100.0,
            }
        ],
        extra_columns=("Reliability(%)",),
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "Assignment w1 w2 Data Height Volume"
    assert "Reliability" not in "\n".join(lines)


def test_normalize_poky_label() -> None:
    """0.2.199-补29co:Poky assignment 按维度分段(2D 两段/3D 三段,连字符)。"""
    assert normalize_poky_label("g1h-g1n") == "G1H-G1N"            # 2D
    assert normalize_poky_label("c16h-k15cb-c16n", ndim=3) == "C16H-K15CB-C16N"
    assert normalize_poky_label("v32ca-k31h-v32n", ndim=3) == "V32CA-K31H-V32N"
    assert normalize_poky_label("g1h-?") == "G1H-?"                # 部分未指认
    assert normalize_poky_label("?-?") == "?-?"
    assert normalize_poky_label("?-?-?", ndim=3) == "?-?-?"
    assert normalize_poky_label("") == ""
    assert normalize_poky_label(None) == ""


def test_poky_label_is_valid() -> None:
    """0.2.199-补29co:段数必须与维度一致(2D 两段/3D 三段)才有效。"""
    assert poky_label_is_valid("G1H-G1N")               # 2D 两段
    assert poky_label_is_valid("G1H-?")
    assert poky_label_is_valid("?-?")
    assert not poky_label_is_valid("G1H")               # 2D 只有一段
    assert poky_label_is_valid("G1H-G1N-G1CA", ndim=3)  # 3D 三段
    assert not poky_label_is_valid("G1H-G1N", ndim=3)   # 3D 只有两段
    assert poky_label_is_valid("?-?-?", ndim=3)
    assert not poky_label_is_valid("xyz")
    assert not poky_label_is_valid("1H-1N")
