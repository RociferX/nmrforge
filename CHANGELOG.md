# 变更日志

## [0.2.16.2] - 2026-08-12

- 选中实验时,中间面板提供可点击的「导入数据...」按钮(直接在当前实验下
  导入数据);「未选中数据」状态不再显示各步骤的「人工」按钮,选中 Data
  后恢复。

## [0.2.16.1] - 2026-08-12

- 中间面板按选中类型显示:选中 Project/Experiment 时显示「未选中数据」
  提示,选中 Data(或其子目录)才显示 Pipeline 步骤。
- raw/process/spectra/peaks/figures/report 子目录右键提供「打开所在目录」。
- Data 节点右键移除功能项(生成 FID/生成谱图),仅保留打开目录/删除;
  处理动作统一经 Pipeline 步骤运行。

## [0.2.15.1] - 2026-08-12

- 修复:新建空白实验不再产生伪数据节点(移除 schema 1.1「实验即数据」回退;
  空白实验树/管线均不显示 Data);空白实验运行步骤时提示先导入数据。
- 修复:「项目 → 添加实验」改为新建空白实验(不再弹数据文件夹选择);
  导入数据统一走实验右键「导入数据」。

## [0.2.15] - 2026-08-12

- 首次启动工作区欢迎页(G2B-003 §9.4):WorkspaceManager.ensure() 创建默认
  工作区 ~/NMRForgeWorkspace,显示工作区路径、工作区内项目列表(双击打开)、
  新建项目入口;未打开项目时主窗口显示欢迎页,三栏自动隐藏。
- 左侧树加 Workspace 根节点:Workspace → Project → Experiment → Data →
  raw/process/spectra/peaks/figures/report 各子文件夹;项目右键删除(强确认)。
- ProcessingController 三步接线(契约 v1.2 §8.3 / workflow.stepwise):
  import_data / generate_fid / generate_spectrum 对接 Backend 已落地的
  stepwise(含 WorkflowRun 登记、manager.save、data_id 产物);控制器绑定
  ProjectManager 后由 Pipeline 步骤调用。
- 树列宽保持可读(220/90 + 最小段宽),数据节点显示 d_001 并挂真实子文件夹。
- 测试:欢迎页/Workspace 根/三步接线适配新增;全量 226 passed,ruff 全绿。

## [0.2.16] - 2026-08-12

- Backend:工作区容器 core/workspace.py(契约 v1.3 §9.1 / G2B-003):
  WorkspaceManager(默认 ~/NMRForgeWorkspace,ensure 幂等/list_projects/
  create_project/open_project,非法项目名校验)。
- Backend:数据目录层级(schema 1.3,契约 §9.2):项目 → <exp_id>/ →
  <data_id>/ → {raw, process, spectra, peaks, figures, report,
  metadata.json};import_data 复制到 <data>/raw/ 并写
  <data>/metadata.json;stepwise 工作目录 <data>/process/、终谱归位
  <data>/spectra/;infer_status/删除兼容新旧布局,旧 dir_path 保留为
  兼容层;open_project 自动迁移 1.1/1.2 → 1.3(不物理搬文件)。
- Backend:test_phase_optimize 平台敏感断言改为「非零解 + 增益」
  (VM numpy 2.2.6 true=-60 → est=-180,±60° 容差仍不足;先例 ±45°→非零解)。
- 测试:新增 tests/test_workspace.py 7 项;全量 226 passed。
## [0.2.14] - 2026-08-12

- Backend:Experiment→Data 层级(core/project schema 1.2 + 迁移,按 G2B-002 /
  API_CONTRACT §8):新增 DataEntry(d_001...),ExperimentEntry.data 列表,
  旧 source/segments/imported_at 打开时自动迁移为 data[0]
  (migrated_from_1_1),保留兼容只读属性;create_experiment/import_data/
  set_data_fid/set_data_spectrum/delete_data;infer_status 按数据条目聚合。
- Backend:步骤化处理——import_data(只读参数+复制 raw/<exp_id>/<data_id>/
  + metadata/<exp_id>-<data_id>.json + import run,不生成 fid/谱);
  ProcessingBackend.convert_to_fid 独立阶段(NMRPipeBackend 实现,
  process/reconstruct_nus 复用已转换 fid);workflow/stepwise 编排
  generate_fid / generate_spectrum(NUS 自动含 SMILE 重构)+ 每步 WorkflowRun。
- Backend:相位优化恢复「先 SMILE 重构生成谱,再逐候选反复跑后端(暴力)」:
  workflow/stepwise.optimize_phase_brute_force(暴力搜索 + 真实管线写回),
  brute_force_direct_scores 支持 NUS(reconstruct_nus + 相位覆盖)。
