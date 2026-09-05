# 实验模板（presets/）

模板 = 先验 + 约束 + 期望行为（框架 §43），具体参数由优化器决定。
YAML 是模板的单一数据源(0.2.111 起):core/experiments/registry.from_yaml +
load_presets 实现,导入 core.experiments 即从 presets/*.yaml 注册全部模板
(按显示名与文件 stem 双注册);原逐模块 Python 模板已删除,不再双源维护。
每个模板的 `peak_sign` 描述峰符号约定：`uniform`＝信号峰同号（HSQC/CBCA(CO)NH 等），
`mixed`＝正负峰共存（HNCACB 等，13Cα/13Cβ 反相）。相位优化按此做早约束：
mixed 用「|净吸收| 中位数 + 正负共存」评分,uniform 用签名净吸收。
`peak_sign_regions` 给出化学位移分区符号先验（如 HNCACB 13C 轴 Cα/Cβ 的
ppm 区间与期望符号），用于 mixed 实验的 ±180° 绝对符号消歧。注意绝对符号
约定随脉冲序列/处理方式可能翻转，本预设默认值来自 VM sampleB 实测
（Cα 负/Cβ 正），若数据集相反可整体取反。
每个模板的 `priors` 给出各核的化学位移范围，可用于实验类型判断时按化学位移进一步确认核
（例如 NCO 的 13C 应落在 165–185 ppm 羰基区，与 NCA 的 13Cα 40–70 ppm 区分）。
化学位移区间按 BMRB 统计（Ulrich et al., Nucleic Acids Res. 36, D402 (2008)）
与固体核磁文献常用范围（1H -5–20、15N 90–140（同核 15N-15N 90–160）、
13C 脂肪 10–75、Cα 40–70、Cβ 15–45、羰基 165–185、13C 全谱 10–190）。

## 1D 谱(直接检测一维,无间接维,不选峰)

- generic_1d.yaml:未知 1D 的安全兜底(Generic1D,不在类型下拉显示);
- 1h_1d.yaml:1H 一维谱(1H-1D,auto_phase);
- 13c_1d.yaml:13C 一维谱(13C-1D);
- 31p_1d.yaml:31P 一维谱(31P-1D);
- 19f_1d.yaml:19F 一维谱(19F-1D);

1D 数据无间接维,生成谱图直连 process(无 SMILE/复型预览/参数优化),
默认不做 EXT(整谱保留);峰挑选步骤对 1D 隐藏。

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

## 固体核磁(液体核磁没有的类型,按核组合 + PULPROG 识别)

实验类型与化学位移范围来自公开文献:固体核磁实验库 ssNMRlib
(Vallet et al., Magn. Reson. 1, 331 (2020));13C 检测主链指认套件
(Wiegand et al., Biomol. NMR Assign. 10, 101 (2016));BMRB 化学位移统计
(Ulrich et al., Nucleic Acids Res. 36, D402 (2008))。PULPROG 关键词为
文献/实测常见命名(SPECIFIC-CP nca/nco、DARR/PDSD/RFDR/CORD/INADEQUATE/
TEDOR/PAIN-CP、FSLGhetcor 等),分类器按「核组合优先,同核组合候选中
PULPROG 精排」识别。

### 2D 15N-13C 主链 / 距离

- nca.yaml：NCA（15N-13Cα, SPECIFIC-CP, Baldus et al., Mol. Phys. 95, 1197 (1998)）
- nco.yaml：NCO（15N-13C' 羰基）
- tedor.yaml：TEDOR（15N-13C through-space 距离, Hing et al., JMR 96, 205 (1992)）
- paincp.yaml：PAIN-CP（15N-13C 质子辅助 CP, Lewandowski et al., JACS 129, 728 (2007)）

### 2D 13C-13C 侧链 / 长程

- darr.yaml：DARR（偶极辅助旋转共振, Takegoshi et al., Chem. Phys. Lett. 344, 631 (2001)）
- pdsd.yaml：PDSD（质子驱动自旋扩散, Szeverenyi et al., JMR 47, 462 (1982)）
- rfdr.yaml：RFDR（射频驱动重耦合, Bennett et al., J. Chem. Phys. 96, 8624 (1992)）
- cord.yaml：CORD（组合 R2nv 驱动, Hou et al., J. Chem. Phys. 139, 064201 (2013)）
- inadequate.yaml：INADEQUATE（13C 双量子 DQ, Bax et al., JACS 102, 4849 (1980)）
- hcc.yaml：HCC（数据命名 cshi.hCC_sd 的 13C-13C 自旋扩散/DARR 类相关）

### 2D 1H-X 相关 / 1H-1H 空间

- hetcor.yaml：HETCOR（1H-13C CP/FSLG, van Rossum et al., JMR 124, 516 (1997)）
- hnhcor.yaml：HNHETCOR（1H-15N CP/FSLG）
- chhc.yaml / nhhc.yaml：CHHC/NHHC（经 13C/15N 的 1H-1H 空间相关,
  Lange et al., JACS 124, 9704 (2002);核组合待真实数据验证）
- nn.yaml：NN（2D 15N-15N 质子辅助重耦合, RNA 碱基配对等）

### 3D 15N/13C/13C（13C 直接检测）

- ncacx.yaml：NCACX（15N-13Cα-13C 侧链）
- ncocx.yaml：NCOCX（15N-13C'-13C 侧链）
- ncacb.yaml：NCACB（15N-13Cα-13Cβ,Cα/Cβ 反相 → mixed）
- ncocacb.yaml：NCOCACB（15N-13C'-13Cα/β,mixed）

### 3D 13C/15N/13C 序列行走

- canco.yaml：CANCO（13Cα-15N-13C'）
- canco_ca.yaml：CAN(CO)CA（13Cα(i/i-1)-15N-13Cα,CO 仅转移不采集）
- cbcanco.yaml：CBCANCO（13Cα/β-15N-13C',Cα/Cβ 反相 → mixed）
- ccc.yaml：CCC（3D 13C-13C-13C 自旋扩散）

### 3D 1H 检测

- cch.yaml：CCH（1H 检测的 13C-13C 相关）
- nnh.yaml：NNH（1H 检测的 15N-15N 相关）

注:1D 类型(CP13C/CP15N/PROTON1D/C13_1D)暂无 YAML——test_gui_presets
目前只允许 ndim=2/3,待 GUI 侧放开 ndim=1 后再补。

## 通用回退

- generic_2d.yaml / generic_3d.yaml：未识别实验的保守管线。
