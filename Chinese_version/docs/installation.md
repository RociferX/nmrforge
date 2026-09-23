# 安装

## 当前发布:v1.0.1(源码 + Linux AppImage)

两条路都可以:**Linux AppImage**(自带 Python 与 Qt,不需要系统环境,界面中英随系统区域切换;见
[Releases](https://github.com/RociferX/nmrforge/releases)),或者从仓库做可编辑安装:

```bash
git clone https://github.com/RociferX/nmrforge.git
cd nmrforge
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[test]"
python main.py
```

`pip install .` 与 wheel 自 2026-09-21 起可用:随包数据(`nmrforge_data/config`、
`nmrforge_data/presets`、`gui/assets`、`ui_support/locales`)随包分发,源码检出与安装态的
相对位置一致。真实处理需要另行安装 NMRPipe;NUS 重构还需要 SMILE。

## AppImage(Linux)

v1.0.1 在发布页提供 AppImage:[Releases](https://github.com/RociferX/nmrforge/releases)
（一份产物,界面中英在运行时切换）。产物自带解释器与 Qt,校验方式:

```bash
sha256sum NMRForge-1.0.1-x86_64.AppImage      # 与发布说明里的校验和对比
chmod +x NMRForge-1.0.1-x86_64.AppImage
./NMRForge-1.0.1-x86_64.AppImage --licenses  # 产物内的第三方许可与构建溯源
./NMRForge-1.0.1-x86_64.AppImage             # 首次运行会装桌面菜单项
```

它们捆绑 PySide6/Qt,这些库按 LGPL-3.0 随产物分发(许可与 nmrForge 源码的 Apache-2.0
并行有效);构建、许可与干净机器验收记录见
维护者私有仓库里的发布检查清单。

## 开发者:可编辑源码安装

```bash
git clone <this repository>
cd nmrForge
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

可选依赖组(在 `pyproject.toml` 中声明):

```bash
pip install -e ".[test]"    # pytest
pip install -e ".[dev]"     # pytest + ruff
pip install -e ".[docs]"    # 文档工具链
```

`python main.py` 会在首次运行时创建本地 `nmrforge/` 虚拟环境,之后复用。
将来的 AppImage 会包住同一个入口点。

可编辑安装会暴露一个命令行脚本:

| 命令 | 作用 |
| --- | --- |
| `nmrforge-viewer` | 独立谱图查看器(`python -m viewer` 也可以) |

主 GUI 刻意不发布成命令行脚本,因为它依赖仓库根目录的资源(决定 PACK-015)。
在可编辑检出里请用 `python main.py`。

处理命令行在 `nmrforge_api` 包里:

```bash
python -m nmrforge_api --help
```

## 验证安装

```bash
python -m pytest -q                                       # 全量测试
python -m ruff check .                                    # 静态检查
python -c "import core, nmrforge_api, gui, viewer; print('imports ok')"
python examples/make_synthetic_dataset.py --out example_data/hsqc_2d
python examples/quickstart.py example_data/hsqc_2d
```

GUI 测试需要显示器,或者 offscreen 的 Qt 平台:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q      # Windows: set QT_QPA_PLATFORM=offscreen
```

## 升级与卸载

```bash
pip install -e .            # 改过 pyproject.toml 或入口点之后重跑
pip uninstall nmrforge      # 移除包与其命令行脚本
```

卸载永远不会删掉你的项目或研究:它们位于你自己选的目录里。
