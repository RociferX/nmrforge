# 实验模板（presets/）

模板 = 先验 + 约束 + 期望行为（框架 §43），具体参数由优化器决定。
YAML 由 core/experiments/registry.ExperimentTemplate.from_yaml 加载（Phase 2 实现）。
每个模板的 `peak_sign` 描述峰符号约定：`uniform`＝信号峰同号（HSQC/CBCA(CO)NH 等），
`mixed`＝正负峰共存（HNCACB 等，13Cα/13Cβ 反相）。相位优化按此做早约束：
mixed 用「|净吸收| 中位数 + 正负共存」评分,uniform 用签名净吸收。
`peak_sign_regions` 给出化学位移分区符号先验（如 HNCACB 13C 轴 Cα/Cβ 的
ppm 区间与期望符号），用于 mixed 实验的 ±180° 绝对符号消歧。注意绝对符号
约定随脉冲序列/处理方式可能翻转，本预设默认值来自 VM sampleB 实测
（Cα 负/Cβ 正），若数据集相反可整体取反。
每个模板的 `priors` 给出各核的化学位移范围，可用于实验类型判断时按化学位移进一步确认核
（例如 HNCO 的 13C 应落在 165–185 ppm 羰基区，与 HNCACB 的 10–80 ppm 区分）。

## 2D 谱

- hsqc.yaml：15N-1H HSQC（1H 6–11, 15N 90–135）
- hsqc_13c.yaml：13C-1H HSQC（1H 0.5–6.5, 13C 10–80）
- hmqc_15n.yaml：15N-1H HMQC
- hmqc_13c.yaml：13C-1H HMQC
- hmbc_13c.yaml：13C-1H HMBC（远程, 13C 10–200）
- hmbc_15n.yaml：15N-1H HMBC
- cosy.yaml / tocsy.yaml / noesy.yaml / roesy.yaml：1H-1H 同核（两轴同为 1H, 0–10 ppm）

## 3D 谱（蛋白三共振/同核）

- hnca.yaml：HNCA（13Cα 40–70）
- hncacb.yaml：HNCACB（13Cα+β 10–80）
- cbcaconh.yaml：CBCA(CO)NH（13Cα+β 10–80）
- cbcanh.yaml：CBCANH（13Cα+β 10–80）
- hnco.yaml：HNCO（13C 羰基 165–185）
- hncoca.yaml：HN(CO)CA（13Cα 40–70）
- hncaco.yaml：HN(CA)CO（13C 羰基 165–185）
- hnha.yaml：HNHA（第三维 1Hα 0–11）
- hcaconh.yaml：H(CA)NH
- hcconh.yaml：H(CCO)NH（13C 脂肪 10–70）
- ccconh.yaml：C(CCO)NH（13C-13C 两轴）
- hbhaconh.yaml：HBHA(CO)NH（1Hα/β 0–11）
- hcch_tocsy.yaml：HCCH-TOCSY（1H-13C-1H）
- cch_tocsy.yaml：CCH-TOCSY（13C-13C-1H）
- noesy_hsqc_15n.yaml：3D NOESY-HSQC（15N 编辑, 1H-15N-1H）
- noesy_hsqc_13c.yaml：3D NOESY-HSQC（13C 编辑, 1H-13C-1H）

## 通用回退

- generic_2d.yaml / generic_3d.yaml：未识别实验的保守管线。
