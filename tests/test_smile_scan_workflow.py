"""SMILE 扫描工作流:排序表 + 前三脚本 + 候选谱删除(0.2.199-补29hz-修3 第 2 步)。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from core.data.bruker_reader import read_dataset
from core.project import ProjectManager
from workflow.smile_optimize import (
    scan_smile_parameters,
    write_smile_scan_output,
)

BRUKER = Path(__file__).resolve().parent / "fixtures" / "bruker"


class _FakeBackend:
    """假后端:不跑 NMRPipe,但按真实语义产出候选 → 评估 → 删除。"""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def smile_scan(
        self,
        experiment,
        params,
        combos,
        *,
        work_dir,
        evaluate=None,
        progress=None,
        delete_spectra=True,
    ):
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        candidates = []
        for index, combo in enumerate(combos, start=1):
            spectrum = Path(work_dir) / f"cand{index:02d}.ft2"
            spectrum.write_bytes(b"x" * (index * 8))
            if progress is not None:
                progress(index, len(combos), f"扫描 {index}/{len(combos)}")
            # 公共峰(跨组合稳定)+ 每组合独有峰;S/N 随 index 递增
            metrics = {
                "peak_count": 2,
                "quality": 40.0 + index,
                "peaks": [
                    {"position": [1.0, 1.0], "height": 1.0, "snr": float(index)},
                    {"position": [float(index) * 10.0, 0.0], "height": 1.0, "snr": 1.0},
                ],
            }
            if delete_spectra:
                spectrum.unlink()
                self.deleted.append(str(spectrum))
            candidates.append(
                {
                    "index": index,
                    "params": dict(combo),
                    "metrics": metrics,
                    "script": f"# smile script nSigma={combo['nsigma']} "
                    f"thresh={combo['thresh']}\n",
                    "ok": True,
                }
            )
        return {
            "success": True,
            "message": "fake scan",
            "logs": [],
            "candidates": candidates,
            "scan_dir": str(work_dir),
        }


def test_scan_ranks_and_deletes_candidates(tmp_path: Path) -> None:
    exp = read_dataset(BRUKER / "nus_3d")
    backend = _FakeBackend()
    scan_dir = tmp_path / "scan"
    result = scan_smile_parameters(exp, backend, {}, scan_dir=scan_dir)

    assert result["n_combos"] == 16  # 默认 4x4(0.2.199-补29hz-修5)
    assert len(result["rows"]) == 16
    assert len(backend.deleted) == 16          # 候选谱评估后即删
    assert not any(scan_dir.glob("*.ft2"))     # 扫描目录不留谱

    # 公共峰在 25 组都出现 → 每组稳定峰数≥1;排序按平均 S/N 降序
    ranks = [row["rank"] for row in result["rows"]]
    assert ranks == list(range(1, 17))
    assert all(row["stable_count"] >= 1 for row in result["rows"])
    assert result["rows"][0]["mean_snr"] >= result["rows"][-1]["mean_snr"]
    assert "script" not in result["rows"][0]   # 脚本文本不外泄到排序表
    assert len(result["scripts"]) == 3
    assert all(text for text in result["scripts"].values())


def test_write_scan_output_layout(tmp_path: Path) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    exp = manager.create_experiment("HNCA")
    data = manager.import_data(exp.id, "/fake/1")
    backend = _FakeBackend()
    result = scan_smile_parameters(
        read_dataset(BRUKER / "nus_3d"),
        backend,
        {},
        scan_dir=tmp_path / "scan2",
    )

    paths = write_smile_scan_output(
        manager, exp.id, data.id, result["rows"], result["scripts"]
    )

    csv_path = Path(paths["csv"])
    json_path = Path(paths["json"])
    assert csv_path.parent == manager.data_dir(exp.id, data.id, "smile_optimized")
    assert json_path.is_file()
    with csv_path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 16
    assert rows[0]["rank"] == "1"
    assert json.loads(json_path.read_text(encoding="utf-8"))["count"] == 16

    for rank in (1, 2, 3):
        script = manager.data_dir(exp.id, data.id, "process") / (
            f"{data.id}_nus_rank{rank}.com"
        )
        assert script.is_file()
        assert "nsigma" in script.read_text(encoding="utf-8").lower()
