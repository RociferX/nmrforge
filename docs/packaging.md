# AppImage 打包方案（NMRForge）

状态：**暂缓分发**。v0.9.0 只发布 Apache-2.0 源码，不附带 AppImage。

本页保留未来 Linux AppImage 的构建方案；真正分发前必须完成根目录
`APPIMAGE_RELEASE_CHECKLIST.md`，并按最终捆绑的 PySide6/Qt 与其他组件复核许可。

## 发行策略（PACK-015，2026-09-12 定案）

- **当前 v0.9.0 发行物只有源码。** AppImage 是预留的未来二进制形态；它由 PyInstaller 打成，`config/`、`presets/`、`gui/assets/` 通过 spec 的 `datas` 进 `_MEIPASS`，资源定位见 `core/app_paths.py`。
- **`pip install .` / wheel 不是受支持的发行方式**：运行资源（`config/`、`presets/`、`gui/assets/`）位于仓库根目录、不属于任何 Python 包，setuptools 的包发现与 package-data 覆盖不到；即使装上，`resource_path()` 也找不到这些目录。wheel 只作为开发/依赖解析用途，开发一律使用 `pip install -e .`（见 README 开发流程）。
- `pyproject.toml` 只保留一个控制台入口 `nmrforge-viewer`（独立谱图查看器），它不依赖仓库根资源；主 GUI 入口是 `main.py`（由 AppImage 启动），不作为 console script 发布。
- 回归门禁：`tests/test_audit_rest_fixes.py` 锁定 spec 的 `datas` 必须覆盖三个运行资源目录，防止打包配置被误删后 AppImage 静默缺资源。

## 产物边界

**打包进 AppImage：**

- 应用本身（Python 字节码 + PySide6 + pyqtgraph + numpy/scipy/matplotlib/nmrglue 等依赖）。
- config/ 与 presets/（通过 PyInstaller datas 打进 _MEIPASS，见 core/app_paths.py）。
- 应用图标与 desktop 文件。

**不打包（运行时发现）：**

- NMRPipe/SMILE 等外部后端：沿用 NMRFlow 的运行时查找策略
  （PATH → csh 环境 ~/.cshrc 的 NMRPIPEBIN → 常见安装目录），AppImage 不内置。
  AppImage 内应用通过 `core/app_paths.py` 定位自身资源，与外部后端无关。

## 构建流程（在 Linux 构建机/VM 上执行）

```bash
# 1) 安装 appimagetool（只需一次）
#    https://github.com/AppImage/appimagetool/releases

# 2) 构建（脚本内部：venv → pip install -e . → PyInstaller → AppDir → appimagetool）
packaging/linux/build_appimage.sh

# 产物：build/appimage/NMRForge-<core.__version__>-x86_64.AppImage
#      （版本号来自 core.__version__，不在此写死）
```

关键步骤：

1. `python3 -m venv` + `pip install -e .`（依赖与 pyproject.toml 一致）。
2. PyInstaller 按 packaging/linux/NMRForge.spec 打包：入口 main.py，
   datas 包含 config/ 与 presets/；`console=False`（Qt GUI）。
3. 组装 AppDir：`usr/bin/NMRForge`（可执行 + _internal）、
   `usr/share/applications/NMRForge.desktop`、
   `usr/share/icons/hicolor/256x256/apps/nmrforge.png`。
4. 生成可执行 `AppRun`（appimagetool **不**自动生成）。AppRun 内置桌面
   集成：首次/移动后自动把 desktop 入口与图标装到 `~/.local/share`（Exec/
   TryExec 指向 AppImage 真实路径，直接删除 AppImage 文件后菜单项自动隐藏）；
   `./NMRForge.AppImage --remove-desktop`（或 `--uninstall-desktop`）可移除
   入口与图标；`NMRFORGE_NO_DESKTOP=1` 跳过自安装。
5. `appimagetool AppDir` 生成单文件 AppImage（runtime 优先用本地缓存
   `~/.cache/nmrforge-appimage/runtime-<arch>`，可用 `RUNTIME_FILE` 覆盖）。

## 版本与命名

- 版本单一来源：`core/__init__.py` 的 `__version__`（构建脚本自动读取）。
- 产物名：`NMRForge-<version>-<arch>.AppImage`。
- 发布节奏：每次 tag 后构建；后续可加 CI（GitHub Actions Linux runner）。

## 已知注意点

- **pyinstaller-hooks-contrib hook-workflow 冲突**：contrib 的泛用 hook-workflow
  会把本地顶层包 `workflow/` 当 PyPI 发行包并 `copy_metadata('workflow')`，
  构建报 PackageNotFoundError。已用 packaging/linux/hooks/hook-workflow.py
  空 hook 遮蔽（spec `hookspath` 生效）。
- **hookspath 路径基准**：PyInstaller 的 hookspath 按进程 cwd 解析（构建脚本
  在仓库根运行），与 datas 的 spec 目录基准不同；spec 内必须用
  `os.path.abspath(os.path.join(SPECPATH, "hooks"))`，否则目录不存在被静默
  跳过。
- **AppRun 必须自带**：appimagetool 不生成 AppRun，缺失时 AppImage 解压后报
  "AppRun: No such file or directory"。

## 构建记录（2026-09-02 首次端到端验证）
- 验证（桌面集成，隔离 HOME）：自动安装 → desktop 的 Exec/TryExec 指向
  AppImage；`--remove-desktop` 删除入口与图标；再次运行恢复；`NMRFORGE_NO_DESKTOP=1`
  不创建入口。应用均正常启动。
