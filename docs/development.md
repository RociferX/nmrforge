# 开发流程

## 环境

- Windows 写码（本仓库）；Linux 测试虚拟机（NMRPipe/SMILE 后端）后续接入。
- 入口：`python main.py`（首次运行自动创建 venv `nmrforge/` 并 `pip install -e .`）。
- 依赖见 pyproject.toml；numpy 限制 <2.5（nmrglue 0.11 的 dtype 别名问题）。

## git 工作流

- 分支 `master`；一次提交对应一个逻辑变更。
- 例行：本地改 → `pytest`（`--basetemp`）→ `ruff check .` → commit →
  （如有 VM）push/pull → 全量回归。

## 测试

- `pytest` 全量；GUI 测试用 `QT_QPA_PLATFORM=offscreen`。
- **全路径回归（强制，0.2.163-补8）**：每次修改代码后必须运行
  `python -m pytest tests/test_full_paths.py`。该测试覆盖自动与人工入口的
  2D/3D uniform + NUS 主路径（含诊断与处理参数优化）。Batch 当前仅支持 2D，
  不应表述为覆盖全部四路径；该能力边界记录为审查项 `BATCH-012`。
  新增或改动处理流程时必须同步更新该文件。
- 不依赖真实 NMRPipe 的测试优先（FakeBackend/MockBackend 模式）。
- Windows 沙箱默认 basetemp 被 ACL 锁死：pytest 必须带
  `--basetemp=<新临时目录>`（如 `$env:TEMP\pytest_nmrforge`）。

### 测试/临时产物落点(强制,2026-09-13 用户)

**home 根目录(~)不是工作区**:测试、脚本、venv、临时文件都不许放在那里,
也不许散落到别的目录。约定如下:

| 内容 | 该放哪 |
| --- | --- |
| 测试产物(pytest temp / pycache / ruff cache / 冒烟日志) | VM: `~/nmrforge-test-artifacts/`(`scripts/vm_test.sh` 已指向);本地: `--basetemp`/`$env:TEMP` |
| 需要长期保留的脚本 | 仓库 `scripts/`(随 git 版本管理) |
| 一次性排查脚本 | 用完即删;确需留存则归档到 `~/archive/<日期>/scripts/` 并写 README 说明来源 |
| Python 环境 | 用项目自己的 venv(`~/NMRForge/nmrforge`);不要在 home 另建 venv |
| 研究/数据产物 | 各自项目目录内(如 `~/nmr-uncertainty/studies/`),不要放 home 根 |

**测试夹具纪律(强制,2026-09-16,Phase 12)**:测试**不得**依赖开发机上的绝对路径文件
(例如某个开发机上的绝对路径)。这类依赖不会报错,只会让整组
用例在 VM/CI 上被 `skipif` 静默跳过,等于没有测试。需要真实文件布局时,在
`tests/conftest.py` 里用合成数据复刻布局,并在夹具内部断言解析器确实接受它。示例:
`nmrpipe_fid_template`(2D NMRPipe fid:`FDDIMCOUNT=2`、`FDQUADFLAG=0`、
**`FDF2QUADFLAG=0`**、2048 字节头 + 每迹实部块/虚部块),触点清单见
`docs/manager/project_map.md` §5.16。

清理检查(VM):

```bash
ls ~/*.py ~/*.sh ~/*.csv ~/*.yaml 2>/dev/null   # 应为空
du -sh ~/* ~/.[!.]* 2>/dev/null | sort -h | tail
```

## 改动前先备份(强制,2026-09-17 用户)

改任何文件之前,先保证这份状态是**可恢复**的:

1. 已提交的部分天然有三份:本地 git、`vm` 远程、VM 工作副本 `~/NMRForge`;
2. **进行中的改动**(未提交)必须先备份再继续:
   ``git add -A && git stash create`` 得到一个 stash 提交,给它打标签
   ``backup/<日期>-<主题>``;再把完整仓库与补丁归档到
   ``~/archive/<日期>/<主题>/``(``git bundle create … --all`` + ``wip.patch``);
3. 备份失败/未做就不许继续改;恢复方式:``git checkout backup/<标签> -- <文件>``
   或 ``git bundle clone`` 到新目录。