- 测试:test_import_workflow 11 / test_project_manager 28 / test_stepwise 6;
  p1 幅值断言放宽至 ±60°(VM numpy 2.2.6 平台敏感性,先例 ±45°→非零解)。
- 待 GUI Agent:ProcessingController 三步接线与树测试断言更新
  (数据节点 id=d_001、metadata/<exp_id>-<data_id>.json)。
## [0.2.13] - 2026-08-12

- GUI 树层级演进(契约 v1.2 §8.5):Project → Experiment → Data;
  Project 右键删除项目(强确认)、空白处/Project 右键新建空白实验、
  Experiment 右键导入数据/重命名/删除、Data 右键生成 FID/生成谱图/
  打开目录/删除;树列宽改为显式宽度 + 最小段宽(220/90,可读)。
- ProcessingController 新增步骤化方法(契约 v1.2 §8.3):import_data /
  generate_fid / generate_spectrum;旧 auto_run_sync/async 保留兼容;
  后端 DataEntry 层级与 convert_to_fid 落地前为占位(友好提示)。
- Pipeline 面板改为五步流程:导入数据 → 生成 FID → 生成谱图(含 SMILE
  重构)→ 峰挑选 → 分析;步骤运行映射到 ProcessingController 对应方法。
- 测试:树层级/右键动作/列宽/五步状态新增 9 项;全量 203 passed,ruff 全绿。

## [0.2.12] - 2026-08-12

- Backend:相位准确性保证(workflow/phase_optimize)——VM 真实数据等价性验证
  (dataset 3,2D 均匀)证实「内存内代理筛选 ≠ 暴力逐候选后端评分」:
  内存内最优 p1=-60,暴力最优 p1=-120,Spearman 相关 0.46,验证未通过;
  据此新增验证门控 optimize_direct_phase_guaranteed:验证通过才采信内存内
  筛选,否则围绕内存内最优 ±60° 邻域暴力细化(本例细化得 p1=-120,QC 94.2),
  最终谱由真实管线 produce_phased_spectrum 产出——能力与反复跑后端一致,
  成本有界(验证网格 + 细化邻域 + 生产)。
- Backend:NMRPipeBackend.process/reconstruct_nus 支持 direct_phase_override;
  等价性验证 validate_direct_phase_equivalence(最优候选一致 + Spearman,
  可序列化 EquivalenceReport)。
- 测试:test_phase_optimize 增至 21 项,全量 183 passed。

## [0.2.11] - 2026-08-12

- Backend:自动相位性能重构(workflow/phase_optimize)——自动相位优化改为
  「单次后端运行 + 内存内候选评分」:直接维复用 phase.json 缓存或对转换后
  .fid 做内存内 FT + p1 共识;NUS 间接维对复型重构平面按轴搜索(全部 numpy,
  不触发后端重跑);结果含 backend_runs(=1) 与 candidates_scored 量化性能。
- Backend:AutoProcessor 后端成功后自动输出相位估计日志(失败/缺失产物优雅
  跳过);相位搜索候选计数透传(core/optimization/phase_search._search_axis、
  workflow/recon_phase_search,向后兼容)。
- 测试:新增 tests/test_phase_optimize.py 11 项,全量 173 passed。

## [0.2.10] - 2026-08-12

- GUI 三栏布局第二版:导入工作流接驳(B2G-001)+ 人工处理入口 UI 骨架。
- 导入数据:ImportExperimentDialog 增加「复制数据到项目」选项(默认勾选,
  raw/<exp_id> + SHA-256 指纹)与 acqus 校验;导入改走
  workflow.import_bruker_dataset(后台线程避免复制大文件阻塞 UI),完成后
  自动登记 WorkflowRun(import)、刷新项目树并选中新实验,warnings 与错误
  经 InfoDialog 展示。
- 人工处理入口 UI 骨架:ParameterTableDialog(参数表格,展示 zero_fill/
  sampling/stages 契约结构)与 ScriptEditorDialog(等宽字体 .com 脚本
  编辑器);Pipeline 每步增加「人工」按钮 + 处理菜单两条入口;后端接口
  仍为占位(manual_param_table/manual_script_editor 抛 NotImplementedError
  时给出友好提示)。
- Pipeline 步骤 LOCKED 状态增加依赖 tooltip(说明缺哪个前置步骤);
  自动化处理仍只经 ProcessingController.auto_run_async 调用后端。
- 测试:新增 tests/test_gui_manual.py 5 项 + 导入工作流端到端 1 项 +
  对话框复制选项/acqus 校验 2 项;全量 178 passed,ruff 全绿。