- runtime 下载抖动处理：appimagetool 需要联网下载 type2-runtime，GitHub
  抖动会报 "Failed to download runtime"；可从 appimagetool 自身提取
  （`--appimage-offset` + `dd`）后放入缓存目录，脚本自动复用。


- 构建机：VM Ubuntu 22.04（glibc 2.35）、uv python 3.12.13、
  PyInstaller 6.22.2、appimagetool continuous 8c8c91f、mksquashfs 系统包。
- 命令：`PYTHON=<python3.12> PATH=$HOME/nmrforge-test-artifacts/tools:$PATH
  APPIMAGE_EXTRACT_AND_RUN=1 bash packaging/linux/build_appimage.sh`。
- 产物：`build/appimage/NMRForge-0.1.0-x86_64.AppImage`（约 123 MB，
  858 文件，zstd squashfs）。
- 验证：`_internal` 内含 config/presets/gui/assets；`ldd` 无缺失系统库；
  `timeout 15 env QT_QPA_PLATFORM=offscreen ./NMRForge-*.AppImage
  --appimage-extract-and-run` 退出码 124（正常启动被超时结束，无 traceback）。


- **glibc 兼容**：PyInstaller 不静态链接 glibc，应在较老发行版（如 Ubuntu 20.04/22.04）
  上构建，产物才能覆盖更多目标机器。
- **FUSE**：部分系统需 `./NMRForge.AppImage --appimage-extract-and-run`，
  或设置 `APPIMAGE_EXTRACT_AND_RUN=1`；这是 AppImage 通用行为，非本软件问题。
- **Qt 插件**：PyInstaller 的 PySide6 hook 会自动收集插件；若出现
  "could not find or load the Qt platform plugin"，在打包机验证
  `QT_QPA_PLATFORM=offscreen` 下的 smoke 测试。
- **路径**：代码一律用 `core/app_paths.py` 定位资源，禁止硬编码绝对路径，
  保证 AppImage 可移动到任意目录运行。

## 验证清单（打包后）

```bash
./build/appimage/NMRForge-*.AppImage --appimage-extract-and-run   # 能启动
QT_QPA_PLATFORM=offscreen ./build/appimage/NMRForge-*.AppImage    # 无显示环境可跑
ldd usr/bin/NMRForge | grep 'not found'                            # 无缺失系统库
```

打包相关文件：

- packaging/linux/NMRForge.desktop：desktop 入口
- packaging/linux/NMRForge.spec：PyInstaller 配置
- packaging/linux/build_appimage.sh：一键构建脚本
- packaging/linux/icons/：应用图标（SVG 源 + PNG 产物）
- core/app_paths.py：开发/冻结态资源路径解析


## 许可与合规（LGPL）

AppImage 把 PySide6 与 Qt 库打进产物，因此属于**分发 LGPL 覆盖的库**：必须随产物提供许可正文
与声明，并允许接收方替换/重链接这些库。相关机制已固化，构建脚本会在失败时报错而不是静默漏发：

| 机制 | 位置 | 作用 |
| --- | --- | --- |
| 许可正文入库 | `packaging/linux/THIRD_PARTY_LICENSES/LGPL-3.0.txt`、`GPL-3.0.txt` | LGPL-3.0 以引用方式并入 GPL-3.0，故两者都随产物提供（PySide6 的 wheel **不带**任何 LGPL 正文） |
| 来源与哈希 | `THIRD_PARTY_LICENSES/PROVENANCE.txt` | 记录抓取 URL、字节数与 SHA-256，是校验的唯一事实来源 |
| 声明 | `THIRD_PARTY_LICENSES/NOTICE.md` | 列出被打包组件与所用许可选项、对应源码获取方式、替换/重链接步骤 |
| 构建前校验 | `scripts/check_third_party_licenses.py` | 正文缺失/被改、NOTICE 关键内容被删、构建脚本不再引用或不再提供 `--licenses` 都会失败 |
| 打入产物 | `build_appimage.sh` 第 2.5 步 | 复制到 `usr/share/doc/NMRForge/third-party/`，并写入 `BUILD_INFO.txt`（版本、git 提交、工作区是否脏、构建时间、依赖版本） |
| 运行时自述 | `AppRun --licenses` | 打印声明与各文件位置，产物内可自查 |
| 可替换/重链接 | `build_appimage.sh` 的 `PYSIDE6_REQUIREMENT` / `SHIBOKEN6_REQUIREMENT` / `EXTRA_PIP_ARGS` | 用自建/修改过的 Qt 绑定重建 AppImage；构建脚本与 spec 均在公开源码中，故重链接所需材料齐备 |

构建机验收（除了常规启动测试）：

```bash
python scripts/check_third_party_licenses.py                 # 正文与声明一致
python scripts/audit_third_party.py --csv /tmp/audit.csv      # 构建环境里的完整依赖许可审计
./build/appimage/NMRForge-*.AppImage --appimage-extract-and-run --licenses   # 产物内自述
find build/appimage/NMRForge.AppDir/usr/share/doc -type f     # 应有 LGPL/GPL 正文、NOTICE、BUILD_INFO
```

> 注意：`NOTICE.md` 明确标注需由权利人复核；本节是工程实现与合规机制，不构成法律意见。