一轮改动完成后,备份标签随主题保留(不删),便于事后回溯「改之前长什么样」。

## 测试分类(unit / integration / regression)

分类表的**单一来源**是 [`tests/categories.py`](../tests/categories.py),`tests/conftest.py`
按文件名给每个用例打标记(不挪文件——审计 F.2 已判定夹具路径依赖让挪目录得不偿失):

```bash
pytest -m unit          # 纯逻辑,秒级:提交前的快速回归
pytest -m integration   # 真实 I/O / Qt / ProjectManager
pytest -m regression    # 缺陷、审查整改与发布不变量的守卫
```

**新测试文件必须登记进 `tests/categories.py`**,否则 `tests/test_test_categories.py` 失败
(未登记的按 `integration` 保守处理)。

## 公开 API 的 docstring(强制,2026-09-17,Phase 19)

公开入口必须写清五节,缺一节 `tests/test_api_docstrings.py` 就失败:

```text
Parameters(含类型与物理含义)/ Returns / Raises / Side effects / Examples
```

覆盖范围:对外 `nmrforge_api` 的处理与研究入口、`workflow/batch.run_batch`、
`core.peaks.localize.localize_peak`、QC API(`core.qc.spectrum_quality.evaluate`、
`core.audit.qc_audit.read_audit`、`workflow.direct_diagnostics.*`)、sampling API
(`core.experiment.sampling_detector.detect`、`core.data.nus_reader.*`)。
**内部私有函数不要求**完整用户文档;新增公开入口时要把它加进守卫清单并补文档。

## 日志(Phase 22,强制)

- 库代码(`core/` `backend/` `workflow/` `viewer/` `gui/`)只做
  `logging.getLogger("nmrforge.<模块>")`,**不在 import 期配置 handler**;
- 入口负责配置:GUI 的 `main.py` 与命令行 `python -m nmrforge_api` 都调用
  `core.logging_setup.configure_logging()`。级别取 `NMRFORGE_LOG_LEVEL`
  (DEBUG/INFO/WARNING/ERROR,默认 WARNING);日志写 **stderr**——CLI 的 stdout 只放 JSON;
- **界面层禁止裸 `print()`**(`tests/test_logging_setup.py` 扫描 `gui/`、`viewer/` 守卫);
  需要给用户的输出走界面控件/日志面板,需要给运维的输出走 logging;
- 每次 run 的日志:`core.logging_setup.attach_run_log(<run_dir>)` 在该目录挂 `run.log`
  (延迟创建:没有日志记录就不留空文件;结束时 `detach_run_log`)。失败路径用
  `logger.exception` 把完整 traceback 写进 `run.log`。API 逐 run 的 `log.txt` 仍是处理日志
  契约的一部分,两者并存、互不改名;
- 日志文本涉及绝对路径时用 `core.logging_setup.sanitize_path()` 把 home 折成 `~`。

## 用户可见错误与调试信息(强制,2026-09-17,Phase 21)

用户看到的消息必须能照着改:

- **前端/CLI 出口**把路径、权限、输入格式、缺字段这类问题翻译成「错误: <怎么修>」;
  已知接口错误(`nmrforge_api.errors.SensitivityError` 家族)的消息本身就是给用户写的,直接用;
- **禁止**只显示 `KeyError` / `IndexError` / `TypeError` / `NoneType` 之类的类型名或裸 traceback;
- 完整 traceback 只走 debug 通道:CLI 用 `--debug` 或 `NMRFORGE_DEBUG=1`(打印到 **stderr**),
  GUI/后端写进运行日志,不进对话框正文;
- 未知异常保留类型名 + 原文(不吞信息、不编造原因),但同样加一句可执行提示。

参考实现:`core/user_errors.py::describe_exception`(GUI/CLI 共用的翻译,Qt-free 叶子模块)与
`nmrforge_api/cli.py::_report_unexpected`(CLI 出口)。守卫测试 `tests/test_user_errors.py`
会扫描 `gui/`、`viewer/`,一旦出现 `type(exc).__name__` 形式的用户可见文本即失败。

## 处理流程改动覆盖原则(强制,0.2.163-补15)

