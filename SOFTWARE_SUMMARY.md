# NMRFlow 软件全景与开发事项总结（重写参考）

> 目的：重写软件时保持开发流程不变，先读懂「这个软件做了什么」。
> 基于提交 `62217f4`（2026-08-11），本地与 VM 全量 **255 passed**、ruff 全仓通过。
> 详细审计见 [docs/framework_audit_2026-08-11.md](framework_audit_2026-08-11.md)；
> 变更史见 CHANGELOG.md；开发状态交接见 PROJECT_STATE.md。

## 1. 一句话定位

NMRFlow 是一个 **Linux 本地运行的生物核磁共振 workflow 软件**：接入本机 NMRPipe，
导入 Bruker 原始数据，一键完成「NUS 重建 → 处理 → 谱图可视化 → 拾峰 → 报告」，
强调 **可复现**（每次处理生成 WorkflowRun + 脚本快照 + 参数/历史入档）。

- v1 聚焦：15N-1H HSQC（2D）+ 15N 骨架 3D（HNCACB/CBCACONH 等，F3=1H）。
- 明确不做：完整结构解析、自动 assignment、AI 结构预测、NOE、1D 处理（已撤销）、
  容器/远程执行（Docker/SSH 已移除）。
- 技术栈：Python ≥3.10 + PyQt6 + pyqtgraph + numpy/scipy + nmrglue + matplotlib，
  后台 NMR 计算全部委托 NMRPipe / SMILE（`backend/` 以下才接触 NMRPipe 语义）。

## 2. 软件功能全景（现状）

### 2.1 项目与数据管理
- 新建/打开/保存项目：目录模板（raw/processing/spectra/peaks/analysis/figures/report/metadata）、
  `project.json` 原子写（tmp + os.replace）、最近项目（QSettings 最多 8 条）。
- 实验：导入记录、状态推断（已导入→已处理→已拾峰→已分析）、右键重命名/删除
  （删除仅清实验产物，WorkflowRun 审计保留）、自定义标题。
- 样本（schema 1.1）：S001 自动编号、实验关联、删除引用保护（GUI 未接线，API 已测）。
- 审计历史：`processing_history` 只追加，每条含 id/时间戳/action/字段。
- WorkflowRun：`R-YYYYMMDD-NNN`、只追加；记录输入 SHA-256、pipeline/preset/参数、
  工具版本、输出、本次 history 切片、脚本快照（`processing/<exp>/runs/<run_id>/snapshot/`
  脚本副本 + params.json）。
- Workflow 模板：从成功 WorkflowRun 提取 pipeline/preset/overrides/nus 存为
  YAML/JSON（`presets/templates/`）。

### 2.2 Bruker 数据导入
- 解析 `acqus`/`acqu2s`/`acqu3s`（跨行数组、引号/尖括号剥离；TopSpin 4 间接维参数
  在 acqu2s/acqu3s）；提取 TD/SW/O1/SFO/NUC/DECIM/DSPFVS/GRPDLY/BYTORDA/FnMODE。
- 维度识别（PARMODE + 存在性启发式）、实验类型识别（PULPROG + 核 + 维数）、
  NUS 识别（存在 nuslist，或 NusT2/NusTD/NusJSP>0 且 NusAMOUNT<100）。
- SHA-256 校验、复制到 `raw/<id>/`、写 `metadata/<id>.json`、登记 history。
- 多段（ssNMR 防场飘）：多目录导入同一实验，参数一致性校验，处理时逐段转换 +
  时间域 co-add 合并。

### 2.3 处理引擎
- YAML 预设（hsqc_standard / backbone_3d_standard）：有序阶段（convert +
  SP/ZF/FT/PS/EXT/TP/POLY 等），`sampling` 段控制 FnMODE 相关标志与自动相位。
- 确定性 .com 脚本生成（同一输入 → 字节级一致；LF 行尾；csh 语法）。
- 转换：优先 `bruker -AUTO` 生成 fid.com → `patch_fid_com` 按 acqus 交叉修正
  （含 xMODE/yMODE/zMODE/decim/dspfvs/grpdly，2026-08-11 起）→ 执行；
  bruker 不可用时回退内置 bruk2pipe 参数（2026-08-11 修复 MODE/DSP 标志、
  移除 `-DMX`）。
- 标准管道：`xyz2pipe → F2: SP/ZF/FT/PS/EXT(默认 10–6ppm) → TP → F1: ZF/FT/POLY/PS →
  TP → pipe2xyz`；显式 `ZF -size = next_pow2((td/2)×zero_fill)`。