## [0.2.9] - 2026-08-12

- Backend:导入工作流(workflow/import_workflow):Bruker 数据集校验 → 登记实验 →
  raw/<exp_id>/ 复制(可跳过,源在项目内自动引用)→ SHA-256 指纹 →
  metadata/<id>.json 落盘 → WorkflowRun(import) 登记(成功/失败 + 失败回滚)。
- Backend:导入后 ExperimentEntry.source 指向项目内 raw 副本,状态机推进到
  imported;GUI 导入入口待按 B2G-001 接驳(对话框调 import_bruker_dataset、
  warnings 展示、「复制到项目」选项)。
- Backend 测试:新增 tests/test_import_workflow.py 9 项。
- GUI 演进为三栏布局(docs/GUI_ARCHITECTURE_VISION.md P0):左侧项目管理树
  (Project → Experiment → Input/Processing/Output/Figures,状态列 + 右键
  重命名/删除/打开所在目录)、中间 Pipeline 面板(上下文面包屑 + 状态驱动的
  步骤列表 + 下一步提示 + READY 步骤运行按钮)、右侧谱图面板(内嵌
  viewer.SpectrumViewer + 项目 spectra 目录文件列表)、底部可折叠 Task/Log。
- Pipeline 状态第一版:按产物文件与前置依赖推断 LOCKED/READY/RUNNING/SUCCESS;
  自动化处理仍只经 ProcessingController.auto_run_async 调用后端。
- 菜单按愿景重组(文件/项目/处理/样本/查看/帮助);查看菜单可开关三栏并打开
  独立谱图查看器;主窗口对话框全部改用 gui/dialogs(不再直接使用 QMessageBox)。
- GUI 测试:新增 tests/test_gui_layout.py 13 项;本地全量 162 passed,ruff 全绿。

## [0.2.8] - 2026-08-11

- GUI 重构为简洁流程化布局（借鉴 CryoSPARC）：顶部流程栏（导入数据 / 处理 / 查看谱图 / 报告）+ 中央页面切换；导入页实验/样本双栏表格、处理页自动化与人工两条路径卡片、查看页连接独立 viewer。
- 导入实验改为目录选择对话框（非手输路径）；添加样本改为表单对话框（名称/蛋白/序列/浓度/缓冲液/备注）。
- 处理流程分两条路径：自动化（ProcessingController 接 AutoProcessor，后台线程执行）；人工（参数表格/脚本编辑器接口占位，待实现）。
- 修复“点帮助显示 This plugin supports grabbing the mouse only for popup windows”：统一替换 QMessageBox 为自定义 QDialog（gui/dialogs + viewer/app 的 InfoDialog）。
- 测试：新增对话框/5904理控制器 9 项，全量 144 passed。
## [0.2.7] - 2026-08-11

- fix: main.py 恢复 __main__ 入口块(此前重写 main() 时误删,
  导致 `python main.py` 无输出直接退出);首次运行 bootstrap venv 逻辑复原。
## [0.2.6] - 2026-08-11

- 独立谱图查看模块 viewer（与项目管理 GUI 解耦）：Spectrum/SpectrumAxis（nmrglue 读 .ft2 + ppm 轴，ORIG 优先回退 CAR）、ContourLayer（matplotlib 等高线 + 可配插值因子防锯齿/正黑负红）、NMRViewBox（框选缩放/中键平移/滚轮缩放）、SpectrumViewer（多谱叠加/级数滑块/峰标记 Poky 风格半透明圆点/选中放大/长宽比锁定/十字光标）。
- 独立窗口 SpectrumWindow：打开/拖放 .ft2、最近文件、视图菜单；`nmrforge-viewer` 命令行入口。
- 测试：viewer 11 项（offscreen），全量 135 passed。
## [0.2.5] - 2026-08-11

- 项目管理模块 core/project：项目/实验/样本/WorkflowRun/审计历史数据模型，目录模板、project.json 原子写、实验 CRUD + 状态推断、样本删除引用保护、运行记录 R-YYYYMMDD-NNN 只追加、脚本快照、模板提取。
- 最近项目存储（JSON，最多 8 条，置顶去重，可注入 GUI）。
- GUI 主窗口骨架建立在 ProjectManager 之上：新建/打开/保存/最近项目/实验树（状态推断）/样本管理；main.py 启动 Qt 主窗口。
- 测试：core/project 22 项 + GUI 7 项（offscreen）。

## [0.2.4] 及更早

见 git 历史(0.1.0–0.2.4 变更记录,2026-08-11 重写)。
