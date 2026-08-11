# 变更日志

## [0.2.4] - 2026-08-11

- B 方案落地：复型重构平面拆包（core/data/pipe_io）+ 按维相位搜索报告
  （workflow/recon_phase_search + scripts/recon_phase_search.py，不改管线）。
- 验证结论：搜索机制在复型重构数据上可行（各轴增益为正）；当前数据集
  重构相位已接近最优（全量平面增益 0.01-0.02；16 平面大增益为小样本假象）。
- 搜索稳健性：峰高加权均值 + top-K 强迹线抽样。

## [0.2.3] - 2026-08-11

- 实测确认：NMRPipe 谱文件（.ft1/.ft2/.ft3）第一轴实/虚交错存储复型，
  nmrglue 会读成翻倍实型，需拆包还原（core/data/pipe_io）。
- 回退「终谱事后调相」集成（实型读取 + 复→实写会损坏输出）；一次重构的
  直接维相位方案 = 复型 .fid 的 p1 共识写进脚本 PS（默认开启）。

## [0.2.2] - 2026-08-11

- 最终谱按维相位搜索（search_spectrum_phase）与后端集成；
  后因 NMRPipe 输出实型而回退，保留为复型数据工具。

## [0.2.1] - 2026-08-11

- 直接维统计相位搜索（core/optimization/phase_search）：复型 .fid 直接维
  FT 迹线上 p1 共识搜索（峰区吸收度 + 符号消歧 + 增益门控），写进脚本 PS。

## [0.2.0] - 2026-08-11

- 后处理参数优化（workflow/param_optimize + scripts/param_optimize.py）：
  相位 p0/p1 + 基线在终谱上内存内优化，只重构一次；NUS 与非 NUS 同一逻辑。

## [0.1.9] - 2026-08-11

- SMILE 参数优化模块（workflow/smile_optimize + scripts/smile_optimize.py）：
  逐组反馈 + 300s 超时；不进入自动流程，用户可后选。
- 修复 bruker -AUTO sampleCount 误判（Sampletest/4 sampleCount=2 卡死）。
- Sampletest/4 九组 nSigma×thresh 扫描：全部 accept；thresh=0.95 最优；
  默认 5/0.95 已在最优区，无需改默认。

## [0.1.8] - 2026-08-11

- 批量验证 sampleM（CBCANH 3D NUS）时宿主断电；根因：SMILE 大网格满核。
- 安全护栏：间接网格 >5000 点时 SMILE 线程数上限 2；转换后清理 ser_full/mask.fid。
- 全目录验证：2D 均匀 6 个 ACCEPT、3D NUS 6 个 ACCEPT、多段 61/63/65/67 ACCEPT 74.1。

## [0.1.7] - 2026-08-11

- SMILE 参数覆盖（nSigma/thresh/xQ3/scaling/report）。
- 默认参数更新（真实验证）：<20% 采样档 7/0.85 → 5/0.95；xQ3=2、scaling=1。

## [0.1.6] - 2026-08-11

- 清理 VM 旧软件遗留卡死进程；多段实验支持（read_segments + addNMR 合并 + nuslist 校验）。

## [0.1.5] - 2026-08-11

- Phase 3：bruker 原生识别确认（NUS acqu3s TD=1 → NusTD），移除切片追加；
  SMILE 重构端到端（真实 3D NUS 验证）。

## [0.1.4] - 2026-08-11

- NMRPipe 后端接入：csh 路径查找、bruker -AUTO 转换 + fid.com 修补、
  确定性脚本生成（-ws 8 -noi2f / 无 -DMX / MODE 标志 / -aq2D）。

## [0.1.3] - 2026-08-11

- Bruker 数据接入：ser/fid 二进制读取（BYTORDA/布局/大小校验）、
  超复数合并、fid.com 交叉核对、AutoProcessor.run 端到端。

## [0.1.2] - 2026-08-11

- Phase 1 处理与 QC：numpy 处理原语、QC 指标全套、DAG 管线（缓存/失败隔离）、
  AutoProcessor.process_matrix 最小闭环。

## [0.1.1] - 2026-08-11

- Phase 1 数据理解：Bruker 参数解析、内部数据模型、NUS/采集模式检测、
  基础分类、轴映射；真实数据解析修复（SW_h 优先、O1P 回退）。

## [0.1.0] - 2026-08-11

- 初始化项目工作树：core 八大子模块、backend、workflow、gui、presets、
  config、docs、tests。
- 定名 NMRForge；AppImage 打包规划；git master + VM 同步链路。
