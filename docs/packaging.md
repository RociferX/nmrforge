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
4. `appimagetool AppDir` 生成单文件 AppImage（自动生成 AppRun）。

## 版本与命名

- 版本单一来源：`core/__init__.py` 的 `__version__`（构建脚本自动读取）。
- 产物名：`NMRForge-<version>-<arch>.AppImage`。
- 发布节奏：每次 tag 后构建；后续可加 CI（GitHub Actions Linux runner）。

## 已知注意点

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
