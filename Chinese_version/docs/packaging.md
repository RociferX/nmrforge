# 打包

NMRForge 软件版本为 **1.0.5**，脚本 API 版本为 **1.1.1**，两者分别版本化。本次发布提供源码和
Linux AppImage。AppImage 是最便捷的桌面安装方式；NMRPipe 与 SMILE 需要另行获取和安装。
NMRPipe 用于转换和处理，SMILE 用于 NUS 重构。

## Linux AppImage

在装有 Python 3.12 或更高版本、pip 和 `appimagetool`（位于 `PATH` 中）的 Linux 环境构建：

```bash
bash packaging/linux/build_appimage.sh
```

脚本从 `core.__version__` 读取软件版本，从 `uname -m` 读取架构。它会创建构建虚拟环境，以可编辑模式
安装项目和 PyInstaller，并使用[`packaging/linux/NMRForge.spec`](../../packaging/linux/NMRForge.spec)
冻结 `main.py`。单一产物位于 `build/appimage/NMRForge-<version>-<arch>.AppImage`。

spec 会按包内相对布局加入运行资源：`nmrforge_data/config`、`nmrforge_data/presets`、
`nmrforge_data/tutorial`、`gui/assets` 和 `ui_support/locales`。它也会加入项目许可/声明并收集已安装依赖
的许可文件。构建前会校验第三方许可文本；冻结后会检查资源目录和语言包，并以 offscreen 模式启动
冻结 GUI 进行 20 秒冒烟检查。构建会在 `usr/share/doc/NMRForge/BUILD_INFO.txt` 中记录版本、源码提交、
工作树状态、构建时间、主机、Python 版本及部分依赖版本。`AppRun` 启动 `usr/bin/NMRForge` 并提供
`--licenses` 选项；首次启动时安装桌面入口和图标。`--remove-desktop` 可移除该集成，
`NMRFORGE_NO_DESKTOP=1` 可跳过安装。

AppImage 不包含 NMRPipe 或 SMILE。用户需单独安装这些工具，并按[外部依赖](external-dependencies.md)
中的发现方式配置或提供它们。

## 源码安装与 wheel 资源

项目支持可编辑开发安装和 wheel 安装。`pyproject.toml` 显式列出随包资源：默认配置文件
`nmrforge_data/config/nmrforge.yaml`、预设、教程、GUI 资源和运行时语言包。机器本地覆盖文件
`nmrforge.local.yaml` 会排除在外。`core`、`backend`、`workflow` 和 `nmrforge_api` 包中的 README
也会随包提供，因为兼容性 `behavior_digest` 覆盖这些目录中的全部文件；若漏掉 README，安装状态计算出的
指纹会与声明不一致。

语言资源逐项列出。`default.json`、`en.json`、`zh.json` 在运行时使用；`source.json` 和
`converted.json` 服务于开发期界面文本检查，不是运行时语言包。显式的 package-data 清单可让本地配置和
工具文件不进入分发包，同时确保 `core/app_paths.py` 在源码检出、安装包和冻结应用中解析到一致的运行资源布局。

## 许可与声明

NMRForge 源码采用 [Apache-2.0](../../LICENSE)。AppImage 包含采用各自许可的第三方组件，其中 Qt/PySide6
使用其 LGPL 许可选项。构建会将 `packaging/linux/THIRD_PARTY_LICENSES/` 中的许可正文、来源记录和组件声明
复制到 `usr/share/doc/NMRForge/third-party/`；冻结包内还保留依赖许可文件，并包含项目许可和声明。应用的
`--licenses` 选项会显示第三方声明位置。

若使用其他 PySide6/shiboken6 wheel 重建，构建脚本支持 `PYSIDE6_REQUIREMENT`、
`SHIBOKEN6_REQUIREMENT` 和 `EXTRA_PIP_ARGS`，例如：

```bash
PYSIDE6_REQUIREMENT="PySide6 @ file:///path/to/PySide6.whl" \
EXTRA_PIP_ARGS="--find-links /path/to/wheels" \
bash packaging/linux/build_appimage.sh
```

替换 wheel 需匹配构建平台，且 PySide6 与 shiboken6 版本应配套。可通过公开的构建脚本和 spec 使用这些库
重新构建应用。组件与来源详情见随包的[第三方声明](../../packaging/linux/THIRD_PARTY_LICENSES/NOTICE.md)。