- 自动相位：2D 非 NUS 本地走 `_phase_search`（切全部信号一维迹线、按吸收度/对称性
  搜索 p0/p1，**逐候选重跑完整后端，约 64 次**，已知性能问题）；其余走 autops 兜底
  （原地改写 ft2，参数不同步，已知问题）。两项均按用户要求暂不处理。
- 处理历史：每次处理追加 `process` 条目（preset/overrides/输出/log/工具版本/nus）。

### 2.4 NUS 重建（SMILE）
- 2D NUS：自生成 `_nus2d.com`（直接维 FT+EXT → `SMILE -nDim 2` → 间接维 FT，
  间接维 `-alt/-neg` 按 FnMODE 推断）输出**终谱** ft2（跳过标准管道）。
- 3D NUS：`_nus3d.com` 三步法（直接维 FT/PS/EXT 多文件 → SMILE 重构间接维 →
  间接维 FT/ZTP → ft3），窗口默认 12.0–4.0 ppm，可由用户在 3D 查看器输入重跑。
- 经验参数：按采样率分档（≥50%: nSigma5/thresh0.95；20–50%: 6/0.90；<20%: 7/0.85）。
- 资源限制：线程=核数一半、内存=总-2GB、时长/迭代/峰数上限、超时保护；
  运行期系统监控（CPU/温度/频率/内存 → `*_smile_monitor.csv`）。
- 自动优化：初跑 + 基线/相位/nSigma 坐标上升（2026-08-11 起含 nSigma）+ 最优复跑，
  质量分驱动（峰数/S/N），带运行预算；另有 nSigma×thresh 小网格手动优化入口。
- AI 重建：仅预留接口（provider=ai 快速失败），候选模型 EDHRN/JTF-Net/DM-NMR。

### 2.5 谱图查看与交互
- 2D 查看器：等高线（matplotlib contour → QPainterPath，2× 插值，2% 底 × 20 级）、
  ppm 轴（FDF*ORIG 优先）、多谱叠加、强度滑块、缩放/平移（左键框选、右键平移、
  滚轮缩放、中键拖拽）、十字光标、峰标记。
- 3D 查看器：F1 切片平面查看 + 1H 窗口重跑 + SMILE 脚本查看/参数优化入口。
- 方向约定：1H 高 ppm 左、15N 高 ppm 底；正峰层色、负峰红色实线（带图例）。
- nmrDraw 外部打开（本机 DISPLAY 检查 + 进程存活校验）。

### 2.6 拾峰与峰表
- 自动拾峰：maximum_filter 局部极大值 + 强度>噪声×sigma + S/N 阈值 + 邻域去重，
  2D/3D 通用；阈值在峰面板可调。
- 峰表：`peaks/<id>.csv`（2D: Peak_ID/H_shift/N_shift/Intensity/SN/label；
  3D: F1/F2/F3_shift）；手动加峰/点击删峰/表格删除；亚像素质心（选中/全部）；
  导出 Poky/Sparky `.list`；查看器 ↔ 峰面板信号双向联动。
- WorkflowEngine 处理完成后自动拾峰（配置 `workflow.peaks`）。

### 2.7 批量 / 模板 / 脚本编辑
- 批量处理：勾选实验串行复用 WorkflowEngine，单实验失败不中断，实时进度/日志。
- 手动处理：半自动（先跑预设再优化）/全手动，表格/脚本双编辑、函数说明与建议。
- 脚本编辑器：查看/编辑/保存/执行 fid.com / process.com / nus3d.com / nus.com。

### 2.8 环境与后端接入
- NMRPipe 查找：PATH → csh 环境（`~/.cshrc` 的 NMRPIPEBIN）→ 常见目录；
  多选、测试、一键安装（`scripts/install_nmrpipe_linux.sh`，支持 sudo/离线包）。
- 首启向导（backend/verified 键）、设置对话框、环境/依赖检查对话框。
- `main.py` venv 引导：自动创建 `nmrflow/` venv + `pip install -e .` + exec 切换。

## 3. 架构与分层

```text
GUI（main_window / 各对话框 / 工作台）
  │  ProcessingService（处理编排：心跳/日志/历史/自动相位）
  ▼
WorkflowEngine（流水线编排：WorkflowRun + nus_reconstruct + process + pick_peaks）
  │  ProcessingBackend 协议（backend/base.py）
  ▼
LocalBackend（backend/local_backend.py）
  │
  ├─ run_processing_pipeline（backend/execution.py：转换 + 管道 / 2D NUS / 3D NUS）
  └─ create_nus_reconstructor（backend/nus.py：SMILE / AI 占位）
  │
  ▼
LocalRuntime（processing/runner.py：subprocess + csh/tcsh 执行 NMRPipe）
```

