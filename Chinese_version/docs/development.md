# 开发指南

Linux 是目标运行平台，开发环境需要 Python 3.12 或更高版本。创建可编辑源码安装并启动桌面应用：

```bash
git clone https://github.com/RociferX/nmrforge.git
cd nmrforge
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python main.py
```

`test` 依赖组安装 pytest；`dev` 安装 pytest 和 Ruff。PySide6 与 pyqtgraph 是常规运行依赖。
NMRPipe 和 SMILE 是单独安装的工具：NMRPipe 用于数据转换和处理，SMILE 用于 NUS 重构；源码和
AppImage 均不包含这两个工具。资源安装和引擎发现方式见[安装指南](installation.md)与
[外部依赖](external-dependencies.md)。

## 测试与检查

测试分组在[`tests/categories.py`](../../tests/categories.py)中登记，标记由
`tests/conftest.py` 应用。使用 `python -m pytest -m <group>` 单独运行 `unit`、`integration`
或 `regression` 组；使用 `python -m pytest -q` 运行全部测试。完整处理路径回归为：

```bash
python -m pytest tests/test_full_paths.py
```

该测试覆盖 2D/3D、均匀采样/NUS 的自动与手动入口。批处理目前覆盖 2D。常规测试在引擎边界使用
fake 或 mock，不需要安装 NMRPipe、SMILE；真实引擎检查应单独报告。无显示器时，Qt 测试可设置
`QT_QPA_PLATFORM=offscreen`。Lint 检查命令为 `python -m ruff check .`。

## 处理路径与兼容性

修改处理行为时，沿自动和手动入口检查 2D/3D、均匀采样/NUS 路径。路径专属行为应限于确有需要的
操作，并说明其适用范围；用 `tests/test_full_paths.py` 检查路径覆盖。运行记录应写入实际应用的值，
请求值、解析值和应用值需要区分。

`nmrforge_api` 兼容清单记录内容指纹 `behavior_digest`、规范化代码指纹 `token_digest`，以及
`same`、`additive`、`behavior_changed`、`contract_changed` 之一和受影响的处理步骤。内容指纹覆盖
`core/`、`backend/`、`workflow/`、`nmrforge_api/` 下的全部文件，以及随包默认配置和预设。修改这些内容后，
先用以下命令查看兼容清单与黄金向量结果：

```bash
python -m nmrforge_api compat --out compat.json
python -m nmrforge_api compat --golden
```

使用 `scripts/update_compat_declaration.py` 更新声明，例如：

```bash
python scripts/update_compat_declaration.py --level behavior_changed \
  --affected localization,sweep_detection --note "说明数值变化"
```

可用步骤见 `nmrforge_api.compat.AFFECTED_STEPS`；脚本校验分级，
默认重算黄金向量哈希，`--no-golden` 可保留已有哈希。仅文档变化且可执行 token 不变时，
使用 `--level same --affected "" --no-golden`。

数值变化归为 `behavior_changed`；字段、列或错误码变化归为 `contract_changed`。这两种级别都要填写
`affected`。行为或配方变化时同步更新黄金向量哈希。`same` 要求 token 指纹不变。兼容性回归会检查
声明与代码、黄金向量是否一致；不要手工拼写运行戳或兼容记录。

## 运行资源与界面文本

默认配置、预设和教程位于 `nmrforge_data/`；`gui/assets/` 与 `ui_support/locales/` 也是运行资源。
`core/app_paths.py` 会从源码检出、安装包或冻结的 AppImage 布局解析资源。请保持这些资源使用一致的
相对布局，并确保发布文件不包含机器本地的 `nmrforge.local.yaml`。

界面文本以传入 `tr()` 的英文原文为键，中文译文位于 `ui_support/locales/zh.json`。修改界面文本时，
同步译文和抽取元数据，然后运行：

```bash
python scripts/i18n_extract_ui.py --write
python scripts/i18n_extract_ui.py --check
```

检查会核对已登记文本、已转换文件覆盖情况和中文覆盖率下限。`source.json` 与 `converted.json` 是开发/
CI 元数据，不是运行时语言包。运行时目录使用 `default.json`、`en.json` 和 `zh.json`；打包配置会逐项
声明资源文件。

## 公开接口与文档

软件版本 **1.0.5** 与脚本 API 版本 **1.1.1** 分别版本化。修改 API 时，同步更新实现、测试、兼容性声明、
API 契约及相关外部 API 页面。示例应使用受支持的参数和输出。参见[贡献指南](../../CONTRIBUTING.md)、
[API 契约](API_CONTRACT.md)、[外部 API 指南](external-api/README.md)和[总体架构](architecture.md)。

示例应使用通用路径和合成数据。不要公开凭据、机器专用路径、私人数据集或内部项目记录。
