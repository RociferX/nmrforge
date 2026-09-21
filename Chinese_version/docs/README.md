# NMRForge 文档导航

仓库自带文档的统一入口。

## 从这里开始

1. [README](../README.md)——nmrForge 是什么、怎么安装、怎么运行。
2. [开始使用](getting-started.md)——AppImage 用户路径 + 不依赖 NMRPipe 的开发走查。
3. [安装](installation.md)——源码安装与 AppImage 的边界。

## 两条轨道

nmrForge 的两条使用轨道成熟度不同,按需要选择:

### Track A:图形界面(成熟)

- [GUI 指南](gui.md)——窗口布局、四步 pipeline、典型会话。
- [QC system](qc-system.md)——FID / 采样 / 谱图三级质量控制与运行报告结构。
- [Peak picking](peak-picking.md)——检测与亚格点定位、峰表格式、参考峰约束。
- [Batch processing](batch-processing.md)——批量处理(仅 2D)与参数扫描的区别。
- [External dependencies](external-dependencies.md)——NMRPipe/SMILE 定位顺序与缺失行为。
- [真实数据实测证据](evidence/real-data-comparison.md)——自动 vs 手工(匿名)的对比图、人工核验
  结论、按默认阈值选峰的匹配率、QC 评分、两套处理参数,以及真机重复性与耗时快照。

### Track B:Python/CLI 接口(仍在演进)

名称、默认值与这些调用写出的记录在 v1.0 之前都可能变化:请固定提交号,并在把一批数字
当作可比之前核对 `nmrforge_api.compat_manifest()`(`behavior_digest` / `compat_level` /
`affected`)。

- [Python API](python-api.md)——`nmrforge_api`(公开)与 `core`/`backend`/`workflow`(内部)的分界。
- [CLI 参考](cli.md)——`python -m nmrforge_api` 子命令与常用参数。
- [对外接口 nmrforge_api](external-api/README.md)——参数组合处理执行器的快速上手、API/CLI
  参考、输入与输出记录、方法与 QC 口径、输入与输出边界。
- [Processing model](processing-model.md)——理解 → 规划 → 执行 → 举证,及步骤顺序约束。

- [Troubleshooting](troubleshooting.md)与 [FAQ](faq.md)。

## 设计与契约

- [总体架构](architecture.md)——分层、职责和依赖方向。
- [API 契约](API_CONTRACT.md)——GUI、Workflow、Backend 和项目模型之间的共享契约。
- [开发规范](development.md)——本地开发、测试和环境注意事项。
- [打包说明](packaging.md)——AppImage 与安装包相关说明。
- [路线图](roadmap.md)——长期规划,不等同于当前实现状态。

## 发布与许可

- [THIRD_PARTY.md](../../THIRD_PARTY.md)——第三方依赖与许可。
- [LICENSE_OPTIONS.md](../../LICENSE_OPTIONS.md)——源码 Apache-2.0 与 AppImage 里 Qt/PySide6 的
  LGPL-3.0 义务。
- 发布记录与发布检查清单在维护者私有仓库;每个版本的用户可见要点看 GitHub Releases 页面。

## PySide6 迁移(许可阻塞项的处置路径)

许可阻塞项(PyQt6 的 GPL-3.0-only)已通过 PySide6 迁移解除;目标架构**计算核心与 Qt 无关、
只有 GUI 依赖 Qt 且该依赖为 PySide6** 已经落地。该分支的产出(审计、方案、边界与护栏):

- [Qt 依赖审计](pyside6-migration/qt-dependency-audit.md):逐文件、逐符号、逐 API 的实测清单;
- [迁移方案](pyside6-migration/migration-plan.md):目标架构、`qtcompat` 单一绑定边界、分阶段
  工作与验收门禁,以及迁移后必须重做第三方许可审计的前置条件;
  **这两篇是英文原文**:它们是该次迁移的历史工程记录(逐符号审计清单与带日期的方案),
  不适合翻译,中文文档树里也保留英文;
- Stage 5(删除 PyQt6、依赖改为 PySide6)与 Stage 4(AppImage 构建 + 冒烟)均已完成。

对应回归护栏:`tests/test_qt_independence.py`(核心层不得引入 Qt、全仓只允许一个绑定)。