- 分层原则：GUI/Workflow 只依赖 `ProcessingBackend` 协议，不接触 NMRPipe 语义；
  NMRPipe 语义（bruker、fid.com、.com）只存在于 `backend/` 与 `processing/`。
- 重要事实：**单实验处理（手动/自动/处理对话框）从 2026-08-11 起也走
  WorkflowEngine**（之前直调 ProcessingService，无 WorkflowRun）；批量本就走 engine。

### 3.1 目录结构（源码）

```text
main.py            入口：venv 引导 + 首启向导 + 主窗口
core/              领域模型(models)、项目管理(project)、Bruker 导入(bruker)、
                   配置(config)、历史(history)、日志(logger)、NUS 参数(nus_params)、版本
processing/        预设(presets)、脚本生成(script_generator)、处理服务(processor)、
                   运行器(runner)、fid.com 交叉核对(bruker_workflow)、系统资源(system_info)
backend/           协议(base)、LocalBackend(local_backend)、公共流水线(execution)、
                   NUS 抽象(nus)、3D NUS 优化(nus_optimize)、NMRPipe 查找(nmrpipe_finder)、
                   系统监控(system_monitor)
workflow/          pipeline/registry/engine/batch/templates
plotting/          spectrum(读谱+ppm 轴)、spectrum_viewer、contour_layer、nmr_viewbox
analysis/          peak_picking、peak_table、spectrum_quality
gui/               主窗口、工作台、各对话框、峰面板、3D 查看器、任务面板
presets/           hsqc_standard.yaml、backbone_3d_standard.yaml
config/            nmrflow.yaml（+ nmrflow.local.yaml 本地覆盖）
scripts/           setup_env.sh、install_nmrpipe_linux.sh
docs/              需求/架构/路线/审计/调研等
tests/             255 项（含 fake_backend、fixtures、schema_v11、engine、gui smoke）
```

### 3.2 核心协议与工厂
- `ProcessingBackend`（backend/base.py）：health/capabilities/process/reconstruct_nus；
  `create_backend(cfg, nus)` 固定返回 LocalBackend。
- `ToolRuntime`（processing/runner.py）：run/script_command/bruker_script_path/
  script_path/test_connection（upload/download 已删）。
- `NusReconstructor`（backend/nus.py）：available/reconstruct；
  `create_nus_reconstructor(nus_cfg, runtime)` 按 provider 分发。

## 4. 数据模型与文件产物

### 4.1 project.json（schema 1.1，兼容读 1.0，保存升级）
```json
{
  "schema_version": "1.1",
  "name": "...", "protein": {"name","sequence","notes"},
  "experiment_type": "15N_HSQC | 13C_HSQC | 15N_3D",
  "created": "...", "updated": "...",
  "directories": {"raw","processing","spectra","peaks","analysis","figures","report","metadata"},
  "experiments": [{"id":"exp_001","title","source","status","metadata","imported_at","notes","sample_id","segments"}],
  "processing_history": [{"id","timestamp","action", ...}],
  "samples": [{"sample_id":"S001","name","protein_name","sequence","notes","concentration_um","buffer","created"}],
  "workflow_runs": [{"run_id":"R-YYYYMMDD-NNN","workflow_ref","experiment_id","sample_id",
                      "inputs","scripts","params","outputs","software_version","tool_versions",
                      "started_at","finished_at","status","message","snapshot_dir","history","decisions"}],
  "decisions": []
}
```

### 4.2 metadata/<id>.json（BrukerDatasetInfo）
维度、实验类型、PULPROG、nuclei、每维 observe_frequency_hz/spectral_width_hz/carrier_hz/
td、decim、dspfvs、grpdly、byterda、fnmode、fnmode_list、nus、segments、
files(SHA-256)、params/params_f1/params_f2（原始 acqus/acqu2s/acqu3s 键值）。

