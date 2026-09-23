# 故障排查

nmrForge 的错误信息应该说明「缺什么、该怎么办」。如果在 GUI 或 API 里看到裸的
`KeyError`/`TypeError`/`NoneType` 栈回溯,那是 bug,请报告(见
[CONTRIBUTING.md](../../CONTRIBUTING.md))。

## 提示「NMRPipe NOT FOUND」/ 处理无法启动

原因:后端定位不到 NMRPipe 可执行文件。

1. 确认 NMRPipe 已安装,且它的 `bin` 目录里有 `nmrPipe`;
2. 在 `nmrforge_data/config/nmrforge.yaml`(或 `nmrforge_data/config/nmrforge.local.yaml`)里
   显式指定:

   ```yaml
   backend:
     nmrpipe:
       path: /opt/NMRPipe/nmrbin.linux212_64   # 目录或可执行文件
   ```

   定位顺序:显式路径 → `nmrpipe_bin` → `PATH` → `csh` 环境 → 常见安装位置
   (`backend/nmrpipe_finder.py`);
3. `python examples/quickstart.py <数据集>` 会先把「有没有找到 NMRPipe 与 SMILE」打出来,
   这是确认检测结果最快的办法。

数据理解与 QC 不需要 NMRPipe;转换、FT、相位、基线与 SMILE 需要。

## 提示「本机未找到 tcsh/csh (NMRPipe scripts need a C-shell)」

NMRPipe 脚本是 C-shell 脚本,nmrForge 通过 `csh`/`tcsh` 执行它们。请在机器上装一个 C-shell
(`tcsh`)。这条消息由 `backend/runtime.py` 以 `ToolError` 抛出。

相关的一条是 `未找到 proj3D.tcl (NMRPipe projection tool)` —— 说明 NMRPipe 安装不完整,
或它的 `com/` 目录不在定位器预期的位置。

## 重建失败,或机器失去响应 / 内存耗尽

SMILE 重建的内存主要由「直接维填零点数 × 迭代间接维 FT 网格」决定,所以填零会让它涨得很快。
nmrForge 在开始前先估算峰值,并把显式的 `-maxMem` 传下去;估算超过可用内存时它拒绝启动并说明原因。

怎么办:

1. 降低直接维填零,或收窄直接维范围(提取窗口),减少每平面的点数;
2. 降低 `smile.nthread`;
3. 确认估算值确实低于机器能提供的量 —— 机器同时还在跑别的东西时,它就不是你测出那个数字时的机器;
4. 如果框架为了适配内存把直接维填零降到了 1×TD,它会报出来:
   「内存不足:直接维填零已降为 1×TD 以降低 SMILE 内存」。请尽量把这个降低显式化,
   让它记为你的选择而不是回退。

项目历史上有过一次低估重建内存、导致宿主机器断电的案例,所以这道护栏偏保守而不是偏乐观。

## 采样分类给出 `uncertain`,处理拒绝启动

这是安全闸门,不是 bug。它表示元数据自相矛盾 —— 最常见的是 `nuslist` 或脉冲序列声称是 NUS,
而实际采到的点覆盖了完整网格,或者采样表里有重复/越界坐标。

1. 看日志里的采样证据行:它们会说明触发了哪条规则、用的是哪些数字;
2. 如果数据确实是完整采样,数据集会自动改判为 `uniform`;若仍是 `uncertain`,说明参数确实
   不一致,需要人来判断;
3. 修正分类比看上去重要:uniform 与 NUS 会改变每个处理参数的含义,所以没有「照样处理」的开关。

## 提示「Gaussian peak fitting is currently supported only for 2D spectra.」

Gaussian 定位是二维模型,所以对 1D/3D 谱直接报错,而不是静默退回抛物线精修。请用
`localization: parabolic`,或只处理 2D 平面。

如果某个峰的 Gaussian 拟合失败,该峰单独退回抛物线,原因记录在 `<峰表>.localization.json`
与 run 参数里。

## AppImage 起不来

| 现象 | 处理 |
| --- | --- |
| 双击没有任何反应 | 先确认执行位:`ls -l NMRForge-*.AppImage` 是否为 `-rwxr-xr-x`;不是就 `chmod +x NMRForge-*.AppImage`。图形界面里也可以右键 → 属性 → 勾选「允许作为程序执行」。从 Windows 共享目录 / U 盘拷进虚拟机时经常丢这一位 |
| 解包后报 "AppRun: No such file or directory" | 用 `./NMRForge-<版本>-x86_64.AppImage --appimage-extract-and-run`,或设 `APPIMAGE_EXTRACT_AND_RUN=1` |
| FUSE 相关的挂载错误 | 同上;不是每台机器都有 FUSE,这是 AppImage 的通用行为 |
| "could not load the Qt platform plugin" | 确认没有在 shell 里覆盖 `QT_QPA_PLATFORM`;日常桌面使用请 unset |
| 应用菜单里没有图标 | 先启动一次让它装好桌面入口;删掉 AppImage 文件入口会自动隐藏 |
| 完全不想要桌面集成 | `NMRFORGE_NO_DESKTOP=1 ./NMRForge-<版本>-x86_64.AppImage` |

## 实验类型判错

分类器用的是脉冲序列、各维核种组合、维序与采集参数,不看文件名。要检查两件事:

1. 脉冲序列是不是该实验的标准版本?本地改过的脉冲序列可能识别不出来,此时分类器会退到更宽的
   家族,并给出更低的置信度和理由;
2. 处理前先修正实验模板。`presets/*.yaml` 是实验模板的唯一来源;模板错了会一路传到相位处理、
   符号约定与窗函数缺省值,而且没有任何质量指标能可靠地发现它。

## 测试报 `PermissionError ... pytest-of-<user>`

Windows 上测试套件把临时目录建在系统临时目录下。如果那个路径被锁(受限或只读的临时目录,
或上次中断留下的 `pytest-of-<user>` 目录),pytest 就建不了它的 base temp 目录。

按优先级:

```bash
python -m pytest --basetemp=/tmp/nf_pytest -q              # Linux
python -m pytest --basetemp=$env:TEMP\nf_pytest -q          # PowerShell
```

如果原因是残留的 `pytest-of-<user>` 目录,删掉即可 —— 它本来就是一次性的。

## GUI 测试报显示相关错误

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q      # Windows: 设 QT_QPA_PLATFORM=offscreen
```

## 日志在哪?

- GUI:日志面板按当前选择作用域(单数据 / 数据组 / 实验类型 / 全局);
- 运行:每个 `WorkflowRun` 在项目记录里带自己的消息与告警列表;
- 参数扫描:研究根下每个 run 一个目录,里面有脚本、候选谱、峰表与 run 记录;研究根下有
  `manifest.json` 与 `runs.json`。
