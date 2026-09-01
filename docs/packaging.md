# AppImage 打包方案（NMRForge）

目标：最终以单个可执行文件（`NMRForge-<版本>-<架构>.AppImage`）在 Linux 发行，
无需用户安装 Python/Qt 依赖。

## 产物边界

**打包进 AppImage：**

- 应用本身（Python 字节码 + PyQt6 + pyqtgraph + numpy/scipy/matplotlib/nmrglue 等依赖）。
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

# 产物：build/appimage/NMRForge-0.1.0-x86_64.AppImage
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
- **Qt 插件**：PyInstaller 的 PyQt6 hook 会自动收集插件；若出现
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