### 4.3 预设 YAML（presets/*.yaml）
```yaml
name/version/target_experiments/description
zero_fill: 2
sampling: {ft_neg: auto, ft_alt: auto, flip_f1: false, auto_phase: true}
stages:
  - {id: convert, tool: bruk2pipe, extra_args: ["-bad","0.0","-aswap","-AMX"]}
  - {id: window_x, tool: nmrPipe, macro: SP, params: {off: 0.45, end: 0.95, pow: 1, c: 0.5}, param_docs: {...}}
  - {id: zerofill_x, tool: nmrPipe, macro: ZF, params: {auto: true}}
  - {id: ft_x, macro: FT, params: {auto: true}}
  - {id: phase_x, macro: PS, params: {p0: 0, p1: 0, di: true}}
  - {id: extract_x, macro: EXT, params: {x1: "10ppm", xn: "6ppm", sw: true, round: 2}}
  - {id: transpose_1, macro: TP}
  - {id: zerofill_y, macro: ZF, ...} / ft_y / baseline_y(POLY) / phase_y(PS) / transpose_2
```

### 4.4 脚本与产物
- 脚本：`raw/<id>/fid.com`、`processing/<id>/{<id>_convert.com, <id>_process.com,
  <id>_nus.com, <id>_nus2d.com, <id>_nus3d.com, <id>.fid, <id>_recon.fid, *.log,
  <id>_smile_monitor.csv, runs/<run_id>/snapshot/}`。
- 谱图：`spectra/<id>.ft2|ft3`（float32 实数；NUS 2D 为终谱）。
- 峰表：`peaks/<id>.csv`（2D/3D 列），Poky 导出 `.list`。

## 5. 关键流程（数据流）

1. **新建项目**：NewProjectDialog → ProjectManager.create_project（目录模板 + 历史）。
2. **导入 + 转换**：ImportDialog → BrukerImporter.import_*（复制 + SHA-256 + metadata）
   → ImportConvertWorker → convert_imported_experiment（bruker -AUTO → fid.com →
   patch_fid_com → 执行 → 搬移 test.fid / fid 切片）。
3. **单实验处理**：ProcessingDialog/Manual/Auto → ProcessingThread → WorkflowEngine.run
   → （3D NUS 且单 fid 已生成：backend.reconstruct_nus；2D NUS：跳过）→
   ProcessingService.process → LocalBackend.process → run_processing_pipeline
   （转换 → 2D NUS SMILE 终谱 / 3D NUS SMILE / 标准管道）→ 自动相位 → 历史 →
   （engine）pick_peaks → _finish_run（WorkflowRun 收尾 + 脚本快照）。
4. **批量**：BatchDialog → run_batch → 串行 engine.run。
5. **查看/优化**：open_spectrum → Spectrum.load_from_ft2/ft3 → Viewer（+ PeakPanel）；
   3D 窗口重跑 → run_single_3d_nus；SMILE 优化 → optimize_smile_reconstruction。
6. **模板**：工作台「另存为模板」→ build_template_from_run → presets/templates/。

## 6. NMR 处理技术要点（重写必须保留）

### 6.1 FnMODE → 间接维 FT 标志（_SAMPLING_FLAGS）
- FnMODE 0（States）/3（QF）：普通 FT。
- FnMODE 1（TPPI）/2（States-TPPI）：`FT -alt`（±-neg 视数据）。
- FnMODE 5（TopSpin 4 的 States-TPPI，常见于 NUS 实验）：`FT -alt`（桌面 xy.com 模板实测）。
- FnMODE 4/6（Echo-Antiecho）：普通 FT（bruk2pipe 转换时已做 E/A 重建，无需 IMAGINE；
  2026-08-11 真实数据验证复谱镜像 ~0.03）。
- `flip_f1` 可选上下翻转（个别实验）。

### 6.2 bruk2pipe 转换关键参数
- `-xMODE DQD`；`-yMODE Echo-AntiEcho`（FnMODE 4/6）否则 `Complex`；3D 加 `-zMODE Complex`。
- DSPFVS=21（TopSpin 4 数字滤波）需 `-ws 8 -noi2f`；DSPFVS=20 不需要。
- **禁用 `-DMX`**（走 DMX 转换路径会错直接维点数 946 vs 1024、峰位偏移）。
- `-aq2D` 用数值（4/6→3，5→2）；bruker 生成模板用关键字 `Complex`（两版 bruk2pipe 都可接受）。
- `-ext -aswap -AMX -decim -dspfvs -grpdly` 齐全；`-bad 0.0`。

### 6.3 ppm 轴与轴序
- ppm 用 NMRPipe `FDF*ORIG`（EXT 后只更新 ORIG 不更新 CAR），索引递减；
  缺失回退 CAR。
