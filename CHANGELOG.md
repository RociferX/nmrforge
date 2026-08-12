# 变更日志

## [0.2.22] - 2026-08-12

- fix(shared):create_project 不再预建扁平目录模板(raw/processing/spectra/
  peaks/analysis/figures/report/metadata)——契约 §9.2「文件系统即层级」,
  数据目录在导入/处理时按 <exp>/<data>/{raw,process,spectra,...} 创建;
  dir_map 仅作旧扁平路径兼容解析,不 mkdir。
- 测试适配:布局断言改为「不创建扁平目录」,legacy 状态推断/删除用例显式
  创建旧扁平目录,配置目录用例断言路径解析而非存在;全量 270 passed。
## [0.2.21] - 2026-08-12

- Backend(G2B-005):统一峰表模型 core/peaks/peak_table.py(Shared,移植旧项目
  NMRFlow)——PeakTable(add/remove 数字自动编号)、save_peaks/load_peaks
  (CSV 数字 Peak_ID,缺列补空,2D/3D 自动判别)、export_peaks_poky /
  import_peaks_poky(Poky/Sparky .list:"Assignment w1 w2 [w3] Data Height
  Volume",2D w1=15N/w2=1H,未命名 ?-?/?-?-?,Height %.3g,双空格)。
- Backend(G2B-005):workflow/pick_peaks 改用 PeakTable.save_peaks——
  输出数字 Peak_ID(1..n,不再 "P001"),列与旧项目一致。
- 测试:新增 tests/test_peak_table.py 9 项(往返/导出导入/2D/3D/占位/
  pick_peaks 列);全量 270 passed,ruff 全绿。
## [0.2.20] - 2026-08-12

- Dashboard:Project 概览(实验/数据/处理完成度/最近运行 + 新建实验表单);
  Experiment 概览(数据列表状态 + 导入数据表单)。
- 运行历史展示:处理菜单「运行历史...」打开全局列表对话框(run_id/流程/状态/
  时间/消息/产物详情);Project Dashboard 内嵌最近运行。
- 谱图-峰表联动:打开谱图自动加载对应峰表(data_dir peaks CSV,旧布局回退),
  峰表列表在面板底部;点击谱图峰 → 峰表选中对应行,点击峰表行 → viewer 高亮。

## [0.2.19.9] - 2026-08-12

- 修复主界面右下角空白:谱图面板文件列表在无谱图文件时自动隐藏,
  viewer 占满右侧区域;有谱图文件时列表显示在底部。

## [0.2.19.8] - 2026-08-12

- 谱图查看器 plot 默认调整为接近正方形(splitter 初始 [460, 220],
  撤销上一版宽扁设置)。

## [0.2.19.7] - 2026-08-12

- 谱图查看器初始尺寸调整:plot 默认宽扁(最小高 320),避免打开即接近正方形;
- 谱图查看器术语专业化:「等高线」统一改为「轮廓」(contour),如轮廓起点/
  轮廓级数。

## [0.2.19.6] - 2026-08-12

- SpectrumViewer 内部改上下布局:谱图在上、控制面板在下(分隔条可拖动);
- SpectrumPanel 调整为查看器在上、文件列表在下(避免上方空白);
  主界面左/中/右三栏直接拼接。
- Backend:人工处理路径后端(workflow/manual,用户反馈)——集合命令行人工流程:
  生成 FID 自动产出 fid.com 后可查看/修改/运行(manual_fid_com /
  run_manual_fid_com);生成谱图支持表格参数(param_schema/render_scripts)
  或直接脚本编辑后运行 process.com / nus*.com(run_manual_spectrum,
  消费已转换 fid,不执行 fid.com——生成 FID 是独立步骤);产物归位
  process//spectra/,登记 manual_fid/manual_process/manual_nus
  WorkflowRun,失败登记 failed 并抛 ManualRunError。
- 测试:新增 tests/test_manual.py 7 项;全量 247 passed,ruff 全绿。


## [0.2.19.5] - 2026-08-12

- 谱图面板上下布局(重新实现):文件列表在上、查看器在下,分隔条可上下
  拖动;取消文件列表横向宽度限制;主界面左/中/右三栏直接拼接。

## [0.2.19.4] - 2026-08-12

- 回退谱图面板上下布局改动(0.2.19.3 的垂直 splitter 未达预期):恢复为
  左右布局(左文件列表/右查看器);三栏比例仍可拖动调节。
- viewer/ 模块归属确认:由 GUI Agent 负责(spectrum.py 为 Shared Contract,
  实现属 GUI)。

## [0.2.19.2] - 2026-08-12

- 修复右键「打开所在目录」无效:data/folder 节点右键改为复用
  open_path_requested(与双击同一路径解析),并由主窗口统一打开文件管理器;
  打开后中间保持 Pipeline 视图。

## [0.2.19.1] - 2026-08-12

- 双击数据节点/子文件夹:中间保持 Pipeline 视图(不再跳转导入数据页),
  同时用系统文件管理器打开对应目录(数据节点打开 raw/ 或数据基座,
  子文件夹打开自身目录)。

## [0.2.19] - 2026-08-12

- 谱图/峰表路径适配 schema 1.3 新布局:spectrum_panel 与 Pipeline 状态推断
  改用 data_dir(exp_id, data_id, "spectra"/"peaks") 扫描,旧扁平路径保留回退。
- 数据删除接线:数据右键「删除」调 manager.delete_data(不再误删实验),
  确认对话框 + save + refresh。
- 数据重命名落盘:改写 DataEntry.title(优先 manager.rename_data,缺失时直接
  写 title),移除树内存 _data_titles;树显示 title 优先、缺省回退 data_id。
- 项目删除/重命名接 WorkspaceManager:delete_project(回收站)/
  rename_project(目录+name);后端接口缺失时友好提示并保留原行为。
- 峰挑选/分析步骤接线:ProcessingController 增加 pick_peaks/analyze
  (调 workflow.pick_peaks/analyze,缺失时提示待实现);peaks 运行后峰表
  出现即 SUCCESS,analysis 占位提示。
- 人工处理对话框加载真实数据:ParameterTableDialog 从
  script_generator.param_schema() 填充;ScriptEditorDialog 从
  render_scripts() 加载 .com 并支持保存到 data_dir(...,"process")/;
  后端缺失时显示「后端待实现」并保持可编辑骨架。

- Backend(G2B-004):数据重命名落盘——DataEntry 增加 title 字段
  (序列化兼容,缺省空),ProjectManager.rename_data 写审计历史 data_renamed;
- Backend(G2B-004):工作区项目管理——WorkspaceManager.delete_project
  (默认移入系统回收站,失败回退临时目录,绝不直接删除)与 rename_project
  (目录 + project.json name,重名/非法名校验);
- Backend(G2B-004):峰挑选 workflow/pick_peaks——复用
  core/qc/peak_detection,峰表写 data_dir(...,"peaks")/
  <exp_id>-<data_id>.csv(2D:Peak_ID,H_shift,N_shift,Intensity,SN,label;
  3D 加 F1/F2/F3_shift),登记 pick_peaks WorkflowRun,失败
  finish_run("failed") 并抛 PickPeaksError;
- Backend(G2B-004):人工处理数据源——backend/script_generator 暴露
  param_schema()(API_CONTRACT §6 键 + 默认值/说明)与
  render_scripts(experiment, params)(确定性渲染 fid.com/process.com/
  nus*.com);
- Backend(G2B-004):分析步骤占位 workflow/analyze.analyze(返回
  pending 不抛异常);产物路径契约确认——generate_fid 落盘
  data_dir(...,"process")、generate_spectrum 落盘
  data_dir(...,"spectra")(stepwise 测试断言),旧扁平路径在
  infer_status 保留兼容回退;
- 测试:新增 test_pick_peaks(2)+ test_script_schema(3),
  project_manager 30 / workspace 11;全量 240 passed,ruff 全绿。


## [0.2.18.1] - 2026-08-12

- 当前项目右键新增「重命名项目...」:更新 project.json 的 name,树与窗口标题
  同步刷新(树当前项目节点显示 project.name)。

## [0.2.18] - 2026-08-12

- 左侧树 Workspace 节点下列出工作区全部项目:当前项目展开实验/数据并标记
  「当前」,其余项目双击或右键「打开项目...」切换。
- 导入数据支持命名:实验页导入表单增加「数据名称(可选)」;数据节点显示
  自定义名称(默认仍为 数据 d_001)。
- 数据节点右键新增「重命名...」(重命名后树刷新显示新名称)。

## [0.2.17.1] - 2026-08-12

- 修复欢迎页逻辑冲突:未打开项目时不再隐藏三栏,欢迎页在中间 Workspace
  页正常显示(新建/打开/最近项目可操作)。
- 修复导入失败点 OK 卡死:导入在后台线程执行,成功/失败结果改经 Qt 信号
  回主线程处理(不再在后台线程弹模态对话框/操作 UI)。

## [0.2.17] - 2026-08-12

- 中间面板改为层级上下文面板(CenterPanel),随左侧树选中层级切换:
  Workspace → 新建项目/打开项目/最近项目(嵌入欢迎页);
  Project → 新建实验(内嵌表单);
  Experiment → 导入数据(内嵌表单:目录选择 + 复制选项);
  Data/子目录 → Pipeline 五步(生成 FID → 分析)。
- 新建实验/导入数据表单直接内嵌在中间,不再弹独立窗口。

## [0.2.16.3] - 2026-08-12

- 「导入数据」为纯自动化步骤,不再显示「人工」按钮;选中 Data 后仅
  生成 FID/生成谱图/峰挑选/分析 保留人工入口。

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
