"""3D NUS 相位门控路径复核测试(SMILE 资源护栏 + backend_runs 计数)。"""

from __future__ import annotations

from pathlib import Path

from backend.nmrpipe_backend import enforce_smile_thread_guardrail
from core.data.bruker_reader import read_dataset
from workflow.phase_optimize import optimize_phase_sequential


class _Nus3DBackend:
    """记录 3D NUS 相位优化调用的假后端(reconstruct/finalize)。"""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = str(work_dir)
        self.reconstruct_params: list[dict] = []
        self.finalize_calls: list[tuple[dict, str | None]] = []

    def reconstruct_nus(self, experiment, params) -> dict:
        self.reconstruct_params.append(dict(params or {}))
        return {
            "success": True,
            "spectrum_path": f"{self.work_dir}/out.ft3",
            "logs": [],
        }

    def finalize_nus(self, experiment, phases=None, work_dir=None, baseline=None) -> dict:
        self.finalize_calls.append((dict(phases or {}), work_dir))
        p1 = list((phases or {}).values())[-1][1]
        return {
            "success": True,
            "spectrum_path": f"{self.work_dir}/out_p1{int(p1)}.ft3",
            "logs": [],
        }


def _score_from_path(path: str) -> tuple[float, dict[str, float]]:
    """评分函数:从路径解析 p1,真值 30° 处得分最高。"""
    p1 = float(path.split("p1")[1].split(".")[0])
    return 100.0 - abs(p1 - 30.0), {}


def test_3d_nus_phase_optimize_guardrails_and_runs(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """3D NUS:1 次 SMILE 重构(默认护栏参数)+ 间接维 F2/F1 候选 finalize。"""
    experiment = read_dataset(bruker_dir / "nus_3d")
    work = tmp_path / "work"
    backend = _Nus3DBackend(work)
    result = optimize_phase_sequential(
        experiment,
        backend,
        p0_values=(0.0,),
        p1_values=(-60.0, 0.0, 30.0, 60.0),
        score_fn=_score_from_path,
        refine=False,
        work_dir=work,
    )
    # SMILE 只跑 1 次,参数不覆盖 nthread(默认 2,遵守资源护栏)
    assert len(backend.reconstruct_params) == 1
    assert backend.reconstruct_params[0] == {}
    # 间接维候选全部走 finalize_nus(不重跑 SMILE),工作目录透传
    assert len(backend.finalize_calls) == 2 * 4
    # work_dir 以 Path/str 均可透传(finalize_nus 签名接受两者)
    assert all(str(call[1]) == str(work) for call in backend.finalize_calls)
    # 直接维 F3 不在 finalize 相位中(SMILE 重构时固化)
    assert all("F3" not in phases for phases, _ in backend.finalize_calls)
    assert set(result.phases) == {"F2", "F1"}
    assert all(result.phases[axis][1] == 30.0 for axis in ("F2", "F1"))
    assert result.backend_runs == 1 + 2 * 4


def test_smile_thread_guardrail_default_and_cap() -> None:
    """SMILE 线程护栏(D006):默认 2;大网格(间接点>5000)强制≤2。"""
    assert enforce_smile_thread_guardrail(2, 1000) == (2, "")
    assert enforce_smile_thread_guardrail(4, 1000) == (4, "")
    capped, log = enforce_smile_thread_guardrail(4, 6000)
    assert capped == 2
    assert "大网格 6000" in log
    assert enforce_smile_thread_guardrail(2, 6000) == (2, "")