- 3D 数据流怪癖：形状 (F1,F2,F3)，头部 FDF1/FDF2/FDF3 映射为
  axis0←FDF3、axis1←FDF1、axis2←FDF2（真实 HNCACB 峰位验证）。

### 6.4 SMILE 注意事项
- **VM 上不要跑 10.5–6.5 ppm 窗口的 SMILE**（100% CPU 卡死，曾致异常断电）；
  验证用 9.0–7.5 或手动优化。
- 资源限制默认：线程 2、内存 2GB、时长 0.5h、max_iter 600、超时 600s；
  max_npks 设 0（实测设置会严重拖慢）。
- `nice -10` + smile_monitor 全程监控。

### 6.5 自动相位（现状）
- 2D 非 NUS 本地：`_phase_search`（吸收度/对称性评分 + ±90° 消歧 + 平台中心化 +
  p1 精修），**逐候选重跑完整后端（约 64 次），耗时分钟级**——已知性能问题，暂不处理。
- 其余：`_auto_phase`（nmrglue autops，原地改写 ft2，参数不同步）——已知问题，暂不处理。

## 7. 开发流程（保持不变）

### 7.1 环境
- Windows 写码：仓库 `O:\llm\workspace\NMRFlow`；测试虚拟机 Linux
  `ssh <lab-host>`，代码同步在 `~/NMRFlow`，venv `nmrflow/bin/python`。
- 入口：`nmrflow/bin/python main.py`（Windows venv 在 `nmrflow\Scripts\python.exe`）。
- 依赖：pyproject.toml（PyQt6/pyqtgraph/numpy<2.5/scipy/pandas/matplotlib/PyYAML/
  nmrglue/reportlab）；numpy 限 <2.5（nmrglue 0.11 的 dtype 别名问题）。

### 7.2 git 工作流
- 分支 `master`；远程 `vm=ssh://<lab-host>:<port>/home/<lab-user>/nmrflow.git`。
- 例行：本地改 → `pytest`（--basetemp）→ ruff → commit → `git push vm master` →
  VM `git pull` → VM 全量测试（回归）。

### 7.3 测试
- 本地与 VM 均全量 **255 passed**；GUI 测试用 `QT_QPA_PLATFORM=offscreen`；
  MockBackend/FakeBackend 不依赖真实 NMRPipe。
- Windows 沙箱默认 basetemp 被 ACL 锁死：pytest 必须带
  `--basetemp=<新临时目录>`（如 `$env:TEMP\pytest_xxx`）。
- ruff：`ruff check .` 应全仓通过（2026-08-11 已清零预存错误）。

### 7.4 沙箱/补丁模式（重要经验）
- Windows 沙箱辅助进程常报 ACL 错误：普通 shell 命令需提权
  （`sandbox_permissions=require_escalated`）执行。
- **apply_patch 修改已有文件会被 ACL 拒绝**；可用模式：
  「apply_patch 新建补丁脚本 → 提权 python 执行 → 删除脚本」。
- **中文不能直接进 exec_command 参数（会乱码）**：中文只能放 UTF-8 文件里
  （补丁脚本/文档），脚本内用 `open(..., encoding='utf-8')` 读写。
- ssh 远程命令的引号会被 Windows OpenSSH 剥离：避免在 ssh 命令串里用双引号
  包管道/正则，改用单引号或 base64 脚本通道。

### 7.5 提交约定
- 消息风格：`feat:` / `fix:` / `refactor:` / `docs:` + 中文简述；
  一次提交对应一个逻辑变更；文档与代码同步更新（CHANGELOG/PROJECT_STATE/README）。

## 8. 已知问题与坑（重写时注意）

1. **SMILE 宽窗口卡死**：VM 上不要跑 10.5–6.5 ppm 窗口（曾 100% CPU 异常断电）。
2. **自动相位性能**：`_phase_search` 约 64 次重跑/次处理（默认开启）；`_auto_phase`
   原地改谱与参数不同步（按用户要求暂不处理）。
3. **2D NUS 窗口重跑 GUI 入口缺失**：后端已支持 `overrides["nus.ext_lo/ext_hi"]`，
   GUI 尚未提供 2D 窗口重跑（3D 有）。
4. **真实稀疏 2D NUS 数据定标待补**：现有 2D 数据均全采样或合成；SMILE 参数
   分档基于合成数据。