用户对处理流程(生成 FID/生成谱图/人工/批量等)提出的改动,默认必须
同时应用到全部四种路径:**2D uniform / 3D uniform / 2D NUS / 3D NUS**
(自动与人工两条入口亦然);只有改动确实只属于某一个或某几个路径特有
(如 NUS 的 SMILE 重构、3D 的切片流、uniform 无重构)时,才允许只改
对应路径,并须在改动说明/CHANGELOG 中写明例外原因。
执行检查:`tests/test_full_paths.py` 已覆盖四种路径(自动 2D/3D uniform
+ NUS、人工、批量),每次改动后必须全绿。

### 四路径功能对齐检查(强制,0.2.166)

新增/改动任何处理功能时,对照以下清单确认四条路径(2D/3D × uniform/NUS,
自动/人工)没有「一条有、其它没有」的差异:

- 参数开关透传:direct_poly_time、window/baseline/zero_fill/extract/ext
  窗口、segment_shift_hz、phases;自动相位为实验类型级回退
  (presets processing_hints.auto_phase,幅度谱如 HMBC 全轴跳过搜索),
  sampling.auto_phase 仅 route=none 逃生口直连后端时有效;
- 流程能力:数据质量诊断、直接维窗/基线/填零/间接窗优化、初跑脚本保留
  (before_optimize.com)、终跑质量与优化汇总、直接维相位缓存指纹
  (必须包含影响谱面的所有参数,如 window);
- 宏语义:以目标 NMRPipe 版本宏定义为准(如 gaussian→GMB),禁止
  「看起来对」的写法;
