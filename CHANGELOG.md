# 变更日志

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