5. **1D 已撤销**：导入 1D 数据按 unknown（无管道可处理）。
6. **Sample GUI 未接线**：schema 1.1 样本 API 已实现测试，界面无入口。
7. **AI NUS / AI 摘要未接入**：仅占位（provider=ai 快速失败）。
8. **多段 NUS 不支持**：多段仅均匀采样 co-add。
9. **nmrglue numpy 2.x DeprecationWarning**：非失败，pyproject 已限 <2.5。
10. **`config/nmrflow.local.yaml` 曾含 SSH 明文**：已删除，勿再写入凭据。

## 9. 审计结论与重写建议（docs/framework_audit_2026-08-11.md）

### 应保留的架构决策
- 分层（GUI → WorkflowEngine → ProcessingBackend → LocalRuntime），
  NMRPipe 语义隔离在 backend/processing 内。
- 确定性脚本生成 + WorkflowRun + 脚本快照（可复现核心）。
- 线程模型：长任务一律 queue.Queue + 主线程 QTimer 轮询
  （PyQt6 6.11 跨线程信号 + Python 对象会原生崩溃 0xC0000409）。
- csh/tcsh 执行 .com、LF 行尾；project.json 原子写；history 只追加。
- 参数权威源 acqus/acqu2s；fid.com 交叉核对（含 MODE/DSP）。

### 已修复的问题（重写不要重复犯）
- 单实验处理必须走 WorkflowEngine（否则无 WorkflowRun/拾峰）。
- 2D NUS 重建只保留一条路径（engine 阶段跳过，管道内 `_nus2d.com` 完成）。
- `SCRIPT_CANDIDATES` 要含 convert/nus/nus2d/nus3d/process/fid.com。
- 配置默认值统一（optimize.enabled=False）；SMILE 用 `timeout_s` 配置。
- 3D 自动优化名实相符（含 nSigma 坐标上升）；共用执行/评分函数。
- bruk2pipe：禁 `-DMX`；dspfvs=21 → `-ws 8 -noi2f`；MODE 标志按 FnMODE。
- 删除死代码（spectrum_optimize_panel、detect_1h_signal_range/f3_projection、
  upload/download、ONE_D_1H）。

### 有意保留（不是问题）
- Sample CRUD（schema 1.1 已测 API，GUI 接线留待后续）。
- WorkflowTemplate.steps（模板自包含需要）。
- AppConfig 顶层 ai.* 与 workflow.nus.ai 是两个独立预留面（已注释区分）。

## 10. 测试覆盖概览（tests/）

- core：schema_v11（Sample/WorkflowRun/engine 落盘）、bruker 解析与导入、project、
  config、nus_params。
- processing：script_generator（golden 断言）、bruker_workflow（fid.com 核对/MODE 修正）、
  processor（阶段历史/相位消息）、presets、runner。
- backend：fake_backend 驱动的 pipeline、nus_reconstructor、nus_optimize（预算/网格）、
  system_monitor、nmrpipe_finder、system_info。
- workflow：pipeline 注册、engine NUS 编排（3D 重建/2D 跳过/失败中止/拾峰）、batch。
- plotting/analysis：spectrum（ppm 轴/方向/噪声）、contour 颜色、peak_picking、
  peak_table（CSV/Poky 导出）、spectrum_quality。
- gui：smoke、manual/auto dialog、phase（手动/自动/搜索）、peak_panel 动作、
  process button 颜色、viewer aspect/中键、batch、script editor 等。

## 11. 路线与待办

- 短期：真实稀疏 2D NUS 端到端定标；Echo-Antiecho 回退转换的真实数据复核；
  自动相位性能重构（内存内评分，避免 64 次重跑）；2D NUS 窗口重跑 GUI。
- 中期（原 M3）：CSP 匹配/计算/图、出版图（SVG/PDF）、自动报告（reportlab）；
  `report/`、`database/` 落地；Sample GUI 接线；阶段 C：优化 decisions 写入 WorkflowRun。
- 远期：AI NUS 重建接入评估（EDHRN/JTF-Net/DM-NMR）、AI 规则模板摘要。
- 已知边界：3D 切片查看器暂无峰表联动；ssNMR 多段不支持 NUS。

## 12. 文档索引

- README.md：用户文档/功能状态。
- PROJECT_STATE.md：开发状态交接（模块表、设计原则、已知问题）。
- CHANGELOG.md：变更史。
- docs/framework_audit_2026-08-11.md：代码审计（逐脚本 IO、问题/冗余清单、修复记录）。
- docs/architecture.md / integration-plan.md / roadmap.md / requirements.md：
  架构设计与路线。
- docs/research/smile-calibration.md：SMILE 参数校准结论。
- docs/research/nus-ai-reconstruction.md：AI NUS 调研。
