"""内存相位搜索引擎测试(复型数据 + 旧算法判断标准,零后端)。"""

from __future__ import annotations

import numpy as np

from workflow.memory_phase_search import (
    joint_recheck_memory,
    rotate_real,
    score_axis_memory,
    search_axis_memory,
)


def _complex_axis_2d(
    size: tuple[int, int],
    axis: int,
    p0: float = 0.0,
    p1: float = 0.0,
    *,
    width: float = 1.5,
) -> np.ndarray:
    """构造只在一个轴上是复型的二维谱(与真实「逐轴复型预览」一致):
    被评轴 = 解析复型 Lorentzian 并施加已知相位;其它轴 = 实型纯吸收
    Lorentzian(等价于已 -di 的其它维,避免相位污染)。
    """
    n0, n1 = size
    k0 = np.arange(n0, dtype=float)
    k1 = np.arange(n1, dtype=float)
    data = np.zeros(size, dtype=np.complex128)
    for c0, c1, amp in (
        (n0 * 0.35, n1 * 0.45, 400.0),
        (n0 * 0.62, n1 * 0.58, 320.0),
    ):
        z0 = 1.0 / (1.0 + 1j * (k0 - c0) / width)
        z1 = 1.0 / (1.0 + 1j * (k1 - c1) / width)
        real0 = 1.0 / (1.0 + ((k0 - c0) / width) ** 2)
        real1 = 1.0 / (1.0 + ((k1 - c1) / width) ** 2)
        if axis == 0:
            data += amp * np.outer(z0, real1)
        else:
            data += amp * np.outer(real0, z1)
    return _apply_ramp(data, axis, p0, p1)


def _apply_ramp(arr: np.ndarray, axis: int, p0: float, p1: float) -> np.ndarray:
    n = arr.shape[axis]
    k = np.arange(n, dtype=float)
    ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
    shape = [1] * arr.ndim
    shape[axis] = n
    return arr * ramp.reshape(shape)


def test_rotate_real_recovers_absorptive() -> None:
    """旋转回去取实部应恢复纯吸收实谱(与旧 PS -di 语义一致)。"""
    base = _complex_axis_2d((128, 96), axis=0)
    mixed = _apply_ramp(base, 0, 90.0, 0.0)
    recovered = rotate_real(mixed, 0, -90.0, 0.0)
    assert float(np.max(np.abs(recovered - np.real(base)))) < 1e-6


def test_search_axis_memory_recovers_known_p0_axis0() -> None:
    """常数相位 -40° 应恢复为校正相位约 +40°(粗网格 30° 精度)。

    0.2.199-补29dn:评分面平坦时保持粗网格最优(不再用平台圆中位数——
    平坦区中位数会漂移,如 sampleI F1 粗网格 90° 被带偏到 80°);因此
    恢复精度为粗网格步长(30°),容差放宽到 ±15°。
    """
    mixed = _complex_axis_2d((128, 96), axis=0, p0=-40.0)
    est = search_axis_memory(mixed, axis=0)
    assert est is not None
    assert abs((est.phase[0] - 40.0 + 180.0) % 360.0 - 180.0) <= 15.0, est
    assert abs(est.phase[1]) <= 5.0, est


def test_search_axis_memory_recovers_known_p0_axis1() -> None:
    """0.2.199-补29dn:平坦面保持粗网格最优,恢复精度为粗网格步长(±15°)。"""
    mixed = _complex_axis_2d((96, 128), axis=1, p0=-50.0)
    est = search_axis_memory(mixed, axis=1)
    assert est is not None
    assert abs((est.phase[0] - 50.0 + 180.0) % 360.0 - 180.0) <= 15.0, est


def test_search_axis_memory_near_zero_stays_zero() -> None:
    base = _complex_axis_2d((96, 80), axis=0)
    est = search_axis_memory(base, axis=0)
    assert est is not None
    assert abs(est.phase[0]) <= 5.0, est
    assert abs(est.phase[1]) <= 5.0, est


def test_score_axis_memory_matches_formula() -> None:
    base = _complex_axis_2d((64, 48), axis=0)
    real0 = np.real(base)
    indices, positions = [], []
    for i in range(real0.shape[0]):
        if float(np.max(np.abs(real0[i, :]))) > 0:
            indices.append(i)
            positions.append(int(np.argmax(np.abs(real0[i, :]))))
    s = score_axis_memory(base, 0, 0.0, 0.0, indices, positions)
    assert 0.0 <= s <= 100.0
    assert s > 95.0  # 纯吸收谱应接近满分


def test_window_nets_flattens_baseline_before_sign_split() -> None:
    """基线整体偏移(正/负)不再污染净吸收:拉平后吸收峰净吸收≈+1,
    色散峰≈0;偏移谱与零基线谱评分一致(0.2.175 用户方案恢复)。"""
    from workflow.memory_phase_search import _window_nets

    base = _complex_axis_2d((64, 48), axis=0)
    real0 = np.real(base)
    indices = [
        i
        for i in range(real0.shape[0])
        if float(np.max(np.abs(real0[i, :]))) > 0
    ]
    positions = [int(np.argmax(np.abs(real0[i, :]))) for i in indices]

    nets_flat = _window_nets(real0, 0, indices, positions)
    # 整体抬升基线(峰高约 400,偏移 30 相对显著):拉平后净吸收应接近
    # 零基线结果;不拉平则整体正偏使 net 虚高
    shifted = real0 + 30.0
    nets_shifted = _window_nets(shifted, 0, indices, positions)
    assert len(nets_flat) == len(nets_shifted) >= 2
    med_flat = float(np.median(nets_flat))
    med_shifted = float(np.median(nets_shifted))
    assert med_flat > 0.5, med_flat
    assert abs(med_shifted - med_flat) < 0.15, (med_flat, med_shifted)