- 实验类型模板:presets/*.yaml 是唯一数据源(0.2.111);新增模板必须
  同步补 `_PULPROG_TYPES` 关键词(长/具体关键词排前,防子串抢先匹配)
  并跑 test_data_understanding 分类精排回归;幅度谱(phase_sensitive:
  false)模板必须标 processing_hints.auto_phase=false;
- 核组合匹配约定(0.2.168):直接维核精确匹配、间接维按核种类计数
  (不计顺序)——同一核在直接维 vs 间接维视为不同指纹(HETCOR 13C@直接
  维 vs HSQC-13C 13C@间接维);间接维模板顺序可任意(历史不统一),
  但同核出现次数必须准确(如 NNH 两个 15N);viewer 同核投影走文件头
  槽位 + Hx/Hy 下标标签,不得按核种类从 3D 谱取轴;
- NUS/SMILE 特有功能(fid_noise、内存护栏、nuslist 清理、轻量/显示层
  相位搜索、直接维相位缓存)允许保留差异,但必须在代码注释/CHANGELOG
  写明例外原因。

## 脚本生成约定(强制,0.2.165)

所有 nmrPipe 宏参数必须以目标版本宏定义为准,不允许「看起来对」的写法:

- 高斯窗(gaussian)一律渲染为 `nmrPipe -fn GM -g1 X -g2 Y`(0.2.170,
  NMRPipe 原生参数,缺省 g1=8/g2=15)。禁止 GMB -lb/-gb(实测窗在 FID
  尾部爆炸放大,谱图完全不对)与 GM -lb/-gb(被静默忽略,窗不生效);
  lb/gb(Bruker 语义)不再直接映射;
- 直流偏置纠正 POLY -time 只进终跑完整脚本(direct_poly_time),首遍
  复型预览不加(避免带偏直接维相位搜索);uniform 由
  generate_process_script 的 direct_poly_time 控制,NUS 由
  generate_2d/3d_nus_script 控制,四条路径必须一致;
- process() 只有在真正执行 bruker→NMRPipe 转换时才发「开始转换 fid」,
  复用已转换 fid 时发「复用已转换 fid(跳过转换)」;不得无条件发转换
  进度(unified 的 preview/joint/候选会多次调用 process)。

## 旧项清理原则(强制,0.2.164)

改动/新增一个功能时,必须同时清理被其取代或重复的旧实现:

- 被新路径取代的旧实现(旧相位优化、旧自动编排、本地等价实现等)一律在
  本次改动中删除或合并到新路径,不允许「新代码上线、旧代码留守」;
- 死代码(产品与测试均无引用)、未接线的骨架占位、仅测试/验证脚本使用的
  旁支模块,一并删除(旧 git 历史保留,可随时恢复);
- 同一职责只保留一个实现:配置加载、Experiment 读取、工作区、峰表 IO、
  相位搜索等重复实现优先合并到新路径(0.2.164 已按此收敛);
- 删除必须同步:关联测试、scripts/ 工具、ownership 前缀、文档引用
  (README/PROJECT_STATE/architecture/AGENT_PROMPTS/API_CONTRACT)、
  __all__/包导出,全部一起更新;
- 交付检查:清理后 `pytest` 全量 + `ruff check .` 全绿;
  `git diff --stat` 删除行数应显著大于新增行数(纯清理改动)。

## 报告约定(强制,0.2.169)

- 末尾报告固定三节结构(`_append_final_summary`):◆ 最终谱图质量(综合
  判定 + 信噪比/相位/基线/伪影分项等级分数 + 基线指标 + 检查说明)、
  ◆ 数据质量诊断(检出 N 项/已自动处理 M 项结论 + 明细)、◆ 处理参数
  与优化;不得再退回单行浓缩式「谱图质量: ...」;
- 数据质量诊断(处理前)的日志必须排在流程最开头(uniform 与 NUS 一致),
  不得延后到优化之后;
- 日志按选中上下文隔离(0.2.186):单个数据独立 log、数据组组内共用、
  实验类型实验级、其余归全局;选中变化由主窗口 set_scope 切换显示,组批量
  进度显式路由到组作用域——不得把不同数据的日志混进同一缓冲区。
- 人工运行/重新运行终脚本必须实时转发脚本输出(0.2.188):run_manual_*
  经 progress 透传 CshRuntime.run(on_line=...),GUI 逐行显示;不得只输出
  最终完成一行而无中间进度。
- 窗函数(0.2.190):直接维/间接维均为真实优化——候选池含无窗(none),
  用 FWHM/SNR/线形内存评分(不用 spectrum_quality,避免相位/基线联动把
  窗从无窗带偏);间接维评分带分辨率保留因子,自然衰减轴正确落到无窗、
  截断轴选温和窗;顺序约束:基线校正先于窗函数优化(2 基线 → 2.1 重渲
  → 2.5 直接维窗 → 3 间接维窗);直接维候选 0.5-0.98 优先
  (off=0.5 end=0.98 pow=2),分辨率过滤 1.25x;gaussian 不做缺省候选
  (GM 模型未经源确认,渲染/手工配置仍支持)。
- 窗向量必须与 NMRPipe 逐点一致(0.2.191):SP 首点乘 -c(SP 缺省 0.5,
  GM/EM 缺省 1.0);GM 高斯常数 k=1/(2*sqrt(ln2))=0.6005612(VM 实测,
  非 nmrglue 0.6 近似),GM/EM 依赖谱宽 SW(取 fid 头 FDFxSW,轴标签
  F1→FDF1SW);间接维分辨率池 1.15x(直接维 1.25x),自然衰减轴确定落无窗。
- GM 加入直接维缺省候选(0.2.192):GM g1=8 g2=15(g3=0,c=1.0),依赖谱宽
  SW,未提供 SW 时跳过 GM/EM 候选;间接维候选池不加 GM(保持自然衰减轴
  无窗)。人工脚本编辑器:非模态 show 打开(不锁定主界面),「保存」立即
  落盘并关闭,「运行」先保存、发 run_requested 后自动关闭。
- 脚本编辑器打开必须快且不重复(0.2.193):质量诊断只在点「运行」时执行
  (run_manual_spectrum 内),打开编辑器只读脚本不重跑诊断;同数据同步骤
  按 (data_id, step) 去重,重复点击只聚焦已开窗口,不同数据/步骤可并存。
- 导入/数据组间分析下拉保持主窗口子部件方案(0.2.194 修订):不要用独立
  顶层窗口(位置问题无法解决,0.2.163-补4 因此弃用);子部件必须设置
  Qt.WA_AlwaysStackOnTop,保证绘制在中央部件之上(Windows 首次弹出
  不可见即缺该属性导致被盖住),并相对主窗口坐标定位。
- NUS 转换/坏点删除必须按采样参数判断布局(0.2.195):fid.com 中
  nusExpand 与 bruk2pipe 必须使用同一 NusTD 网格(patch_fid_com 强制
  nusExpand -yT/-zT,xN 保持 serPadSize 补齐值不被 acqus TD 覆盖);
  坏点源头删除按 _ser_point_layout 推导每点字节块(直接维 TD 补齐 +
  字长 + 冗余数),不符时回退生成 FID 清理;fid 清零按 States 布局
  切片 2*f1+1/2*f1+2、行 2*f2/2*f2+1。
- 坏点潜在问题只报告不自动处理(0.2.196):NaN/Inf、uniform 全零迹线、
  持续异常高能量迹线(>100×非零能量中位)在直接维诊断中报告并给指标,
  不改数据;ser 行数与 nuslist 点数不一致/冗余数不一致时在源头清理
  日志中明确报告并回退,不强行删除。
- 坏点移除后网格必须按实际采样范围调整(0.2.197):源头删除坏点
  (source_removed)后按清理后 nuslist 每维 max+1 推导网格,更新
  acqu2s/acqu3s 的 NusTD(只缩小);交叉验证(参数修正)必须使用调整后的
  NusTD,不得把 fid.com 网格改回静态原值——否则 fid 网格与清理后数据
  不一致,重构错位。
- 分段容器判定(0.2.198):数据段 = 直接含 acqus 的子目录;缺 acqus 或
  无数据的子目录一律忽略,不报「顶层缺 acqus」;子目录参数不一致时明确
  报「不是分段实验」。任何会修改 acqus/acqu2s/acqu3s/nuslist/ser 等原始
  文件的程序必须先备份 .bak(仅首次),暂存目录用复制而非硬链接,防止原地
  写入穿透链接污染原件。
- 分段 3D NUS 处理(0.2.199):patch_fid_out_name 改名主输出时必须同步改
  mask 阶段的 `-in`;convert_to_fid 分段分支要像 reconstruct_nus 一样先
  清理坏点再按合并 nuslist 实际范围调整 NusTD;分段数据的质量检查按首段
  评估(raw/ 容器根目录无 acqus/ser,不得误报)。
- 分段与普通 NUS 一致走切片流(0.2.199-补1):分段各段同样应用 acqu3s TD
  修正暂存(先 .bak 备份,副本内修改),bruker 直接输出 fid/test%03d.fid
  切片;_split_slices 在输出目录已有切片时跳过拆分(只处理单文件输出),
  多段合并仍由 _merge_slices 完成。
- 人工 fid.com 单数据集与分段一致(0.2.199-补2):人工改的参数经
  parse_fid_com 提取为 overrides,由后端 convert_to_fid 统一执行转换/
  坏点清理/归位,不得直接 csh 运行用户脚本(结构性改动不保留);人工
  编辑器提示头说明「只调参数,勿改输出名」。
- 直接维范围可应用到优化过程(0.2.199-补3):范围对话框默认开启「应用此
  范围到优化过程」——开启时用户范围同时用于首遍重构/相位搜索与优化评估
  (基线/填零/窗函数),并降低 SMILE 内存;关闭时优化用默认 6.5-10.5 大范围,
  仅终跑用该范围。SMILE 内存不足提示须同时给出「收窄直接维范围+开启该选项」
  的建议。
- pipeline 步骤行按钮区必须能自动折行(0.2.199-补4):生成谱图完成后一行
  最多 5 个按钮(直接维范围/重新优化/重新运行终脚本/展示谱图/人工),宽度
  不足时自动另起一行,不得单行硬撑;隐藏按钮不占位。
- 左侧树数据运行中状态(0.2.199-补5):任何处理过程(自动步骤/重新运行终
  脚本/人工脚本/数据组批量)开始时,左侧树对应数据显示「运行中」,结束后
  恢复产物推断状态;标记须在主线程维护(后台线程经排队信号),不得直接
  跨线程触碰界面。
- 内存相位搜索必须快且可取消(0.2.199-补6):直接维相位评分只允许对信号
  窗行做旋转(先取行后旋转,与全平面旋转结果一致),不得每次对全平面复乘;
  搜索网格循环必须检查 backend.runtime 取消标志并在检查点抛「任务已取消」,
  每 ~100 次评分输出一次进度。「终止任务」必须同时请求取消内存计算
  (terminate_current_tasks + request_cancel)并清扫不在注册表的孤儿 NMRPipe
  进程(cleanup_orphan_tasks,按工具名/工作区路径匹配);主窗口 closeEvent 同样
  先 terminate + request_cancel 再 cleanup,防止关闭应用遗留后台进程。
- 基线优化必须带进度且可取消(0.2.199-补7):optimize_baseline 逐轴/逐候选
  输出评分进度,cancel 置位时在候选间检查并抛「任务已取消」;调用方
  (phase_routes uniform/NUS)必须透传 progress 与 cancel_requested。任何
  逐迹稳健拟合/全谱评分类内存优化同样适用此约束。
- 基线优化必须控制开销(0.2.199-补8):候选评分只允许在迹线子采样副本
  (≤max_traces,默认 4096)上进行,不得在全部迹线上做稳健逐迹拟合;off
  评分 ≥95 的轴跳过候选直接保持 off;子采样保持 ndim,off 与候选在同一
  基底上比较,选择语义不变。
- 基线优化条纹否决必须是相对语义(0.2.199-补9):只否决比原谱明显更差的
  候选(校正后条纹比 > 原谱+4 且仍 >8),原谱已有条纹时允许改善性校正;
  原谱条纹比 >8 的轴禁止子采样(细条纹会被漏检),必须全量评估;日志必须
  区分「被条纹否决」与「增益不足」,不得笼统写「候选未优于当前配置」。
  条纹比封顶(0.5)且各候选评分与 off 相同/更差时,竖条纹是数据/采集伪影
  (DC 偏置、能量分布不均),不是可被 POLY 校正的基线,保持 off 并如实报告。
- Qt 事件 API 统一用绑定实际提供的名字(PySide6/`qtcompat`;0.2.199-补10 起):拖动处理路径
  不得使用不存在的 scenePosition;QGraphicsSceneMouseEvent 取 scenePos(),
  普通 QMouseEvent 取 position(),按类型分支;3D slice 拖动条保持足够长
  (最小宽度 ≥220)。
- NUS 直接维窗必须固定 SP(0.2.199-补11):SMILE 要求直接维输入已加窗且
  尾部衰减到零(实验室 smile.com 同款),直接维窗优化候选(none/gaussian/
  exp)不得覆盖直接维;generate_2d/3d_nus_script 对直接维一律生成 SP 行
  (sine_bell 系按配置,其余回退默认 SP);SMILE 内部错误("SMILE Error" 在
  输出中)必须显式判为失败,禁止把失败重构当成功出谱。
- GUI 主线程禁止读大谱算质量(0.2.199-补12):生成谱图参数报告
  (spectrum_quality_report_lines 读整张 ft3 + 全谱评估)必须按谱文件指纹
  + 参数指纹(路径+mtime+大小+参数 SHA256)缓存,谱与参数未变不得重复
  读取;同一面板同一时刻只允许一个运行(运行中标志),防止连续点击叠加
  任务卡死。
- SMILE 必须每次按当前可用内存设置 -maxMem(0.2.199-补14):重构前实时
  计算 MemAvailable×MEM_SAFETY 并传入 generate_2d/3d_nus_script(max_mem),
  与内存护栏同一预算;不得依赖 SMILE 自动上限,防止预估偏差/并发占用
  导致峰值超限硬扛。脚本生成器缺省不生成该行(测试字节级稳定)。
- SMILE 内存估计必须对齐 SMILE 自报口径(0.2.199-补15):模型 = 直接维
  点数 × 间接维迭代 FT 尺寸乘积 × 16B(复 double)× 1.06,迭代 FT =
  next_pow2(3×NusTD);改常量前先用 SMILE 启动横幅 Memory Used 实测点
  校验(sampleK 填零1024 = 2.8GB,网格 146×145→219×217→FT 1024²)。
- NUS 直接维相位必须在复型频域终谱上搜(0.2.199-补18):recon 平面是间接维
  时域,单点时域迹线被 t1 混叠,直接维搜索不得在 recon 平面上做;finalize
  keep_complex(全部 PS 不加 -di)输出复型频域终谱,直接维=最后一轴
  (3D),在此轴上评分;2D 保持 recon.ft1 轴 0。布局约定:nus3d_rc 平面
  为「每直接维点一个平面」(0.2.85 切片流起),平面内是间接维——任何
  按「直接维在平面内轴」的假设都是过时错误。
- 直接维对称性评分(0.2.199-补19~21):峰窗对称性必须形状感知——纯对称
  计分(负吸收峰同样左右对称),符号惩罚只用于同号谱(负窗 sym×0.2 轻罚,
  异常反号峰降权);正负峰共存谱(mixed)用全局纯对称;±180 由近最优平台
  最小修正消歧,平台容差 1(不得 5)。实验模板 peak_sign 必须正确
  (溶液 HNN=hncannh/hncocannh→mixed,勿误映射固体 NNH=uniform)。
- 转换必须单文件化(0.2.199-补16):四种途径(自动/人工 × 单数据集/分段)
  fid.com 一律生成单文件 {dataset_id}.fid(bruker -AUTO 按 NusTD 网格 +
  -aq2D Complex,与实验室 fid.com 一致);不得再用 acqu3s TD 修正暂存
  (TD=1 时 bruker 同样输出单文件全网格 fid);3D NUS 切片只在 SMILE 脚本
  step1 直接维处理后产生(nus3d_1/test%04d.ft1);多段合并用 addNMR 合并
  单文件(merged/{dataset_id}.fid),段频移用 PS -rs。转换期切片是历史产物,
  新代码不得生成;_zero_bad_point_fid 的切片回退仅兼容旧工作目录。
- 开发环境 SMILE 测试内存上限(0.2.199-补15):开发 VM(16GB,宿主 32GB,
  电源/散热不稳)已验证安全的 SMILE 峰值 ≤ 2.8GB(填零1024,sampleK);
  ≥5.6GB(填零2048 直接维翻倍)会触发宿主意外断电。测试数据/复跑必须
  保证 estimate_smile_peak_mb ≤ 2.8GB,禁止逼近 5.6GB 量级;
  test_dev_smile_memory_ceiling 固化该约束。
- 质量评估报基线不平时必须对照基线优化结果输出原因(评估基准差异/
  保持 off 门槛),不得只报「建议基线校正」;基线评估逐存储轴取最差并
  标注最差轴(0.2.170),不得只检最后一个轴;
- 相位评分必须与相位优化同源(0.2.172/0.2.185):峰窗签名净吸收
  (score_axis_memory 同公式),sign_mode 由实验类型模板 peak_sign 决定——
  mixed 谱(正负峰共存)真实负峰不算相位错误,禁止用负面积/负峰比例把
  吸收型负成分误罚;四条路径(2D/3D × uniform/NUS)相位优化评分统一为
  净吸收(NUS 直接维 0.2.185 起与其余路径同源);
- 净吸收正负判定先拉平基线(0.2.185):峰窗减基线水平(峰两侧基线区
  中位数均值)后再分正负,不以零为界——基线整体偏移不再污染净吸收;
  偏移≥1%峰高才拉平;1D 合成谱保持零界;
- NUS 直接维用对称性搜索(0.2.95/0.2.187):recon 平面直接维上净吸收
  评分区分度差且 mixed 无法消歧 ±180°,直接维按正峰谱对称性+正峰约束
  处理;间接维保持净吸收。直接维与间接维评分途径不同是有意的(见 0.2.187)。
- 报告内容面向最终用户:用 ✓/⚠/✗、良好/需注意/较差、明确建议,避免
  只有技术指标没有结论。

## 静态检查

- `ruff check .` 应全仓通过。

## 提交约定

- 消息风格：`feat:` / `fix:` / `refactor:` / `docs:` + 中文简述。
- 文档与代码同步更新（CHANGELOG / PROJECT_STATE / README）。

## Windows 沙箱经验（重要）

- 沙箱辅助进程常报 ACL 错误：普通 shell 命令需提权
  （`sandbox_permissions=require_escalated`）执行。
- apply_patch 修改已有文件可能被 ACL 拒绝；可用模式：
  「apply_patch 新建补丁脚本 → 提权 python 执行 → 删除脚本」。
- 中文不要直接进 exec_command 参数（会乱码）：中文只能放 UTF-8 文件里
  （补丁脚本/文档），脚本内用 `open(..., encoding='utf-8')` 读写。
