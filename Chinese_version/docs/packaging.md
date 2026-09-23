# AppImage 打包方案（NMRForge）

状态：**v1.0.1 已发布**。源码保持 Apache-2.0；Linux AppImage 随 Release 分发（2026-09-22 起 Release 上只有**一份**产物，界面语言在运行时切换；该产物按公开快照提交 `90e0d8a` 重建，见 `APPIMAGE_RELEASE_CHECKLIST.md`）。

本页记录 Linux AppImage 的构建方案；每次发布前必须完成根目录
`APPIMAGE_RELEASE_CHECKLIST.md`，并按最终捆绑的 PySide6/Qt 与其他组件复核许可。

## 发行策略（PACK-015，2026-09-12 定案）

- **v1.0.1 同时发布源码与 AppImage。** AppImage 由 PyInstaller 打成，`nmrforge_data/config`、`nmrforge_data/presets`、`gui/assets`、`ui_support/locales` 通过 spec 的 `datas` 进 `_MEIPASS`（数据包保持同形），资源定位见 `core/app_paths.py` 与 `ui_support/i18n.py`。
- **单产物 + 运行时语言（2026-09-21）。** 界面文案改成 `tr("English text")` + `ui_support/locales/zh.json` 查表层后，构建脚本只产出一个 `NMRForge-<版本>-<arch>.AppImage`；界面语言按「`NMRFORGE_LANG`/`NMRFORGE_LANGUAGE`(临时钉死)→ 设置里的偏好(`nmrforge.local.yaml` 的 `language:`)→ 系统区域(QLocale / `LANG` / `LC_ALL`)→ 本树 `ui_support/locales/default.json`」解析(GUI 里可改,见 [gui.md](gui.md))。公开英文树构建的产物默认英文，私有主干默认中文；两者都随包带 `zh.json`，中英用户拿的是同一个文件。spec 另排除本项目用不到的 Qt 模块（体积），排除项只影响体积，用到时从 `_EXCLUDED_QT_MODULES` 删掉即可；
  `PySide6.QtTest` **不能**排除（`qtcompat/__init__.py` 无条件 import 它），已留注释与守卫。
- **`pip install .` / wheel 受支持（2026-09-21，方案 A）**：运行资源不再散在仓库根目录 —— 默认配置与实验模板进了数据包 `nmrforge_data/`（`config/`、`presets/`），`gui/assets` 与 `ui_support/locales` 走 `package-data`（见 `pyproject.toml` 的 `[tool.setuptools.package-data]`）。**源码检出与装机态的相对位置一致**，`resource_path("config/nmrforge.yaml")`、`resource_path("presets")` 两边都指得到；开发仍推荐可编辑安装（`pip install -e .`）。包内的 `core|backend|workflow|nmrforge_api/README.md` 也随包一起发 —— `behavior_digest` 覆盖这四棵树的**全部**文件，缺了它们装机态算出来的指纹就和 `compat_declaration` 对不上（`compat_verified` 会恒为 false）；反过来，`ui_support/locales/{source.json,converted.json}` 只是 `scripts/i18n_extract_ui.py` 的翻译工具清单，运行期只读 `default.json`/`en.json`/`zh.json`，所以 `package-data` 里逐个点名而不用 `locales/*.json` 通配（`package_data` 按文件系统展开，`exclude-package-data` 对显式 `package_data` 不生效）。本机重复构建前先删 `build/`：`setuptools` 的 `build_py` 不清理 `build/lib`，改过 `package-data` 后原地重建会把上一轮的残留文件一起打进 wheel（CI 在干净检出里构建，不受影响）。PyPI 发布是另一件事，尚未进行。
- `pyproject.toml` 只保留一个控制台入口 `nmrforge-viewer`（独立谱图查看器），它不依赖仓库根资源；主 GUI 入口是 `main.py`（由 AppImage 启动），不作为 console script 发布。
- 回归门禁：`tests/test_audit_rest_fixes.py` 锁定 spec 的 `datas` 必须覆盖四个运行资源目录，防止打包配置被误删后 AppImage 静默缺资源。

## 产物边界

**打包进 AppImage：**

- 应用本身（Python 字节码 + PySide6 + pyqtgraph + numpy/scipy/matplotlib/nmrglue 等依赖）。
- nmrforge_data/config 与 nmrforge_data/presets（datas 打进 _MEIPASS/nmrforge_data，见 core/app_paths.py）。
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
   datas 包含 nmrforge_data/config 与 nmrforge_data/presets；`console=False`（Qt GUI）。
   打包后立即跑两道构建期检查：**运行资源自检**（在 `_internal` 内容目录里查
   `nmrforge_data/{config,presets}`、`gui/assets`、`ui_support/locales` 与语言包）与
   **冻结态启动冒烟**（独立 HOME + `QT_QPA_PLATFORM=offscreen` 启动打包好的可执行文件，
   20 秒内不退出才算通过）。两道都必要：文件在 ≠ 跑得起来 —— 2026-09-21 首次真机构建时，
   产物文件齐全却因为排除了 `PySide6.QtTest`（`qtcompat` 无条件 import 它）一启动就崩。
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
- 命令：`PYTHON=<python3.12> PATH=$HOME/appimagetool:$PATH
  APPIMAGE_EXTRACT_AND_RUN=1 bash packaging/linux/build_appimage.sh`。
- 产物：`build/appimage/NMRForge-0.1.0-x86_64.AppImage`（约 123 MB，
  858 文件，zstd squashfs）。
- 验证：`_internal` 内含 nmrforge_data/config、nmrforge_data/presets、gui/assets；`ldd` 无缺失系统库；
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