def test_lock_discrete_traces_excludes_clump() -> None:
    """离散尖峰迹线入选,中央混杂大团(宽平台)被排除(用户反馈:混杂峰团
    会带偏相位,只调离散峰)。"""
    from workflow.memory_phase_search import _lock_discrete_traces

    n0, n1 = 128, 128
    k0 = np.arange(n0, dtype=float)
    k1 = np.arange(n1, dtype=float)
    arr = np.zeros((n0, n1), dtype=np.complex128)
    w = 1.2
    z0 = 1.0 / (1.0 + 1j * (k0 - 30) / w)
    arr += 400.0 * np.outer(z0, 1.0 / (1.0 + ((k1 - 40) / 2.0) ** 2))
    # 中央大团:空间集中在 k1≥64 的「一团」(幅度 200、宽 8、间隔 8 叠加),
    # 不污染 k1≈40 的离散峰列
    for c0 in range(60, 97, 8):
        for c1 in range(64, 97, 8):
            zz0 = 1.0 / (1.0 + ((k0 - c0) / 8.0) ** 2)
            zz1 = 1.0 / (1.0 + ((k1 - c1) / 8.0) ** 2)
            arr += 200.0 * np.outer(zz0, zz1)
    idx, pos = _lock_discrete_traces(arr, 0)
    assert idx, "应有迹线入选"
    # 离散峰位于 k0=30;大团位于 k0>=60。多数入选迹线峰位应落在离散峰附近
    near = sum(1 for p0 in pos if abs(p0 - 30) <= 5)
    assert near / len(pos) >= 0.7, (near, len(pos))
    assert len(idx) < n1, "不应选中全部迹线"


def test_joint_recheck_tie_keeps_fixed() -> None:
    """联合复核 p1 平坦(±5° 同分)时,不应显著优于顺序固定(调用方按
    PHASE_SCORE_FLAT_MARGIN 门控,不再整体回退)。"""
    from workflow.memory_phase_search import PHASE_SCORE_FLAT_MARGIN

    size = (96, 80)
    arr0 = _complex_axis_2d(size, axis=0, p0=-30.0)
    arr1 = _complex_axis_2d(size, axis=1, p0=-20.0)
    est0 = search_axis_memory(arr0, axis=0)
    est1 = search_axis_memory(arr1, axis=1)
    assert est0 is not None and est1 is not None
    fixed = {"F1": est0.phase, "F2": est1.phase}
    arrays = {"F1": arr0, "F2": arr1}
    index = {"F1": 0, "F2": 1}
    traces = {"F1": est0.traces, "F2": est1.traces}
    best, best_score, fixed_score, zero_score = joint_recheck_memory(
        arrays, index, traces, fixed
    )
    # p1 平坦:联合最优不会比顺序固定显著更优(否则门控会更新)
    assert best_score - fixed_score < PHASE_SCORE_FLAT_MARGIN + 1e-9
    assert fixed_score >= zero_score - 1e-9


def test_joint_recheck_row_scoring_matches_full_array() -> None:
    """补29fi:joint 行式评分与旧全数组 score_axis_memory 等价(相位/评分)。"""
    import itertools

    size = (96, 80)
    arr0 = _complex_axis_2d(size, axis=0, p0=-30.0)
    arr1 = _complex_axis_2d(size, axis=1, p0=-20.0)
    est0 = search_axis_memory(arr0, axis=0)
    est1 = search_axis_memory(arr1, axis=1)
    assert est0 is not None and est1 is not None
    fixed = {"F1": est0.phase, "F2": est1.phase}
    arrays = {"F1": arr0, "F2": arr1}
    index = {"F1": 0, "F2": 1}
    traces = {"F1": est0.traces, "F2": est1.traces}
    axes = list(fixed)
    combos: list[dict] = []
    for combo in itertools.product((-5.0, 0.0, 5.0), repeat=2):
        ph = dict(fixed)
        for axis, off in zip(axes, combo):
            p0, p1 = fixed[axis]
            ph[axis] = (p0, p1 + off)
        combos.append(ph)
    combos.append({axis: (0.0, 0.0) for axis in axes})

    def ref_score(ph: dict) -> float:
        vals: list[float] = []
        for axis in axes:
            idx, pos = traces[axis]
            vals.append(
                score_axis_memory(
                    arrays[axis], index[axis],
                    ph[axis][0], ph[axis][1], idx, pos,
                )
            )
        return float(np.mean(vals))

    best_ref = max(combos, key=ref_score)
    best, best_score, fixed_score, zero_score = joint_recheck_memory(
        arrays, index, traces, fixed
    )
    assert best == best_ref, (best, best_ref)
    assert abs(best_score - ref_score(best_ref)) < 1e-6
    assert abs(fixed_score - ref_score(fixed)) < 1e-6
    assert abs(zero_score - ref_score({a: (0.0, 0.0) for a in axes})) < 1e-6
