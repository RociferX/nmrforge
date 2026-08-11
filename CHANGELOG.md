# 变更日志

## [0.1.7] - 2026-08-11

- SMILE 参数覆盖：nSigma/thresh/xQ3（SP 幂次）/scaling/report 可经 reconstruct_nus params 传入。
- 默认参数更新（真实验证）：<20% 采样档由 7/0.85 改为 5/0.95；xQ3=2、-scaling 1 对齐实验室模板。
- data/100 单段 3D NUS（CBCA(CO)NH）验证：默认参数 QC 78.8 ACCEPT。
- 61/63/65/67 多段 HNCA 参数对比：7/0.85 → warning 57.5；5/0.95+xQ3=2+scaling → accept 74.1。

## [0.1.6] - 2026-08-11

- 清理 VM 旧软件遗留卡死进程（16h 空转的 nmrPipe 管道）。
- 多段实验支持（参考实验室 data/脚本：1stfid.com + 2ndAdd.com）：
  每段 bruker 转换 → 拆 fid 切片 → addNMR 逐对合并 → 统一 SMILE。
- Experiment.segments + read_segments（参数一致性校验）+ merge_nuslists。
- 真实多段验证（61/63/65/67，HNCA 3D NUS）：4 段 × 52 切片 → addNMR 合并 → 合并 nuslist 348 点（63 号 1 个越界点 27 2350 自动丢弃）→ SMILE rc=0 → ft3（QC 58.8 warning，低采样伪影）。
- 修复：CshRuntime 管道符被引号化、切片轴 -z→-x、多段幂等复用。
- 新增 4 项测试；本地 75 passed。

## [0.1.5] - 2026-08-11

- Phase 3（NUS）：bruker 原生识别确认（acqu3s TD=1 时按 NusTD 取 zN，生成 nusExpand/ser_full/mask.fid + 单文件 test.fid）；移除切片追加 workaround。
- NMRPipeBackend.reconstruct_nus：2D/3D NUS SMILE 重构（单文件直接维处理 → SMILE → 间接维 FT）。
- SMILE 经验参数按采样率分档（≥50%: 5/0.95；20-50%: 6/0.90；<20%: 7/0.85）。
- 真实 3D NUS 验证（VM verify_flow/exp_001，HNCACB 25%）：SMILE 三步 ~15s、QC 93.7 ACCEPT。
- 发现并修复 tcsh 包装内叠加 nice 会导致脚本完成后挂起；A/B 验证 yMODE（bruker 原生 Complex vs acqus 推导 Echo-AntiEcho）最终谱一致。
- 新增 NUS 脚本/后端测试；本地 71 passed。

## [0.1.4] - 2026-08-11

- NMRPipe 后端接入（Linux/csh）：bruker -AUTO → patch_fid_com → 执行 → NMRPipe 处理管道。
- 查找路径按 csh 实际响应（source ~/.cshrc; which nmrPipe），支持显式 bin 目录。
- 3D NUS 规避 acqu3s TD=1：fid.com 用 NusTD 修补，并强制输出 fid 切片而非单文件。
- 确定性脚本生成（LF 行尾）；转换参数符合审计结论（-ws 8 -noi2f/无 -DMX/MODE 标志/-aq2D）。
- AutoProcessor.run 支持 NMRPipe 后端路径（成功后读谱 QC）。
- 真实数据验证（VM Desktop/sampleF）：SW_h 优先于 SW(ppm)、O1P 缺失回退 O1/SFO1。
- 新增 13 项测试；本地 64 passed。

## [0.1.3] - 2026-08-11

- Bruker 数据接入（参考 NMRFlow）：ser/fid 二进制读取（BYTORDA 字节序、2D/3D 布局、大小校验）。
- 超复数间接维合并原语（States/States-TPPI；Echo-Antiecho 待 NMRPipe 后端）。
- fid.com 解析/交叉核对/修补（backend/bruker_workflow，acqus 为权威源）。
- NUS 检测跨 acqus/acqu2s/acqu3s；AutoProcessor.run 端到端（uniform 2D/3D）。
- 新增 13 项测试；本地 53 passed。

## [0.1.2] - 2026-08-11

- Phase 1（处理与 QC）：numpy 原生处理原语（apodization/ZF/FT/phase/baseline/calibration/transpose/sign）。
- QC 指标实现：robust MAD 噪声、峰检测、SNR、相位/基线质量、孤立峰簇伪影、综合质量评分（ACCEPT/WARNING/ROLLBACK）。
- DAG 拓扑排序 + 缓存命中 + 失败隔离的 PipelineRunner；默认处理计划（逐维 SP→ZF→FT→PS）。
- AutoProcessor.process_matrix 最小闭环（uniform 2D/3D 合成矩阵）；新增 21 项测试。

## [0.1.1] - 2026-08-11

- Phase 1（数据理解）：实现 Bruker 参数解析（acqus/acqu2s/acqu3s，跨行数组、引号/尖括号剥离）。
- 实现 NUS 检测、采集模式检测（FnMODE）、PULPROG 多证据基础分类与采集/处理/显示轴映射。
- 新增 Bruker fixture（HSQC/NUS-HSQC/HNCA/unknown）与 13 项数据理解测试。

## [0.1.0] - 2026-08-11

- 初始化项目工作树（依据《自动化 NMR 2D/3D 数据处理与优化软件：完整技术框架》重建）。
- 建立 core/ 八大子模块：experiment / data / planning / processing / optimization / qc / experiments / reporting。
- 建立 backend/（ProcessingBackend 协议 + NMRPipe/Native 占位）、workflow/（AutoProcessor 编排占位）、gui/（面板占位）。
- 建立 presets/（实验模板 YAML）、config/、scripts/、docs/、tests/。
- 开发流程：pyproject（pytest/ruff 配置）、master 分支 git 仓库、README / PROJECT_STATE / CHANGELOG。
- 正式定名 NMRForge；规划 AppImage 打包（packaging/linux/ + docs/packaging.md）。
