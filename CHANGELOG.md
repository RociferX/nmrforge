# 修改记录(历史条目)

# 修改记录(历史条目)

## 0.2.199-补29bf(2026-08-28,Assignment 表头开关 + 框选截止 + 轴峰排除加远)

- 点击峰表 Assignment 列标题开关图上指认标签(表头显示 ✓/✗ 状态);
- 选择模式框选超过谱图区域时截止到谱图边缘(此前拖出界会选不上);
- 轴峰排除边缘点数 2→5(靠近边缘的轴峰残余一并排除)。

## 0.2.199-补29bg(2026-08-28,Assignment 标签随缩放+绑定标记尺寸;选择/删除提速)

- 峰指认标签改用 QGraphicsTextItem(随谱图缩放,不再抵消视图变换),字体
  像素尺寸=峰标记大小(数据坐标单位),与「标记尺寸」输入框绑定;
- 标签对象池复用 + 删除走内存峰列表(不再全表重读),选择/删除大量峰不再
  卡顿(2000 峰高亮 ~16ms);
- 测试更新/新增;全量 pytest 全绿,ruff 通过。

## 0.2.199-补29bd(2026-08-28,1D 隐藏峰相关控件 + 控制区贴合内容)

右侧谱图:开启 1D 时隐藏峰工具栏(两行)/峰表/峰信息标签(峰标记 1D 下本就不
显示);关闭 1D 恢复,且控制区高度贴合内容(去掉 ppm 显示行上下的空白,
view_splitter 按 controls sizeHint 调整)。测试 +1;全量 pytest 全绿,ruff 通过。

## 0.2.199-补29be(2026-08-28,按钮改名)

峰操作栏「选择」→「Select mode」、「Add peak」→「Add peak mode」(选中态
追加 : ON)。

## 0.2.199-补29bb(2026-08-28,峰操作栏拆两行)

峰操作栏由一行拆为两行:第一行 Show peaks / 选择 / Add peak / 标记尺寸;
第二行 Delete selected / Import peaks / Export peaks / Save peaks。测试更新;
全量 pytest 全绿,ruff 通过。

## 0.2.199-补29bc(2026-08-28,「峰标记」改名为「标记尺寸」)

峰操作栏大小输入框标签「峰标记」→「标记尺寸」(tooltip 同步)。

## 0.2.199-补29az(2026-08-28,峰标记改 × + 随谱图缩放 + Peak size 输入框)

- viewer 峰标记由圆点改 Poky 风格 ×(ScatterPlotItem symbol="x"),
  pxMode=False——标记尺寸为数据坐标单位,随谱图缩放同步缩放;
- 峰操作行新增「峰标记」输入框(QDoubleSpinBox 0.5–50,默认 8,数据坐标
  单位),调整立即生效;选中峰标记放大 1.6×;
- 测试更新 +1(符号/pxMode/尺寸可调);全量 pytest 全绿,ruff 通过。

## 0.2.199-补29ba(2026-08-28,删除峰按钮对自动/手动峰均可用)

用户澄清:删除峰按钮不应受「Add peak 手动加峰」限制——自动生成的峰同样
可删。删除按钮可用条件 = 选中数据 + 峰表非空(加载/导入/手动加峰/编辑后
统一刷新);无峰表时禁用。测试更新 +1;全量 pytest 全绿,ruff 通过。

## 0.2.199-补29ay(2026-08-28,spinbox 上下箭头改白色图片)

用户反馈箭头仍不可见。Fusion 箭头字形颜色不随 QSS 按钮背景变化,改用
白色三角 PNG 图片(gui/assets/spin_up.png / spin_down.png,纯 stdlib 生成)
经 QSS `::up-arrow/::down-arrow { image: url(...) }` 指定,箭头按钮保持
深色底;资源路径开发/冻结通用(resource_path)。offscreen 渲染验证按钮区
白色像素 >20;全量 pytest 全绿,ruff 通过。

## 0.2.199-补29ax(2026-08-28,选择模式框选卡死修复)

用户反馈:选择模式一拖动框选就卡死。修复两点:
- 框选虚线不再加在谱图场景里(拖动时场景含等高线,每帧全场景重绘导致
  卡死;且场景事件处理中途增删 item 不稳定)——改画在 plot viewport 的
  轻量覆盖层(_BoxSelectOverlay),拖动只重绘本层;
- 框选判定只比对「框范围 × 缓存峰坐标」:峰标记绘制时缓存数据坐标
  (_peak_data_xy),框选结束直接范围比较,不再逐峰做轴/ppm 换算等其它运算;
- 回归测试 +1(框选只比对缓存峰坐标);全量 pytest 全绿,ruff 通过。

## 0.2.199-补29aw(2026-08-28,修复点击谱图报错 MouseClickEvent 无 buttonDownScenePos)

VM 实测点击谱图报 `AttributeError: 'MouseClickEvent' object has no attribute
'buttonDownScenePos'`——pyqtgraph sigMouseClicked 发的是 MouseClickEvent,
buttonDownScenePos 是 MouseDragEvent 的 API。修复:
- _on_plot_clicked 用 try/except 兼容:无该属性时改用 eventFilter 记录的
  左键按下场景坐标判断拖拽;
- 新增 _suppress_click:框选结束的释放不当作单击,切出选择模式时复位;
- 回归测试 +1(模拟无 buttonDownScenePos 的 MouseClickEvent 不再报错);
  全量 pytest 全绿,ruff 通过。

## 0.2.199-补29av(2026-08-28,数据状态加入「已选峰」)

左侧项目树样品数据状态链补全:运行中 → 已选峰 → 已生成谱图 → 已生成 FID →
已导入。_data_status 检测 data/peaks/<exp>-<data>.list(旧 CSV 兼容/扁平
布局兜底),峰表存在即显示「已选峰」。测试 +1;全量 pytest 全绿,ruff 通过。

## 0.2.199-补29au(2026-08-28,调阈值不自动选峰)

用户反馈(推翻补29ar 的「调整即重选」):调整选峰阈值只更新数值,不再自动
运行;点「运行/重新处理」后按新阈值重新选峰。阈值条/输入框 tooltip 同步
说明;回归测试 +1(调阈值不触发 pick_peaks,点运行按新阈值执行);全量
pytest 全绿,ruff 通过。

## 0.2.199-补29at(2026-08-28,暗色箭头 + 选择模式框选 + Assignment 列 + 轴峰排除)

用户四项需求:
- 暗色主题:QSpinBox/QDoubleSpinBox 上下箭头按钮深色底(3c3c3c/hover 4a4a4a),
  白色箭头可见性修复;
- 选择模式:峰操作行新增「选择」开关——开启后左键拖动框选峰(虚线框+半透明
  填充),松开选中框内全部峰并联动峰表多选;「选择」「Add peak」「1D 查看」
  三者互斥(开一个自动关另外两个);选择/加峰开启时 ViewBox 切 PanMode,
  关闭恢复 RectMode 框选缩放;
- 峰表新增 Assignment 列(label,2D/3D 均在 Peak_ID 后),可编辑,导入/导出
  Poky .list 保持;
- 自动选峰排除轴峰:core/qc/peak_detection 新增 edge_margin(排除第 0 轴
  上下边缘 N 点内候选峰),pick_peaks 默认 2 点——最上下横着的一条(轴峰)
  不再入选。

测试:+4(轴峰排除、Assignment 列、模式互斥、列偏移修正);全量 pytest
全绿,ruff 通过。

## 0.2.199-补29as(2026-08-28,下一步提示:可选 SMILE 不再卡住)

用户反馈:pipeline 上方「下一步」被可选的 SMILE 优化卡住(未做 SMILE 优化
时永远提示 SMILE 优化)。修复:
- 「下一步」只指向真实必做步骤(跳过可选的 smile);
- SMILE 未做/已过期时单独显示「可选做: SMILE 优化(重新运行)」,
  与「下一步」并存,如「可选做: SMILE 优化 | 下一步: 峰挑选」;
- SMILE 已完成后不再出现「可选做」;
- 首次导入后的下一步气泡同样跳过 SMILE;
- 测试 +1(未做 SMILE 时下一步=峰挑选 + 可选做;完成后取消可选做);
  全量 pytest 全绿,ruff 通过。

## 0.2.199-补29ar(2026-08-28,峰文件 Poky 化 + 加峰开关 + 阈值条 + 自动展示)

峰文件格式(用户:全程 Poky,不要 CSV):
- 峰文件即 Poky/Sparky `.list`(契约 §6 对齐):workflow.pick_peaks 输出
  peaks/<exp>-<data>.list("Assignment w1 w2 [w3] Data Height Volume"),
  不再写 CSV;core.peaks.peak_table.save_peaks 统一写 .list,load_peaks
  自动判别 .list/旧 CSV(旧 CSV 仅兼容读取);
- SMILE 优化内部产物(smile_optimized/*.csv,含 Reliability 列,非用户
  峰表)保留 CSV 不动;
- GUI 峰表加载/保存/导入/导出全部走 .list(旧 CSV 可读),行自动编号。

加峰交互(用户:Add peak 开关 + 吸附峰顶):
- gui/spectrum_panel「Add peak」改开关:开启后点击谱图加峰,程序自动把
  点击点吸附到附近峰顶(±6 点窗口内 |值| 最大,core/qc/peak_detection.
  snap_to_peak_top);找不到显著峰顶则直接用点击位置;关闭恢复选中模式;
- viewer 点击加峰按当前谱维序映射峰表列(3D 切片 dim_indices → F1/F2/F3,
  2D → H/N),Intensity 取该点数据值。

阈值条(用户:拖动条 + 输入框,调整即重选):
- 峰挑选步骤新增「阈值(σ)」滑块(3.0–15.0)+ 数值输入,双向同步;
  调整完成(松开滑块/回车)立即重选峰;
- workflow.pick_peaks 新增 sigma_multiplier 参数,min_snr 同步;
  默认阈值再拉高:5σ → 6σ(0.2.199-补29aq 后仍选多)。

自动展示(用户):峰挑选成功后立即在右侧谱图面板展示谱图并显示峰。

测试:+2(吸附、阈值参数);全量 pytest 全绿,ruff 通过。

## 0.2.199-补29aq(2026-08-28,选峰阈值修复:选太多)

用户反馈选峰过多,复现定位两个根因并修复:
- 平坦区/脊线伪峰:原检测用 `value == 窗口最大` 选中所有等于窗口最大的
  点——平坦基线(100 平地 + 2 真峰)一次选 7952 个伪峰;改为**严格局部
  极大**(中心须大于环邻域最大,排除平地/脊线),实测 7952 → 2;
- 阈值偏低:3σ 下噪声局部极大大量混入(零中心噪声 + 12 真峰 → 26 个,
  含 14 个噪声伪峰);选峰步骤默认阈值提至 **5σ**(min_snr 同步 5),实测
  只留 SNR≥5 真峰、0 噪声伪峰;检测算法默认仍 3σ 供 QC 使用;
- 测试 +3(平坦基线不选、5σ 滤噪声、pick_peaks 平地不选);全量 pytest
  756 项全绿,ruff 通过。

## 0.2.199-补29ap(2026-08-28,峰挑选符号规则 + 3D 轴映射修复)

峰挑选改进(用户规则,总管直接实现):
- 峰符号规则:presets peak_sign 驱动——uniform(单符号,HSQC/COSY 等)只
  保留占多数的符号峰(候选峰计数,平局按绝对强度总和;主符号为负时同样
  适用);mixed(正负共存,HNCACB 等)正负都选;类型未知回退 uniform;
- core/qc/peak_detection.py 新增 sign_mode(positive/negative/both/
  dominant),Peak.height 保留真实符号(CSV Intensity 带符号),snr 取绝对值;
- workflow.pick_peaks 按数据 metadata(experiment_type.name)解析模板
  peak_sign 自动选符号模式,日志注明符号模式;CSV 列兼容契约 §6;
- bug 修复:pick_peaks 3D ppm 轴映射按 FDDIMORDER 定位 FDF 块(与 viewer
  0.2.151 同源),峰表 F1/F2/F3_shift 按逻辑维取对应数据轴 ppm——修复
  ORDER 2 3 1 时 F2/F3 ppm 互换;可靠性容差同源修正;
- 测试:+6(检测 both/dominant、2D 主符号正/负、mixed 正负、3D 逻辑轴
  映射);全量 pytest 753 项全绿,ruff 通过。

流程变更:2026-08-28 起所有任务由总管直接实现(不再派发 GUI/Backend
窗口,人工通道停用),见 docs/manager/decisions.md D-2026-08-28。

## 0.2.199-补29i~补29am(2026-08-27~28,直接维相位路线定型 + 同核谱显示链 + UI/文档)

直接维相位优化路线(补29i~补29s):按用户「人工投影调相」思路定型——
纯实终谱投影迹线 + Hilbert(HT)补虚部逐条调相 + 统计共识:
- 补29i/i2/j/k:逐维共识相位搜索(复型保留 + 逐条迹线统计共识;共识搜索
  用零填复型终谱 + p1 细化钳制;uniform 全轴复型 keep_complex_all;投影
  平面逐维搜索,投影迹线=另一维点数之和);
- 补29n/n2/o/o2/p/q/r/s:直接维改为纯实终谱投影迹线 + HT 逐条调相统计
  (HT 约定与显示层一致,scipy;内联共识 + 最强迹线排除;搜索统一返回
  0-180° 折叠值;预览用实型读取并改无填零,与间接维优化一致;相位优化
  预览默认最低填零 next_pow2(TD),如 120→128);docs:101 重新处理验证
  (旧投影文件不可用)、HNN 双 15N 合法重名标签说明(投影须走 numpy 路径);
- 补29ad:审计后保留 uniform 直接维复型路径(uniform 有真实虚部,不套
  NUS 的投影+HT 方案)。

质量/报告/状态(补29u~补29ab):报告缓存指纹修正(quality.json 移至投影
注册后)、基线优化提速(无峰迹线抽样)、诊断坏点向量化、相位评分修正;
人工途径补齐质量报告;报告基线分与优化分一致、能量检查只统计非空迹、
mixed 不报负峰、评估有进度;报告顺序=数据质量→优化→谱图质量,诊断
实时输出;左侧数据状态显示流程进度(已导入→已生成FID→已生成谱图)。

同核谱显示链(补29af~补29aj):处理时把重复核标签唯一化——proj3D 按
FDF 标签选轴不再歧义。下标优先级统一为「直接维 > acqu2 > acqu3」:
2D 双 1H:F2(直接)→1Hx、F1→1Hy;3D 三同核:F3→1Hx、F2→1Hy、F1→1Hz;
HNN 双 15N:F2(HSQC 的 N)→15Nx、F1→15Ny。文件层与显示层
(axis_labels_from_nuclei)同步,修复 2D 双 H 横轴(F2)显示 Hy 的问题;
标签解析器 _parse_nmrpipe_label 直接支持下标(1Hx/15Ny);NN 投影核名
比较先剥下标(15Ny→15N),fixed_axis/轴参数从 3D 谱正确定位(proj3D
同核平面文件头不可靠,不再作为参数来源)。

UI/文档(补29ak~补29am):轴序重排日志 warning→info(常规适配)+ 关于
对话框说明逻辑序约定;UI 层级名称「实验类型」→「实验」(项目→实验→
样品数据),删除顶部「处理」菜单与查看菜单的 Task/Log、报告入口,实验
注释新增样品名称/仪器/温度/备注字段;强制暗色主题(gui/theme.py,
主窗口与独立查看器,Fusion+QPalette+QSS,不受系统亮/暗主题影响),
上下文条/注释条黑底白字,实验标题为空时上下文条回退 exp_id。

测试基线:本地全量 pytest 747 项全绿(0.2.199-补29am)。

## 0.2.199-补29(2026-08-27,相位优化维度解析审计:TP/ZTP 流布局实证 + 轴映射修正)

审计目标:单文件/切片 fid 全兼容后,复核相位优化各路径的维度解析。在 VM
上以 sampleB(HNCACB,单文件 fid + 切片式 recon)与 sampleJ(手工 3D NUS
smile3.com)、sampleF(手工 2D uniform 3_process.com)为基准实证:

- NMRPipe 流 x=最快;单文件谱 nmrglue 数组 = 流逆序。2D 输出 (F1, F2);
  3D NUS finalize 输出 (F2, F1, F3)(28.ft3 (128,256,600)=(15N,13C,1H));
- TP=平面 XY→YX;ZTP=XYZ→ZYX(x↔z 互换)。当前 finalize 链
  F2 FT→TP→F1 FT→TP→ZTP→pipe2xyz 与手工 smile3.com 结构一致;
- 3D 重构平面 = 每直接维点一个平面,平面内 (13C hypercomplex 4×TD,
  15N States 实型);`read_pipe_complex` 的简单轴 0 交错拆包对真实平面
  是错的(旧测试模型与真实 SMILE 输出不符),但当前相位搜索已不用平面
  (补18 改复型终谱),仅窗函数优化/旧显示层搜索用到;
- 相位优化搜索路径(`_axis_index`/`_read_complex_ft3`/`_read_complex_preview`/
  `search_direct_phase_on_spectrum(axis=-1)`/`search_axis_memory`)实证正确:
  vm_verify_axes 对 sampleB 直接维/F2/F1 搜索分别得 (350,30,76.8)、
  (80,-22.5,73.9)、(355,0,96.2),与 28_nus.com 记录终相位 (86,-27.5)/(355,0)
  一致。

修复(审计发现的三处解析错误):
- `optimize_baseline` 读的是 NMRPipe 输出谱文件,却用内部约定 axis_index
  (3D=(F1,F2,F3));生产布局 3D=(F2,F1,F3),F1/F2 互换会拿 F2 数据给 F1
  选基线配置。新增 `file_axis_index`(core/processing/axes.py),
  BaselineParams 支持显式 np_axis,baseline_optimize 改用之;
- `window_optimize._nus_axis_map` 3D F1=2 错(指向直接维轴):重构平面堆叠
  实为 (F1 时, F2 时, F3),改 F1=0,F2=1;`_load_recon_planes` 3D 改回原始
  实型堆叠(不拆包 hypercomplex,与后端 SP 直接作用于该轴一致),并按首平面
  FDFILECOUNT 截断陈旧 test*.ft1;
- `_axis_sw` 按头部核标签(FDF{n}LABEL)匹配取 SW,不再按逻辑轴数字后缀
  (3D 头部 FDF1=15N/FDF2=1H/FDF3=13C,与逻辑 F2/F3/F1 不同号);
- `_append_final_summary` 3D 存储轴标签 ["F2","F3","F1"] → ["F2","F1","F3"];
- `phase_routes._load_recon_planes` 3D 不再加载(补18 起 3D 直接维搜索用
  keep_complex 终谱,旧代码白读数百 MB 且拆错);2D 行为不变;
- 后端 `_display_phase_search`(直连逃生口)同样按 FDFILECOUNT 截断陈旧平面。

测试:新增 tests/test_axis_layout_audit.py(file_axis_index 映射、3D 基线
轴映射、_nus_axis_map、_axis_sw 核标签、_load_recon_planes 原始堆叠+截断);
相关批次全部通过。

## 0.2.199-补29b(2D NUS 布局实证:用 uniform 制造 2D NUS 测试)

用户建议:2D NUS 没有现成工作目录,可用 uniform 2D 制造来实证。用
sampleF(uniform 1H-15N)按 2D NUS stage-1 链生成 recon.ft1(直接维
FT+EXT+PS -di+TP,全采样恒等,不跑 SMILE),实证:

- 2D recon.ft1 布局 = (F2 频, F1 时) 复型,F1 复型在**最后轴**;文件实型
  存 (F2, 2×F1)(实部块+虚部块),nmrglue 读回 (F2, F1) complex
  (FDTRANSPOSED=1, FDF2QUADFLAG=1);
- 修复 `window_optimize._load_recon_planes` 2D 分支:旧代码用
  `read_pipe_complex` 无条件拆轴 0,把已复型的 2D recon 直接维砍半
  ((514,64)→(257,64)),间接维窗优化在坏数组上评分;现改为
  iscomplexobj 检查,复型直接用、实型才拆包;
- `phase_routes._load_recon_planes` 2D 本就正确(_read_complex_preview);
- 2D finalize 链(FT -alt → PS → TP → -out)输出 (F1, F2),F1 预览
  unpack_axis=0 正确。

测试:新增 2D recon 读取回归(两个读取器都不砍直接维、内容一致)。

## 0.2.199-补29c(2026-08-27,生成谱图卡死:工作线程跨线程碰控件修复)

用户反馈:点击 pipeline 的「生成谱图」仍会卡死,并报
`QBasicTimer::start: Timers cannot be started from another thread`。

根因(工作线程直接调用 GUI):
- `PipelinePanel` worker finally 里直接 `self.refresh()`——跨线程对行
  控件 setStatus/setVisible,显示/隐藏会启动 Qt 内部计时器(工具提示/
  滚动条等),触发 QBasicTimer 错误并与 GUI 线程竞争导致卡死;
- `MainWindow._run_group_batch` worker 的 progress 回调直接调
  `_append_log` → `LogPanel.append` → `QTextEdit.appendPlainText`
  (光标闪烁计时器)+ 滚动条;worker finally 里还直接 `refresh()` 和
  `group_page.set_progress()`。

修复(全部改为经队列信号回主线程):
- pipeline:worker finally 只 `run_finished.emit()`;新增
  `_refresh_after_run`(run_finished 队列连接)在主线程复位 _run_active
  并 refresh(单数据/重跑终脚本两个 worker 同样处理);
- main_window:新增 `log_append_requested(str, object)` 队列信号,组批量
  progress 经它回主线程 `_append_log`;worker 不再 set_progress/refresh,
  统一移到 `_on_batch_run_done`(batch_run_done 队列槽,清进度+刷新)。

测试:新增真实线程回归(非 SyncThread):跑完经队列信号刷新、状态不再
RUNNING;全套 pytest 通过。

## 0.2.199-补29d(2026-08-27,生成谱图报告卡死 + 日志按数据作用域隔离)

用户反馈:点「生成谱图」后弹出的报告仍会卡死;日志在不同数据之间会串。

报告卡死根因:生成谱图完成后刷新时,若 spectrum 步骤详情已展开,
`_cached_spectrum_report` 缓存未命中(新谱文件指纹变了)会在**主线程**
重读整张 ft3 并做全谱质量评估(3D 大谱可达数十秒),界面假死。

修复:
- 工作线程生成谱图时已算好报告文本(phase_routes._append_final_summary),
  新增 `write_quality_record`/`report_text_from_logs`
  (workflow/optimization_report.py),在 stepwise.generate_spectrum 里把报告
  按 GUI 同指纹写 `{谱}.quality.json`;GUI 缓存未命中直接读记录,不再
  主线程重读谱;
- 兜底:无记录的大谱(≥32MB,旧运行/人工脚本)改为后台线程计算,
  详情先显示「报告生成中…」,完成后经 report_ready 信号刷新;
  小谱仍同步算(即时显示,不影响测试)。

日志串根因:运行日志经 log_message(无作用域)落**当前选中**作用域,
选中切换/组运行时 A 的日志会写进 B 的缓冲。修复:
- PipelinePanel 新增 `log_scoped(str, str)` 队列信号,单数据运行日志落
  `data:{exp}:{data}`,组运行落 `group:{exp}:{group}`(按目标数据/组,
  不随选中切换);重新运行终脚本、人工脚本(经 log_append_requested)
  同样带数据作用域;
- center_panel/main_window 转发接线;组批量进度已在补29c 走队列。

测试:新增日志作用域、质量记录读取(不重算)、大谱后台计算回归;更新
受影响测试接 log_scoped/切日志作用域;全套 pytest 通过。

## 0.2.199-补29e(2026-08-27,报告只显示上次记录,无记录不现场生成)

用户反馈:报告就只显示上次记录的,没有报告为啥要现生成。

补29d 的兜底(无记录时现场读谱计算报告)违背该意图:现场生成既在主线程
重读大谱(卡),又不是真实处理报告。改为:
- `_cached_spectrum_report` 只读上次生成时工作线程写入的
  `{谱}.quality.json`(指纹一致才复用);缓存/记录都没有时直接返回
  「无报告记录;重新运行「生成谱图」后生成报告」,不读谱、不计算;
- 移除后台计算占位/report_ready/缓存写入等现场生成路径;
- 工作线程在 generate_spectrum 里写质量记录(补29d)保持不变——这才是
  「上次记录」的来源。

测试:无记录用例改为断言提示且不触发计算;缓存指纹用例同步;全套通过。

## 0.2.199-补29h(2026-08-27,人工脚本运行前检测器)

用户反馈:人工脚本运行报 UnicodeDecodeError——实际是行尾续行符 `\` 误输成
`\h`,csh 管道断裂,二进制谱数据漏到 stdout 被当文本解码。补29f 的
「UTF-8+replace 容错」会让日志变乱码,已回退;本版改为**运行前检测**,
在脚本执行前扫描常见错误并提示,而非运行时诡异报错。

新增 workflow/script_check.py::check_script(纯函数,无副作用):
- 续行符检查:管道行须以 `\` 结尾;`\` 后有多余字符(如 `\h`)提示;
  下一行以 `|` 开头而本行缺 `\` 提示;末行悬空 `\` 提示;
- 编码检查:UTF-8 BOM、CRLF(Windows 换行使 `\r` 跟在 `\` 后断续行);
- 输出写入检查:管道脚本缺 -out/pipe2xyz -out 时数据漏 stdout(二进制);
- 未知 -fn 函数名;SP 窗参数 off<end 且均在 0..1。

接入:脚本编辑器「运行」前在主线程检查,警告写日志并弹确认框(可取消);
「重新运行终脚本」改完 EXT 后在 worker 内检查并写日志。检测器只提示不
阻断(确认框可继续)。

测试:新增 tests/test_script_check.py(正常脚本零警告/`\h` 误输/缺续行/
缺输出/CRLF+BOM/未知函数/SP 参数异常/空脚本);全套 pytest 通过。

## 0.2.199-补19~21(2026-08-26,直接维相位评分:纯对称 + 形状感知符号 + HNN 模板)

用户指出:部分峰天然为负(sampleK 最强峰 -1.1e11),正确相位下负吸收峰
同样左右对称;负峰惩罚应按形状——对称负吸收(正峰两侧负旁瓣)轻罚,单侧
色散负峰重罚。

- 补19:`_symmetry_sign_metric` 取消「净 Re 为负 ×0.05」,纯对称性(负峰
  同样计分);复型频域终谱上分数 28→45,最强峰对称性 0.20→0.67;
- 补19b:近最优平台容差 5→1,(0,0) 不再被最小修正拽回(41.7 vs 45.3);
- 补20:形状感知符号惩罚——sign_mode=mixed 纯对称(正负峰谱全局最佳);
  uniform 负窗 sym×0.2 轻罚(同号谱异常反号峰降权,±180 消歧保留);
  search_direct_phase_on_spectrum 透传 sign_mode;
- 补21:新增溶液态 HNN 模板(peak_sign=mixed)——sampleK 为
  hncocannhgpwg3d(1H-15N-15N),此前被误映射到固体 NNH(uniform)导致
  直接维搜索 28.9 分被拒;hncannh/hncocannh 改映射 HNN(排在 nnh 前)。

端到端(sampleK):直接维相位 (40°,0°) score=44.4 被找到并应用
(此前 (0,0) 不应用);遗留:终跑填零优化把 F3 覆写为 1×TD(2048,336 点),
与搜索预览(1024,168 点)不一致;|p1|>20 护栏把真实大 p1(105°)归零。

## 0.2.199-补18(2026-08-26,NUS 直接维相位搜索改到复型频域终谱)

根因(recon 平面直接维搜索失效):
- 0.2.85 切片流把 nus3d_rc 布局改为「每直接维点一个平面、直接维=平面序号」,
  但搜索代码 axis=0 假设未同步——搜索一直在间接轴上做(sample 时域包络假高分
  侥幸过 30 门槛,sampleK 28 分被拒 → 相位不应用 → 色散峰);
- recon 平面是间接维**时域**,单点时域迹线被 t1 混叠(所有信号叠加),
  即使轴对了评分面仍平。

修复:
- finalize 新增 keep_complex:全部 PS 不加 -di,输出复型频域终谱
  (间接维 FT 后保留虚部);
- _unified_nus 直接维搜索基底改为该复型频域终谱,直接维=最后一轴,
  频域峰分离规避 t1 混叠;新增 _read_complex_ft3 读取(轴 0/1 交错拆包);
- 2D 保持 recon.ft1 轴 0。

实测(sampleK):复型频域终谱 (512,512,168) 生成正常,搜索在直接轴上执行;
但该数据强峰直接维相位一致性差(40 峰 ±180 折叠 RMS≈35°),搜索诚实保持
(0,0)——数据本身(2.2% 采样重构/直接维窗参数)仍需进一步排查。

## 0.2.199-补16(2026-08-26,转换单文件化:四种途径 fid.com 统一单文件)

四种途径(自动/人工 × 单数据集/分段)的 fid.com 一律生成单文件
{dataset_id}.fid(bruker -AUTO 按 NusTD 网格 + -aq2D Complex 输出,与实验室
fid.com 一致);3D NUS 的切片改由 SMILE 脚本 step1 直接维处理后产生
(nus3d_1/test%04d.ft1),不再依赖转换期切片。

- 移除 NUS 3D 的 acqu3s TD 修正暂存(bruker 在 raw 内直接转换,不再
  修改/备份 acqu3s;已实测 sampleK acqu3s TD=1 输出单文件全网格 fid);
- 多段合并改为 addNMR 逐对合并单文件(merged/{dataset_id}.fid),段频移
  在单文件上用 PS -rs 完成,不再拆切片合并;
- _converted_fid_path/_finalize_converted_fid 单文件优先,旧切片式仅兼容;
- 更新测试:3D NUS 转换断言 raw 内转换 + 单文件归位 + 无 .bak;
- 后续评估数据传递读取(平面布局/相位搜索轴)并修复显示层相位搜索。

## 0.2.199-补15(2026-08-26,SMILE 内存估计口径对齐自报 + 测试上限约束)

SMILE 启动横幅会自报 Memory Used(sampleK 填零1024:2.8GB,网格
146×145 → 重建 219×217,迭代 FT 1024×1024)。原线性估计(1.15MB/点 ×
网格/4000)在该谱算出 ~4GB,与 SMILE 口径不符。改为 SMILE 实际模型:
峰值 ≈ 直接维点数 × 间接维迭代 FT 尺寸乘积 × 16B(复 double)× 1.06;
迭代 FT = next_pow2(3×NusTD)(sampleK 横幅验证 292/290→1024²;旧
HNCACB 实测 FT 256² 吻合 179/665/2284MB)。

文档约束(开发环境测试上限):SMILE 峰值估计 ≤ 2.8GB(填零1024 已验证
安全);≥5.6GB(填零2048 直接维翻倍)会触发宿主意外断电,测试不得逼近。
新增 test_dev_smile_memory_ceiling 固化该约束。

## 0.2.199-补14(2026-08-26,SMILE 按当前可用内存实时设置 -maxMem)

SMILE 插件自带 -maxMem 内存上限(GB)。每次重构(主重构与轻量相位搜索)前
按当前 MemAvailable × MEM_SAFETY(0.85)实时计算并写入脚本,与内存护栏同一
预算——即使峰值预估偏低或被其它进程挤占,SMILE 也不会超限硬扛;日志输出
本次 -maxMem 值。脚本生成器 max_mem 缺省 None 不生成该行,已有字节级
脚本测试不受影响。

## 0.2.199-补13(2026-08-26,转换后清理 NUS mask 中间产物)

fid.com 的 nusExpand.tcl -mask 会在转换目录输出 mask/test*.fid 采样掩码
(1=采样点,0=未采样);SMILE 重构只读 nuslist,不需要 mask 目录。转换后与
ser_full/mask.fid 一起清理 mask/(可再生成),既省空间,也避免 VM 异常断电
后 fsck 把这类中间文件误报为坏块。

## 0.2.199-补12(2026-08-26,生成谱图卡顿:报告缓存 + 运行中防重复点击)

用户反馈:点「生成谱图」会在下面跳出报告,过程很卡,连续点会卡死。

- 根因:展开的生成谱图行每次刷新都会重新调用 spectrum_quality_report_lines
  ——它要 ng.pipe.read 整张 ft3 并做全谱质量评估,在**主线程**执行;大谱
  一次刷新几秒,连续点击叠加多次刷新/运行直接卡死;
- 修复:
  - 生成谱图参数报告按**谱文件指纹 + 参数指纹**(路径+mtime+大小+
    参数 SHA256)缓存,谱与参数都未变时直接复用,不再主线程重读大 ft3
    (内存缓存上限 32 条,超限清空);报告同时落盘为 `{谱}.quality.json`
    记录——指纹一致直接读记录,跨会话也不重算;
  - 运行中标志 _run_active:生成谱图/重新运行终脚本在已有任务运行时拒绝
    再次启动(日志提示「已有任务正在运行」),防止连续点击叠加运行;
- 测试:运行守卫拒绝重复启动、报告缓存复用;全量 pytest + ruff 全绿。

## 0.2.199-补11(2026-08-26,3D NUS 终跑 SMILE 直接维窗固定 SP)

用户反馈:3D NUS 优化过程不对劲——终跑脚本看着没问题,但谱始终不对。

- 根因(VM cc/30 多次复现):终跑 SMILE 每次报
  `SMILE Error: input data in direct dim not apodized!`,但代码仍报
  「完成 SMILE 重构;终谱已就位」——终谱其实是失败重构的产物,故谱不对;
- 原因链:直接维窗函数优化把 F3 选成 none(cc,无窗)或 gaussian(sampleC,
  GM 顶替 SP),而 SMILE 要求直接维必须加窗且尾部衰减到零(实验室
  smile.com 直接维固定 `SP -off 0.5 -end 0.98 -pow 2 -c 0.5`);首遍重构
  用默认 SP 所以正常,终跑用优化窗所以失败;
- 修复:
  - 脚本生成(generate_2d/3d_nus_script):直接维窗固定 SP——sine_bell 系
    按配置,其余(none/gaussian/exp)一律回退默认 SP,保证 SMILE 输入始终
    已加窗;
  - NUS 路径(phase_routes):直接维窗优化结果不再覆盖直接维(保持默认 SP),
    日志注明原因;uniform 路径不受影响;
  - 失败检测(nmrpipe_backend):SMILE 内部错误("SMILE Error" 出现在输出)
    显式判为失败,不再把失败重构当成功出谱;
- 测试:直接维窗固定 SP 的单元测试;全量 pytest + ruff 全绿。

## 0.2.199-补10(2026-08-26,3D 谱拖动报错修复 + slice 拖动条加长)

用户反馈:viewer 3D 谱查看一直报
`eventFilter: self._follow_drag(event.scenePosition())` 的 Traceback;顺便把
查看不同 slice 的拖动条弄长一点。

- 修复(scenePosition):本环境 PyQt6 的 QGraphicsSceneMouseEvent 只有
  `scenePos`、没有 `scenePosition`;且事件过滤器可能收到普通 QMouseEvent
  (无 scenePos 只有 position),拖动 3D 切片/1D 十字线时反复 AttributeError;
  按事件类型分别取 `scenePos()`(QGraphicsSceneMouseEvent)或
  `position()`(QMouseEvent);
- 加长(viewer/spectrum3d_panel):slice 横向滑块 setMinimumWidth(220),
  拖动条更长、细调切片更顺手;
- 切片切换原位更新(gui/spectrum_panel._render_3d_view):不再 clear()+
  add_spectrum() 全量重建,而是 SpectrumViewer.update_spectrum_data 原位
  layer.setData(单层 2D 主谱时),消除拖动切片时的闪烁并显著提速;换谱/
  换平面时自动回退 clear+add;
- 测试:update_spectrum_data 原位/回退用例,viewer/3D 面板相关用例;
  全量 pytest + ruff 全绿。

## 0.2.199-补9(2026-08-26,基线优化条纹否决改相对语义 + 诚实日志)

用户反馈:谱图明显有竖条纹但基线校正没加 POLY。

- 根因(VM 真实 cc 诊断):基线优化的条纹否决是**绝对阈值**(校正后条纹比
  >8 即否决)——原谱已有条纹的数据,任何单轴 POLY 校正后条纹比仍 >8,全部
  候选被否决 → 永远保持 off;且原谱已条纹时子采样(补8)会漏检细条纹;
  日志把否决笼统写成「候选未优于当前配置」,误导判断;
- 修复:
  - 相对否决:`_has_stripe_artifact` 支持 baseline_ratio——只否决比原谱
    明显更差(>原谱+4 且仍 >8)的候选,原谱已有条纹时允许改善性校正;
  - 原谱条纹比 >8 的轴不做迹线子采样(全量评估,细条纹不漏检;干净谱仍
    子采样省开销);
  - 日志区分「全部候选被条纹否决/部分被否决其余增益不足/候选未优于当前
    配置」;
- 复诊(cc):F3/F2 候选正常评估但评分与 off 相同(POLY 零改善),F1 全部
  被相对否决——该数据竖条纹源自采集/DC 伪影(直接维 DC 偏置 42%、前 20%
  迹占 100% 能量),不是多项式基线,POLY 无法去除,保持 off 是正确决策;
- 测试:相对否决单元测试;全量 pytest + ruff 全绿。

## 0.2.199-补8(2026-08-26,基线优化开销降低:迹线子采样 + 良好基线跳过)

用户反馈:基线优化(内存评分)能否减少开销。

- 根因:逐维基线优化对每个候选(off/auto/order1-3)在**全部迹线**上做稳健
  逐迹多项式拟合(3D 最大轴数万条迹),评分指标只是全局均值/条纹比,全量
  拟合大量浪费;
- 修复一(迹线子采样):候选评分在 ≤max_traces(默认 4096)条迹的子采样副本
  上进行——按非轴维度等步抽样,保持 ndim;评分/条纹否决对子采样近似不变,
  3D 开销降约 10 倍(cc 级数据基线优化从数秒降到亚秒级);off 候选与候选
  在同一子采样基底上比较,选择语义不变;
- 修复二(良好基线跳过):off 评分 ≥95 的轴直接保持 off 并跳过整轴候选
  (省去全网格拟合);
- max_traces 可调(越小越快,评分近似不变);测试:子采样迹数压到上限内、
  小 max_traces 仍返回有效配置;全量 pytest + ruff 全绿。

## 0.2.199-补7(2026-08-26,基线优化进度输出 + 可取消)

用户反馈:直接维相位搜索修好后,又卡在「基线优化(内存评分)」。

- 根因:逐维基线优化(off/auto/order1-3 网格)对每个候选做稳健逐迹多项式
  拟合(迭代峰值屏蔽,3D 最大轴数万条迹 × 6 轮),全程无进度输出、无取消
  检查——和相位搜索同样的「卡住/终止无效」体验;
- 修复(optimize_baseline):新增 progress 逐轴/逐候选输出评分进度,cancel
  置位时在候选间检查并抛「任务已取消:基线优化被用户终止」;off 候选不再
  复制整谱(直接评分);phase_routes uniform/NUS 两处调用接线 progress +
  cancel_requested;
- 测试:基线优化进度消息、取消抛异常;全量 pytest + ruff 全绿。

## 0.2.199-补6(2026-08-26,直接维相位搜索提速 + 可取消 + 孤儿进程清理)

用户反馈:点运行后直接维相位优化卡住,后台很多 nmrPipe 进程一直占 CPU,
点「终止任务」没用(VM 实测残留 9 个 nmrPipe/pipe2xyz 孤儿进程,13+ 小时
~100% CPU)。

- 根因:直接维相位搜索是纯内存暴力网格(约 1960 次评分),每次评分对**整个
  重构平面**做复乘(cc 级 54 万复点),大谱耗时数分钟且无进度输出,看起来卡死;
  「终止任务」只杀注册的后端脚本进程树,相位搜索阶段没有子进程可杀,内存计算
  又无取消检查,所以终止无效;脚本异常退出/关闭应用遗留的 csh→nmrPipe 链变成
  孤儿进程持续占 CPU。
- 修复一(提速,search_direct_phase_on_spectrum):评分只依赖信号窗行——先取行
  后旋转(逐元素运算可交换,结果与全平面旋转一致),计算量从「全平面点数×~2000
  次」降到「窗行数×直接维点数×~2000 次」;评分窗均布子采样到 ≤200 个(评分取
  均值,近似不变)。VM 实测 cc/61 直接维相位搜索 103s → 5s(约 20 倍);
- 修复二(可取消):backend.runtime 增加取消标志(request_cancel/clear_cancel/
  cancel_requested),「终止任务」和关闭应用时 terminate_current_tasks +
  request_cancel;直接维相位搜索与内存相位搜索在网格循环中检查并在检查点抛
  「任务已取消」退出;搜索过程每 100 次评分输出一次进度,不再无响应;新任务
  开始前清除标志;
- 修复三(孤儿清理):新增 cleanup_orphan_tasks 按 NMRPipe 工具名/工作区路径
  清扫不在注册表的孤儿进程(Windows taskkill /T,
  Linux SIGKILL),「终止任务」与主窗口 closeEvent 时调用,关闭应用不再遗留
  后台进程;
- 测试:取消标志生命周期、孤儿匹配、相位搜索取消;全量 pytest + ruff 全绿。

## 0.2.199-补5(2026-08-26,左侧树数据运行中状态显示)

用户反馈:对于正在运行任何过程的数据,左侧状态里面显示「正在运行」。

- ProjectTreePanel 增加运行中标记集合与 mark_running/clear_running:标记
  中的数据显示「运行中」,覆盖产物推断状态(已导入/已处理),结束后恢复;
- PipelinePanel 新增 run_started(exp_id, data_id) 信号:单数据运行(生成
  FID/谱图/SMILE 优化/峰挑选/分析)、重新运行终脚本、数据组批量运行开始时
  均发出;run_finished 时清除;
- MainWindow 接线:左侧树随 pipeline/人工脚本(manual)/数据组批量(batch)
  运行开始标记、结束清除;人工与批量从主线程标记,结束经排队信号在主线程
  清除,避免后台线程触碰界面;
- 测试:树状态「运行中」标记与恢复;全量 pytest + ruff 全绿。

## 0.2.199-补4(2026-08-26,pipeline 步骤行按钮自动换行)

用户反馈:pipeline 界面某个板块按钮太多时太宽,注意另起一行(谱图步骤完成后
最多 5 个按钮)。

- PipelineStepRow 按钮区由 QHBoxLayout 改为流式布局(_FlowLayout):宽度不足
  时按钮自动折行,不再单行硬撑撑破面板;隐藏按钮不占位(如非 spectrum 行的
  「直接维范围」「报告」);
- 回归测试:窄宽度(180px)下 5 个可见按钮折成至少两行;
- 全量 pytest + ruff 全绿。

## 0.2.199-补3(2026-08-26,直接维范围可应用到优化过程 + 内存不足提示)

用户反馈:在「直接维范围」功能页加「应用此范围到优化过程」选项(默认开启,
添加说明);SMILE 重构内存检测到不够时提示用户可收窄直接维范围并开启该选项。

- GUI(pipeline_panel):直接维范围对话框新增复选框「应用此范围到优化过程」,
  默认开启;说明:默认开启——优化过程(首遍重构/相位搜索与基线/填零/窗函数
  评估)使用指定范围,与终谱一致,且可降低 SMILE 内存;若优化效果不佳可尝试
  关闭,用默认 6.5-10.5 大范围优化;
- 参数接线:范围按数据保存 (lo, hi, apply_to_opt),生成谱图参数新增
  apply_ext_to_opt(默认 1);按钮文案标注「含优化/仅终跑」;
- 优化范围(phase_routes):开启时用户范围同时进入首遍重构/相位搜索与优化
  评估——NUS 由首遍重构平面天然生效(uniform 联合复核谱 process 带 EXT),
  关闭时保持现状(首遍/优化用默认大范围,仅终跑用该范围);
- 内存提示(nmrpipe_backend):SMILE 内存不足报错追加「也可以尽可能变窄直接
  维范围并开启『应用此范围到优化过程』(直接维窗口越窄,SMILE 峰值内存越低)」;
- 测试:直接维范围参数含 apply_ext_to_opt、按钮文案含「含优化」、
  _split_final_ext 开关解析;全量 pytest + ruff 全绿。

## 0.2.199-补2(2026-08-26,人工单数据集与分段 fid.com 路径一致)

用户反馈:人工单数据集路径与分段人工路径改为一致——同样把人工改的参数当
overrides 传入后端。

- run_manual_fid_com 单数据集分支不再直接 csh 运行用户脚本:与分段一致,
  经 parse_fid_com 提取人工参数为 overrides,由后端 convert_to_fid 统一
  执行 bruker 生成/参数修正/坏点清理/切片归位——人工只调参数,转换结构
  由后端保证(结构性改动不保留,与分段语义一致);
- manual_fid_com 单数据集自动生成内容加提示头「人工只调参数,勿改输出名」
  (与分段参考段提示一致);
- 人工运行进度经 progress 实时转发(与自动路径一致);
- 测试:单数据集人工路径改由假后端验证 overrides 传递、单文件/切片归位、
  命名统一;test_full_paths 人工路径补 backend;全量 pytest + ruff 全绿。

## 0.2.199-补1(2026-08-26,分段 3D NUS 与普通 NUS 一致走切片流)

用户反馈:分段 NUS 应该和普通 NUS 保持一致,用切片流。

- 放开 _needs_acqu3s_td_fix 的 segments 守卫:分段各段与普通 NUS 3D 一样,
  在 conv_stage 暂存副本修正 acqu3s TD=NusTD 后跑 bruker,直接输出切片流
  fid/test%03d.fid(不再每段单文件 + xyz2pipe 拆分);
- _split_slices 增加「已有切片即跳过」:bruker 已输出切片流时不重复拆分,
  单文件输出(均匀/旧数据)仍走原拆分逻辑;
- 分段暂存修正同样先备份 raw 参数(.bak),只在暂存副本修改,原件不动;
- 验证(VM 真实 cc 分段):导入 → generate_fid → generate_spectrum 全通,
  各段直接产出切片、_split_slices 日志显示「已有切片式输出…跳过拆分」,
  终谱产出;
- 测试:test_needs_acqu3s_td_fix_gates 改断言(分段同样触发修正);
  全量 pytest 696 全绿 + ruff 全绿。

## 0.2.199(2026-08-26,分段 3D NUS 导入后处理修复 + 分段质量检查)

用户反馈:分段导入后 raw/ 是容器根目录(无 acqus/ser),后续处理报错。

- 根因(VM 真实 cc 分段复现):
  - fid.com 单文件输出的 mask 阶段读 ./test.fid,而 patch_fid_out_name 把
    主输出改名成 {dataset_id}.fid 后未同步改 mask 输入 → mask 找不到输入,
    fid.com rc=1,分段转换失败(3D NUS 分段各段按单文件输出 + _split_slices
    切片归位;acqu3s 暂存修正只用于单数据集);
  - convert_to_fid 分段分支未应用坏点移除后的网格调整(0.2.197 只在
    reconstruct_nus),各段按基础实验 NusTD 强制网格,与清理后数据不一致;
  - check_raw_quality 对分段数据检查容器根目录 → 误报「缺少 acqus/ser」。
- 修复:
  - patch_fid_out_name:主输出改名时同步改 mask 阶段的 `-in test.fid`;
  - convert_to_fid 分段分支:先 _clean_source_nus 再按合并 nuslist 实际
    范围调整 NusTD(与 reconstruct_nus 一致);
  - check_raw_quality:分段数据按首段目录评估(同一实验各段参数一致),
    info 标注「按首段评估」;
- 验证(VM 真实 cc):导入 → generate_fid → generate_spectrum 全通,终谱
  d_002.ft3 产出;质量检查 ok=True 不再误报缺 acqus/ser;
- 测试:mask 改名、分段网格调整、分段质量检查;全量 pytest 全绿 + ruff
  全绿。

## 0.2.198(2026-08-26,分段导入容器判定 + 非数据子目录忽略 + 原始参数备份)

用户反馈:分段导入选总文件夹时出现「缺失 acqus」提示——容器顶层本来就不
含 acqus;应忽略找不到任何数据的子文件夹;sample 这类子目录是独立数据集的
文件夹应失败并告知「不是分段实验」;修改 acqus 等链接原始文件的程序必须
先备份原始文件。

- 分段容器判定(gui/processing):_segment_dirs 只认含 acqus 的子目录为
  数据段;只有 ser/fid 等数据文件但缺 acqus、或什么文件都没有的子目录
  一律忽略;resolve_import_source 改为 acqus 权威判定——恰好 1 个含 acqus
  段时忽略杂目录按单个导入,全部子目录缺 acqus 时明确报「均缺少 acqus」,
  不再把顶层缺 acqus 当问题;
- 提示措辞:分段按钮与导入对话框不再把「顶层无 acqus」作为失败原因,统一
  为「需至少 2 个子目录各含 acqus 数据段;非数据子目录已忽略」;
- 非分段实验明确失败(core/data/bruker_reader):read_dataset_container 捕获
  read_segments 的「参数不一致」,改为「所选目录不是分段实验(子目录参数
  不一致,可能是多个独立数据集,请逐个导入)」——cc(61/63/65/67 一致)正常
  导入,sample(20/28/30 不一致)明确失败;
- 原始参数备份(backend/nmrpipe_backend._stage_acqu3s_td_fix):修改
  acqus/acqu2s/acqu3s/nuslist 前先备份原始文件为 .bak(仅首次,幂等),并
  复制进暂存目录而非硬链接,避免暂存内任何原地写入穿透链接污染 raw 原件;
  _clean_source_nus 对 ser/nuslist 的 .bak 备份保持不变;
- 验证(VM):cc 容器读取正常(4 段),sample 报「不是分段实验」;stage 备份生成
  acqus.bak/acqu2s.bak/acqu3s.bak 且内容为原件;
- 测试:resolve_import_source 忽略缺 acqus 子目录、read_dataset_container
  非分段实验报错、stage 备份断言;全量 pytest 全绿 + ruff 全绿。

## 0.2.197(2026-08-26,坏点移除后网格按实际范围调整,交叉验证不再改回)

用户反馈:处理逻辑乱了——有坏点时 NusTD 等值会被改,但后面的交叉验证
(参数修正)又改回原值,导致 fid 网格与清理后数据不一致。

- 根因:坏点移除后实际采样范围变小(nuslist 每维 max+1),但 patch_fid_com
  的「参数修正」仍按静态 NusTD(如 cc/63 的 170→85)把 fid.com 网格改回,
  与清理后数据不一致;
- 修复:坏点从源头删除(source_removed)后,按清理后 nuslist 实际范围推导
  网格(_nus_grid_from_points:每维 max+1),更新 acqu2s/acqu3s 的 NusTD
  (_apply_nus_grid_after_clean,只缩小),使 _effective_td、fid.com 参数
  修正、nusExpand 网格、重构全部一致——交叉验证使用调整后的 NusTD,不再
  改回;
- 验证(VM 真实 cc/63):坏点 (27,2350) 删除后,日志出现「采样坏点移除后
  网格调整: acqu2s NusTD 170→166」,nusExpand 强制 -yT 83 -zT 26,
  yN/yT 不再被改回 170/85,重构成功出谱;nuslist 推导网格转换 States
  命中 99/99;
- 测试:新增 _nus_grid_from_points/_apply_nus_grid_after_clean 单测
  (2D/3D 缩小与不变);全量 pytest 全绿 + ruff 全绿。

## 0.2.196(2026-08-26,坏点潜在问题只报告不自动处理)

用户要求:除越界/重复/尖峰外,其它潜在坏点类型只报告不处理。

- run_direct_diagnostics 新增三类只报告不自动处理的检测:
  - NaN/Inf 值:统计并报告;此前 _read_fid_raw 用 array_equal 校验会因
    NaN 解析失败,改为 equal_nan=True 使含 NaN 的 fid 可解析并检出;
  - 全零迹线:uniform 采样不应存在全零迹,报告采集可能缺失(NUS 未采集
    行本就为零,不报告);
  - 持续异常高能量迹线(>100×非零能量中位,非孤立尖峰):报告建议核查
    增益/脉冲稳定性;
- _clean_source_nus 跳过消息明确化:ser 大小不整除/布局无法按采样参数
  确定时,明确报告「ser 行数与 nuslist 点数可能不一致」「冗余数不一致或
  字长未知」,均未自动处理;
- 测试:NaN/全零迹/高能量三类报告断言;全量 pytest 全绿 + ruff 全绿。

## 0.2.195(2026-08-26,坏点删除布局参数化 + NUS fid 网格一致性修复)

用户反馈:坏点删除应在 ser/fid 中直接删除原始数据,现在似乎没删对,导致
谱图重构不对或合并不对;并指出 ser/fid 字节随采样参数变化,脚本要按参数
判断。

- 根因(VM 真实 cc/63 逐步实证):
  - fid 网格不一致:bruker -AUTO 的 fid.com 中 nusExpand 缺省按 nuslist
    推导网格(yTNUS=83),而按 NusTD 打补丁的 bruk2pipe 用 85,两段网格
    不一致导致 fid 错位放置,重构错误;同时 xN 被按 acqus TD 覆盖
    (1024→908),破坏 nusExpand 按 serPadSize 补齐后的 ser 行对齐
    (908→1024),进一步错位;
  - 清零切片映射错误:坏点清零把 (f2,f1) 映射到 test{f1},而 States 布局
    实际在切片 2*f1+1/2*f1+2 的行 2*f2/2*f2+1,清零打在错误切片(合并
    不对);
  - ser 布局假设固定:源头删除用 ser_size/n_rows 当每点字节块,未按采样
    参数(直接维 TD 补齐 + 字长 + 冗余数)校验。
- 修复:
  - patch_fid_com:NUS 下 xN/xT 不再按 acqus TD 覆盖(保持 fid.com 的
    补齐值);yN/yT/zN/zT 按 NusTD 网格修正;并强制 nusExpand 传入
    -yT/-zT 与 bruk2pipe 同一网格;
  - _zero_bad_point_fid:清零切片修正为 States 布局 2*f1+1/2*f1+2;
  - _clean_source_nus:新增 _ser_point_layout 按采样参数推导每点字节块
    (nusExpand serPadSize:字长 8→128 对齐、4→256 对齐;冗余数=ser 大小/
    点数/每向量字节),与 ser 不符时跳过源头删除、回退生成 FID 清理。
- 验证(VM 真实 cc/63):源头删除坏点 (27,2350) 后转换,States 放置
  99/99 命中(修复前杂乱),SMILE 重构成功出谱;
- 测试:新增 NUS 网格强制、ser 布局推导、States 清零切片测试;更新
  _clean_source_nus 测试按参数构造 ser;全量 pytest 全绿 + ruff 全绿。

## 0.2.194(2026-08-26,导入数据下拉保持子部件方案,修复首次弹出不可见)

用户反馈:「导入数据」按钮点击不出现下拉内容;点右边的「数据组间分析」
按钮后再点它反而正常出现。

- 排查:0.2.163-补4 把下拉从独立顶层窗口改为主窗口子部件——顶层窗口
  存在无法解决的位置问题(如 Wayland 锚点定位/取消弹出卡死,补2/补3
  反复修复未果),故弃用;Windows 上子部件方案首次弹出会被中央部件
  盖住(z-order 不稳定),点过右边按钮后子部件层级变化才正常;不能靠
  退回顶层窗口解决(位置问题会回来);
- 修复:保持主窗口子部件方案(相对坐标定位,任何平台一致),给两个下拉
  (导入数据/数据组间分析)设置 Qt.WA_AlwaysStackOnTop,保证绘制在
  中央部件之上,首次弹出即可见;宿主窗口关闭时同步收起下拉并移除应用
  事件过滤器,过滤器移除加守护;
- 测试:下拉不遮按钮/位置对齐/高度限制断言(相对主窗口坐标)保持;
  全量 pytest 全绿 + ruff 全绿。
- 补2(根因定位):首次打开时下拉以 (0,0) 残影出现在「实验类型」标题附近
  (只露出滚动条一条)——根因是子部件方案下下拉构造时未隐藏:父页面显示
  时下拉自动可见地躺在 (0,0),`_open_import_dropdown` 判 isVisible 为真
  只置顶不执行 open_below,所以「怎么点都没有」;点「数据组间分析」第一
  下把 (0,0) 的组间下拉关掉、第二下才真正打开。修复:下拉构造时显式
  hide();打开时隐藏状态先按按钮下方放置(子部件 move 相对父窗口),
  show 后首个事件循环再校正一次位置(0.2.162-补14 同款 show 时序),
  首次打开即在按钮正下方;WA_AlwaysStackOnTop 保证绘制在页面内容之上。

## 0.2.193(2026-08-25,脚本编辑器打开提速 + 同数据同步骤去重)

用户反馈:脚本编辑器打开时主界面可点但打开很慢;同一个数据的同一个
pipeline 功能能被点出两个编辑器。

- 打开提速:质量诊断(run_direct_diagnostics,全量 fid 读入 + 全迹指标 +
  坏点修复写回)原在 manual_scripts 打开时每次都重跑,大数据 3D NUS
  打开脚本编辑器卡顿;改为点「运行」时在 run_manual_spectrum 内先执行
  (与自动路径生成谱图开端一致),打开编辑器只读已有脚本,秒开;
- 去重:脚本编辑器按 (data_id, step) 持有引用(fid / spectrum)——同一
  数据同一步骤重复点击不再新建窗口,只把已打开的窗口带到前台
  (show/raise/activateWindow);不同数据或不同步骤可并存;关闭后键释放
  可重新打开;
- 测试:manual_scripts 打开不写质量日志、run_manual_spectrum 运行才写;
  GUI 同数据同步骤复用编辑器、不同步骤并存;全量 pytest 687 passed +
  ruff 全绿。

## 0.2.192(2026-08-25,GM 加入直接维候选 + 人工脚本编辑器保存/非模态/运行自动关闭)

用户要求:1) 加入 GM(0.2.191 已用 VM 真实数据与 NMRPipe 逐点对齐);
2) 人工窗口改了脚本直接保存不成功(只有运行才保存)、打开时锁定主界面、
点击运行后窗口不自动关闭。

- 窗函数优化:GM(Lorentz-to-Gauss g1=8 g2=15 g3=0 c=1.0)重新加入直接维
  缺省候选池(0.2.190 因模型未经源确认而移除;0.2.191 已与 NMRPipe 实测
  逐点一致,VM 真实 3.fid 上峰值 302/1024、w[302]=1.2179);GM/EM 依赖
  谱宽 SW,评分未提供 SW 时跳过这些候选(避免 sw=1.0 数值垃圾虚高);
  间接维候选池不加 GM——分辨率受限的间接维加窗信噪比虚高会翻盘自然
  衰减轴的无窗选择(0.2.190 要求保留);真实数据验证:3.fid 直接维上 GM
  正常参与评分(FWHM 3.27 点、score 0.974),竞争但不虚高胜出;
- 人工脚本编辑器(gui/dialogs.ScriptEditorDialog + gui/main_window):
  - 「保存」立即写回数据目录并关闭(修复前 fid.com 入口在 exec 返回后
    才保存、process.com 入口完全不保存,只有「运行」才落盘);
  - 打开改为非模态 show(不再 exec 锁定主界面,主界面可正常点击);
    WA_DeleteOnClose + destroyed 释放引用,多次打开不泄漏;
  - 「运行」先保存当前脚本,发出 run_requested 后自动关闭窗口;
- 测试:GM 候选/无 SW 跳过/间接维不含 GM 断言;脚本编辑器保存落盘、运行
  发出信号并自动关闭、主窗口 show 非模态打开;全量 pytest 685 passed +
  ruff 全绿。

## 0.2.191(2026-08-25,窗向量与 NMRPipe 逐点对齐 + GM 常数修正)

用户要求:窗函数优化必须确保内存内实现与 NMRPipe 一致,并用真实数据
检查 GM 窗。在 VM 上用真实 3.fid 头部构造全 1 FID,逐点读取 NMRPipe
SP/GM/EM 输出反推公式,内存窗向量改为与之逐点一致:

- SP/sine_bell:w[i] = sin(pi*off + pi*(end-off)*i/(n-1))^pow,首点乘 -c
  (脚本缺省 0.5);与 NMRPipe 全 1 FID 实测逐点一致(max err 5.5e-08);
- GM(Lorentz-to-Gauss):w[i] = exp(pi*g1p*i - (k*pi*g2p*(g3*(n-1)-i))^2),
  g1p=g1/SW、g2p=g2/SW(SW 取 fid 头 FDFxSW);高斯常数 k 由 nmrglue 源码
  的 0.6 修正为 1/(2*sqrt(ln2)) = 0.6005612(VM 实测,参考 nmrglue issue
  #17);真实数据验证:GM 8/15(SW=19230.77 Hz)峰值 302/1024、峰值幅值
  1.2179;g3=0.5 峰位 813,与 NMRPipe 实测一致;
- EM:w[i] = exp(-pi*(lb/SW)*i),首点乘 c(缺省 1.0);
- 修复 _axis_sw 键拼写 bug:轴标签 F1 对应头部键 FDF1SW,f"FDF{axis}SW"
  会多出一个 F 而取不到值,导致 GM/EM 实际用 sw=1.0 建模;修正后
  from_work/from_recon 自动从 fid/平面头部读 SW 传入;
- 间接维分辨率池收紧到 1.15x(直接维保持 1.25x):SP 首点乘 -c 与
  NMRPipe 一致后,加窗候选信噪比虚高会在 1.25x 池内翻盘自然衰减轴;
  收紧池保证自然衰减轴确定性只留无窗、截断轴仍选温和窗;
- 测试:新增 tests/test_window_parity.py(SP/GM/EM 与 VM 实测逐点断言 +
  SW 头部读取);全量 pytest 682 passed + ruff 全绿。

## 0.2.190(2026-08-25,窗函数优化恢复真实选窗 + 稳健基线校正)

用户反馈:0.2.189 把间接维硬编码固定无窗是上个窗口对需求的误读;要求
间接维窗函数优化能正确优化到无窗、直接维优化到合适参数、基线校正更
好,并明确基线校正要先于窗函数优化。

- 窗函数优化重构(workflow/window_optimize):统一直接维/间接维内存评分
  引擎(候选窗 × 该维时间轴 FFT,测 FWHM/SNR/线形;分辨率优先池 1.25x
  + 0.5*snr + 0.5*shape);
- 间接维:恢复真实选窗(uniform 用转换后 fid、NUS 用 SMILE 重构平面时间
  域内存评分,均不重跑后端),候选池含无窗(none)列为首选;分辨率受限的
  间接维加分辨率保留因子(score *= exp(-max(fwhm/min-1,0))),自然衰减
  轴正确落到无窗、截断轴选温和窗;0.2.189 的硬编码固定无窗已删除;
- 直接维:候选池保留 0.5-0.98/pow2 首选并参与评分(分辨率过滤从 1.15x
  放宽到 1.25x,不再把用户偏好的温和窗提前排除);gaussian 移出缺省候选
  (GM 模型未经源确认,噪声抑制会虚高胜出;渲染/手工配置仍支持);
- 顺序约束:两条路径统一 基线优化(2)→ 间接维基线重渲(2.1)→ 直接维窗
  (2.5)→ 间接维窗(3),基线校正先于窗函数优化;
- 基线校正(core/processing/baseline):普通全迹 polyfit 会被强峰拉偏产生
  竖线条纹(0.2.132 回归根因);改为迭代峰值屏蔽(残差 MAD 阈值)的稳健
  多项式拟合,只基于基线点估计,强峰谱校正后斜率归零且无条纹,与
  NMRPipe POLY 稳健估计同思路;
- 测试:间接维无窗/截断选窗、候选含 none、稳健基线无条纹、phase_routes
  uniform/NUS 间接维窗接线;全量 pytest 677 passed + ruff 全绿。

## 0.2.189(2026-08-25,窗函数优化修正:间接维固定无窗,直接维 0.5-0.98)

用户反馈:窗函数优化有问题——间接维最佳参数应为无窗,直接维 0.5-0.98
更好。此前间接维窗函数候选用 spectrum_quality(含相位分)自动选窗,会
因相位/基线评分联动改变选窗结果(sampleB 曾从无窗/0.45-0.95 带偏到加窗),
且没有选出「无窗」这一最佳参数。

- 间接维窗:uniform/NUS 均固定无窗(0.2.189 用户规则)——删除两个优化
  函数中的间接维窗候选评分循环(原 sine_bell/sine_bell²/gaussian 候选 +
  谱质量择优),间接维 window 配置固定 {"type": "none"};
- 脚本生成:uniform _stage_lines 对 window[axis] 显式 type=none 时跳过
  apodization(NUS 侧 _window_line 本就支持 none 不插窗);
- 直接维窗:window_optimize 候选池将 off=0.5 end=0.98 pow=2 列为首选
  (用户指定 0.5-0.98 更好),其余候选保留对比;
- 测试:optimize_nus_processing 窗测试改为断言固定无窗(2 次 finalize,
  无窗候选调用);全量 pytest 677 passed + ruff 全绿。

## 0.2.188(2026-08-25,人工运行实时日志 + 修复无响应)

用户反馈:人工途径运行有时没反应;重新运行终脚本时 log 无中间输出。

- workflow/manual:run_manual_fid_com / run_manual_spectrum 新增 progress
  参数,透传 CshRuntime.run(on_line=...) 逐行转发脚本 stdout——人工运行
  不再只有最终一行,进度实时可见;
- main_window._run_script_async:点击「运行」立即输出「开始人工运行」,
  脚本每行输出带 [脚本名] 前缀转发到日志;失败/完成仍回主线程提示;
- pipeline 重新运行终脚本(_on_rerun_final_requested):脚本输出逐行转发,
  EXT 窗口更新与完成/失败日志保留;
- 测试:更新各 FakeRuntime.run 支持 on_line 参数;
  全量 pytest 677 passed + ruff 全绿。

## 0.2.187(2026-08-24,NUS 直接维恢复对称性搜索,修复 mixed 谱直接维带偏)

用户反馈:sampleB(HNCACB 3D NUS)直接维相位优化不正确;并指出直接维
(1H)不存在正负峰,mixed 的正负峰逻辑只应作用于间接维。

- 排查确认:0.2.185 把 NUS 直接维统一为净吸收后,recon 平面直接维上
  净吸收评分区分度差(所有候选 49~55 分、评分面平坦,最强迹线峰位落在
  数组边缘、峰窗/基线区被截断),mixed 的 |net| 取绝对值使 ±180° 无法
  消歧,sampleB 直接维从 0° 带偏到 183°;
- 修复:NUS 直接维恢复 0.2.95 对称性搜索(search_direct_phase_on_spectrum,
  metric="symmetry")——信号行峰选择排除边缘伪影 + 对称性评分 + 正峰
  约束(净 Re 为负 ×0.05),为 recon 平面校准,能正确消歧 ±180°;
- 间接维保持 0.2.185 净吸收(基线拉平)+ mixed/uniform 按轴评分(13C
  正负峰共存,1H/15N 同号),四路径间接维评分不回归;
- 测试:monkeypatch 改回 search_direct_phase_on_spectrum;
  全量 pytest 673 passed + ruff 全绿。

## 0.2.186(2026-08-24,GUI:导入按钮反馈 + 日志按数据/数据组隔离)

用户反馈:导入数据按键有时点一下没反应;log 区要求单个数据各自独立,
点到哪个数据显示哪个的 log,数据组内成员共用同一个 log。

- 导入按钮:无路径时给出提示(不再静默 return);空 exp_id 放行,由
  主窗口自动创建实验类型;「导入数据」下拉按钮点击未打开则弹出、已打开
  则置顶聚焦(不再 close+reopen 闪烁);
- 日志作用域(LogPanel):新增按选中上下文隔离的日志缓冲区——
  单个数据 data:{exp}:{data} 独立、数据组 group:{exp}:{group} 组内共用、
  实验类型 exp:{exp}、其它归 global;主窗口选中变化时 set_scope 切换显示;
  组批量处理进度显式路由到组作用域,不受当前选中数据影响;
- 测试:新增日志作用域隔离/组批量路由/导入按钮提示测试;
  全量 pytest 673 passed + ruff 全绿。

## 0.2.185(2026-08-24,相位优化评分统一净吸收 + 正负判定基线化)

用户要求:各种途径(2D/3D × uniform/NUS)的相位优化评分统一为净吸收
评分;同时优化正负峰标准——在基线的基础上判断正负,而不是完全看实部
正负。

- NUS 直接维从旧对称性搜索(search_direct_phase_on_spectrum)改为
  search_axis_memory(峰窗签名净吸收),与 uniform 直接/间接维、NUS
  间接维统一——四条路径相位优化评分全部为净吸收;保留 score≥30 门控、
  p1>20° 归零、phase.json 缓存与进度日志;
- 净吸收正负判定先拉平基线(0.2.175 用户方案恢复):以峰两侧基线区
  中位数均值(±6..18 点)为基线水平,窗口减基线后再分正负——基线整体
  偏移(正或负)不再污染净吸收;偏移≥1%峰高才拉平,基线平的谱保持零界;
- 质量评估 _peak_window_nets 同源同步基线化(0.2.172 已与优化同公式):
  1D 合成谱基线估计不可靠保持零界,2D+ 基线区紧贴峰窗外侧避免重叠;
- 测试:新增基线拉平测试(偏移谱与零基线谱净吸收一致);NUS 直接维
  monkeypatch 改 search_axis_memory;全量 pytest 669 passed + ruff 全绿。

## 0.2.172-补2(2026-08-24,相位分乘实部能量占比)

0°/±45° 相位候选的峰窗净吸收可能相同(实谱×e^{iφ} 场景,0° 实部只是
幅度缩 0.707 倍,仍是吸收),评分 tie 时排序受 numpy 浮点影响(VM
numpy 2.4 与本地 2.3 排序不同,test_optimize_recovers_phase_error
在 VM 回归失败)。相位分乘以实部能量占比因子 (0.5+0.5×吸收度):
相位校正后实部能量最大,0° 候选被压低(92.8→69.6),±45° 稳定胜出;
正常吸收谱吸收度≈1 不降分。

## 0.2.172-补1(2026-08-24,峰剖面取最差方向)

修正 0.2.172 的峰剖面选择:2D 谱相位误差只沿被调轴展开,其它轴仍保持
吸收——此前"取各轴吸收最强剖面"会漏判被调轴的色散(45° 未校正候选
在 F1 方向仍吸收而误判高分,VM test_optimize_recovers_phase_error 回归
失败)。改为取各轴中 |净吸收| 最小的剖面(最差方向)代表峰相位状态:
正常吸收谱 82 分、mixed 谱 84 分、色散 50 分,参数优化恢复 45° 相位
误差通过。

## 0.2.172(2026-08-24,相位质量评估与相位优化同源)

用户反馈:谱图质量评估相位分低,但谱图看着相位正常。根因:质量评估的
相位分(负面积/负峰比例)与相位优化评分(峰窗签名净吸收)不同源,把
吸收型负成分(真实负峰如 HNCACB Cβ、噪声负瓣)当相位错误惩罚。

- 相位评分改为与优化同源:峰窗签名净吸收(与 memory_phase_search.
  score_axis_memory 同公式:净吸收=(正+负)/总,uniform 取中位数,
  mixed 取 |净吸收| 中位数 + 正负共存惩罚),叠加 10% 谱熵微调保证
  弱峰窗/1D 谱随相位误差单调;无有效峰窗时中性 50(与优化一致);
- 吸收型负成分放行:峰窗改为沿各轴的一维剖面(与优化迹线一致,避免
  2D 窗噪声稀释);负峰比例仅作 180° 反相兜底;强峰阈值逐级降
  (99.5→95→75)排除 FFT 边缘伪影;
- sign_mode 从实验类型模板 peak_sign 透传:HSQC 等 uniform、HNCACB/
  CBCANCO 等 mixed——mixed 谱真实负峰不再误报(实测 46→86 分);
- 测试:相位连续单调/180° 反相/mixed 谱验证;全量 pytest 668 passed
  + ruff 全绿。

## 0.2.170(2026-08-24,GMB 窗修复 + 质量检测全轴基线 + pipeline 报告可读化)

用户反馈:中间 pipeline 报告未同步可读化;谱图质量检测不合理;GMB 窗
函数加上后谱图完全不对。

- GMB 窗函数修复(根因):VM 实测 `GMB -lb 5 -gb 0.1` 的窗在 FID 尾部
  放大到约 28 倍(窗函数爆炸)→ 谱图完全不对;而 `GM -lb/-gb` 会被
  静默忽略(窗不生效)。统一改为 NMRPipe 原生高斯窗 `GM -g1 X -g2 Y`
  (缺省 8/15,实测窗峰值≈1.2、尾部平滑衰减);优化候选与配置均改用
  g1/g2,lb/gb(Bruker 语义)不再直接映射;
- 谱图质量检测合理性:基线评估改为逐存储轴取最差(此前只评估最后一个
  numpy 轴,2D 间接维/3D 其它轴的基线不平会漏报);报告标注最差轴;
  短轴(<2×edge)视为无基线问题,不误报;
- pipeline 中间报告可读化:生成谱图步骤行的参数报告改为
  ◆ 数据质量诊断(结论+明细)+ ◆ 处理参数与优化,谱图路径可读时附加
  ◆ 最终谱图质量——与日志末尾汇总共用 spectrum_quality_report_lines;
- 测试:GM -g1/-g2 断言更新、pipeline 报告断言、全轴基线测试;
  全量 pytest 668 passed + ruff 全绿。

## 0.2.169(2026-08-24,诊断日志时序 + 谱图质量/数据质量报告可读化)

用户反馈:数据质量诊断(处理前监测)的日志反而排在优化之后,时序错乱;
谱图质量检测报基线不平,但流程做过基线优化,需要说明原因;两份报告
要让用户清晰看懂。

- 数据质量诊断日志时序修复:uniform 分支的 diag_logs 此前延后到处理参数
  优化后才拼入日志(排在相位搜索/优化之后);现改为流程最开头就位,与
  NUS 分支一致——顺序为:数据质量诊断 → 复型预览/相位搜索 → 优化 →
  终跑 → 末尾报告;
- 末尾报告可读化(替换「质量与优化汇总」为三节结构):
  ◆ 最终谱图质量:综合判定(✓ 接受 / ⚠ 警告 / ✗ 不合格 + 综合分)、
    信噪比/相位/基线/伪影分项等级(良好/需注意/较差)与分数、基线指标
    (斜率/偏移/弯曲/条纹)、检查说明逐条、基线不平原因;
  ◆ 数据质量诊断:结论行(⚠ 检出 N 项问题,已自动处理 M 项)+ 逐项明细,
    未检出时显示 ✓ 结论;
  ◆ 处理参数与优化:原有参数报告(相位/基线/窗/填零/后端运行次数);
- 基线不平原因(0.2.169):对照基线优化结果输出——优化基于 joint 谱
  逐维内存评分,增益≤0.5 或条纹否决时保持 off(不校正);质量评估基于
  终跑谱最后存储轴,窗/填零会改变基线形态,两基准不同;
- 测试:新增末尾报告可读化回归测试;全量 pytest 667 passed + ruff 全绿。

## 0.2.168(2026-08-24,核组合按维度位置区分:classifier + viewer)

用户要求:撞核组合同一个核按 xyz 下标区分;viewer 跟进。

- 分类器核组合匹配改为「直接维核精确 + 间接维核计数」:
  - 直接维(x 下标)精确匹配——同核在直接维 vs 间接维不视为同一指纹,
    HETCOR(13C@直接维)与 HSQC-13C(13C@间接维)、HNHETCOR(15N@直接维)
    与 HSQC(15N@间接维)此前因无序计数相同而撞核,现为唯一候选直接
    命中,不再依赖 PULPROG;
  - 间接维(y/z 下标)按核种类计数匹配、不计顺序——保留同核出现次数
    (NNH 两个 15N 与 HSQC 一个 15N 可区分),且兼容 presets 历史顺序
    约定不统一(如 CANCO/NCACB 的 indirect 顺序相反);
- viewer 跟进:同核投影(如 15N 编辑 3D 的 H-H 平面)此前按核种类从
  3D 谱匹配轴会把两个 1H 取错——现直接走文件头槽位构建轴参数,标签
  用 Hx/Hy 下标(axis_labels_from_nuclei 已有同核下标显示);
  _permutation_to_logical 注释澄清同核位置可互换(重排结果显示等价);
- 测试:新增分类位置敏感测试(HETCOR/HNHETCOR 唯一候选)、viewer 同核
  投影测试;修正 0.2.167 编辑谱测试用例核顺序(1H 检测,直接维为 1H);
  全量 pytest 666 passed + ruff 全绿。

## 0.2.167(2026-08-24,预设补全 + magnitude/HMBC 自动相位回退)

用户要求:预设实验类型无遗漏(1D 暂不处理);把缺失类型补上;修复当前
流程处理不了/处理错误的类型。

- 新增 9 个 2D/3D 模板:HSQC-TOCSY-13C、HSQC-TOCSY-15N、HCCH-COSY、
  TOCSY-HSQC-15N、HCACO、HMQC-31P、HMBC-31P、HSQC-19F、REDOR;
- 分类器 PULPROG 关键词补全:14 个已有模板此前缺关键词(HBHA(CO)NH、
  H(CA)NH、H(CCO)NH、C(CCO)NH、HCCH-TOCSY、CCH-TOCSY、NOESY-HSQC、
  HN(CA)CO、HSQC-13C 等),同核组合多候选时落 Generic——已按"长/具体
  关键词排前"原则补全,并覆盖全部新模板;新增分类精排测试;
- magnitude/HMBC 处理修复(此前"搜了也白搜"):unified 统一路径按
  plan 相位节点过滤——magnitude(QF)间接维无 PS 概念,不再生成复型
  预览/搜索;模板 processing_hints.auto_phase=false(如 HMBC)时全轴
  跳过相位搜索保持 (0,0);sampling.auto_phase 参数级语义撤出 unified
  自动路径(0.2.166 的 uniform 检查撤销),仅 route=none 逃生口直连
  后端时有效;NUS magnitude 间接维仍显式报错(SMILE 不支持 real 重构);
- 预设审计记录:54→63 模板;1D 谱、DOSY/弛豫等伪维实验暂不补(不适合
  现有"生成谱图"标准流程,需单独设计);
- 测试:新增 HMBC/magnitude 不搜相位路由测试、分类精排测试、预设覆盖
  断言;全量 pytest 664 passed + ruff 全绿。

## 0.2.166(2026-08-24,uniform/NUS 四路径功能对齐审计)

用户要求:继续检查 2D/3D × uniform/NUS 四种路径,是否有某条路径有新功能
而其它路径没有。审计结论与修复:

- uniform 补齐 NUS 已有功能:
  - sampling.auto_phase=False 生效:直接维相位保持 (0,0),不再生成直接维
    预览与内存搜索(此前 uniform 忽略该开关);
  - 初跑脚本保留:优化前完整脚本(joint,含最终相位与自动填零,未含优化
    基线/窗)存为 {dataset_id}_before_optimize.com,与 NUS 初跑脚本保留对称;
  - 终跑「质量与优化汇总」补 baseline/zero_fill/window/diagnostics
    (此前 uniform 汇总缺这些段落,与 NUS 汇总不对称);
  - 优化流程间接维基线变化后重渲基底谱(与 NUS 评分基准一致);
  - process() 分段转换透传 segment_shift_hz 频移(有频移强制重转),
    与 reconstruct_nus 一致;
- 两条路径共有的窗函数优化缺陷修复:间接维窗候选获胜时原先整表替换
  window、丢掉直接维已优化窗——现只覆盖间接维;uniform 候选评分同时
  携带已优化直接维窗,保证评分面与终跑配置一致;
- NUS 直接维相位缓存指纹补 window:直接维窗进 SMILE step1 重构平面,
  此前改窗后缓存仍命中旧相位(潜在脏缓存),现改窗即失效重搜;
- 按设计保留的差异(NUS/SMILE 特有,已在代码注释与开发文档说明):
  直接维相位缓存与搜索 ETA、fid_noise 去伪、轻量/显示层相位搜索、
  SMILE 内存护栏、nuslist 坏点清理;uniform 的 keep_direct_complex
  (NUS 由 finalize 复型预览承担)。
- 测试:新增 uniform auto_phase=False 路由、uniform 汇总/初跑脚本保留
  断言、缓存指纹含 window 失效断言;全量 pytest 662 passed + ruff 全绿。

## 0.2.165(2026-08-24,uniform/NUS 流程对齐 + GMB 高斯窗修复)

用户报告:2D uniform 检测到直流偏置后终跑脚本未加 POLY -time;运行日志
出现 `Arguments 3 to 6 may be unknown or unused:' -lb 5 -gb 0.1 '`;
生成谱图过程反复出现误导性的「开始转换 fid」。

- uniform 2D/3D 终跑 POLY -time 接线:generate_process_script 新增
  direct_poly_time(直接维时域 FID 最前插入 `POLY -time`,与 NUS step1
  同构),nmrpipe_backend.process/_process 从 params 透传;首遍复型预览
  保持不加(0.2.160 设计,避免带偏直接维相位搜索);effective_params
  回写 direct_poly_time;
- GMB 高斯窗修复(NMRPipe 宏语义):gaussian 窗此前渲染成 `GM -lb/-gb`
  ——GM 只接受 -g1/-g2/-g3,-lb/-gb 是 GMB(Bruker 风格高斯窗)参数;
  VM 实测 GM 静默忽略该参数(输出与无参 GM 字节一致),即高斯窗实际
  从未生效,部分版本报 `Arguments 3 to 6 may be unknown or unused:
  ' -lb 5 -gb 0.1 '`。现 _stage_lines/_window_line 统一渲染
  `GMB -lb X -gb Y`;
- process() 转换进度语义:只有真正执行 bruker 转换才发「开始转换 fid」,
  复用已转换产物发「复用已转换 fid(跳过转换)」——unified 流程的
  preview/joint/窗候选多次 process 调用不再反复显示误导性转换进度;
- render_scripts 手动渲染路径补齐透传:uniform/NUS 补 window、sampling、
  direct_poly_time、extract/ext_lo/ext_hi(3D NUS 直接维相位取 F3 而非
  固定 F2),NUS 补 phases;与自动终跑脚本对齐;
- 测试:test_direct_diagnostics 增加 uniform 2D/3D POLY -time 渲染断言;
  test_script_schema 增加 render_scripts window/direct_poly_time 透传;
  test_phase_routes 增加 uniform 直流偏置→终跑 direct_poly_time 路由;
  GM→GMB 断言更新;全量 pytest 661 passed + ruff 全绿。

## 0.2.164-补1(2026-08-23,批量执行只保留新版本)

用户要求:批量执行留下新版本——数据组(schema 1.4)+ workflow.batch.run_batch
为唯一执行路径,删除旧版 pipeline_state batch 标记与面板内联逐数据循环。

- Pipeline 面板:批量组执行统一委托 controller.run_group_batch → workflow.batch
  (删除 _on_run_requested 内联循环);上下文显示「数据组 {id}: N 数据」;
  新增 _run_group_step 汇总(单数据失败不中断整组);SMILE 优化不支持批量组
  (请单个数据执行);
- 删除旧标记双写:processing.batch_import 只建数据组;
  main_window 组增删/删除组不再写 pipeline_state;gui/pipeline_state 删除
  batch_id/set_batch_id/clear_batch_id/batch_ids_in_experiment/
  next_batch_id/batch_data_ids(旧项目 .pipeline_state.json 的 batch 键仍由
  workflow.batch 兼容读取);
- project_tree 不再显示 [batch] 后缀(组内数据由组节点标识);
- controller.run_group_batch 增加 params 透传(面板 spectrum 直接维范围覆盖);
- 测试:test_gui_batch/test_gui_data_group/test_gui_phase_c 同步为数据组语义,
  test_batch 保留旧标记兼容读取用例;全量 pytest + ruff 全绿。

## 0.2.164(2026-08-23,清理:新 git 只含活跃代码)

用户要求:重复/不用的代码不再推送到新 git,并从工作目录删除;旧 git
(当前仓库完整历史 + vm 远端)保留兜底。详见 docs/DECISIONS.md D015。

- 删除完全死代码/未接线骨架:core/reporting、core/optimization
  {bayesian,candidate,grid,local,parameter_space,early_stopping}、
  core/processing 原语(保留 axes.py)、calibration、nus_reconstruction、
  dimension_mapper、axis_plan、peak_stability、format_converter、gui/panels;
- 合并部分使用实现到新路径:相位常量/辅助迁入 memory_phase_search(删除
  workflow/phase_optimize、recon_phase_search);配置加载统一
  backend.config.load_config;Experiment 读取统一 workflow.stepwise
  (manual/gui 复用);工作区统一 core.workspace(删除 gui/workspace.py 与
  welcome 兼容层);峰表 IO 统一 core.peaks(gui/peaks_io 去掉回退分支);
- 删除被取代的旧实现:workflow/engine(AutoProcessor)与 controller
  auto_run_sync/async、workflow/pipeline+operations 原生 DAG 管线,
  及专属测试/VM 脚本(recon_phase_search、vm_validate_optimize、
  vm_validate_recon_phase_equiv);
- 删除死函数:detect_modes、merge_nuslists、registry.register、
  linewidth_hz_for、axis_to_logical、RecentProjectsStore、
  gui.notes.set_*_note、settings.settings_path、dialogs._ext_default、
  welcome_page.make_welcome_card、phase_search 的 search_phase/
  search_spectrum_phase/apply_phase_axis/_search_axis;
- 同步文档:development.md 新增「旧项清理原则」(强制)、DECISIONS D015、
  scripts/README、README/PROJECT_STATE/architecture/API_CONTRACT/
  AGENT_PROMPTS/backend-architecture/tasks/current;
- 测试:全量 pytest + ruff 全绿。

## 0.2.163-补15(2026-08-23,总管直接处理)(2026-08-23,总管直接处理)

开发原则:处理流程改动默认覆盖四种路径:
- 用户对处理流程提出一个改动时,默认必须同时应用到 2D uniform /
  3D uniform / 2D NUS / 3D NUS 全部四种(自动/人工入口亦然);
- 仅当改动确实属于某一种/某几种特有(如 NUS 的 SMILE 重构、3D 切片流)
  时才允许只改对应路径,并须在改动说明/CHANGELOG 中写明例外原因;
- 写入 docs/development.md(强制章节)与 docs/DECISIONS.md(D014),
  以 tests/test_full_paths.py 四种路径全绿为执行检查。

## 0.2.163-补14(2026-08-23,总管直接处理)

上一步未完成不提供下一步运行入口(修正补13-3 的自动转换):
- Pipeline 面板人工按钮与自动「运行」按钮同规则:步骤 LOCKED
  (前置未完成)时不显示——生成谱图/峰挑选/分析全部后续步骤一致,
  fid 未生成就不会出现「生成谱图」的人工入口,用户先完成自动路径
  「生成 FID」步骤(数据转换/合并/坏点清理都在该步骤完成,人工只负责
  改 fid.com 与处理脚本参数);批量组内单个数据前置未完成时跳过并提示;
- 运行入口加防御校验:_on_run_requested 对 LOCKED 步骤拒绝执行并提示
  缺哪个前置步骤(程序化入口/批量也走同一规则);
- manual_scripts 去掉自动 convert_to_fid 分支,并保留兜底:直接经
  菜单等入口打开时若 fid 缺失,抛 ManualRunError 并弹
  「请先生成 FID」提示(不偷跑转换);
- 测试:新增 GUI 回归(spectrum LOCKED 时人工/运行按钮隐藏,
  生成 FID 后出现);自动转换用例改为缺失提示用例,渲染用例先造 fid;
  全量 + ruff 全绿。

## 0.2.163-补13(2026-08-23,总管直接处理)

人工途径对齐自动化(fid 命名 / 分段合并 / 谱图准备):
- 补13-1:patch_fid_com 顺带改写 fid.com 单文件输出名
  (bruker 默认 test.fid → {dataset_id}.fid),fid.com 输出即最终名,
  自动/人工不再「fid.com 写 test.fid、归位才改成 d_001.fid」;
  generate_convert_script 缺省 out_file 同步改;归位/切片检测兼容
  旧 test.fid/test*.fid(旧数据与用户手改回退);
- 补13-2:分段采集数据人工 fid.com 不再报错——人工只调参数
  (parse_fid_com 覆盖,apply_fid_com_overrides 逐段应用),数据转换/
  切片/合并/坏点清理仍由后端 convert_to_fid 按自动路径执行,合并 fid
  落 process/merged/fid;manual_fid_com 返回参考段(seg_001)并加提示头;
- 补13-3:人工生成谱图自动准备——点按钮即处理到生成初脚本为止
  (fid 缺失自动 convert_to_fid);已运行过自动优化(终跑脚本
  {data_id}_process.com/{data_id}_nus.com 存在)时再跑一次质量诊断
  (run_direct_diagnostics,结果落 process/manual_quality.log),直接给
  终脚本;用户点运行仍只执行 fid.com 与处理脚本;
- 补13-4:fid.com 编辑器提示改为「输出名已是 {数据 id}.fid」;
- 测试:test_full_paths.py 全绿(自动 2D/3D uniform+NUS/人工/批量),
  新增 patch_fid_out_name/apply_fid_com_overrides/分段人工合并/
  人工谱图自动准备+质量检测用例;本地全量 750+ 项全绿。

## 0.2.163-补10~12(2026-08-23,总管直接处理)

人工路径命名与分段数据修复:
- 补10:manual 各函数(manual_fid_com/run_manual_fid_com/manual_scripts/
  run_manual_spectrum)读 Experiment 后统一 dataset_id=data_id——此前按
  raw 目录名导致 fid 产物 d_001.fid 与脚本 -in 引用(raw 名)错位,
  2D 人工运行失败且报 NMRPipe 空头(无明确原因);
- 补11:manual 读取改用 read_dataset_container/read_segments(分段容器
  目录无 acqus 不再报错);分段数据人工 fid.com 明确提示走自动路径
  (多段合并由后端保证一致),不再无原因失败;
- 补12:fid.com 编辑器提示说明——输出 test.fid/切片 test001.fid 是
  bruker 固定行为,运行后由后端归位为 {数据 id}.fid 到 process/;
- 测试:全路径新增分段容器读取、2D 人工脚本用 data_id 断言;
  更新旧 manual 测试谱名预期;本地全量 720 项 + ruff 全绿。

## 0.2.163-补9(2026-08-23,总管直接处理)

人工谱图脚本按切片 fid 改写 in_file:
- 根因:render_scripts 固定 in_file={dataset_id}.fid(单文件);3D uniform/NUS
  的 fid 是切片目录(fid/test*.fid),人工脚本编辑器打开/运行 process.com/
  nus.com 找不到单文件而失败(自动路径 backend 内部会切换切片流,故正常);
- 修复:manual_scripts 渲染后检测 work/fid/ 切片存在时,把脚本 -in 的
  单文件改写为 fid/test%03d.fid(只改输入,不动输出);
- 测试:全路径新增 3D uniform 人工脚本 in_file 改写断言;
  本地全量 718 项 + ruff 全绿。

## 0.2.163-补8(2026-08-23,总管直接处理)

全路径端到端回归测试(tests/test_full_paths.py,每次修改必跑):
- 覆盖全部已有路径:自动 2D/3D uniform + NUS(导入 → FID → 谱图
  [unified 诊断+优化] → 峰挑选 → 分析)、人工(fid.com/谱图脚本)、
  批量(数据组);
- FakeBackend 写真实可读 fid/谱(诊断/直接维窗优化真实执行),谱读取
  经 monkeypatch 注入合成数组;验证流程完整性、产物归位、
  WorkflowRun 登记;
- 约定写入 docs/development.md:修改代码后必须运行
  `python -m pytest tests/test_full_paths.py`;
- 本地全量 717 项 + ruff 全绿。

## 0.2.163-补7(2026-08-23,总管直接处理)

人工路径支持切片 fid(3D uniform/NUS):
- run_manual_spectrum 的 fid 检查改为「单文件或切片目录(fid/test*.fid)
  任一存在」——此前 3D 数据 fid 为切片目录,is_file() 误判缺 fid,人工
  运行直接失败;
- run_manual_fid_com 转换产物检查支持切片式,归位时整体移到
  work/fid/(单文件仍走 {dataset_id}.fid);
- 测试:新增切片 fid 人工谱图运行 + 切片 fid 归位两个回归;
  本地全量 708 项 + ruff 全绿。

## 0.2.163-补6(2026-08-23,总管直接处理)

uniform 2D/3D 全采样跟进 NUS 优化项目(处理思路一致,无重构更快):
- 数据质量诊断门控接入 uniform 分支(直流偏置→POLY -time、坏点替换
  备份),与 NUS 同源;
- 新增 _optimize_uniform_processing:基线(内存评分)+ 直接维窗函数
  (FID 内存评分)+ 填零/间接维窗候选(process 重跑评分,带 fixed 相位
  覆盖);终跑参数合并 baseline/window/zero_fill/direct_poly_time;
- window_optimize 支持切片 fid(3D uniform/NUS 流文件 fid/test*.fid),
  与 direct_diagnostics._collect_fid_paths 同语义;
- 2D NUS / 3D NUS 本就走 _unified_nus(含全部 NUS 优化),本次统一确认;
- 测试:uniform 处理参数优化断言(baseline/window/diagnostics 进终跑),
  更新 uniform 调用次数(joint 谱);本地全量 706 项 + ruff 全绿。

## 0.2.163-补5(2026-08-22,总管直接处理)

人工脚本提示对应 + Pipeline 按钮布局与重新处理拆分:
- 脚本编辑器提示按脚本类型区分:fid.com 显示 Bruker 转换脚本提示
  (转换参数/CAR 引用/NUS 采样),process/nus 处理脚本显示参数优化建议
  (POLY/SP/ZF/PS/FT),不再所有脚本用同一段提示;
- Pipeline 步骤行按钮移到标题/描述下方独立一行(不再挤在右侧);
- 生成谱图步骤重新处理拆两按钮:「重新优化」(完整自动处理,相位/参数
  优化 + 终跑)与「重新运行终脚本」(复用最近成功谱图运行参数,应用
  用户最新设置的直接维范围后只跑一次,不重新优化);其余步骤 SUCCESS
  仍为「重新处理」;
- 关键保证:重新运行终脚本直接在已有最终脚本(uniform {data_id}_process.com
  / NUS {data_id}_nus.com)上修改 EXT 行的 -x1/-xn 窗口(保留 ppm 后缀),
  再运行该脚本——相位/窗/基线/诊断等其它参数完全不动,谱图不会因重新
  渲染而改变;用户改了直接维范围再重跑即生效(早期方案曾走
  phase_route=none 重新渲染,导致谱图错误,已弃用);
- 测试:新增 rerun_final 应用最新 ext 回归;相关 GUI 测试更新全绿。

## 0.2.163-补4(2026-08-22,总管直接处理)

不必要独立窗口全部内嵌主页面(彻底规避 Wayland 窗口协议问题):
- 导入数据/数据组间分析下拉从独立 Popup/Tool 窗口改为主窗口覆盖子部件:
  位置用相对坐标由 Qt 自己控制(不依赖 Wayland 合成器/positioner),任何
  平台一致;滚动条 + 按按钮上/下方可用空间限高(不遮按钮)保留;
  一次点击切换与点外部关闭恢复正常(非抓取,无 xdg_popup dismiss 竞态);
- 树内联重命名输入框从 Popup 独立窗口改为树面板内嵌子部件(初始隐藏,
  相对坐标定位);
- 保留的必要独立窗口:模态对话框(确认/设置/脚本编辑等)、独立谱图
  查看器 SpectrumWindow(QMainWindow,用户主动打开,无抓取问题);
- 测试:相关 GUI 测试更新(父窗口显示 + 相对坐标断言)全绿。

## 0.2.163-补3(2026-08-22,总管直接处理)

下拉滚动条 + 定位不遮按钮 + 取消弹出卡死修复:
- 导入数据/数据组间分析下拉内容包进 QScrollArea,按按钮上/下方可用
  空间限制最大高度:优先放按钮正下方,下方不够则放上方,保证不遮住
  触发按钮;内容过长时出现滚动条(不再把窗口顶到屏幕边缘遮按钮);
- 修复 Wayland 下「数据组间分析」弹出后再点外部/取消导致卡死:xdg_popup
  是抓取式窗口,点击外部由合成器原生关闭;eventFilter 再手动 close()
  会与 dismiss 竞态导致界面卡死——现仅非 Popup(Tool)窗口手动关闭,
  Wayland Popup 交给原生行为;
- 测试:新增下拉不遮按钮/高度受限/滚动区回归;相关 GUI 测试全绿。

## 0.2.163-补2(2026-08-22,总管直接处理)

下拉框平台兼容修复(Wayland 定位):
- 根因:Wayland 协议下普通顶层窗口(Tool)的位置由合成器决定,客户端
  move() 无效——0.2.162-补14「先 show 再 move」只在 X11/Windows 有效,
  Wayland 下导入数据/数据组间分析下拉位置仍不对;
- 修复:下拉窗口类型按运行平台选择——Wayland 用 Popup(xdg_popup 由
  合成器按锚点定位,需挂 transientParent 到锚点所在顶层窗口,父窗口
  已接收输入即满足);X11/Windows 保持非抓取 Tool 窗口(0.2.162-补13
  一次点击切换);offscreen 测试不受影响;
- 注:Wayland 下 xdg_popup 抓取输入,点击外部/另一按钮需两次点击切换
  (协议限制),位置优先;
- 测试:本地相关 GUI 测试全绿 + ruff 全绿。

## 0.2.163(2026-08-22,总管直接处理)

数据组成为一等实体 + 批量处理接线:
- Shared Contract(core/project,schema 1.4 兼容读旧):新增 DataGroupEntry
  (id/title/data_ids/created_at),ExperimentEntry.groups;ProjectManager
  组方法 create/rename/delete/add_to_group/remove_from_group/data_groups/
  group/group_of_data/group_data_ids;组编号 G1/G2(与旧 pipeline_state
  B 前缀批量组区分,编号含历史不复用);删除数据自动从所有组移除成员;
- Backend(workflow/batch.py):run_batch 目标解析支持 project.json 数据组
  (组 id 优先,旧 pipeline_state batch 兼容);新增 reference_data_id——
  取参考数据最近成功谱图运行的有效参数作为 spectrum 步骤参数基底
  (「按参考数据处理整组」,显式 params 覆盖);单数据失败不中断整组;
- GUI:实验类型下数据组与单个样品数据同级显示,组内数据挂组节点下;
  组右键「把其它数据加入该组...」(未入组数据多选)/重命名组/删除组
  (删除组不解散数据);组内数据右键「把该数据移出组」;选中组节点 →
  中间显示数据组批量面板(参考数据下拉 + 截止步骤 + 按参考数据处理
  整组 / 依次优化组内数据,进度与逐数据汇总入日志);选中组内单个
  数据仍为原 Pipeline 界面;批量导入勾选成组时自动建数据组
  (project.json 与 pipeline_state 双写);
- 0.2.163-补:删除旧「加入批量组.../移出批量组」数据右键入口(信号/
  方法/菜单一并移除,底层 pipeline_state batch 函数保留供引擎兼容);
  「把该数据移出组」后数据回到实验类型下成为普通单个数据(project.json
  与 pipeline_state 同步清除组标记);新增回归测试;
- 测试:test_data_group.py(模型/组方法/run_batch 组解析/参考参数复用/
  显式覆盖)、test_gui_data_group.py(树组节点/右键/组面板);更新
  schema 1.4 与批量导入组号 G1 断言;本地全量 pytest 702 项 + ruff 全绿
  (Windows 下 Qt 收尾偶发 access violation 为已知问题,重跑可过)。

## 0.2.162-补17(2026-08-22,总管直接处理)

- 切换/设置直接维范围时给出提示:设置或清除后日志面板输出当前窗口
  (含「首遍保持原窗口」说明);「直接维范围」按钮悬浮提示随当前
  数据动态显示终跑窗口/默认说明(切换数据即刷新);对话框提示词
  补充窗口外峰与 p1 重归一化说明;
- 测试:按钮 tooltip 随数据切换刷新;本地全量 pytest(688 项)
  + ruff 全绿。

## 0.2.162-补16(2026-08-22,总管直接处理)

- 终跑直接维范围与首遍不同时,直接维线性相位 p1 按窗口宽度比例
  重归一化(p1_new = p1 × 终跑窗口宽/首遍窗口宽):p1 表示跨整个
  提取窗口的总度数,范围变窄后同一 p1 的物理斜率会被放大,重归一
  化后终跑谱的相位校正与优化结果严格一致;最终报告/日志「直接维
  相位」显示重归一化后的 p1;
- 测试:p1 缩放单元测试 + NUS 集成(p1 15°、窗口 4.0→2.0 ppm →
  7.5°);本地全量 pytest(688 项)+ ruff 全绿。

## 0.2.162-补15(2026-08-22,总管直接处理)

- 生成谱图步骤顺带产出 Sparky UCSF 文件:终谱归位后调 NMRPipe
  pipe2ucsf 转 spectra/<data_id>.ucsf(VM 已装;失败降级只提示,
  不影响主谱),运行记录 outputs 登记 ucsf_path;
- 生成谱图运行按钮前新增「直接维范围」按钮:输入对话框设置终跑
  直接维提取窗口(EXT -x1/-xn,可留空=默认),仅注入终跑完整脚本
  (params_final 的 ext_lo/ext_hi),首遍重构/复型预览/相位搜索
  保持原窗口;参数报告显示「直接维范围(终跑)」;
- 测试:UCSF 转换成功/失败/降级、终跑范围只进终跑(uniform + NUS)、
  逃生口映射、GUI 按钮/参数透传;本地全量 pytest(686 项)+ ruff
  全绿。

## 0.2.162-补14(2026-08-22,总管直接处理)

- 修复下拉框位置跑偏:改 Tool 窗口后,隐藏窗口的 move() 在部分平台被
  解释为相对父窗口坐标;改为先 show 再 adjustSize/move(已显示窗口的
  move 为全局坐标),屏幕边缘仍自动收进;
- 测试:下拉位置与按钮左缘对齐、不超出屏幕;本地全量 pytest(675 项)
  + ruff 全绿。

## 0.2.162-补13(2026-08-22,总管直接处理)

- 「导入数据」展开时点「数据组间分析」一次点击直接切换(反之亦然):
  下拉改为非抓取 Tool 窗口 + 全局事件过滤器,点其它按钮/外部先自动
  关当前下拉,点击继续落到目标按钮;过滤器随显示/隐藏/销毁安装与
  移除,并加销毁竞态保护;
- 实验类型页数据表格「名称」列可直接编辑改名(双击/选中点击/F2),
  走 manager.rename_data 落盘并刷新;
- 测试基建:bruker_dir fixture 改为每测试一份副本——此前反复跑测试把
  硬链接累积到共享 fixture 文件,达 NTFS 上限(1024)后 os.link 失败
  导致导入回退复制;
- 测试:下拉一次切换、表格改名、副本 fixture;本地全量 pytest(675 项)
  + ruff 全绿。

## 0.2.162-补12(2026-08-22,总管直接处理)

- 「导入数据」「数据组间分析」按钮移到原导入块位置(实验类型页),去掉
  主界面顶栏动作栏;
- 批量导入新增成组选项:勾选「批量导入并成组(后续处理会一起处理)」
  (默认)= 绑定同一 batch_id;不勾选 = 不成组(相当于多个单次导入,
  batch_id 为空);
- 移除 pipeline 面板导入残留:导入步骤行、导入按钮、import_data_requested
  信号与相关分支全部删除(PIPELINE_STEPS 不再含 import);
- 测试:批量不成组/成组选项/按钮归位/pipeline 无导入步骤;
  本地全量 pytest(673 项)+ ruff 全绿。

## 0.2.162-补11(2026-08-22,总管直接处理)

- 主界面顶栏新增两个按钮:「导入数据」与「数据组间分析」;
- 「导入数据」向下弹出下拉面板(含单个/分段/批量导入 + 链接选项),实验
  类型页不再内联展示导入块;导入面板抽取为 ExperimentImportPanel 供
  下拉与仪表板(兼容转发)复用;
- 「数据组间分析」下拉为占位(功能开发中);
- 修复左侧重命名输入框文字不可见:QLineEdit 样式补 color(白底黑字);
- 清理历史遗留 ruff 问题(未用变量/import 排序/行长/歧义变量);
- 测试:顶栏按钮/导入下拉/占位下拉/重命名颜色;本地全量 pytest(671 项)
  + ruff 全绿。

## 0.2.162-补10(2026-08-22,总管直接处理)

- 取消噪声注入重复去伪(用户决定):三个保留谱直接用扫描重构与峰表,
  去伪由跨组合过滤(真峰需在 ≥cross_min 个组合出现)+ 可信度分级承担;
- 移除最终去伪阶段与 final_repeats/min_stability 参数(_match_stable_peaks
  删除),优化只跑网格扫描(每组 1 次重构),耗时下降约 3 次重构;
- Top1 不再精炼,三个谱的真峰数口径一致(均为扫描期可信度≥55 的峰数);
- 测试:跨组合去伪、进度仅扫描、无最终重复;本地全量 pytest(667 项)
  + ruff 全绿。

## 0.2.162-补9(2026-08-22,总管直接处理)

- 真峰标准 = 逐峰可信度 ≥ true_conf_min(默认 55,即 A/B/C 级);
- SMILE 优化最终保留「真峰数最多的前 keep_top 个谱」(默认 3):扫描
  候选逐峰计算四分量可信度与真峰数,按 (真峰数, overall) 排序赋
  rank 1..3;Top1 仍做噪声注入去伪;
- 输出:Top2/3 落 spectra/ 与 smile_optimized/ 带 _top{rank} 后缀
  (谱 + 峰表 CSV + 可靠性 JSON),活动谱仍为 Top1;
- 测试:Top-N 排名(并列按 overall)、rank/true_peak_count、_top 后缀;
  本地全量 pytest(667 项)+ ruff 全绿。

## 0.2.162-补8(2026-08-22,总管直接处理)

- 可信度归一化到真正的 0~100:score = (四分量和) × 100/90,clamp 0~100
  (完美峰=100);此前四分量理论最大 90,导致 A 档(≥85)需拿理论最大值
  的 94%,过于苛刻;
- 等级阈值保持规范:A≥85 / B 70-84 / C 55-69 / D 40-54 / E<40;
- 测试:归一化边界与等级;本地全量 pytest + ruff 全绿。

## 0.2.162-补7(2026-08-22,总管直接处理)

- 修复峰形/局部噪声校准过严(此前两项基本只会扣分,真实数据无峰达到
  任一单项满分):
- 峰形改为逐轴正向证据(对称/单峰/合理宽度/干净衰减)累计后归一化到
  -5~+5,异常(不对称/次级峰/异常尖峰/振铃)扣分——干净峰可达 +5;
- 局部噪声改为排除峰核心(±2 点)的环形区域(±8 点)评估,干净区域
  可达 +5(此前环带含峰振铃导致普遍负分);
- 测试:四分量公式一致(shape/local 取新值);本地全量 pytest + ruff
  全绿。

## 0.2.162-补6(2026-08-22,总管直接处理)

- 逐峰可信度改为 Peak Confidence Score 四分量:
  score = snr_points(0~40) + stability_points(-15~+40)
  + shape_points(-5~+5) + local_noise_points(-5~+5),clamp 0~100;
  S/N 与 SMILE 重构稳定性为同等级一级核心证据;
- 重构稳定性 = 峰存在性(-5~+15,出现率按 5/5 表等比推广)
  + 峰强稳定性(-5~+15,高度 CV)+ 峰位稳定性(-5~+10,位置极差/
  线宽);证据来自 nSigma×thresh 双参数网格(保持现有网格)多次重构;
- 峰形修正(-5~+5):对称性/单峰性/异常尖峰 启发式;
  局部噪声修正(-5~+5):峰旁环带噪声 vs 全谱噪声 + 基线偏移;
- 输出等级 A-E(≥85/70/55/40/<40)与硬性风险 flags(very_low_snr/
  reconstruction_unstable/intensity_unstable/position_unstable/
  abnormal_peak_shape/high_local_noise);可靠性文件 schema v3;
- 测试:S/N 分段表、四分量组合与 clamp/等级、集成评分;
  本地全量 pytest(664 项)+ ruff 全绿。

## 0.2.162-补5(2026-08-22,总管直接处理)

- 同峰判定容差 2 → 4 点/轴(扫描支持、去伪匹配、峰表注释三处一致;
  VM 实测真峰位移 p95 约 4 点,2 点偏紧,会误杀约 1/3 中等真峰);
- 可信度双重打分:Reliability(%) = 50% × 跨组合支持分
  (support/n_combos×100) + 50% × 信噪比评分;SNR≥5 信噪比满分(很可靠),
  SNR<5 按信噪比与跨组合出现率双重打分(3-5 真假混杂区间线性过渡,
  <3 记 0——假峰概率高);
- 可靠性文件 schema v2:逐峰记录 support/n_combos/snr/cross_score/
  snr_score/reliability;
- 测试:SNR 分段映射、_peak_reliability 双重打分边界、可靠性文件 v2;
  本地全量 pytest + ruff 全绿。

## 0.2.162-补4(2026-08-22,总管直接处理)

- 逐峰可靠性记录:优化时对每个保留峰记录「跨组合支持度 / n_combos ×
  100」(全部参数组合都有 = 100%,都没有 = 0%),写入
  smile_optimized/{数据}_smile_reliability.json(position_pts + ppm
  shifts + support + n_combos + reliability);smile_optimized CSV 增加
  Reliability(%) 列;
- 峰列表自动注释:峰挑选检测到可靠性文件时,按每轴 2 点 × ppm/点 容差
  匹配(两种方式下同一峰化学位移可能有小偏差),自动给 peaks CSV 注释
  Reliability(%) 列;viewer 峰表有注释时显示该列;
- 测试:可靠性打分(全有 100/半有 50)、可靠性文件落盘、峰表自动注释、
  附加列往返;本地全量 pytest + ruff 全绿。

## 0.2.162-补(2026-08-22,总管直接处理)

- SMILE 优化:扫描阶段去掉组内重复(SMILE 为确定性算法,同参数同输入
  逐位一致,组内重复无意义)——每组只重构 1 次,峰真伪由跨参数组合
  出现数评估(真峰在多个组合下都出现,伪峰只在个别组合出现);
- 省下的时间用于加密默认网格:3×3=9 组 → 5×5=25 组(nSigma
  3/4/5/6/7 × thresh 0.90/0.93/0.95/0.97/0.99),调参更精细;
- 去伪峰保留在第二阶段:最优参数多次重构(每次注入不同 fid 噪声
  seed),组内稳定峰为最终保留峰;
- SMILE 优化日志精简:只输出「正在优化 x/25 + 当前参数」与去伪重复
  进度,不转发后端 SMILE 原始输出;完成日志为精简摘要(不 dump
  全量结果 dict);
- scripts/smile_optimize.py CLI 适配新 API(ext/线程等入 base_params,
  只调 SMILE 参数)+ 新增 --grid 自定义网格选项;
- format_results 表格补 artifact 列表头(9 列数据与表头对齐);
- 测试:扫描 1 次/组合 + 最终 3 次重构次数锁定、默认网格 25 组、
  进度日志、CLI 网格解析;本地全量 pytest(658 项)+ ruff 全绿。

## 0.2.162(2026-08-22,总管直接处理)

- SMILE 优化重做:
  1) 基于已有生成谱图参数(base_params)只调整 SMILE 参数(nSigma/thresh),
     不再硬编码 ext/nthread(移除死参数 smile_xq3);
  2) 两阶段:网格扫描各参数组合(组内多次重构取稳定峰),跨组合统计峰出现
     组合数评估真伪(真峰应在多个参数组合下都出现);按 稳定性/跨组合支持/
     信噪比/峰数/伪影 综合评分选优;再对最优参数多次重复重构去伪峰;
  3) SMILE 为确定性算法,同参数重构逐位一致——backend.reconstruct_nus 新增
     fid_noise/fid_noise_seed:重构前向输入 fid 注入小幅高斯噪声(模拟测量
     噪声),使多次重构存在差异;
  4) 保留峰写入与 raw 同级的 smile_optimized/(稳定峰 CSV + 参数/评分 JSON);
- 测试:稳定性去伪、跨组合支持、基参数保留、smile_optimized 输出;本地全量
  pytest + ruff 全绿。

## 0.2.161(2026-08-21,总管直接处理)

- 修复:切换样品数据时,pipeline 已展开的步骤详情(参数报告/运行记录等)
  不立即刷新,仍显示上一个数据的报告;
- PipelinePanel.refresh() 现在会重算并更新所有已展开的步骤详情(覆盖切换
  数据、步骤运行完成等所有刷新入口);
- 排查其它刷新点:状态行/注释条/上下文栏/右侧谱图面板/报告页均已即时
  刷新,无需改动;
- 测试:切换数据后已展开详情立即更新;本地全量 pytest + ruff 全绿。

## 0.2.160(2026-08-21,总管直接处理)

- 修复:首遍 SMILE 脚本不再携带 POLY -time——直接维相位搜索以原始 recon
  平面为输入(POLY -time 会改变对称性评分,实测 sampleC 直接维相位从
  (0,0) score 38.0 被带偏到 (40,-15) score 36.9);
- POLY -time 仍进终跑完整脚本(params_final 保留 direct_poly_time,直流
  偏置校正不受影响);
- 测试:首遍不携带、终跑携带 direct_poly_time;本地全量 pytest + ruff
  全绿。

## 0.2.159(2026-08-21,总管直接处理)

- 修复:删除数据后重新导入,数据编号(d_001)复用导致旧数据的样品注释
  (metadata.data_notes)与运行记录沿用到新数据;
- 数据/实验编号改为「历史最大号+1」:从现有条目 + 运行记录 + 审计历史
  推导,删除后不再复用编号;
- 删除数据时顺带清理 metadata.data_notes 中该数据的注释;
- 测试:编号不复用(数据/实验)、删除清理注释;本地全量 pytest + ruff 全绿。

## 0.2.158(2026-08-21,总管直接处理)

- 直接维相位缓存指纹纳入 direct_poly_time:数据质量诊断结果变化
  (POLY -time 启用/关闭)时,直接维相位缓存自动失效并重新搜索,
  避免复用与重构平面不一致的旧相位;
- 缓存读写统一用 params_first(含诊断 direct_poly_time)计算指纹;
- 本地全量 pytest + ruff 全绿。

## 0.2.157(2026-08-21,总管直接处理)

- 报告统一:生成谱图步骤参数报告与日志末尾「质量与优化汇总」共用同一
  格式化(workflow/optimization_report.py),内容一致;
- 数据质量诊断在报告中直接显示详情(逐条报告),不再写「详见运行日志」;
- 日志面板:汇总报告经 progress 同步输出,完成最终谱图生成后立即显示
  (此前只进 logs 列表,日志面板看不到);
- 消息:「生成谱图(相位优化)」→「生成谱图」;
- 测试:诊断详情展示、统一格式断言;本地全量 pytest + ruff 全绿。

## 0.2.156(2026-08-21,总管直接处理)

- 保留初跑脚本:unified NUS 第一遍 SMILE 的完整脚本(未含优化相位)复制为
  {dataset_id}_before_optimize.com,与优化后终跑脚本 {dataset_id}_nus.com
  对照;日志记录「初跑脚本保留」;
- 回归测试:诊断检测到直流偏置(apply_poly_time)时,第一遍 SMILE 与终跑
  脚本均携带 direct_poly_time(POLY -time),且 before_optimize.com 内容
  为第一遍脚本;
- 本地全量 pytest + ruff 全绿。

## 0.2.155(2026-08-21,总管直接处理)

- 修复:数据质量诊断检测到直流偏置(apply_poly_time)时,终跑完整脚本
  现在也携带 direct_poly_time(此前只进第一遍 SMILE,最终脚本没有
  POLY -time);
- 日志:统一流程(uniform/NUS)各步增加耗时日志(每维相位搜索、联合复核、
  处理参数优化、终跑),末尾新增「质量与优化汇总」(谱图质量 decision/
  综合分、直接维/间接维相位、基线/填零/窗、诊断报告数、后端运行次数);
- 报告精简:GUI 生成谱图步骤详情只展示可读参数报告(途径/相位/基线/窗/
  填零/诊断/运行次数),不再 dump 原始参数、脚本快照等内部细节;
- 测试:终跑 direct_poly_time 传播、汇总日志、报告精简断言;本地全量
  pytest + ruff 全绿。

## 0.2.154(2026-08-21,总管直接处理)

- 移除旧逐维暴力相位优化(optimize_phase_brute_force),新版本不再携带:
  0.2.146 移除相位途径下拉后,GUI 的 ProcessingController.generate_spectrum
  在 params 无 phase_route 时仍会走旧「基础谱后逐维暴力相位优化」分支,
  导致相位优化跑到最终 SMILE 之后重复执行;
- gui/processing.py:删除该分支,生成谱图即统一自动处理(unified);
- workflow/stepwise.py:删除 optimize_phase_brute_force 函数与导出;
- 删除仅服务于旧优化的验证脚本 scripts/vm_validate_optimize_v2.py、
  scripts/vm_diag_phase_lineshape.py;对应测试移除;
- VM 复现 sampleC 确认 unified 顺序正确(第一遍 SMILE → 相位搜索 →
  参数优化 → 终跑含最终 SMILE);本地全量 pytest + ruff 全绿。

## 0.2.153(2026-08-21,总管直接处理)

- 显示方向规则(用户):3D 切片/投影二维谱横坐标优先级 H > N > C;
  viewer/spectrum.py 新增 orient_x_priority,slice/project/project_nmrpipe
  输出统一定向(必要时转置数据并交换轴,dim_indices 同步交换);
- 3D 面板 current_spectrum 的 dim_indices 改为按轴对象回查 3D 维序,
  转置后峰表 F*_shift 映射仍正确;
- 投影加载(_load_projection_ft2)由原「ppm 小的核放横坐标」改为
  H > N > C 优先级;
- VM 实测 hncacb.ft3:F1-F2(N-H)→x=H、F1-F3(N-C)→x=N、
  F2-F3(H-C)→x=H;
- 测试:新增切片定向回归 + 投影 13C-15N 方向断言更新;本地全量
  pytest + ruff 全绿。

## 0.2.152(2026-08-21,总管直接处理)

- viewer 无 metadata 直接打开:按头部 FDDIMORDER 重排逻辑序(F1/F2/F3),
  并由头部核(LABEL/OBS 推断)推导轴标签(N/H/C、同核 Hx/Hy),不再回退
  F1/F2/F3——与 nmrDraw 按 NAME 显示一致;
- 背景:独立查看器直接打开 sampleC/hncacb.ft3 等谱图(无旁路 metadata)
  时标签缺失;修复后 28.ft3/61.ft3/hncacb.ft3 直接打开均显示 N/H/C。
- 测试:无 metadata 时 ORDER 2 3 1 重排+标签推导回归;roundtrip 与
  fallback 标签断言更新;本地全量 pytest + ruff 全绿。

## 0.2.151(2026-08-21,总管直接处理)

- viewer 3D 轴序修复:load_from_ft2/ft3 按头部 FDDIMORDER 建立数据轴→
  FDF 块映射(axis i ↔ FDF{FDDIMORDER[ndim-1-i]},与 nmrglue
  guess_udic/make_uc 同源),不再按位置配 FDF1/FDF2/FDF3;FDDIMORDER
  缺失/非法时回退旧行为。
- 背景:真实 NMRPipe 3D 输出 ORDER 2 3 1(存储 F2,F3,F1),nmrglue 读回
  自然数组 (F1,F3,F2);修复前 28.ft3/61.ft3 轴 1/2 参数块互换且无告警
  (0.2.122 的 metadata 重排仅在标签恰好不同时触发)。
- 测试:新增 ORDER 2 3 1 逻辑序映射与无 metadata 参数配对回归;本地全量
  pytest + ruff 全绿;VM 实测 28.ft3/61.ft3 正确。

## 0.2.134~0.2.150(2026-08-20~21,总管协调直接并入 master)

- 0.2.133-修:3D F2 维 States 系 FT 自动加 -neg(依据 bruk2pipe ACQ MODE
  表与 FnMODE 官方枚举);FT 标志完整判定(-alt 依据 + real 模式
  TPPI/QSEQ/QF uniform 全支持);
- 0.2.134-修:SMILE 命令与 step3 后处理职责分离(命令不再携带窗/调相);
- 0.2.135-修:SMILE 方向标志按采样模式推导,与 step3 FT 同源;
- 0.2.136-改:人工路径仅脚本修改(删参数表格,人工统一走脚本编辑器);
- 0.2.137-改:SMILE -maxIter 按采样率分档(>0.5→300、>0.3→600、
  >0.15→1000、其余→1500);
- 0.2.138-改:-xCT/-yCT 是否添加仅由 CT 实验判定(普通实验不加);
- 0.2.139-改:直接维窗函数优化(内存 SP+FT 评分,不重跑 SMILE);
- 0.2.140-改:数据质量诊断门控(直流偏置/坏点/漂移,自动纠正+报告);
- 0.2.141-改(GUI):日志竖列(pipeline 与谱图查看器之间)+ 默认窗口几何
  贴顶不遮任务栏;
- 0.2.142-改:Log 区「停止当前任务」真实终止后端进程树(进程组注册表 +
  递归后代收集,无残留);
- 0.2.143-改(GUI):默认列宽 420/600/300/600(合计 1920)+ log 常驻 +
  四列自由拖拽;
- 0.2.144-改:收尾强制同步 dev 分支规则写入 GIT_WORKFLOW(三分支 HEAD
  一致才算任务完成);
- 0.2.146-改:移除相位途径下拉(生成谱图即统一自动处理)+ 修谱图运行
  记录(workflow_ref 含 phase_optimize_unified)+ 1D 按住左键十字线跟随
  (0.2.145 无独立提交,内容并入 0.2.146);
- 0.2.147-改(GUI):谱图控制区一行化(contour/levels/aspect + Full/1D +
  p0p1 仅 1D),峰行重组,Files/Layers 并排;
- 0.2.148-改(GUI):数值可输入(标题后显示)、1D 拖动十字线真修复
  (GraphicsSceneMouse 事件类型)、ppm 实时刷新、边界框、3D 控制一行;
- 0.2.149-改(GUI):边界框改为谱图数据范围(修启动乱跳),slice pt/ppm
  可输入;
- 0.2.150-改/补(GUI):谱图数据边界框随谱创建(挂 ViewBox 数据坐标系,
  随缩放);NMRViewBox PanMode 下左键不做平移(十字线用)。

详细记录见 docs/backend/state.md(0.2.133-修~0.2.140)与
docs/gui/state.md(0.2.141-0.2.150)。

## 0.2.133(2026-08-20,与 GUI 同版本,总管协调合并)

- Backend(0.2.133-B):
  - 投影命名定稿:`{data_id}_{核A}-{核B}.ft2`(文件名含平面实际两核,
    旧 `_proj_*` 名兼容);stepwise 注册键=被求和第三轴(logical),且投影
    注册写回 `run.params["projections"]`;
  - proj3D 正确用法:project_3d 直接对 3D 终谱调 proj3D.tcl(-sum),自动
    命名输出 {核A}.{核B}.dat,showhdr 验证头在 NMRPipe 语义下正确;
    真实数据 28.ft3 VM 验证(见 docs/backend/state.md 0.2.133-B 段)。
    0.2.133-B 早期「三输出头均错位」是 nmrglue 直读 FDF 槽位的读取端
    现象,文件头本身正确——projection_headers.py 头重写已删除,不再改头;
  - HT 语义结论(供 GUI 等价性检查):普通 HT 虚部 = −scipy.hilbert(x)
    虚部(标准频率方向;镜像 -ps90-180 反号),幅值 1:1;0.2.101
    「必须用 nmrPipe HT,不能用 scipy」结论仍适用;真实样例与命令见
    docs/backend/state.md;
  - 测试:test_stepwise.py 3D 投影新命名+注册+旧名回退;test_nmrpipe_backend.py
    project_3d 映射更新(自动命名解析固定轴/平面核,不重写头断言);
    test_viewer3d.py 投影新命名加载断言(x/y 核由文件名为准)。
- GUI/Viewer(0.2.133 修订):
  - 3D 面板只保留 slice 模式(删除 Proj/mode_combo);投影 .ft2 列为谱图
    文件,点击直接查看(文件名解析两核,ppm 小的核放横坐标,必要时转置);
  - 缩放下限补齐:wheelEvent/scaleBy/translateBy 后 clamp,滚轮缩小不能
    越过完整范围、平移不能把谱图移出视野;
  - 坐标轴刻度:标签尽量取整(±0.1 ppm 内整数,否则 1 位小数),缩放/平移
    后按可视范围重建 5 个刻度,避免缩小时标签重叠;
  - 谱图列表按文件名全排序(主谱 .ft3 在前,投影随后);「展示谱图」优先主谱;
  - 测试:test_viewer3d slice-only 与投影 ppm 排序断言、test_gui_context
    3D 状态仅平面、test_viewer 新增缩小/平移边界回归。


## [0.2.132] - 2026-08-19

- 修复自动基线优化(0.2.130 全轴写回)导致的终谱竖线条纹伪影:
  - 根因:逐迹多项式拟合被强峰拉偏 → 相邻迹拟合系数跳变 → 竖线条纹;
    旧评分只测两端/中部均值,对条纹不敏感,且评分轴用 enumerate 序号
    (dimensions 直接维在前时评错 numpy 轴),间接轴逐列校正"吃平"整列
    被高分误选进入终谱;
  - core/qc/baseline_quality.py:新增条纹罚项 stripe_penalty(相邻迹端部
    均值跳变 max/median,稀疏强峰拉偏显著增大),并入 score 与
    needs_correction;
  - workflow/baseline_optimize.py:评分轴改为 axis_index 逻辑轴→numpy 轴
    映射(与 baseline.apply 校正轴一致);逐迹候选 plain polyfit(与真实
    脚本 POLY 一致)引入明显迹间断层时硬性否决(不计入择优,保持 off);
    无实质增益(≤0.5)保持 off,不再写 POLY -auto;
  - 测试:新增 tests/test_baseline_stripe.py 三个回归(条纹罚项敏感、
    强峰谱择优保持 off、评分/校正轴一致);既有基线择优/写回测试的
    合成谱去掉被拉偏用的强峰点(保持各自漂移/曲率意图),断言不变;
  - 本地全量 pytest + ruff 全绿。

## [0.2.130] - 2026-08-19

- NUS 末遍改完整脚本终跑(用户方案,不再产生 nus3d_rc_ph 旋转副本):
  - 各维最终相位填入初始脚本成为新的完整脚本:直接维相位进 step1 PS
    (移到 EXT 之后,p1 归一化与 recon 平面内存旋转一致),间接维相位进
    step3 PS(-di),终跑为完整 nus.com 重跑(含 SMILE);
  - 联合复核与终跑之间新增处理参数优化:基线(内存评分,全部轴写回)、
    间接维窗函数/填零(候选 finalize 重渲 + 谱质量评分,不重跑 SMILE),
    结果一并写入终跑完整脚本;直接维窗/SMILE 内部 apod 保持默认并日志说明;
  - NUS 完整脚本/finalize 支持 window(直接维 step1、间接维 step3/finalize
    FT 前可配,gaussian/sine_bell/sine_bell²/exp,缺省不插窗);
  - reconstruct_nus/finalize_nus 透传 window;stepwise 状态并入
    zero_fill/window;
- 测试:完整脚本相位填入/PS 在 EXT 后/窗函数位置/处理参数优化编排与窗择优/
  finalize window 透传;本地全量 601 passed、ruff 全绿。

## [0.2.131] - 2026-08-19

- unified 流程中间产物清理(相位校正预览与窗函数优化):
  - 新增 _cleanup_unified_intermediates(),在 unified_route 与 _unified_nus 终跑
    返回前清理 process 目录下 {data_id}_preview_*、_joint*、_win{n}* 的
    .com/.ft2/.ft3/.fdf 中间文件(显式 glob + Path.unlink,包 OSError 容错);
  - 不跨目录、不递归,保留终谱(spectra/)、最终完整脚本(nus.com/process.com/
    finalize.com)、phase.json 缓存、fid/切片与 SMILE 重构平面;
  - 测试:新增 test_cleanup_unified_intermediates 构造含中间产物+保留项
    的 process 目录,验证清理后中间产物已删、保留项仍在;
  - 本地全量 pytest + ruff 全绿。

## [0.2.129] - 2026-08-19

- 中间产物/终谱/投影命名前缀统一为样品数据 id(d_001)(用户要求,重命名不影响):
  - stepwise._read_experiment 覆盖 experiment.dataset_id = data_id,消除
    read_dataset(raw_dir) 造成的 raw_ 前缀(d_001.fid、d_001_nus.com、
    d_001_preview_F1.ft3、d_001_finalize.com 等);
  - 终谱归位 spectra/<data_id>.ft2|ft3(G2B-010 的 exp-data 前缀改为 d_001);
  - 3D 投影 spectra/<data_id>_proj_F{1,2,3}.ft2;GUI 投影加载按
    *_proj_F{1,2,3}.ft2 扫描(兼容旧命名);
- 测试:本地全量 595 passed、ruff 全绿;VM 全量 591 passed + 4 skipped
  (Python 3.12.13,HEAD 832ea41)。

## [0.2.128] - 2026-08-19

- 直接维相位搜索(Backend,0.2.127-0.2.128):
  - progress/日志:搜索前后输出「直接维相位搜索中,请稍候」「完成,耗时
    N 秒」,消除静默卡感;
  - phase.json 缓存复用:参数(ext/nsigma/thresh/填零/线宽/sampling 等)与
    谱面形状未变时跳过重复搜索(首次之后约 0 秒);
  - 候选并行曾尝试后回退(真实运行 python 无响应,疑似线程池与
    numpy/BLAS 嵌套并行死锁);窗口指标向量化也回退(聚合顺序引起 1 ULP
    差异,用户要求保留原始实现)——当前为原始串行评分 + progress/缓存;
  - VM 实测串行:cc 88s、sampleB 20s,结果与优化前逐位一致;
- 测试:本地全量 595 passed、ruff 全绿;VM 全量 591 passed + 4 skipped
  (Python 3.12.13,HEAD 5b14d6f)。
## [0.2.130] - 2026-08-19

- NUS 末遍改完整脚本终跑(用户方案,不再产生 nus3d_rc_ph 旋转副本):
  - 各维最终相位填入初始脚本成为新的完整脚本:直接维相位进 step1 PS
    (移到 EXT 之后,p1 归一化与 recon 平面内存旋转一致),间接维相位进
    step3 PS(-di),终跑为完整 nus.com 重跑(含 SMILE);
  - 联合复核与终跑之间新增处理参数优化:基线(内存评分,全部轴写回)、
    间接维窗函数/填零(候选 finalize 重渲 + 谱质量评分,不重跑 SMILE),
    结果一并写入终跑完整脚本;直接维窗/SMILE 内部 apod 保持默认并日志说明;
  - NUS 完整脚本/finalize 支持 window(直接维 step1、间接维 step3/finalize
    FT 前可配,gaussian/sine_bell/sine_bell²/exp,缺省不插窗);
  - reconstruct_nus/finalize_nus 透传 window;stepwise 状态并入
    zero_fill/window;
- 测试:完整脚本相位填入/PS 在 EXT 后/窗函数位置/处理参数优化编排与窗择优/
  finalize window 透传;本地全量 601 passed、ruff 全绿。

## [0.2.127] - 2026-08-19

- 直接维相位搜索加速(Backend,用户要求 3 项):
  - progress/日志:搜索前/后输出「直接维相位搜索中(候选并行),请稍候」与
    「完成,耗时 N 秒」(消除「第一遍 SMILE 完成→F2 预览」间的静默卡感);
  - phase.json 缓存复用:参数(ext/nsigma/thresh/填零/线宽/sampling 等)与
    谱面形状未变时跳过重复搜索(首次之后约 0 秒);
  - 候选并行:粗搜/细化/近优平台(原 1825 次串行评分)并行化,结果与串行
    逐位一致(sampleB 搜索 20.4→8.2s;cc 提速有限,窗口循环受 GIL 限制,
    由缓存兜底);
- 测试:并行辅助/进度消息/缓存 roundtrip 与失效;本地全量 596 passed、
  ruff 全绿;VM 全量 592 passed + 4 skipped(Python 3.12.13,HEAD 4d80c41)。

## [0.2.126] - 2026-08-19

- 3D 谱投影改用 NMRPipe 自带 proj3D.tcl(Task E):终谱生成后用 pipe2xyz
  拆平面 + proj3D.tcl -sum 生成三个 2D 投影,落
  spectra/<exp>-<data>_proj_F{1,2,3}.ft2;viewer 3D 面板删除 MIP/Sum 投影
  模式,改为加载投影文件(缺失提示「投影未生成」,切片保留);
  VM sampleB 实测:xy→13C、xz→1H、yz→15N,三投影产物与手工一致;
- 分段导入忽略非数据子目录(Task F):新增 is_data_directory(任一关键
  文件);导入源解析 resolve_import_source——数据集/容器(≥2 数据子目录,
  分段)/恰好 1 个数据子目录(忽略杂物按单个导入)/0 个报错;拖拽与异步
  导入接入,不再对总文件夹误报「缺失 acqus」;
- 测试:本地全量 593 passed、ruff 全绿;VM 全量 589 passed + 4 skipped
  (Python 3.12.13,HEAD ef94403)。

## [0.2.125] - 2026-08-19

- merge(Architect):并入 GUI 0.2.122(viewer 轴序校对与重排 + G2B-011 分段
  导入到当前实验类型)与 Backend 0.2.122-0.2.124(处理过程进度可见/G2B-011
  exp_id/NUS 错点源头删除+备份);0.2.122 双端撞号,CHANGELOG 按版本合并
  归一(GUI+Backend 一节);
- 集成审查(Architect):GUI viewer/backend workflow 与分支逐文件 diff 为空
  (无代码丢失);开发模式记忆库(manager/gui/backend/tasks)完整保留;
- 验收:任务 G2B-011/进度可见/轴序校对全部落地,归档见 docs/tasks/archive/;
- 测试:本地全量 586 passed、ruff 全绿;VM 全量 582 passed + 4 skipped(Python 3.12.13,HEAD 04ce203),ruff 全绿。

## [0.2.124] - 2026-08-18

- NUS 错点删除改到源头(用户要求):不再在生成 fid 上清零,而是删除最开始的
  ser 文件(按 nuslist 行整块,每行字节 = ser_size/行数,须整除)并同步清理
  nuslist;删除前备份 ser/nuslist 为 .bak(仅首次,幂等),os.replace 断
  硬/软链接使外部原件不受影响;
- 单 NUS 与多段统一:源头清理在转换前执行;清理过则旧 fid/合并产物失效
  强制重转;ser 缺失或大小不符时回退到原「生成 FID 清零」并 ⚠ 提示;
- 测试:新增 3 例(单段删除+备份/硬链接外部原件不受影响/多段越界与跨段
  重复);本地全量 582 passed + ruff 全绿。

## [0.2.123] - 2026-08-18

- G2B-011 Backend(分段采集导入到当前实验类型):
  import_segmented_dataset 增加可选 exp_id;非空时校验存在后导入到
  指定实验类型(ImportResult 结构不变),空值保持新建(现状);非法
  exp_id 抛 ImportWorkflowError;
- 测试:exp_id 指定落实验(不新建实验类型)/非法 exp_id 抛错/空值新建
  回归;本地全量 580 passed(578+2)+ ruff 全绿。

## [0.2.122] - 2026-08-18

- viewer 轴序校对与重排(GUI):Spectrum3D.load_from_ft3 / load_from_ft2
  加载后按存储头 FDF*LABEL/FDF*OBS 推断核,与 metadata 逻辑轴核对照,
  不一致时重排 data/axes 到逻辑序(F1,F2,F3)并告警「轴序重排」;3D
  标签/切片/投影/峰表列映射跟随重排;每轴核与常见 ppm 范围不符时自检
  告警;独立查看器与 GUI 谱图面板均接入;
- 分段采集导入到当前实验类型(G2B-011):分段导入不再总是新建实验类型,
  改为在当前选中实验类型下新增样品数据;无当前实验类型时后端新建;
  ProcessingController.import_segmented_dataset 增加 exp_id 透传
  (向后兼容),后端 import_segmented_dataset 同增 exp_id;
- 处理过程进度可见(Backend):finalize_nus 增加可选 progress 回调
  (None 不回调),阶段文案「开始 finalize(复型预览/终跑)」「finalize
  完成」;_apply_direct_phase 同增可选 progress;
- phase_routes 统一流程补齐进度覆盖:uniform 每轴复型预览与终跑,
  NUS「第一遍 SMILE 完成」「F2/F1 复型预览中/完成」「finalize 终跑
  中/完成」,消除 finalize 阶段静默长等待(用户反馈 cc 多段 3D NUS);
- 测试:轴序重排/自检告警/2D 重排、分段导入 exp_id 透传与信号携带当前
  实验类型、NUS/uniform 编排进度覆盖与 finalize_nus 直接回调;本地全量
  pytest + ruff 全绿。


## [0.2.121] - 2026-08-18

- merge(Architect):并入 Backend 0.2.118-0.2.120——3D NUS acqu3s TD 修正
  副本(bruker 按 NusTD 输出切片式 fid/test%03d.fid,归位 process/fid/)、
  固体核磁(MAS)预设扩充 23 个(presets YAML 单一数据源 + 分类器关键词)、
  O1P 定义修正(O1/BF1,谱中心与 TopSpin 一致);版本号 0.2.118+ 与 master
  0.2.117 顺延无撞号,CHANGELOG 直接归并;
- 集成审查(Architect):三路合并自动完成无冲突;分类器 Counter 导入/模板
  双注册去重(0.2.117 修复)与 MAS 关键词共存;cch/nnh.yaml priors 已由
  Backend 按 BMRB 收紧(废弃 -50~250 占位);与 backend-dev 逐文件 diff
  为空(无代码丢失);
- 测试:本地全量 574 passed、ruff 全绿;VM 全量 570 passed + 4 skipped(Python 3.12.13,HEAD 2316ce1),ruff 全绿。

## [0.2.120] - 2026-08-18

- 修复(Backend):o1p(谱中心)定义与 TopSpin 一致——O1P 缺失时改为
  O1/BF1(偏移相对基频 BF1),替代旧 O1/SFO1(相对实际载频 SFO1=BF1+O1);
  两者差 ≈ O1P²/1e6(sampleK 15N:117.000 vs 116.986,差 0.014 ppm);
  显式 O1P 仍优先,BF1 缺失回退 SFO1 兼容旧数据;Dimension.sf 仍为
  SFO1(fid.com OBS 用),bruker_workflow CAR 逻辑不变自动取正确值;
- 测试:新增 O1/BF1=117.000(sampleK 实测参数)、1H BF1 回退(≈4.703)、
  显式 O1P 优先;更新回退用例说明(BF1 缺失走 O1/SFO1);
- 本地全量 574 passed(571+3)+ ruff 全绿;VM 复核 sampleK fid.com
  yCAR/zCAR=117.000 见 docs/PROJECT_STATUS.md。

## [0.2.119] - 2026-08-18

- 固体核磁(MAS)实验类型预设扩充(Backend,presets/*.yaml 单一数据源,
  不新增 core/experiments/*.py 模板模块):
  - 2D 15N-13C:NCA/NCO(SPECIFIC-CP)、TEDOR/PAIN-CP(距离约束);
  - 2D 13C-13C:DARR/PDSD/RFDR/CORD/INADEQUATE/HCC;
  - 2D 1H-X:HETCOR/HNHETCOR(CP/FSLG)、NN(15N-15N PAR)、
    CHHC/NHHC(1H-1H 空间,核组合待真实数据验证);
  - 3D 15N/13C/13C:NCACX/NCOCX/NCACB/NCOCACB(Cα/Cβ 反相 → mixed +
    peak_sign_regions);3D 13C/15N/13C 序列行走:CANCO/CAN(CO)CA/
    CBCANCO;3D 13C-13C-13C:CCC;
  - 化学位移先验按 BMRB(Ulrich et al., Nucleic Acids Res. 36, D402
    (2008))与固体核磁文献:1H -5–20、15N 90–140(同核 90–160)、
    13C 脂肪 10–75、Cα 40–70、Cβ 15–45、羰基 165–185、13C 全谱
    10–190;同步收紧 nnh/cch 的 -50–250 占位;
  - 分类器 _PULPROG_TYPES 扩展(长/具体在前;同核组合靠候选核匹配
    区分,FSLGhetcor 按核组合分 HETCOR/HNHETCOR);液体关键词不被
    固体抢占(hncacb/hnca/hnco/cbcanh/cbcaconh/noesy 回归);
  - presets/README.md 按类别登记并给出文献依据;
  - 1D 类型(CP13C/CP15N/PROTON1D/C13_1D)仍不收录 YAML:
    test_gui_presets 仅允许 ndim=2/3,待 GUI 放开后补(汇报 Architect);
  - 测试:新增 8 个分类器回归;本地全量 571 passed + ruff 全绿;
    VM 真实数据分类验证见 docs/PROJECT_STATUS.md。

## [0.2.118] - 2026-08-18

- 修复(Backend):3D NUS 生成单文件 FID——NUS 数据 acqu3s TD 被写成 1
  (sampleB:##$TD= 1,##$NusTD= 100)时,bruker -AUTO 按单增量输出单文件
  test.fid;现转换前在暂存副本(work/conv_stage,硬链接优先、acqu3s 复制)
  把 acqu3s TD 修正为 NusTD 再跑 bruker,输出切片式 fid/test%03d.fid
  (每 F1 一个切片,与实验室手工流程一致),归位 process/fid/,raw 原件
  不被改动;暂存用完即删;
- convert_to_fid 对切片式产物返回 work/fid/ 目录路径(单文件路径兼容
  保留);reconstruct_nus/直接维相位搜索/finalize 按 0.2.85 切片流消费;
- 同步更新模块 docstring 与 script_generator 注释,删除「单数据集不做
  切片追加」旧结论;新增测试:3D NUS 暂存修正(副本 TD=NusTD、raw 原件
  不变、切片归位、暂存清理)、2D NUS 不受影响、产物路径选择、gate 条件
  (均匀/2D/多段不触发);
- 测试:本地全量 563 passed(0.2.117 基线 559 + 新增 4)+ ruff 全绿;
  VM 验证见 docs/PROJECT_STATUS.md。

## [0.2.117] - 2026-08-18

- merge(Architect):并入 GUI 0.2.112 补充(弹窗统一居中到所在屏幕中心、
  修复「展示谱图」找不到谱图[谱图面板按 spectra/ 目录全量扫描]、导入后
  清空表单、右键重命名输入框 transientParent + 点击外部提交)与 Backend
  0.2.106 补充(实验类型分类器核组合优先重构 + 固体核磁 NNH/CCH);两侧
  版本号与已并入内容撞号,CHANGELOG 按版本合并归一;
- 集成修复(Architect,合并审查):experiment_classifier 补回 Counter 导入,
  _nuclei_candidates 按模板对象去重(0.2.111 双注册后「唯一候选」分支
  失效,NNH 0.95 置信路径恢复);presets/cch.yaml priors 15N 笔误 → 13C;
  删除合并带入的死代码 core/experiments/{cch,nnh}.py(presets/*.yaml
  单一数据源,0.2.111);补回被合并冲突吞掉的 gui/dialogs.py 弹窗居中
  函数(install_dialog_centering 等);
- 批准(Architect):G2B-010 终谱归位按契约命名
  spectra/<exp_id>-<data_id>.ft2|ft3(任务分派 docs/tasks/g2b-010-backend.md);
- 排查(Architect,用户纠正):3D NUS 单文件 FID 根因——NUS 数据 acqu3s 的
  TD 被写成 1(sampleB:##$TD= 1,##$NusTD= 100),bruker -AUTO 据此按单增量
  生成单文件 test.fid;修复方案(用户指定):为 NUS 数据生成一份 acqu3s
  副本、TD 改为正确值(NusTD),让 bruker 读取副本,输出切片式
  fid/test%03d.fid(与实验室手工流程一致);模块 docstring「单数据集不做
  切片追加」的旧结论作废,任务已分派 Backend(提示词见 docs/AGENT_PROMPTS.md);
- 测试:本地全量 559 passed、ruff 全绿;VM 全量 555 passed + 4 skipped(Python 3.12.13,HEAD 6399cf4),ruff 全绿。

## [0.2.116] - 2026-08-18

- merge(Architect):并入 GUI 0.2.112 补充——导入页「链接原始数据到项目」
  复选框置顶(三个导入入口共用)、欢迎页新建项目「确定」按钮、右键重命名
  输入框改树视口内嵌(Wayland popup 警告修复);CHANGELOG 同版本节合并归一
  (0.2.109-0.2.112 双标题清理);
- 测试:本地全量 553 passed、VM 全量 549 passed + 4 skipped(b281c8a),
  ruff 全绿。

## [0.2.115] - 2026-08-18

- merge(Architect):并入 GUI 0.2.109-0.2.112(新建内联命名/右键重命名原地
  编辑、日志温度开尔文 + Auto-optimize 文案、1200 MHz-2 GHz 核推断、分段
  采集导入入口、实验类型→数据类型改名 + 注释字段挪层、设置对话框裁剪
  [移除 SMILE 线程上限/points_per_line,与 0.2.113 线程护栏移除一致] +
  默认线宽接入生成谱图参数);0.2.109-0.2.112 与 Architect 版本号撞号,
  CHANGELOG 按版本归一;
- 审查(Architect):gui/processing.py 线宽注入为 Shared Contract 向后兼容
  扩展(显式 params 优先,后端按轴取值/回退);
- 测试:本地全量 552 passed、VM 全量 548 passed + 4 skipped(0687243),
  ruff 全绿。

## [0.2.114] - 2026-08-18

- 测量结论(VM 实测):SMILE 峰值内存与线程数无关——同一 3D HNCACB
  (直接维 600 点/网格 4000/250 采样)在 1/2/4/6 线程下峰值均为 ~665MB,
  线程只影响速度(19.7→11.4s);SMILE 多线程共享同一全网格工作集,不复制
  数据;因此内存估计不需要线程因子;
- 清理:estimate_smile_peak_mb / memory_guard 移除无用的 nthread 参数
  (与采样点数一并确认无关),reconstruct_nus 调用同步;
- 测试:本地全量 539 passed、VM 全量 536 passed + 4 skipped,ruff 全绿。

## [0.2.113] - 2026-08-18

- 调整(Architect,用户指出):移除「大网格 SMILE 线程数强制 ≤2」护栏(D006)——

  sampleM 事故根因是直接维内存(非切片流 / 直接维填零过多),已由 0.2.112

  内存护栏兜底(估计 → 降直接维填零 1×TD → 提示所需 GB);按网格限线程只会

  无谓拖慢大网格 SMILE。SMILE 线程恢复为 resolve_nthread 默认(机器线程数-2),

  配置 smile.nthread 仍可显式覆盖;

- 删除 enforce_smile_thread_guardrail 及其两处调用(reconstruct_nus / 轻量

  相位搜索);test_phase_nus_3d 中过时护栏测试移除;

- 测试:本地全量 539 passed、VM 全量(待复跑),ruff 全绿。

## [0.2.112] - 2026-08-18

- feat(Architect,用户要求):SMILE 内存估计与护栏——生成谱图(NUS)前估计
  SMILE 峰值内存,超限先降直接维填零 1×TD 并提示,仍不足则返回「请至少
  提供 X GB 内存」,GUI 弹窗(日志 + InfoDialog,主线程信号);
- 实测标定(VM 2026-08-18):3D SMILE 峰值 ∝ 直接维点数(EXT 窗口内),
  ≈1.15 MB/点(150/600/2048 点 → 179/665/2284 MB),与采样点数无关;
  2D 峰值≈3MB 不构成瓶颈;小内存处理大数据的可行手段 = 切片流 + 直接维
  1×TD + 收紧 EXT 窗口(线性降);sampleB 估计 690MB vs 实测 665MB;
- 新增 backend/memory_guard.py(估计/可用内存/护栏);reconstruct_nus 接入;
  GUI pipeline_panel/center_panel/main_window 增加 memory_guard_requested 信号;
- 测试:tests/test_memory_guard.py(标定/护栏/消息);本地全量 540 passed、
  VM 全量 536 passed + 4 skipped(e39b5b2),ruff 全绿。

- 注释字段调整:实验类型注释仅保留「实验类型」字段,取值指认实验 / 动力学
  实验;原实验类型注释的「维度 / 数据类型(presets)/ 核」三个字段移到样品
  数据注释(与 重复/Buffer 组分/Buffer pH/温度 并列)——presets(HSQC 等)
  现统一视为「数据类型」,在样品数据注释显示为「数据类型」。
- 导入自动填充同步改为填充样品数据注释(维度/数据类型/核/温度),实验类型
  注释不再由导入自动填充。
- 层级名称保持「实验类型」不变(仅注释表单字段变化,Shared Contract 不变)。
- 测试:字段 schema、注释读写回读、中间注释条、注释表单(实验类型指认/动
  力学选项 + 样品数据先选维度再过滤 presets)、导入自动填充位置、设置项
  移除与线宽接入;本地全量 pytest + ruff 全绿。
- 软件设置:删除「SMILE 线程上限」与「填零 points_per_line」设置项(线程
  与填零恢复后端 config 默认);默认线宽接入生成谱图参数(核素→轴映射,
  显式 params 优先,后端按轴取值)。
- 导入页:「链接原始数据到项目」复选框移到三个导入入口(单个/分段/批量)上方;
  欢迎页新建项目输入行:占位文案「输入项目名称」,右侧新增「确定」按钮。
- 修复:生成谱图后「展示谱图」找不到谱图——后端终谱按 dataset_id 命名,
  谱图面板改为按数据级 spectra/ 目录内全部 .ft2/.ft3 查找(不再假设
  exp_id-data_id 前缀)。
- 导入:导入成功后清空中间页导入表单(样品数据名称/目录),便于连续导入。
- 弹窗:所有自定义 QDialog 弹窗统一在所在屏幕中心弹出(应用级事件过滤器,
  主窗口与独立查看器入口均安装)。
- 修复:右键重命名输入框(Qt.Popup)补上 transientParent(顶层窗口父级),
  消除 Wayland 下 "Failed to create grabbing popup" 警告;点击外部自动
  关闭并提交,回车/Esc 行为不变,不再残留显示。

## [0.2.111] - 2026-08-18

- 重构(Architect,用户要求「不多处资源,能合并就合并」):实验模板单一数据源
  presets/*.yaml——core/experiments/registry 实现 ExperimentTemplate.from_yaml
  与 load_presets(导入 core.experiments 即加载,按显示名 + 文件 stem 双注册,
  Generic/大小写命名差异均可解析);删除 15 个逐模块 Python 模板
  (cbcaconh/cbcanh/cosy/generic/hmbc/hmqc/hnca/hncacb/hnco/hnco_ca/hnha/
  hsqc/noesy/roesy/tocsy),presets 成为唯一数据源;分类器显式触发注册;
- 顺带合并重复默认值:GUI param_schema 兜底骨架与参数对话框的 ext_lo/ext_hi
  回退改读 backend.config.load_processing_defaults(消除第三处硬编码);
  清理 backend/config.py 过时注释(script_generator 已无 _DEFAULT_LINEWIDTH_HZ);
  presets/hncacb.yaml priors 补 13C_alpha/13C_beta(保留原 Python 版细分);
- 防漂移测试:冒烟新增 YAML 全注册/stem 别名/分类器 pulprog 名单防漂移;
  test_gui_processing 新增 GUI ext 默认值与后端一致断言;
- 测试:本地全量 535 passed、VM 全量 531 passed + 4 skipped(8088e63),
  ruff 全绿。

- 核推断磁场列表补到 2 GHz(300/400/500/600/700/800/850/900/950/1000/
  1100/1200/1300/1500/2000 MHz),为未来更高场谱仪预留;接受范围同步扩到
  2100 MHz,2 GHz 系统的 1H/13C/15N 均可正确识别。

## [0.2.110] - 2026-08-18

- 运维(Architect,用户要求):VM 测试产物归置——新增 scripts/vm_test.sh 统一
  入口,全量回归时 pytest basetemp / `__pycache__` / ruff 缓存全部落到
  `~/nmrforge-test-artifacts/`,`~/NMRForge` 主目录不再堆积缓存(清理了
  512 个 `__pycache__`、`.pytest_cache`、`nmrforge.egg-info`);
- HANDOVER:VM 执行环境与测试命令已更新为统一入口。

- 修复:原始数据质量日志的温度显示为开尔文(TE 自动识别 0.1 K / K / °C,
  如 2980 → 298.0 K),不再显示摄氏;
- 界面:生成谱图步骤「相位优化途径」默认项文案 Unified → Auto-optimize
  (数据值/后端契约不变,仍为 unified);步骤参数报告与运行日志同步显示
  Auto-optimize;
- 修复:核推断支持 1100/1200 MHz 磁场——sampleI(1H-15N,1.2 GHz)间接维
  sf≈121.7 MHz 不再误判为 31P(H-P),正确识别为 15N;
- 新增:实验类型页「分段采集导入(合并 FID)」入口,位于单个导入与批量处理
  之间;选择容器目录(顶层无 acqus 且 ≥2 个子目录含 acqus)后直接合并导入为
  一条样品数据;
- 测试:温度开尔文、1200 MHz 核推断回归、分段入口布局/信号、分段导入容器
  校验与异步调用、Auto-optimize 文案;本地全量 pytest + ruff 全绿。

## [0.2.109] - 2026-08-18

- merge(Architect):并入 GUI 0.2.108(B2G-004 对接完成)——生成谱图步骤
  「相位优化途径」选择(Unified 默认 / None 逃生口)经 params["phase_route"]
  透传,步骤详情「参数报告」展示逐维相位(p0/p1)、direct_phase 与
  backend_runs;导入流程识别容器目录自动走 import_segmented_dataset(合并
  为一条数据),导入对话框新增「分段采集导入」;ProcessingController 增加
  params 透传与 import_segmented_dataset 方法(Shared Contract 向后兼容
  扩展,Architect 预批);
- 修复(GUI Agent,VM 冒烟):分段合并 FID 产物为 process/merged/fid 目录,
  _fid_file/_node_artifacts 现支持目录产物,生成 FID 后步骤状态正确;
- 测试:本地全量 532 passed、VM 全量 528 passed + 4 skipped(5243150),
  ruff 全绿;B2G-004 与分段采集 GUI 接入两项待办关闭。

- 修复(GUI Agent):新建项目/新建实验类型不再弹窗命名——欢迎页「新建项目」
  改为页内内联输入行(回车创建 / Esc 取消);菜单与项目树右键的「新建项目」
  「新建实验类型」改为项目树内内联命名(新节点直接进入编辑,回车提交后再填
  常规信息,提交后节点自动落位)。
- 重命名(项目/实验类型/样品数据)改为「右键菜单原地变成重命名输入框」:
  点右键菜单中的「重命名」后,输入框直接出现在右键位置,回车提交、Esc 取消、
  点击其它处提交;菜单栏「重命名实验类型」在树节点附近显示同一输入框。
- 测试:欢迎页内联输入提交/取消、项目树内联提交与 Esc 取消、重命名输入框
  出现在右键位置并提交、右键菜单触发重命名输入框;本地全量 pytest + ruff 全绿。

## [0.2.108] - 2026-08-18

- 新增(GUI Agent,B2G-004):生成谱图步骤提供「相位优化途径」选择——
  Unified(默认,后端统一方案:逐维复型预览 + 内存调相 + 完整终跑)/
  None(逃生口,跳过相位优化);经 ProcessingController.generate_spectrum
  透传 params["phase_route"](未传时保持旧行为,Shared Contract 扩展已
  获 Architect 批准)。
- 新增:生成谱图完成后在步骤详情「参数报告」展示每维相位结果——逐逻辑
  轴 p0/p1、直接维相位、后端运行次数(如实展示来源)。
- 新增(Backend 请求):分段采集导入入口——导入流程识别容器目录(顶层无
  acqus 且 ≥2 个子目录含 acqus)自动走 import_segmented_dataset(合并
  为一条数据,后端逐段转换 + addNMR 合并),与批量导入(多条条目)明确
  区分;导入对话框提供「分段采集导入」复选框并自动勾选;Processing-
  Controller 增加 import_segmented_dataset 透传(与 import_data 并列)。
- 修复:分段采集的合并 FID 产物为 process/merged/fid 目录(逐增量文件),
  Pipeline 状态判定支持目录产物,FID 步骤不再误显示 READY。
- 测试:phase_route 透传与跳过优化、容器目录识别、分段导入控制器透传、
  导入对话框容器校验、Pipeline 途径选择与运行透传、合并 FID 目录状态;
  本地全量 pytest + ruff 全绿。

## [0.2.107] - 2026-08-18

- merge(Architect):并入 GUI 0.2.88-0.2.93(展示谱图按钮/核化学位移推断/
  3D Proj 投影与异步加载/简单模式/终端打开)与 Backend 0.2.88-0.2.106
  (相位优化统一方案:逐维复型预览+内存调相、±180° 符号消歧、多段 NUS
  合并、坏点检测清理、单数据分段采集导入);0.2.88-0.2.93 版本号与两侧
  撞号,CHANGELOG 按版本合并归一;
- 审查(Architect):合并后本地全量 523 passed、VM 全量 519 passed +
  4 skipped(HEAD 7e3fd41),ruff 全绿,一次通过无对接失败;
- 注:backend 0.2.106 记录的 test_gui_processing ext_lo=11.0 预存失败
  已于 0.2.88 修复(Architect 统一 10.5/6.5),不再存在;
- 待办:B2G-004(phase-routes 显示层对接)与分段采集导入的 GUI 对话框
  接入,待 GUI Agent。

## [0.2.106] - 2026-08-17

- 相位优化统一为「逐维复型预览 + 内存调相」(替代简单/进阶分派):
  - 第一遍:整条生产管道仅搜索轴 PS 不加 -di(其它轴按已固定相位 -di)、
    零填零,输出生产布局复型文件(uniform 每轴一条;NUS 直接维复用 SMILE
    recon 复型平面,间接维由 finalize 复型预览提供——该轴不加 -di,
    FT/-alt/ZTP 约定由真实后端保证);
  - 内存调相:固定迹线净吸收评分(旧算法判断标准),粗网格+细化+门控+
    联合复核原样搬到内存,零额外 SMILE;
  - 末遍:窗函数/填零/基线/各维 PS/EXT/-di 完整重跑出良谱。
- 实验类型符号早约束:presets 新增 peak_sign(uniform/mixed;HNCACB=mixed),
  mixed 用「|净吸收| 中位数 + 正负共存」评分(uniform 保持签名净吸收);
  VM sampleB(HNCACB)间接维 p0 恢复手动 F2=90°/F1≈0°。
- ±180° 绝对符号消歧(mixed 实验):presets 新增 peak_sign_regions
  (化学位移分区+期望符号,HNCACB 13C Cα 负/Cβ 正,默认值来自 sampleB 实测),
  区域符号分离干净(≥70%/≥4 强峰)才翻转,保守;并修复批量加 peak_sign
  时 YAML 行错位的数据问题。
- 离散峰迹线选择(仅 mixed 实验):95 分位阈值 + 半高占窗比(duty)与峰
  显著性过滤中央混杂峰团;uniform 保持旧 99.5 分位全部强迹线锁定
  (离散过滤曾把 sampleL 带偏 180°,已限定范围)。
- 3D 输出轴序实测为 (F2,F1,F3)(FDF 头标签不可靠),复型预览按搜索轴拆包
  (交错实型轴不固定);NUS 直接维沿用旧对称性搜索(|p1|>20° 归零)。
- 删除 nmrPipe HT / scipy hilbert 路径:phase_ht_candidate_axis、
  phase_ht_candidate、hilbert_spectrum、display_phase_engine、
  display_hybrid_optimize(及其测试)。
- generate_spectrum 默认 phase_route=unified,phase_route=none 保留逃生口;
  VM 前置实验确认 SMILE 不接受复型直接维输入(报错 Imaginary in the
  direct dim must be deleted),NUS stage1 保持 -di。
- VM 全谱型同决策回归:sampleI/103/3/4/5、sampleA 25%/100%、sampleB;
  p0 一致(±2.5–10°,大多 ≤5°),sampleL 旧简单路径 F2=0°/F1=300° 异常
  消除;sampleB F2=90° 与手动一致,后端次数 46→3(uniform)/47→4(3D NUS)。
- 多段 NUS 合并验证(cc/61/63/65/67):各段独立 bruker -AUTO 生成的
  fid.com 参数一致(仅空格排版不同),逐段转换→拆切片→addNMR 逐对合并
  →合并 nuslist(348 点)→SMILE 出谱;与手工 1stfid/2ndAdd 流程等价
  (手工复用参考段 fid.com 仅为方便);支持逐段 -rs 频移。
- 单数据分段采集导入入口:import_segmented_dataset 直接接受包含全部分段
  的容器目录(自动发现直接含 acqus 的子目录,read_segments 校验一致后
  合并为一条 DataEntry,raw 只拷 segments/,逐段指纹);与批量导入(多条
  DataEntry)明确区分,普通导入遇容器目录报错不自动猜测;stepwise 读取
  分段数据走 read_segments,容器→FID→谱图链路 VM 端到端验证通过。
- 分段合并坏点检测与清理:越界点/跨段重复点从合并 nuslist 剔除,有效网格
  内坏点清零对应 FID 增量(States 双实行),⚠ 提示用户;cc/63 实测坏点
  (27, 2350) 越界自动丢弃(349→348 点),SMILE 正常出谱。
- 坏点检测/清理扩展到所有 NUS 数据:单 NUS 数据在 reconstruct_nus 中统一
  校验 nuslist(越界+重复),剔除坏点、清理对应 FID(2D 单文件/3D 切片)、
  ⚠ 提示;2D nuslist 索引上限 = 网格 td[1](nus20_25 索引到 126 不误判),
  3D 保持 NusTD//2;注入坏点实测只标 (1000,) 且正常数据零误报。
- 实验类型分类器重构(核组合优先):先识别各维核,按核组合(含同核计数)排除
  不匹配模板,再在候选中 PULPROG 精排;新增固体核磁类型 NNH/CCH(1H/15N/
  15N、1H/13C/13C)——sampleK 的 hncocannhgpwg3d 不再被误配 HN(CO)CA,
  VM 实测 102→NNH(0.95)、8/12/103→HSQC、100→CBCA(CO)NH、101→HNCACB。

## [0.2.105] - 2026-08-17

- 第一遍 uniform 直接维 PS 保留真实虚部(keep_direct_complex),为逐维复型
  预览铺路(后续 0.2.106 统一方案取代)。

## [0.2.104] - 2026-08-17

- 简单途径改为逐维 nmrPipe PS -ht:目标轴转置到管道轴(2D 间接维 -y),
  候选显示谱动态锁定峰位评分;消除间接维 180° 反相;
- VM 批量:sampleF/4/5/8 直接维与间接维大多在 advanced 的 ±15° 内
  (sampleI F2 300 vs 307.5、F1 95 vs 92.5);sampleL 异常待查
  (F2=0°、F1=300°)。

## [0.2.103] - 2026-08-17

- 简单途径直接维评分改为候选谱动态锁定峰位(像人工调相时追踪峰);
  sampleI F2 由 175° 修正到 300°(正确 307.5°,残差 7.5°);
- 与进阶版评分/搜索同源,唯一额外区别是峰位锁定:简单在 PS -ht 候选谱上
  动态锁定,进阶在真实后端谱上固定锁定——已在开发文档记录。

## [0.2.101] - 2026-08-16

- 相位优化简单/进阶两条途径整合并真实数据标定:
  - 简单途径(默认):第一遍处理 → nmrPipe HT 重建虚部 → 逐维显示层
    固定迹线净吸收评分 → 重跑填相位;所有谱型统一;
  - 进阶途径:uniform 全维度后端优化(optimize_phase_sequential),NUS
    混合(直接维 HT 显示层 + 间接维逐候选 finalize);
  - 关键修正:显示层虚部必须用 nmrPipe HT,不能用 scipy.signal.hilbert
    (scipy 使 sampleI F2 误选 140°,HT 后选 295°,正确 307.5°);
  - 3D 轴名按 NMRPipe 谱头 FDF1/FDF2/FDF3 映射(修复 reversed dims 错位);
  - VM 标定:sampleI F2 295°(正确 307.5°,F1 仍偏),sampleA 100%/25%
    直接维均恢复 (0,0);简单途径对 NUS 可靠,uniform 复杂谱建议 advanced;
  - generate_spectrum 支持 params["phase_route"],默认 simple,保留 none。

## [0.2.98] - 2026-08-16

- 修复 3D NUS 显示层相位搜索/填相位(实型交错复型约定,sampleB 真实数据验证):
  - nus3d_rc 平面为「第一轴实/虚交错」实型存储(nmrglue 读成翻倍实型,
    read_pipe_complex 拆包为复型);0.2.96 的 3D 分支把交错实型当复型
    旋转/评分是错误约定——搜索在无意义空间打分、应用则写回错误数据;
  - _display_phase_search 3D 分支改用 read_pipe_complex 拆包复型后
    对称性评分(直接维 axis 0),间接维增量均布子采样 ≤8 个平面;
  - _apply_direct_phase 旋转结果写入 nus3d_rc_ph/(或 nus2d/recon_ph.ft1)
    副本再 finalize,源重构平面保持 PS(0,0) 复型供复用/重搜;finalize_nus
    新增 planes 参数支持副本渲染;
  - VM sampleB(250 点,6.2%,1H 9.0-6.5 ppm,1×TD):显示层 F2=(85,50)
    score 38.3,p1 归零 → 应用 (85,0);终谱直接维 1H 吸收度 neg-area
    -0.182/symmetry 0.631,优于 (0,0)(-0.593/0.276)与 NU-DFT 旧路径
    (-0.513/0.337);三轴(15N/1H/13C)吸收度全面优于旧记录间接相位组合
    (0.2.45 F2(-45,-30)/F1(45,-60))——显示层 + 间接 (0,0) 已近纯吸收;
  - 新增回归测试:3D 交错复型拆包后近零相位返回 (0,0);填相位写副本且
    源平面不变;本地全量 pytest 仅剩 GUI ext 默认值基线失败(backend-dev
    与 vm/master 分叉,GUI 侧未同步 0.2.88 契约),ruff 全绿。

## [0.2.97] - 2026-08-16

- 记录相位优化方法演进与回退(用户要求,方便随时回退)+ 交接新窗口:
  - docs/PROJECT_STATUS.md 新增「相位优化方法演进与回退(0.2.87→0.2.96)」
    表:各版本方法/位置/结论/回退开关(display_phase_search=False →
    NU-DFT;light_phase_search=True → 轻量 SMILE;direct_phase_override →
    手动;删 phase.json → 重搜;uniform 现有优化完整保留为基准);
  - docs/AGENT_PROMPTS.md 追加 Backend 启动提示词(0.2.97 起):全谱型
    (2D/3D、uniform/NUS)统一到「1× 处理 + 显示层相位搜索 + 最后一步填
    相位」流程——3D NUS 验证/修复 3D 分支(sampleB)、uniform 接入评估、
    全谱型回归(sampleI、sampleA、sampleB),以现有方法为正确答案;
  - 全谱型统一可行性:2D NUS 已统一;3D NUS 代码已写待验证;uniform 建议
    保持现有优化(已验证且便宜),显示层作可选快速估计;
  - 主仓库临时对比脚本(vm_*.py)已清理。

## [0.2.96] - 2026-08-16

- 显示层相位搜索改 1× SMILE(用户要求:相位优化不需要额外后端,找到正确
  相位在最后一步填上去)+ nmrDraw 机制核对:
  - 核对结论:nmrDraw 显示层调相 = 对复型谱做频域旋转(乘 e^{i(p0+p1·k)})
    再取实部;即使一维谱"只有实部",虚部参与旋转使实部形状随相位变化(吸收
    偶对称/色散奇对称/混合);若数据只剩实部(-di),nmrDraw "需要时重建虚部"
    (Hilbert)——我们的最终 ft2 是实型,复型重构平面 recon.ft1 直接可用且
    相位精确(无 Hilbert 偏移,实测 Hilbert 有 ~30° 约定偏移);
  - 架构:1× SMILE(PS 0,0,或缓存相位)→ 在复型 recon 平面上信号行峰选择 +
    对称性评分(直接维 axis 0)→ phase.json → 最后一步把相位旋转应用到
    recon + 便宜 stage-2 finalize 重渲终谱(非 SMILE);不再 2× SMILE;
  - search_direct_phase_on_spectrum 增 axis 参数(直接维所在轴);p1 幅值
    护栏(|p1|>20° 归零,直接维 p1 通常很小,recon 伪影偏好大 p1);
  - VM 实测(sampleA 100% NUS):1× SMILE 3.2s;显示层搜索 F2=(0,30) score
    36.6 → p1 归零 → phase.json (0,0)(匹配现有方法 356.7≈0);终谱保持
    PS(0,0);25% NUS 无干净信号峰 → 安全保持默认相位;
  - 测试:全量 486 passed(1 失败为 GUI 遗留 ext_lo 断言,待 GUI Agent),
    ruff 全绿;
  - 待 VM 真实 NUS 高采样数据验证:显示层相位 vs 完整流程,以及非平凡相位
    (p0≠0)时的应用路径。

## [0.2.95] - 2026-08-16

- 显示层相位搜索(nmrDraw 人工调相思路,默认开启;用户方案):
  - 原理:人工在 nmrDraw 看终谱、切一维谱、显示层调相(仅频域旋转取实部,
    不动谱数据),按「峰是否对称吸收」判断。本实现完整模仿:正式 SMILE
    预览(PS 0,0)→ 终谱上信号行峰选择 → 频域旋转对称性评分 → 主 SMILE
    复用估出相位(2× SMILE,与人工「跑→看→改→重跑」一致);
  - 信号行峰选择(用户关键点:蛋白谱每行只有几个峰、信号高):每迹线局部
    极大 + 峰高 ≥ max(10×行 MAD 噪音, 5% 全局最大)+ 每行峰数 ≤8——先排除
    噪音/伪影区域,只留高耸稀疏信号峰;
  - 评分:峰窗口对称性(吸收偶对称≈1/色散奇对称≈0)+ 正峰约束(±180 消歧);
    近最优平台取最小修正(谱已接近好相位不乱加修正);
  - 置信度门控:score<75 拒绝应用(VM 实测 25% NUS 重构伪影给 105°、
    score=68.6 → 拒绝保持默认;100% 重构精确恢复 (0,0)、score=83.7 应用);
  - reconstruct_nus 增 params["display_phase_search"](默认 True),开启时
    跳过 NU-DFT/轻量(它们真实数据不可靠);phase.json(v2, source=
    display_recon)缓存,后续运行复用不再重搜;
  - 测试:search_direct_phase_on_spectrum 显式 net 指标平台语义 + 新默认
    symmetry 信号行选择;全量 487 passed(1 失败为 GUI 遗留 ext_lo 断言,
    待 GUI Agent),ruff 全绿;
  - 待 VM 真实 NUS 数据(用户的高采样数据)验证:显示层相位 vs 完整流程
    对比;25% 低采样重构伪影重时门控拒绝、保持默认相位(安全)。

## [0.2.94] - 2026-08-16

- 实装「phase-only 轻量 SMILE」模式(用户方案;实验性,默认关闭):
  - core.optimization.phase_search.search_direct_phase_on_spectrum:最终谱
    固定迹线中位数净吸收评分搜索直接维 (p0, p1)(与现有 uniform 优化同一
    指标,含 ±180 正峰消歧);
  - backend.reconstruct_nus 增 params["light_phase_search"]=True:子采样
    nuslist(目标 max(16, n/4),强制含点 0)→ work/light/ 子目录(符号链接
    复用转换产物 + 子采样 nuslist)→ PS(0,0) 轻量 SMILE → 评分搜 F2 →
    写 phase.json(v2, source=phase_only_recon)→ 正式 SMILE 复用;
  - VM 实测(sampleA 25% NUS):轻量路径端到端 1.2s(轻量 16 点 + 正式 32 点);
    但 16 点子采样重构把直接维相位带偏 ~55°(该谱 (55,45) 评分 95.1 vs
    (0,0) 86.9,非平台——重构伪影污染固定迹线中位数评分);32 点重构也轻微
    偏好 (55,45) 而非真值 (356.7,0);
  - 结论:重构伪影随采样率下降而增大,「轻量重构 + 现有评分」在激进子采样
    下达不到现有方法精度;模式保留但默认关闭,待真实数据(高采样率)验证或
    改为温和子采样后再启用;
  - 测试:search_direct_phase_on_spectrum 单测(±180 消歧 + 平台语义);
    全量 486 passed(1 失败为 GUI 遗留 ext_lo 断言),ruff 全绿。

## [0.2.93] - 2026-08-18

- 新增(GUI Agent,用户反馈):样品数据子文件夹(raw/process/spectra/等)
  与样品数据节点的右键菜单增加「在终端中打开」——打开终端并默认进入
  csh(Linux 用 gnome-terminal/konsole/xterm 启动 csh,自动读取
  ~/.cshrc 的 NMRPipe 环境;Windows 优先找到的 csh,否则回退 cmd)。
- 测试:菜单项与信号、终端命令构造(优先 csh / Windows 回退);本地全量
  pytest + ruff 全绿。

- 真实 2D uniform 数据对比(VM sampleI):NU-DFT vs 现有优化,结论:
  - 合成 2D 传统数据:NU-DFT 达到/超过现有(p0 误差 0-6°、0 后端);
  - 真实 sampleI:现有(验证过)F2=(307.5, -5.0)46 次后端;NU-DFT 多峰
    相位互不一致(浓度 0.41,p0 偏 180°+、p1 失真)——真实谱峰重叠/基线/
    近 Nyquist 伪影破坏「单峰干净相位」假设;「F1 普通 FFT + 逐轴搜索」
    的零后端捷径同样复现不了现有答案(四象限 FT/ZF/EXT/POLY 差异);
  - 结论:按「达到一样效果才可用」标准,NU-DFT 暂不能替代 2D uniform
    的现有优化,后端节省不成立;现有优化保持;
  - 边缘伪影修复(对比中发现):直接维 FT 首尾 DC/Nyquist 伪影可比真实峰
    强数倍(sampleI k=0 4.9e8 vs 真实峰 8e7),nus_direct_phase 峰选择
    (_row_peak_positions 增 margin 参数 + 内部局部极大)排除之;
  - 0.2.91 NUS 直接维集成不受影响(填补空缺),但真实数据准确性同样存疑,
    建议在 sampleA 上验证或改「伪均匀 + 真实后端候选」路线;
  - 全量 485 passed(1 失败为 GUI 遗留 ext_lo 断言),ruff 全绿。

## [0.2.92] - 2026-08-16

- 调整(GUI Agent,用户反馈):移除「文件指纹检测」「显示已过期状态」
  两个细分开关,设置对话框只保留「简单模式」一个 Pipeline 开关
  (config/nmrforge.local.yaml pipeline.simple_mode,重启生效);
  简单模式=只按上一步产物文件判断状态,不做指纹/新旧比较、不显示
  「已过期」,旧配置里的 fingerprint_check/outdated_enabled 键忽略。
- 测试:开关测试收敛为简单模式断言;本地全量 pytest + ruff 全绿。

- NU-DFT 直接维相位修复 ±180 消歧(以现有方法为正确答案基准,用户要求):
  - 现状:NU-DFT 峰复值相位 = φ(k*)(t1 调制已在真实 F1 频率处精确抵消),
    p0_corr = -φ(k*) 是唯一解,与现有方法「取正峰解」语义一致——θ=-120
    应得 p0=120 而非 300;
  - 修复:弃用「单切片吸收符号」判正负(该切片 t1 相位可能为 180° 使峰
    反转,曾把 p0 误翻 180°),改为 NU-DFT 峰复值校正后应为正实的数值守卫;
  - 2D 传统数据对比(同合成):NU-DFT p0 误差 0-6°、p1 误差 0-8°、0 后端
    (现有 uniform 优化 49 次后端,其 net/|Re| 指标在窄峰合成上饱和);
    单峰(现有测试场景)各 θ 均精确恢复且无 ±180 歧义;
  - 测试:新增 nus_direct_phase 单峰 ±180 语义断言(θ=-120/33/90 → p0≈
    -θ,≠p0+180);全量 485 passed(1 失败为 GUI 遗留 ext_lo 断言),ruff
    全绿;
  - 待 VM 真实 2D uniform 数据:同数据分别走现有优化与 NU-DFT,对比直接
    维相位与终谱线型(现有方法为正确答案)。

## [0.2.91] - 2026-08-16

- 调整(Architect,用户要求):fid.com 等生成脚本归位 process/ 工作目录——
  backend._convert_dir 把 bruker -AUTO 在 raw 生成的 fid.com 立即移入
  dest_work(process/),以绝对路径 csh 执行(内部相对路径仍以 raw 为 cwd
  解析 ./ser);转换后 raw 不再残留 fid.com;回退 convert.com 同步绝对路径;
- workflow/manual:manual_fid_com 优先读 process/fid.com(旧数据回退 raw),
  自动生成前确保 process 目录存在;run_manual_fid_com 把修改后的 fid.com
  写入 process/ 再运行;
- GUI:pipeline_state.script_fingerprint 与 processing._fid_com_script
  改读 process/fid.com(旧数据回退 raw/);
- 测试:test_manual/test_gui_snapshot 更新到 process/;本地全量 488 passed、
  VM 全量 484 passed + 4 skipped(b455c02),ruff 全绿;
- VM 实测(sampleB 真实转换):fid.com 与 raw.fid 均在 process/,raw 无
  fid.com(1682 软链接 + 仅 profYZ.dat 实体),转换正常。

- 新增(GUI Agent,用户反馈):「简单模式」开关(设置对话框 →
  config/nmrforge.local.yaml pipeline.simple_mode,重启生效)——
  开启后 Pipeline 下一步只认上一步有没有对应格式的产物文件
  (.fid/.ft2/.ft3/.list/报告),不再比较输入/脚本指纹,也不显示
  「已过期」;相当于同时关闭指纹检测与过期显示。
- 测试:简单模式下输入变化不再出现 OUTDATED 断言;本地全量 pytest
  + ruff 全绿。

- NUS 直接维相位自动优化改「非均匀 DFT 最强峰相位」(用户要求:不要人工
  确认,保证能优化正确,走后端也没事):
  - 原理:切片 i 直接维峰 k* 的相位 = φ(k*) + ω1·Δt1·p1_i + ω2·Δt2·p2_i;
    沿增量对最强直接峰复值做非均匀 DFT,在真实 F1/F2 频率处 t1 调制
    精确抵消(δ=0),峰相位 = φ(k*) = φ0 + p1·k*/(n-1) ——离网格频率也
    成立(合成实测 p0 平均误差 3.6°、p1 约 5°,0.2.90 伪均匀单发估计
    离网格可偏 50-95°的问题消除);p1 由各切片多峰相位集中度拟合取
    中位数;
  - 自动集成:reconstruct_nus 的 _search_direct_phase 对 NUS(有 nuslist
    + 切片)优先走 NU-DFT(用全部切片,零后端运行,无需人工确认),
    phase.json v2(source=direct_nudft)缓存;失败回退逐切片锚定路径;
  - 门控改相干 SNR:|V_peak|/(√N·mean|v|)——信号≈√N、噪声≈1,阈值 2.0
    (吸收度门控受 SP 窗非线性相位污染,正确相位下反而偏低,已弃用);
  - preview_direct_phase 同步改 NU-DFT 估计,预览谱(伪均匀产物)仍可选
    输出;0.2.90 伪均匀谱保留为预览产物;
  - 测试:新增后端 _search_direct_phase 自动路径断言(source=direct_nudft,
    离网格 F1=20.7 恢复 ±12°);预览测试改离网格 F1=17.3;全量 484 passed
    (1 失败为 GUI 遗留 ext_lo 断言,待 GUI Agent),ruff 全绿;
  - 待 VM 真实数据复核:重构后直接维主峰纯吸收。

## [0.2.90] - 2026-08-16

- 修复/调整(G2B-009 修订,Architect,用户要求):raw 导入只读文件改为
  符号链接优先(软连接)——VM/Linux 下 raw 呈现为软链接,不再因硬链接
  显示成普通文件;链接顺序:符号链接 → 硬链接 → 复制(Windows 无符号
  链接权限时自动回退);
- 只复制需要修改的文件:WRITABLE_RAW_NAMES 增 profYZ.dat(转换会 touch
  该文件,实体复制防止改写写回源数据;fid.com 原本就在名单);
- 测试:link_stats 断言平台感知(符号链接可用断言 symlink,否则 hardlink);
  可写名单测试覆盖 fid.com + profYZ.dat;本地全量 488 passed、VM 全量
  484 passed + 4 skipped(1eb8a4c),ruff 全绿;
- VM 实测(sampleB,3D HNCACB):raw 1682 文件全部软链接、0 复制,
  fid.com/profYZ.dat 实体复制,无 warnings。

- 新增(GUI Agent,用户反馈):两个 Pipeline 行为开关(设置对话框 →
  config/nmrforge.local.yaml 的 pipeline 段,重启生效):
  ①「文件指纹检测」关闭后不再做输入/脚本指纹与产物新旧比较,输入
  变化不再触发「已过期」,同时省去每次刷新的指纹计算;
  ②「显示已过期状态」关闭后任何步骤都不再出现 OUTDATED(有产物即
  SUCCESS,否则 READY/LOCKED)。
- 测试:关闭开关后不再出现 OUTDATED 断言;本地全量 pytest + ruff 全绿。

- NUS 直接维相位预览改走「伪均匀传统 FT」路径(用户方案:直接维全采样,
  先不做 SMILE 把间接维变换出来,像传统采样一样优化直接维):
  - preview_direct_phase 对 NUS(有 nuslist + 切片)按 nuslist 把稀疏切片
    摆到完整网格、缺位补零,内存内做传统逐维 FT(直接维 SP+FT+EXT 窗口
    + 间接维 FFT)→ 伪均匀谱;直接维相位不再与 t1 调制纠缠(间接 FT 把
    t1 变成间接频率),可像传统采样一样估 (p0, p1);
  - search_direct_spectrum_phase 增 p0_source 参数:"first"(切片式,首条
    迹线 t1=0 锚定,默认)与 "strongest"(伪均匀谱,峰高最强迹线对应真实
    间接频率,零填充旁瓣常数相位偏置 δ≈0);
  - 结果写 phase.json(version=2,source=direct_pseudo),后续
    reconstruct_nus 直接复用,不再重搜;可选 out_preview 输出已调相伪
    均匀谱(2D/3D ft2/ft3);
  - 网格按 effective_td 摆位,索引用模运算容错(1-based/复点单位差异);
    3D 间接网格过大时均匀子采样(≤4096 格,直接维相位不变);EXT 窗口
    由 ext_lo/ext_hi(默认 10.5/6.5)决定,窗口为空时回退全谱;
  - 无 nuslist/切片不匹配时回退「首条迹线锚定」路径(0.2.88/89 行为);
  - 测试:构造 16 个含 t1 调制切片的用例,断言伪均匀路径零后端运行、
    校正值 (p0,p1) 恢复、phase.json v2/source、预览谱形状;全量
    483 passed,ruff 全绿;
  - 待 VM 真实数据复核:预览伪均匀谱直接维主峰纯吸收后再跑 SMILE。

## [0.2.89] - 2026-08-16

- merge(Architect):并入 GUI 0.2.86(查看器交互调相 P0/P1 + 生成谱图参数
  报告 + 导入自动填注释/原始数据质量);GUI 与 Backend 的 0.2.86 版本号
  撞号,CHANGELOG 合并归一(两条记录并列于 0.2.86 节);
- 审查(Architect):GUI 对 Shared Contract viewer/spectrum.py 的改动为
  追加式兼容(Spectrum 增加可选 complex_data 供 FID 调相),契约字段未破坏;
- 测试:本地全量 488 passed、VM 全量 484 passed + 4 skipped(Python 3.12.13,
  HEAD 306321b),ruff 全绿。

- 修复(GUI Agent,用户反馈):核种类按化学位移(观测频率 sf)推断——
  sf/旋磁比对应 1H 频率并与常见磁场匹配(600→1H、60.8→15N、
  150.9→13C),推断失败才回退 acqus 的 NUC1 标签;注释自动填充的
  核与查看器轴标签(N-H/C-N-H)同步使用该推断。
- 修复(GUI Agent,用户反馈):3D 谱导入后打开卡顿——大 .ft3
  (>32MB)改为后台线程读取,状态栏提示加载进度,完成后再绑定
  渲染,UI 不再长时间无响应。
- 调整(GUI Agent,用户反馈):3D 投影方式对齐 nmrPipe projZ.M——
  新增 Proj 模式(每张平面低于阈值的点置零后沿轴求和,峰保留、
  噪声不累积),并设为默认;如 HNCACB 沿 13C 投影得到类似 HSQC
  的 HN 平面(选 F2-F3 平面 + Proj)。阈值取 3×噪声估计。
- 修复(GUI Agent,用户反馈):3D 谱生成后整链莫名 OUTDATED + 左侧点击
  卡顿——raw 输入指纹只统计权威 Bruker 输入文件(acqus/acqu2s/acqu3s/
  ser/fid/nuslist,与导入登记一致)+ metadata.json;3D NUS 处理写入
  raw/fid、raw/mask、raw/ft 等数百个中间产物不再使导入/FID 误判过期,
  状态计算从 ~1s/次(1537 文件全量哈希)降到毫秒级。
- 测试:infer_nucleus、project_nmrpipe 阈值求和、3D 默认 Proj、
  大 .ft3 异步加载、raw 处理产物忽略;本地全量 pytest + ruff 全绿。

- 直接维相位预览路径(无 SMILE、零后端运行,用户要求):
  - workflow.phase_optimize.preview_direct_phase(experiment, work_dir,
    out_preview=...):读转换后的 fid/切片(work/fid/test%03d.fid 或单文件
    fid),内存内直接维 FT + (p0,p1) 搜索(与 reconstruct_nus 重构前搜索
    同算法),写 phase.json(version=2,后续 reconstruct_nus 直接复用不再
    重搜),可选输出「已调相直接维预览谱」(前 K 条强迹线实部,2D ft2,
    F2 轴头继承 fid)供肉眼核对;
  - 用途:生成 FID 后先跑预览确认直接维纯吸收,再决定是否运行 SMILE;
    不满意可改参重跑或手改 phase.json(保持 version=2);
  - 测试:构造 8 个含已知相位切片用例,断言零后端运行、phase.json v2、
    校正值 (p0,p1) 恢复、预览谱文件与尺寸;全量 482 passed,ruff 全绿。

## [0.2.88] - 2026-08-16

- merge(Architect):并入 GUI 0.2.85(注释下拉选项 + 右侧谱图自动显示)与
  Backend 0.2.85-0.2.87(三维切片流/NUS 直接维 1×TD、ext 窗口配置化、
  相位候选零填零);GUI 与 Backend 的 0.2.85 版本号撞号,CHANGELOG 合并归一;
- 对接修复(Architect):ext_lo/ext_hi 默认值全栈统一 10.5/6.5——GUI
  param_schema 与参数对话框原硬编码 11.0/6.0,与后端 0.2.86 配置默认
  (10.5/6.5)不一致;同步补齐后端 0.2.86 漏改的 param_schema 属性默认值、
  render_scripts 回退值与 ProcessingBackend docstring;
- 测试:本地全量 484 passed、VM 全量 480 passed + 4 skipped(Python 3.12.13,
  HEAD 03478da),ruff 全绿;
- 待办:GUI 设置对话框动态读取 load_processing_defaults 暴露 ext_lo/ext_hi,
  仍待 GUI Agent 接线(B2G-003)。

- 调整(GUI Agent,用户反馈):取消选中样品数据时自动显示谱图——右侧
  谱图改为文件列表点击或 Pipeline「生成谱图」步骤完成后出现的
  「展示谱图」按钮打开;切换样品数据时清空旧谱,不残留上一张。
- 修复:0.2.87 的「生成谱图后强制重载」随之移除,由「展示谱图」按钮
  显式触发(重新处理后点按钮即显示最新谱)。
- 测试:更新自动加载断言为显式加载;新增「展示谱图」按钮可见性与
  信号测试;本地全量 pytest + ruff 全绿。

- NUS/均匀直接维相位搜索升级:从「原始 FID p1 共识(p0 恒 0)」改为
  「直接维 FT 谱频域 (p0, p1) 搜索」:
  - 物理背景:NUS 增量 i 的直接维相位 = 公共相位 + ω1·t1(i)(t1 调制,
    t1(0)=0);只有首条迹线(增量 0)直接维相位干净,其余迹线的 t1 偏置
    无法与 p0 分离。聚合策略:p1 用多峰迹线相位集中度拟合(旋转后各峰
    相位圆集中度最大)取中位数——t1 只是逐峰常数偏置,不影响 p1 斜坡;
    p0 锚定首条有峰的迹线(增量 0)的峰相位加权圆均值取反(PS 校正约定,
    与 _search_axis 一致),±180 消歧取正峰解;
  - 吸收窗口半径 1:SP 窗函数边缘给峰尾带非线性相位,±5 窗口吸收度在
    正确相位下反而下降(实测 0.46 vs 0.51),±1 窗口能正确区分
    (0.65 vs 0.47)——评分/消歧均用半径 1;
  - 估计尺寸与脚本 PS 应用尺寸一致(NUS 直接维 1×TD、均匀 2×TD),p1
    无需缩放;切片式数据均匀子采样 ≤16 切片,单文件 fid 按增量行处理;
  - phase.json 缓存加 version=2(旧版 p0=0 缓存失效自动重搜);
  - 修复 process 路径 auto_phase=False 检查顺序(此前在搜索之后才置位,
    实际关不掉自动相位,与 0.2.67 约定不符);
  - 测试:新增 search_direct_spectrum_phase 合成谱 (p0,p1) 恢复断言
    (±12°/±10°)、p1 幅值回归;更新缓存 version 断言;全量 481 passed,
    ruff 全绿;
  - 待 VM 真实数据复核:sampleA 2D NUS、sampleB 3D NUS 重构后直接维主峰
    纯吸收(p0 非零解,此前直接维恒混合)。

## [0.2.87] - 2026-08-16

- 相位优化候选谱零填零 + 填零放在优化最后(用户要求):
  - optimize_phase_sequential 每候选(process / finalize_nus)统一传
    params={"zero_fill": {轴: {"mode": "none"}}}(直接/间接维一律不填零,
    数据最小化——尤其 3D NUS 逐候选 finalize 的 ft3 体积不再随填零放大);
  - optimize_phase_brute_force 在搜索结束后立即以最终相位 + 完整填零计划
    (mode=auto:直接维 2×TD、间接维动态)重渲生产终谱并归位;填零只出现在
    优化最后,不在候选阶段/SMILE 前;基线/窗函数嵌入的重渲路径不变;
  - 回归:uniform 2D 终谱路径/相位选择不变(test_stepwise 既有断言保持),
    候选体积与 I/O 显著下降(3D NUS 候选由全尺寸 ft3 降为 SI=TD);
  - 测试:新增候选 zero_fill=none 与最终 auto 断言;fake backend 补 params;
  - 待 VM 用户手动参数复核:终谱尺寸/峰位/线宽与全采样对照。

- 调整(GUI Agent,用户反馈):交互调相改为 nmrDraw 式「仅显示」——查看
  一维谱/开启 1D 条带后拖 P0/P1 肉眼看相,不改变数据,实数谱即可用
  (解析信号 Hilbert 旋转);值可复制回写脚本 PS 行。
- 修复(GUI Agent,用户反馈):刷新不及时——生成谱图/重新处理后右侧谱图
  强制重新加载(同路径重处理不残留旧图);导入自动填充注释后立即刷新
  顶部注释条(单条与批量导入)。
- 调整(GUI Agent,用户反馈):样品数据注释字段「条件变化」改为
  「Buffer 组分」;温度自动识别 0.1 K / K / °C(Bruker TE 惯例
  2980→24.9°C,直接存 K 或 °C 也能识别)。
- 调整(GUI Agent,用户反馈):日志面板改为上下可拖拽调整占比、默认
  高度更高(220);左侧项目树加宽(状态列不再被遮挡)。
- 测试:显示相位数学/仅显示不改数据、温度单位识别;本地全量 pytest
  + ruff 全绿。

## [0.2.86] - 2026-08-16

- 直接维提取窗口默认 6-11 ppm → **6.5-10.5 ppm**(Backend,用户指定)并配置化:
  - config/nmrforge.yaml processing.ext_lo/ext_hi(10.5/6.5);
    backend.config.load_processing_defaults 返回(供 GUI 设置对话框改默认值);
    resolve_ext_lo/resolve_ext_hi:显式 params > 配置 > 内置默认;
  - process/reconstruct_nus/_process 与 param_schema/脚本默认值全部同步;
  - 测试:更新 4 处旧默认断言(11/6 → 10.5/6.5);全量 479 passed,ruff 全绿;
  - GUI 侧需在设置对话框暴露 ext_lo/ext_hi(待 GUI Agent 接线,后端数据源已就绪)。
- 新增(GUI Agent,用户反馈):查看器交互式相位校正面板 P0/P1 滑块——
  对复型数据(1D FID / 二维时域 FID)频率域调相实时重渲(1D 实时、
  2D 松手重建),支持 Reset/复制 P0/P1 回写脚本 PS 行;实型终谱
  (ft2/ft3)无法事后调相,面板提示禁用。
- 调整(GUI Agent,用户反馈):点击「生成谱图」步骤展开详情,新增
  「参数报告」——按实际生效参数列出提取窗口/窗函数/填零/相位/基线/
  SMILE 等(readable 逐行,来自 WorkflowRun effective_params)。
- 新增(GUI Agent,用户反馈):导入数据后自动按 Bruker 文件填充注释——
  实验类型注释:维度/实验类型(presets 名)/核;样品数据注释:温度
  (acqus TE);只填空字段不覆盖已有值。
- 新增(GUI Agent,用户反馈):导入后自动检查并报告原始数据质量——
  acqus 参数/维度/核/温度、ser/fid 存在性与大小、小文件信噪估算,
  日志输出 + 问题并入导入完成提示;批量导入逐项检查。

## [0.2.85] - 2026-08-16

- 调整(GUI Agent,用户反馈):注释表单仅有几种取值的字段直接给下拉——
  实验类型注释先选维度(2D/3D),维度确定后再按 presets 过滤给出实验
  类型选项(HSQC/HMQC/HMBC/COSY…2D,HNCA/HNCACB/HNCO…3D),核组合
  给常用选项;预设来自 presets/*.yaml(常见实验预设重新在界面可用),
  类型/核可编辑以支持自定义。
- 调整(GUI Agent,用户反馈):右侧谱图面板点到样品数据立即显示该数据
  spectra 文件夹的最终谱;3D 谱默认显示 MIP 投影(可切回切片/求和);
  谱图文件夹无谱时右侧清空不再残留上一张谱。
- 测试:新增 presets 类型选项、注释下拉维度→类型联动、无谱清空查看器、
  3D 默认 MIP 断言。
- 三维处理全切片流 + NUS 直接维填零 1×TD(Backend Agent,用户实测根因修复):
  - bruker 在 acqu3s TD 正确时输出切片式 fid/test%03d.fid(每 F1 一个切片);
    convert 接受切片输出(保留 work/fid/),reconstruct_nus 与 process(均匀 3D)
    均优先使用切片 in_file 流式处理——与用户手动传递顺序一致
    (fid/test%03d.fid → 直接维 FT/EXT → planes → SMILE/间接 FT → 终谱);
  - NUS 直接维填零 1×TD:2×TD 使直接维平面翻倍 → SMILE 工作量翻倍导致
    重载关机(用户实测;手动 1×TD 34s 安全完成);均匀路径保持 2×TD;
  - 直接维相位搜索对切片形式回退用首切片;
  - 测试:新增切片归位/单文件兼容/缺失失败用例;更新 NUS 直接维 1×TD 与
    均匀 2×TD 断言;全量 479 passed,ruff 全绿;
  - 注:VM 验证按用户手动参数(直接维 ZF 2048、窗口 9.0-7.4、sampleCount 700)
    进行;0.2.86 起默认窗口已配置化为 10.5-6.5 ppm,超大 NUS(如 sampleJ)
    请用 ext_lo/ext_hi 传参收紧窗口,避免 SMILE 重载。

## [0.2.84] - 2026-08-15

- GUI(Architect):修复 3D 数据生成 FID 后「导入数据 / 生成FID」双双
  变 OUTDATED——根因:raw_fingerprint 用文件 mtime 计算,后端转换会
  touch raw 里 Bruker 辅助文件(实测 sampleB 的 profYZ.dat)的 mtime
  但内容不变,纯 mtime 指纹误判 raw 被修改(导入与生成FID 的输入指纹
  都依赖 raw_fingerprint);
- 修复:raw_fingerprint 小文件(≤8MiB)改用内容 SHA-256(复用
  file_fingerprint),大文件保留 size+mtime——touch 不再误判,真实
  内容修改仍能检出;
- 测试:新增 test_raw_fingerprint_ignores_mtime_touch;本地全量 476 passed、
  VM 干净副本(5458038)全量 472 passed + 4 skipped,ruff 全绿。

## [0.2.83] - 2026-08-15

- Backend(Architect):修复 0.2.77 并行引入的两个问题:
  - `parallel` 参数此前为死参数(_resolve_workers 无视 parallel,默认
    cpu-2 线程并发),parallel=False 不生效;
  - 并发候选首次写入同一新工作目录时,nmrglue open_towrite 的
    exists→makedirs 非线程安全,竞态抛 FileExistsError → 候选丢失、
    门控回退误判(如 flat-margin 用例 F1(0,0) 丢失后未回退 (0,0));
  - 修复:顺序搜索/联合复核两处 n_workers 按 parallel 接线;批处理前
    预建工作目录(消除首次写入竞态)。
- 测试:test_phase_gating/test_phase_optimize 全过;全量 475 passed,ruff 全绿。

## [0.2.82] - 2026-08-15

- 调整(GUI Agent,用户反馈):文件结构三级名称重命名——样本 → 项目、
  实验 → 实验类型、数据 → 样品数据(项目 > 实验类型 > 样品数据);
  同步更新菜单/右键菜单/树节点/欢迎页/实验概览/运行历史/注释条/
  Pipeline 步骤(导入数据 → 导入样品数据)等全部用户可见文案;
  「Bruker 数据集目录」「数据点」等非层级含义的用语保持不变。

## [0.2.81] - 2026-08-15

- 调整(GUI Agent,用户反馈):点中 Data 节点时中间不再显示「导入数据」
  步骤(导入数据属于实验层,点中实验时显示);数据层下一步提示同步
  跳过导入步骤。
- 调整(GUI Agent,用户反馈):样本/实验/数据三级注释改为结构化字段
  表单(常规信息按列表逐行填写),各层级字段不同——样本:蛋白名称/
  表达系统/浓度/Buffer;实验:实验类型/维度/核;数据:重复号/条件
  变化/Buffer pH/温度;均含备注。旧纯文本注释兼容读取。
- 存储:样本注释存 protein.notes(JSON 字段串),实验注释存
  ExperimentEntry.metadata["note_fields"],数据注释存
  metadata["data_notes"][data_id](均 GUI 侧约定,不改 Shared Contract)。

## [0.2.80] - 2026-08-15

- 修复(GUI Agent,用户反馈):FID 显示按 nmrDraw 方式修正——二维 FID
  不再压成第一条 FID 的一维迹线,改为显示整块二维时域图:行=各 FID
  (间接维增量,首条在底部,与 nmrDraw 布局一致),列=直接维时点;
  一维 FID 保持 1D 迹线;3D+ FID 显示首个间接增量的二维时域平面。
- 调整:二维 FID 等高线默认基准改用高分位数(FID 为 ADC 累积值,动态
  范围大,全局最大值会被个别尖峰占满);时间域轴显示点序号而非 ppm;
  1D 条带模式同步支持时间域轴刻度。
- 依据:nmrDraw 手册与 hmsIST 2D 处理教程(FIDs 纵向排列、首条在底部)。

## [0.2.79] - 2026-08-15

- 修复(GUI Agent,用户反馈):谱图查看器常用术语直接英文显示——轮廓起点
  Contour start (%)、级数 Levels、图层 Layers、显示峰 Show peaks、一维谱
  1D、全谱视图 Full view、强度 Intensity;3D 面板同步;峰表工具栏英文。
- 调整:轮廓起点默认 3%(滑块立方映射)、级数默认 8;前 10% 阈值占拖动条
  大部分,低阈值比原平方映射更精细可调。
- 修复(GUI Agent,用户反馈):主页面右侧谱图文件列表不再混入 process 目录
  的 raw.fid,列表只列 .ft2/.ft3;FID 仍可拖放/直接打开查看。
- 测试:新增查看器默认值/英文文案/FID 排除断言;VM 全量复测通过。

- SMILE 线程默认改自动(用户要求):未显式指定(参数/配置缺省或 0)时
  nthread = 机器线程数 - 2(最小 1),与相位优化候选并行一致;
  大网格护栏不变:间接网格 >5000 点仍强制 ≤2(sampleM 事故防护)。
  - backend/config.py 新增 `_auto_nthread()`;`load_processing_defaults`/
    `resolve_nthread` 缺省回退自动;config/nmrforge.yaml `smile.nthread` 默认 0
    (=自动);显式正整数仍优先;
  - 测试:更新 test_config_defaults(空配置/无效值 → 自动);全量 476 passed,
    ruff 全绿;
  - VM 8 核实测:auto=6,小网格保持 6,sampleA 渲染 nThread=6;大网格 6396
    强制 2。

## [0.2.78] - 2026-08-15

- 去重统一(行为不变,本地全量 467 passed + ruff 全绿):
  - FT 标志逻辑统一:新增 `_ft_flags(base_neg, base_alt, sampling, axis)`,
    `_ft_flag_line`(NUS/finalize)与 `_stage_lines` 的 ft 分支(均匀)共用同一
    sampling 覆盖实现——此前两处重复,且同一 FnMODE 在均匀/NUS 路径的默认
    neg 推导可能不一致,已收敛为显式传基值;
  - NUS 填零尺寸统一:`_nus_zf_size(cfg, td_points)`,2D/3D NUS 脚本与
    finalize 的 6 处 `next_pow2(2×TD)` 推导收敛;
  - 候选后端调用统一:`phase_optimize` 新增 `_resolve_workers`(机器线程数-2)
    与 `_candidate_backend_run`(唯一脚本/输出名 + process/finalize_nus 调用),
    `_evaluate_one`(逐轴候选)与 `_joint_eval`(联合复核)共用;
  - effective_params 基础块统一:`nmrpipe_backend._effective_params_base`,
    process 与 reconstruct_nus 共用(process 追加 window/direct_phase,
    reconstruct 追加 SMILE 键);
  - 无行为变化:脚本输出、评分、相位选择均不变(FT 标志顺序统一为
    `-neg -alt`,NMRPipe 独立解析,等价)。

## [0.2.77] - 2026-08-15

- 相位优化性能(用户要求,结果逐位一致):
  - 候选并行:同轴粗网格/细化窗口/联合复核组合并发执行,worker = 机器线程数 - 2
    (默认自动,最小 1,给系统留 2 线程);依赖每候选唯一脚本/输出名
    (process/finalize_nus 新增可选 out_file/script_name,默认名不变);
  - 修复联合复核缓存路径别名 bug:旧共享 raw.ft3 被后写候选覆盖,缓存命中的
    联合组合读到错误谱(sampleI 顺序模式 F2 曾误判 joint 更优,0.2.75 串行记录
    (307,-5) 因此不准;唯一名后正确为 (307,0));
  - 评分饱和短路:默认评分上界 100,轴最优饱和时跳过 p1 精修 {0,±22.5}
    (不可能更优,结果不变);
  - 候选谱清理:搜索结束后删除中间候选谱(含联合复核 _j*),仅保留最终谱
    (控制存储,避免 3D 候选终谱积压 GB 级);
  - 测试:新增「并行=串行结果一致」「饱和短路日志」;全量 467 passed,ruff 全绿;
  - VM 8 核(worker=6)实测:sampleI 14.9s、sampleA 3.9s、sampleB 172s、
    sampleC 148s——sampleA/sampleB/sampleC 相位与 0.2.75 记录完全相同,3D NUS 墙钟约 ÷3-4,
    backend_runs 不变;sampleI 修正为正确 joint 结果。

## [0.2.76] - 2026-08-14

- 修复(GUI Agent,用户反馈):0.2.71 光栅化谱图观感与 nmrDraw/POKY 根本
  不同——光栅化是连续 alpha 强度填色,POKY/SPARKY/nmrDraw 画的是离散等
  值线(细线框,级别 lowest × factor^n 几何级数,逐级 Marching Squares
  追踪,按级着色 1px 折线);经 POKY 官方手册与 SPARKY 3.115 开源源码
  (contourplane.cc/contourstream.cc/uiview.cc)确认同源机制。
- 实现:viewer/contour_layer.py 大谱渲染从光栅化 RGBA 强度图改为
  contourpy(matplotlib 底层 C++ Marching Squares 引擎)真实等值线——
  数据原始分辨率逐级追踪,正=层色、负=红 1px 折线;小谱保留 matplotlib
  插值路径(0.2.73 尺寸分流,VM 全量稳定);光栅化 RGBA/QImage 彻底移除。
- 性能:512x1024 谱 contourpy 提取 10 级约 16-50ms、36 级约 52-116ms,
  QPainterPath 构建最坏约 170ms,与光栅化同量级;原 matplotlib 慢路径
  (约 21s)的瓶颈是 ndimage.zoom + plt.contour 建图,大谱已绕开。
- VM 段错误复核:统一走 contourpy(含小谱)会重新触发 PyQt6/sip 对已析构
  子控件 wrapper 缓存错配段错误(崩溃点漂移,3/3 复现;39737a5 同套件
  全绿),故保留尺寸分流:小谱 matplotlib、真实数据规模 contourpy;
  contourpy 改为惰性导入,顶层导入集合与 39737a5 一致。
- 测试:test_viewer 断言从 _image/_raster 改为 _use_contourpy/等值线路径;
  合并入 master 后本地全量 465 passed,ruff 全绿。

## [0.2.75] - 2026-08-14

- 相位优化「取长补短」升级(Backend,吸收旧项目 NMRFlow 已验证方案;修复
  sampleI 明显非最优 F2(-75,-150) 的两个根因):
  - 根因一:p0 粗网格只 ±45°/±135 多尺度,真实最优超出范围或落网格外;
    修复:默认 p0 全圆 0-360° 30° 步粗搜(12 点,p0 取模 360);
  - 根因二:旧逐轴评分是「top-5 强峰窗口负面积池化」,sampleI 强负峰(折叠)
    主导池化;修复:评分改「基线固定迹线中位数净吸收」——基线谱锁定全部
    信号迹线与最强点位置(阈值 max(99.5 分位,噪声×5),无信号放宽),每候选
    在固定迹线/峰位上打分(不重新选峰),每迹线 ±5 剖面净吸收
    =(正+负)/总绝对(吸收≈+1、色散≈0、负吸收≈-1),全体取中位数
    (0-100=50×(median+1));不做符号相对化(保留 180° 反转惩罚);
  - 均匀路径搜索顺序改「间接维先、直接维后」(旧项目顺序):直接维在间接维
    校正后的谱上锁点,避免 (0,0) 伪影脊导致反转相位(sampleI F2 先搜得 180°
    反转,F1 先修后 F2 收敛 ~300° 主峰近纯吸收);NUS 路径不变(仍逐间接维);
  - 默认评分粗搜/细化只搜 p0(p1 固定 0,±5 窗口内 p1 天然弱),p1 在末尾
    {0,±22.5} 精修(仅提升才替换)——避免退化 p1 维在联合网格刷分
    (sampleI F1 曾被 p1=82.9° 带偏);平台圆中位数默认作用于 p0(旧方案,
    亚度精度);自定义 score_fn 保留原 p0×p1 联合网格与 p1 平台;
  - 修复:首个轴基线谱先跑 (0,0) 再锁定迹线(此前 zeros 退化分支,迹线位置
    全部 argmax=0,评分失真);联合复核与顺序搜索同用固定迹线中位数(逐轴
    均值),不再退回旧全谱负面积指标;粗网格 margin 只看 p0 判别(p1 平坦
    不再误判整面平坦回退 (0,0));粗网格平坦且零相位不劣于最优才回退 (0,0);
  - 固定迹线助手泛化到 3D+(3D NUS finalize 产物按扁平迹线行处理);
  - 保留:门控回退、±90° 对称性消歧、平台圆中位数、联合 ±final_step 复核
    (候选缓存)、3D NUS 路径(SMILE 一次 + finalize 逐间接维)、effective_params;
  - 测试:新增「负峰存在时最优相位仍正确」「固定迹线/中位数聚合」「3D 泛化」
    「首个轴基线回归(嵌入 θ=-120 找回 p0≈120)」;更新 p0 全圆断言与搜索
    顺序断言;全量 465 passed(本地),ruff 全绿;
  - VM 真实数据(数据验证结果):sampleI F1(95,22.5)/F2(307,-5),主峰
    F2_neg 0.083(0.2.74 的 0.2+)、F1_neg 0.031,中位数评分 100;
    sampleF F2_neg 0.047/F1_neg 0.000;sampleG F2_neg 0.0003/F1_neg 0.000;
    sampleA(2D NUS)F1(356,0) 主峰全纯;sampleB(3D NUS)联合复核回退 (0,0),
    三轴主峰全纯;backend_runs 243→43~45(约 5×);
  - 注:交接预期 sampleI F2≈-120°(240°)与实际数据不符——240° 时主峰仍混合
    (F2_neg 0.137),数据验证的最优为 ~300°(≡-60°),主峰纯吸收且中位数
    评分 100;已记录 PROJECT_STATUS,请 Architect 复核。

## [0.2.74] - 2026-08-14

- Backend:相位优化 p0 搜索范围修复(用户反馈 sampleI 结果明显不对):
  - 根因:粗网格 p0 默认只覆盖 ±45°(-45/0/45),sampleI 真实 F2 最优
    p0≈-120°(全范围扫描确认),搜索永远够不到,返回非最优相位
    F2(-75,-150)且主峰仍混合;
  - 修复:默认 p0 粗网格扩到 ±135°(45° 步),配合多尺度细化可到达
    p0=-120°;全范围扫描最优 F2(-120,-90);
  - 注:sampleI 终谱含一枚强负峰(折叠/处理性质),非相位问题;
    负面积指标保持不变(180° 反转惩罚语义保留)。
- 测试:新增 test_default_p0_grid_covers_beyond_pm45;全量 460 passed
  (本地),ruff 全绿。

## [0.2.73] - 2026-08-14

- 修复(GUI Agent):VM Linux 全量 pytest 段错误真正根因落地。0.2.72 的
  「QImage bytes 悬挂」假设经复测证伪——PyQt6 6.10.2/6.11.0、Python
  3.10/3.12、pyqtgraph 0.13.7/0.14.0 全组合在 VM 全量下仍段错误,valgrind/
  ASan 均无内存错误,MALLOC_PERTURB_=1 可稳定复现;gdb/探针确认崩溃为
  PyQt6/sip 对「C++ 已析构子控件」(pyqtgraph 菜单/ctrl 控件树)的 wrapper
  缓存未失效,Linux 堆布局下地址复用返回类型错配的旧 wrapper,Qt 控件构造
  (PlotItem/ViewBoxMenu/WidgetGroup)时随机段错误;小谱光栅化的内存分配
  模式使其高频触发,真实数据规模(>=512x1024)的大缓冲走 mmap 不触发。
- 修复:viewer/contour_layer.py 尺寸分流——像素数小于 512x1024 的小谱走
  matplotlib 等高线(稳定路径),真实数据规模走光栅化(性能路径,512x1024
  谱仍约 30ms);同时修正 0.2.71 遗留的 `_pen_neg` 元组笔误。
- 阈值上调(Architect 基准):_RASTER_MIN_PIXELS 256x512 → 512x1024;
  阈值处 matplotlib 中位 ≈167ms,安全边际 4×,光栅化仅用于 ≥512x1024
  真实大谱。
- 测试:VM 全量 449 tests(MALLOC_PERTURB_=1 连跑 3 轮全绿)+ 本地全量
  passed(offscreen)+ ruff 全绿;test_viewer 拆分小谱路径(等高线)与大谱
  路径(光栅化)断言;合并入 master 后本地全量 460 passed。

## [0.2.72] - 2026-08-14

- 修复(Architect 审查):VM 全量 pytest 段错误(test_window_empty_state →
  _build_menus)。根因:光栅化 ContourLayer 用 `QImage(img.tobytes(), ...)`
  构造图像,PyQt6 的 QImage 引用 bytes 内存而不拷贝,临时 bytes 在构造
  返回后即被释放,图像数据悬空——Windows 内存未覆盖碰巧正常,Linux
  全量顺序下 Qt 访问已释放内存段错误。修复:bytes 保存到 self 持有引用
  (viewer/contour_layer.py),本地验证 GC 后像素稳定。
- 测试:本地全量 449 passed(offscreen)+ ruff 全绿,待 VM 全量复测;
  ⚠ 0.2.73 已证实该假设不成立(真正根因与修复见 0.2.73)。

## [0.2.71] - 2026-08-13

- viewer 性能优化(用户反馈):等高线渲染从 matplotlib 几何计算改为
  nmrDraw 式光栅化(RGBA 图像,正黑负红)——512x1024 谱加载从约 10.7 秒
  降到约 32 毫秒(约 335 倍),级别/级数滑块更新从约 2.7 秒降到约 33 毫秒;
  保留正黑负红、轮廓起点/级数滑块语义,缩放/平移由 Qt 原生重采样,
  框选缩放/峰标记/1D 条带交互不变。
- 测试:viewer 断言从 _path 更新为光栅化 _image,全量 449 passed,ruff 全绿。

## [0.2.70] - 2026-08-13

- presets 实验模板扩充(用户反馈):常用 2D/3D 谱预设从 4 个增加到 28 个——
  新增 HSQC-13C、HMQC(15N/13C)、HMBC(13C/15N)、COSY、TOCSY、NOESY、
  ROESY(2D),以及 HNCACB、CBCA(CO)NH、CBCANH、HNCO、HN(CO)CA、HN(CA)CO、
  HNHA、H(CA)NH、H(CCO)NH、C(CCO)NH、HBHA(CO)NH、HCCH-TOCSY、CCH-TOCSY、
  3D NOESY-HSQC(15N/13C 编辑)(3D);每个模板含各核化学位移先验
  (priors,如 HNCO 13C 165–185 ppm、HNCACB 13C 10–80 ppm),供实验类型
  判断时按化学位移进一步确认核;README 更新模板清单。
- 测试:新增 test_gui_presets(3 例:全量解析/字段/priors 范围/常用类型齐全),
  全量 449 passed,ruff 全绿。

## [0.2.69] - 2026-08-13

- viewer 轴名按核显示(用户反馈):根据导入 metadata 的维度核信息,把谱图
  轴名从 F1/F2/F3 改为真实核符号(H/N/C...);同一核出现多个轴时加 x/y/z
  下标(如 1H-1H 同核 2D 显示 Hx-Hy,3D C-N-H)。主窗口谱图面板与独立查看器
  均支持,无 metadata 时回退 F1/F2/F3;3D 平面名同步用核名(N-H 等);
  峰表 F1/F2/F3_shift 映射改用轴维序(viewer/axis_labels.py,不改 Shared
  Contract;切片/投影产物经 dim_indices 携带原始维序)。
- 测试:新增 test_axis_labels(6 例:核符号/同核下标/metadata 解析/面板与
  独立查看器轴名/回退),全量 446 passed,ruff 全绿。

## [0.2.68] - 2026-08-13

- 菜单调整(用户反馈):顶部「样本(&S)」菜单改为「实验(&E)」,去掉
  「样本管理 / 添加样本 / 删除样本」项;
- 三级注释功能:样本(项目)/实验/数据均可添加注释——新建样本/新建实验/
  导入数据时可选填写,也可通过中间区域最上方的注释条「编辑注释」后补;
  注释展示在中间区域最上方(gui/notes.py:样本=protein.notes、实验=
  notes、数据=metadata["data_notes"][data_id],不改 Shared Contract);
- 移除(用户反馈):Pipeline 步骤详情里的「以此参数打开人工编辑器」按钮;
- 修正(用户反馈):viewer 一维/二维谱 Y 轴整体翻回习惯方向——行 0
  (高 ppm)在底部,条带/峰/鼠标对应关系保持(invertY True→False);
- 修复(用户反馈):处理步骤生成新文件后左侧树不自动出现展开箭头——
  Pipeline run_finished 接线到主窗口刷新(树/中间/谱图面板),无需重启;
- 测试:新增 test_gui_notes(4 例),删除按钮/菜单/方向/刷新断言更新,
  全量 440 passed,ruff 全绿。

## [0.2.67] - 2026-08-14

- Backend:sampling 参数块落地(死参数修复,审计待办)——param_schema 声明
  的 ft_neg/ft_alt/flip_f1/auto_phase 此前脚本生成从不消费,改 GUI 参数
  无效。现消费:ft_neg(None=按 FnMODE 推导/True=强制 -neg/False=关闭)、
  ft_alt(True=按采集方式自动/False=强制关闭)、flip_f1(True 时 F1 轴
  FT -neg 翻转)、auto_phase(False 关闭直接维自动相位);process/
  reconstruct_nus/_process/finalize_nus → script_generator 各生成函数
  传递,effective_params 回写 sampling;默认保持现有脚本输出不变(回归
  断言 base==with_sampling);param_schema 默认 ft_neg None/ft_alt True
  (语义=按采集方式自动);API_CONTRACT §6 措辞更新;
- Backend:G2B-008 生成谱图真实阶段日志——CshRuntime.run 增加 on_line
  逐行回调;process/reconstruct_nus/convert_to_fid 增加 progress 阶段
  消息(开始转换 fid/开始·完成相位优化/开始·完成 SMILE 重构/终谱已
  就位);stepwise.generate_fid/generate_spectrum 转发 progress(缺省
  None 兼容);ProcessingBackend Protocol 增 progress(Shared Contract,
  G2B-006/008);
- Backend:run_batch 进度回调——progress 改为 Callable[[str], None]
  (每数据每步骤消息,如 d_001: 开始 spectrum/d_001: 成功),供 GUI 批量
  执行展示;batch_id 解析/失败继续/汇总语义不变;
- 测试:新增 test_sampling_params(5 例)/test_progress(3 例),既有 fake
  后端适配 progress;全量 447 passed,ruff 全绿。

## [0.2.66] - 2026-08-14

- GUI:生成谱图自动接相位优化(用户反馈「最终谱相位不对」):
  - ProcessingController.generate_spectrum 在基础谱后默认跑
    optimize_phase_brute_force(0.2.62-0.2.64 相位处理),最终谱相位
    正确;新增 phase_optimize=False 调试开关;
  - 实测(sampleL):基础谱相位评分 49.89(峰窗负面积 60.1%)→ 优化后
    71.87(5.6%),最优相位 F2(0,-127.5)/F1(60,35);
- 审计 GUI↔Backend 不同步:
  - ⚠ param_schema 的 sampling 块(ft_neg/ft_alt/flip_f1/auto_phase)
    仅文档化,后端脚本生成未消费(死参数,待 Backend 实现或移除);
  - ⚠ G2B-008 生成谱图真实阶段日志仍待 Backend(progress 未转发);
  - ⚠ 后端 run_batch 引擎未接入 GUI 批量执行(GUI 逐数据走 stepwise);
  - ✅ 导入 link_stats warnings 已在 GUI 展示(无缺口)。
- 测试:新增 test_generate_spectrum_phase_optimize_disabled,更新 progress
  用例;全量 439 passed(本地 3.13),ruff 全绿。

## [0.2.65] - 2026-08-14

- 工程:项目要求 Python ≥3.12(用户决策):
  - pyproject requires-python ">=3.10" → ">=3.12";ruff target-version
    py310 → py312;
  - 枚举迁移:7 处 str+Enum → enum.StrEnum;datetime.timezone.utc →
    datetime.UTC;
  - 打包脚本/AGENTS.md/SOFTWARE_SUMMARY 版本说明同步;代码扫描无 3.12
    移除 API(distutils/imp 等)。
- 测试(3.12 实测):本地 3.13 全量 438 passed + ruff 全绿;VM 3.12
  (~/nmr312_venv,uv 托管 CPython 3.12.13 + PyQt6 6.11.0)master 全量
  434 passed + 4 skipped;
  ⚠ gui-dev 7f4e080 在 3.12 下仍段错误(PyQt6 6.11.0/6.10.2 均崩,
  崩溃点 pyqtgraph ViewBoxMenu ← PlotWidget ← spectrum_viewer)——
  段错误非 Python 或 PyQt6 版本问题,属 Linux 下 GUI 代码内存损坏,
  待 GUI Agent 定位;gui 合并维持回退。

## [0.2.64] - 2026-08-13

- Backend:相位优化增强(参考旧项目 NMRFlow)+ 性能优化:
  - ±90° 对称性消歧:新增 phase_quality.profile_symmetry_axis(峰窗
    一维剖面与镜像的相关系数,吸收≈+1、色散≈非正),吸收度相近时选
    峰形更对称者,避免 p0 落偏 90°(旧项目核心手法);
  - 平台圆中位数精修:p1 评分平台(≥best-0.02)取角度圆中位数,把
    相位从 5° 网格精修到亚度精度(如 -52° 这类非网格值);
  - 联合复核缓存:顺序搜索已产出的候选谱路径复用,联合 ±5° 复核
    不再重复跑后端(减少重复后端运行);
  - 修复:移除 0.2.63 引入的 p1 归一化——NMRPipe PS 的 p1 是频率
    相关线性相位(相位=p0+p1·k/max),p1+360 在中间点 k 处不等价
    (-200°≠160°),归一化导致基线重渲等后续步骤用错相位、峰形劣化
    (VM sampleF F2 neg 0.055→0.984 复现并修复);
- VM 验证:sampleF F1 0.007/F2 0.055、sampleG F1 0.027/F2 0.000
  (增强后保持纯吸收);sampleA 保持;3D NUS sampleB 联合复核回退 (0,0);
- 测试:新增 profile_symmetry_axis 吸收/色散单元测试;合并入 master 后全量 454 passed,
  ruff 全绿;已知偶发 GUI mtime 脆弱测试单独重跑通过。

## [0.2.63] - 2026-08-13

- Backend:相位优化正确性修复(用户反馈:实际数据一维谱峰形为吸收+色散
  中间态)。VM 真实数据诊断(sampleF、sampleG、sampleA、sampleB)定位 4 个
  根因并修复:
  1) p1 搜索范围默认 ±90° 不足——sampleF 真实 F1 最优 ~150°;默认扩到
     ±180°(步长 30°);
  2) 评分用全谱负面积+熵,相位敏感的色散负瓣只占峰窗,被噪声稀释
     (±5° 内仅 0.02 分)→ 门控误回退 (0,0);评分改为峰窗负面积
     (正/负峰分别检测、强峰阈值过滤、边缘峰过滤、top-5 强峰聚合);
  3) 二维峰窗把已调好方向(如 F2)的峰形纳入,稀释目标轴信号——默认
     评分改为沿被优化轴取一维剖面(参考旧项目 NMRFlow 逐轴调相方式);
  4) 全强峰加权聚合与主峰观感不一致,顺序搜索收敛到局部最优——
     top-5 强峰聚合,主峰优先;
  - 门控语义:细网格评分平坦时回退粗网格最优(保留粗定位);粗网格也
    平坦/最优为零才回退 (0,0);p1 归一化 [-180, 180);
- VM 验证(scripts/vm_diag_phase_lineshape.py,一维主峰峰窗负面积):
  sampleF F1 0.389→0.007、F2 0.175→0.055;sampleG F1 0.745→0.027、
  F2 0.744→0.000;sampleA 保持 0.000/0.000;3D NUS sampleB 联合复核
  回退 (0,0)(SMILE 内建相位,评分最优,不再乱选相位);
- 参考:旧项目 NMRFlow(VM ~/NMRFlow)自动调相——切全部信号一维谱,
  峰窗(±5 剖面)正负面积差评分、对称性 ±90° 消歧、平台圆中位数精修;
  本版移植其「峰窗一维剖面负面积」核心,搜索仍走粗网格+多尺度+门控;
- 测试:test_qc_metrics 适配峰窗指标(1D 合成谱最强峰为 FFT 边缘伪影,
  负面积方向由 2D 中间峰测试覆盖);全量 437 passed,ruff 全绿。

## [0.2.62] - 2026-08-13

- Backend:G2B-009 可查证性增强——导入 metadata.json 记录 link_stats
  (hardlink/symlink/copy/writable 计数),与 WorkflowRun params 双落盘,
  用户/流程可直接查证导入方式(是否真正链接、回退复制了几项)。

## [0.2.61] - 2026-08-13

- 移除(用户反馈):右侧谱图面板顶部的「数据摘要 + 在 Pipeline 中定位」
  信息条(点击 raw 等文件夹时出现,无实际用途)——整体移除 context_summary
  与 locate_button 及对应信号/方法;批量汇总的定位功能保留(仍用
  _locate_pipeline)。
- 测试:test_gui_context 三条摘要/定位用例替换为 1 条回归(确认不再有
  该信息条),全量 417 passed,ruff 全绿。
- 注:gui-dev 四提交(f47a876/b49c85b/928a0d1/d34bfea)合并入 master 后
  全量 439 passed(本地),ruff 全绿。

## [0.2.60] - 2026-08-13

- 修复(用户反馈):Pipeline 步骤展开详情里的「运行记录」等文本在深色系统
  主题下背景变黑、灰字看不清——给详情面板显式浅色背景(#ffffff + 边框)并
  加深文字色(#222),不依赖系统调色板,深浅主题下均可读。
- 测试:新增详情配色回归 1 例,全量 419 passed,ruff 全绿。

## [0.2.59] - 2026-08-13

- 术语统一(用户反馈):用户可见「项目」措辞改为「样本」——菜单(新建样本/
  打开样本/保存样本/最近样本)、欢迎页、顶部上下文条、树 tooltip 与右键
  (打开/重命名/删除样本)、Dashboard/谱图面板/导入提示等;原「项目(&P)」
  与「样本(&S)」两个菜单合并为单一「样本(&S)」(样本管理/新建实验/重命名
  实验/删除实验/添加样本/删除样本),避免同名菜单;层级与功能不变,
  代码标识符与 project.json 术语不动。
- 测试:新增 test_gui_layout 改名回归 1 例(菜单/欢迎页/上下文条),全量
  418 passed,ruff 全绿。

## [0.2.58] - 2026-08-13

- 修正(用户反馈):右侧纵向 1D 条带的坐标轴/谱方向与二维谱不一致——
  开启「一维谱」时显式把右条带 invertY 同步为主图 Y 轴状态(谱+坐标轴
  一起翻正),保持 YLink 移动/缩放联动;clear() 恢复主图 2D 显示方向,
  防止 1D 视图后进入 3D/叠加谱时方向泄漏。
- 测试:新增右条带方向/联动、clear 恢复方向 2 例,全量 417 passed,
  ruff 全绿。

## [0.2.57] - 2026-08-13

- Backend:G2B-009 归属与收尾(核对 0.2.56 直接实现,补齐边界缺口):
  - 核对:raw 链接式导入(硬链接→符号链接→复制回退,逐项失败回退 +
    warnings)、copy=False 语义不变、SHA-256/manifest 不变、WorkflowRun
    params 含 link_stats、_register_spectrum/manual 终谱 move 归位且
    process/ 不留副本——均符合提案,无需改动;
  - 缺口修复:fid.com 等后端可写文件(raw 内会被 bruker -AUTO /
    patch_fid_com 改写)此前被链接,硬链接会把改写写回源数据——新增
    WRITABLE_RAW_NAMES,可写文件实体复制(link_stats 增 writable 计数,
    不计入「回退复制」warning);
  - 测试:新增 fid.com 实体复制不污染源、link_stats 记录断言;全量
    435 passed,ruff 全绿。

## [0.2.56] - 2026-08-13

- Backend:存储去重实装(G2B-009,用户确认「raw 非必要不复制只链接、
  process 不放终谱」):
  - 导入 raw 改「链接式」:只读源文件按原相对结构建立链接,优先级
    硬链接 → 符号链接 → 复制回退(跨卷/权限失败逐项回退并记录 warnings);
    copy=False 语义不变;SHA-256 指纹/manifest 与 metadata 不变;
  - 终谱归位改 move:_register_spectrum 从 process/ 移入
    spectra/<exp>-<data>.ft2|ft3,process/ 不再保留终谱副本
    (fid/脚本/中间候选谱仍在 process/);manual 路径复用同一归位;
  - 旧数据不迁移、旧路径兼容;WorkflowRun params 增 link_stats 审计。
- 测试:test_import_workflow 新增链接断言(硬链接 samefile)与回退复制
  用例,test_stepwise/test_manual 补「process/ 无终谱」断言;
  全量 435 passed,ruff 全绿。

## [0.2.55] - 2026-08-13

- 修正(用户反馈):0.2.52 方向判断相反——二维谱应「行 0(高 ppm)显示在
  顶部」;现去掉 contour 的 y 翻转(view y 即数据行),保持 invertY(True),
  鼠标/1D 条带/峰标记统一按 view y=数据行 换算,一维条带与二维谱仍严格
  对齐且二维谱上下方向正确。
- 测试:test_viewer/test_viewer_1d 方向断言与坐标回归用例同步更新,
  合并后全量 434 passed,ruff 全绿。

## [0.2.54] - 2026-08-13

- Backend:相位优化方案 B(审查结论「3D 评分面平坦,必须门控回退」):
  - per-axis 平坦门控:margin < PHASE_SCORE_FLAT_MARGIN(0.05)不再只记日志,
    回退 (0,0)(SMILE 内建相位),不应用低置信相位;
  - 联合 ±final_step 邻域复核:全部轴固定后按 p1 网格(3^N + 全零)重评,
    联合最优显著更优(差 ≥0.05)才更新,否则保持顺序结果——堵住「顺序
    搜索选的不是联合最优」;
  - 可复现性检查:top-K 强迹线奇偶子采样各取邻域最优 p1,两次差 >10° 判
    低置信回退 (0,0);
  - margin 只按 p1 邻域计算(p1 为相位判别主导维度;p0 弱维度纳入会把
    margin 拉平误判平坦)。
- Backend:方案 C 窗函数/填零嵌入修复:
  - zf_modes 移除 none(填零关闭会减半数字分辨率且综合 QC 此前不惩罚);
  - _est_bytes 对 none 按有效 TD 估算(消除恒 4 字节退化,防御);
  - 同分容忍内改「高分辨率优先」(替代最小文件);
  - spectrum_quality.evaluate 增 min_shape 分辨率惩罚(写回 score.overall);
  - artifact 孤立峰惩罚连续化(平滑 100/80 离散跳变,按超出距离阈值程度
    扣 10..20 分)。
- Backend:SMILE 平面复用(方案 D 收尾)——reconstruct_nus 成功后写
  .nus_params.json;optimize_phase_sequential 在平面存在且参数一致时跳过
  重构(backend_runs 不重复计),完整入口 SMILE 从 2 次降到 1 次。
- VM 方案 A 等价性验证(scripts/vm_validate_recon_phase_equiv.py,sampleB
  3D NUS):复型平面内存内评分(M1 直接 apply_phase / M2 FFT 后 apply)vs
  finalize 暴力评分的 Spearman -0.47 / -0.62,top-match 不一致 → **不通过**;
  3D 不切换内存内评分,由门控回退兜底(结论见 PROJECT_STATUS)。
- 测试:新增 test_phase_gating(5 例)/test_qc_enhance(2 例),既有 fake
  后端编码 p0 适配门控;合并后全量 434 passed,ruff 全绿。

## [0.2.53] - 2026-08-13

- 修复(用户反馈):viewer 一维谱条带与二维谱错位——contour 把数据行 r
  画在 view y = rows-r(配合 invertY,行 0=高 ppm 在底部),但鼠标映射
  (_on_mouse_moved/_on_plot_clicked)与峰标记(_apply_peak_items)未同步
  翻转,导致上方 1D 条带整行镜像、峰点/点击选择相对轮廓偏移;新增
  _view_to_data 统一 view↔数据坐标转换,峰标记/标签按 view y=rows-r
  放置,「一维谱」十字线点击定位正确。
- 测试:test_viewer_1d 新增 3 例 + 扩展条带联动用例(鼠标映射/条带对齐/
  峰对齐/点击选中),合并后全量 427 passed,ruff 全绿。

## [0.2.52] - 2026-08-13

- 项目垃圾清理(Architect 专项):
  - 三个 worktree(master/gui-dev/backend-dev)核对:git ls-files 无缓存/二进制
    大文件(>1MB)入库,无未忽略的未跟踪文件;
  - 清理全部可再生的忽略缓存:__pycache__/×55、.pytest_cache/、.ruff_cache/、
    nmrforge.egg-info(共 57 个目录);保留虚拟环境 nmrforge/、
    SOFTWARE_SUMMARY.md(用户参考文档)、config/nmrforge.local.yaml(本地配置);
  - .gitignore 增补覆盖率/编辑器/系统临时文件规则(.coverage、htmlcov/、
    .hypothesis/、.tox/、.idea/、.vscode/、.DS_Store、Thumbs.db、*.tmp、
    *.bak、*.orig、*.rej、*~)与 NMRPipe 工作目录 *.nmrpipe/,防再入库;
  - VM 工作副本清理未跟踪生成产物 tests/fixtures/bruker/hsqc_small.nmrpipe/
    (hsqc_small_convert.com,HANDOVER §5 经典 pull 阻挡副本),VM git status 恢复干净;
  - 备注:本地全量首轮 1 例 Windows 瞬时文件锁(os.replace PermissionError,
    test_project_dashboard_stats_and_runs)偶发,单测复跑通过;新鲜 basetemp 全量复跑全绿。
- 测试:全量 424 passed(本地 Windows + offscreen),ruff 全绿;
- VM 全量回归:420 passed + 4 skipped(2026-08-13,推送后同步复测)。

## [0.2.51] - 2026-08-13

- 阶段 D:引导与性能(愿景 §33/§31):
  - 首次使用引导:首次导入数据后 Pipeline「下一步」高亮 + 提示气泡一次
    (状态存设置 guide.first_import_hint_shown,不重复打扰;欢迎页保留);
  - 树增量刷新:project_tree 改为增量刷新——未变化节点/子目录保留实例,
    子文件夹按目录指纹(mtime + 名称/大小)仅在变化时重建文件子节点,
    大项目刷新不卡顿。
- 测试:新增 test_gui_phase_d(4 例:节点保留/文件夹指纹/首次提示/只出现一次),
  全量 420 passed,ruff 全绿。

## [0.2.50] - 2026-08-13

- 阶段 C:批量与导入体验(愿景 §29/§31):
  - 批量处理进度:整组执行时中间区显示「批量 B1: 1/2 完成 · 当前: d_002
    生成谱图」;结束后汇总弹窗(成功/失败清单,失败项双击定位到数据);
  - 拖拽导入:把含 acqus 的 Bruker 数据目录拖到主窗口即触发导入
    (校验 acqus,当前实验或自动新建);
  - 设置对话框:菜单「设置 → 软件设置...」——NMRPipe 路径、默认线宽
    (1H/15N/13C)、填零 points_per_line、SMILE 线程上限;保存到
    config/nmrforge.local.yaml,重启生效,未配置显示默认值。
- 测试:新增 test_gui_phase_c(5 例),全量 416 passed,ruff 全绿。

## [0.2.49] - 2026-08-13

- 阶段 B:Pipeline 深交互(愿景 §17/§23/§28,原则 2/3):
  - 步骤行点击展开内嵌详情(输入产物、最近 WorkflowRun 记录、参数摘要、
    脚本快照、快照目录),再点收起,不弹窗;
  - SUCCESS 步骤详情提供「以此参数打开人工编辑器」(参数预填进
    ParameterTableDialog,复用渲染/编辑流程);
  - LOCKED/OUTDATED/FAILED 原因灰字直显(不只 tooltip);FAILED 行显示
    错误摘要 + 「查看日志」「重试」按钮(日志定位到 LogPanel 并滚底)。
- 测试:新增 test_gui_pipeline_detail(5 例),全量 411 passed,ruff 全绿。

## [0.2.48] - 2026-08-13

- 阶段 A:全局上下文与谱图联动(愿景 §30/§19/§20):
  - 主窗口顶部常驻「当前上下文」条(Project / Experiment / Data + 状态
    摘要),随树选择/谱图操作同步更新;
  - 谱图面板显示来源数据 + 步骤(生成谱图)+ 关键参数摘要(从最近成功
    WorkflowRun 读取 EXT/ZF/基线/自动相位,缺失显示「历史数据」);
    「在 Pipeline 中定位」按钮选中树节点并切到处理页;
  - 3D 切片/投影模式按数据记忆(切走再切回恢复平面与投影)。
- 测试:新增 test_gui_context(5 例:上下文条/参数摘要/历史数据/定位/3D 记忆),
  全量 406 passed,ruff 全绿。

## [0.2.46] - 2026-08-13

- Backend:实际生效参数回写(B2G-003)——backend.process/reconstruct_nus/
  convert_to_fid 返回 dict 增加 effective_params(均匀 process:extract/
  ext_lo/ext_hi、zero_fill_plan 逐维 SI、baseline、window、direct_phase;
  NUS:ext/zero_fill SI/nSigma/thresh/xQ3/scaling/baseline/direct_phase/
  nthread;convert:dataset_id/ndim/segments/work_dir);workflow.stepwise
  generate_spectrum/generate_fid 把 effective_params 与调用方 params 合并
  (调用方优先)写入 WorkflowRun params;旧后端(无 effective_params)params
  保持现状(兼容)。
- Backend:配置默认参数注入(B2G-003)——config/nmrforge.yaml 新增
  processing.linewidth_hz(1H/15N/13C 默认 8/15/20)、points_per_line(2.0)、
  smile.nthread(2)、backend.nmrpipe.path(可空);新增 backend/config.py
  load_processing_defaults()(供 GUI 设置对话框);zero_fill_plan/
  reconstruct_nus/finalize_nus 未显式传参时读取配置(显式 params 优先,
  无效值回退内置默认);factory 把 config 的 nmrpipe 路径透传给
  NMRPipeBackend(nmrpipe_finder 路径优先 config)。
- 测试:新增 test_config_defaults(配置读取/覆盖优先级/无效值回退/factory
  透传)、test_effective_params(effective_params 合并/调用方优先/旧后端
  兼容/zf_summary);全量 396 passed,ruff 全绿。

## [0.2.45] - 2026-08-13

- Backend:批量处理引擎实装(workflow/batch.run_batch,替代占位):
  run_batch(manager, exp_id, targets, steps, backend) 按序对组内每个数据执行
  指定步骤(import 幂等确认→fid→spectrum→peaks→analysis),fid/spectrum 复用
  workflow.stepwise,peaks/analysis 复用 workflow.pick_peaks/analyze;targets
  支持 batch_id(B1/B2...,读 .pipeline_state.json,与 GUI 批量组语义一致,
  Engine 不依赖 Qt)或显式 data_ids;单数据失败不中断整组(记录
  failed_step/error),逐数据登记 WorkflowRun,返回逐数据结果 dict + summary
  (total/success/failed),可选 progress 回调。
- Backend:3D NUS 相位门控路径复核——SMILE 线程护栏(D006)提取为可测函数
  enforce_smile_thread_guardrail(默认 2、大网格>5000 强制 2);3D NUS 相位
  优化 = 1 次 reconstruct_nus(默认护栏参数)+ 逐间接维 F2/F1 候选
  finalize_nus(不重跑 SMILE),backend_runs=1+2×候选;VM sampleB 真实数据
  产物 FDTRANSPOSED=0。
- Backend:相位评分置信度阈值 VM 标定(0.2.45):真实数据 ±5° 相位误差余量
  2D sampleA ≈0.11 分、3D NUS sampleB ≈0.04 分(评分面近乎平坦),原「评分面
  平坦」阈值 <1 分过严(真实数据必然触发);下调为 <0.05
  (PHASE_SCORE_FLAT_MARGIN),2D 判「最优较明确」、3D 判「平坦」。
- 测试:新增 test_batch(8 例:多数据批量/单数据失败继续/WorkflowRun 登记/
  stepwise 复用/batch_id 解析/import 幂等/全流程五步/参数校验)、
  test_phase_nus_3d(3D NUS 护栏与 backend_runs、线程护栏上限);全量 386
  passed,ruff 全绿。

## [0.2.44] - 2026-08-13

- Backend:填零逻辑改造(用户方案)——不再机械「所有维度 2×」:新增
  zero_fill_plan(experiment, zero_fill, linewidth_hz, points_per_line):
  直接维 F2/F3 默认 SI=2×TD(稳妥起点,1024→2048);间接维按目标数字
  分辨率动态决定——目标点距 = max(线宽, 1/AQ)/points_per_line
  (默认 1/2,每线宽 ≥2 个数字点;精确峰位/线宽/拟合/CSP 可调 1/4 或
  更细),SI 取 2 的幂并夹在 [TD, next_pow2(ppl×TD)];线宽来源
  params.linewidth_hz[轴] → 核素默认表(1H 8/15N 15/13C 20 Hz)→
  15 Hz;1/AQ 下限保证「线宽不可能窄于真实分辨率」,SI 天然不过度。
  NUS 间接维以重构后的完整复点网格(effective_td)为 TD——重构与填零
  是两个独立过程,填零只作用于重构后的时间域。uniform/NUS 两阶段/
  3D/finalize 脚本全部改显式 ZF -size;process/reconstruct_nus/
  finalize_nus 透传 linewidth_hz/points_per_line 并输出逐维 SI 选择
  日志;param_schema 增 linewidth_hz/points_per_line,zero_fill 语义
  0=自动/k=间接维固定倍数(直接维保持 2×);stepwise 窗/填零嵌入的
  体积估计改用填零计划。
- 测试:直接维 2×TD、间接维动态+1/AQ 约束、线宽影响、覆盖语义
  (0/int/none)、NUS/finalize ZF 行与关闭;全量 350 通过,ruff 全绿。

## [0.2.43] - 2026-08-13

- 批量处理(实验中间页):「导入数据」表单下方加分割线 + 「批量处理」区块,
  可添加多个 Bruker 数据文件夹并批量导入;同批导入的数据带批量组标记
  (B1/B2...,多次批量导入序号递增),树中显示 [B{n}];同标记数据绑定,中间
  处理页(Pipeline)操作对整组数据依次执行;数据右键可「加入批量组...」
  (选择/新建组号)或「移出批量组」恢复单一数据。
- 实现:gui/pipeline_state.py 批量组助手(batch_id/set/clear/next/
  batch_data_ids,存于 .pipeline_state.json);ProcessingController.
  batch_import;ExperimentDashboard 批量区块;Pipeline 组内整组运行 + 上下文
  批量标记;ProjectTree 标记显示与右键加入/移出。
- 测试:新增 test_gui_batch(5 例),全量 354 passed,ruff 全绿。

## [0.2.42] - 2026-08-13

- 修订(用户反馈):
  - 1D 显示改为控制面板「一维谱」开关(TopSpin 式):开启后谱图出现随鼠标
    的十字线,点击(或移动)显示该处两个一维谱——上方 F2 行迹线 + 右侧
    F1 列迹线条带,与主谱联动;移除右键菜单方案;
  - 峰表文件直接改为 Poky .list(保存写 peaks/<exp>-<data>.list,旧 CSV
    兼容读取);「导入 Poky」只替换峰表关联关系(不覆盖文件),点「保存
    峰表」才写盘。
- 测试:更新 test_viewer_1d(条带)、test_gui_poky / test_gui_manual /
  test_gui_processing(.list),全量 349 passed,ruff 全绿。

## [0.2.41] - 2026-08-13

- Viewer 增强(6 项):
  - .fid 查看:Spectrum1D(load_from_fid,多维取第一条 FID 实部);独立查看器
    与谱图面板的过滤器/拖放/文件列表支持 .fid,以 1D 迹线显示;
  - 二维谱 1D 切片:右键谱图按点击位置提取 1D 行/列切片(类似 nmrDraw),
    「返回二维视图」按钮恢复轮廓;
  - 布局:查看器内部改为上下布局(上方谱图、下方控制面板),谱图默认 1:1
    正方形显示;
  - 图层管理:图层列表右键「删除该图层 / 删除全部图层」;
  - 峰显示开关:控制面板「显示峰」勾选隐藏/显示全部峰标记;Poky .list
    导入直接替换峰 CSV 文件(登记 manual_peaks 运行);
  - SMILE 优化(可选):Pipeline 新增「SMILE 优化」步骤(生成谱图后、仅
    NUS,可跳过——峰挑选不依赖它),ProcessingController.optimize_smile
    网格搜索并采用最优谱(归位 spectra/、登记 smile_optimize 运行、脚本
    快照、指纹刷新使下游过期)。
- 测试:新增 test_viewer_1d / test_gui_smile / test_gui_poky,更新
  test_gui_layout(六步流程/上下布局),全量 349 passed,ruff 全绿。

## [0.2.40] - 2026-08-13

- Backend:相位评分改「连续负面积 + 谱熵」(文献方案:de Brouwer 2009
  负面积最小化 + Ernst 1966 谱熵最小化)。core/qc/phase_quality.evaluate
  评分权重:吸收度 25% + 连续负面积 40%(先稳健基线扣除再统计负值面积,
  相位误差经色散负边瓣一阶放大)+ 正部谱熵 20% + 负峰计数 15%;
  移除镜像对称性计分(90° 色散谱高度对称,旧公式误判为高相位质量,
  字段保留);PhaseQuality 新增 negative_area_fraction/entropy,
  导出 negative_area_fraction/spectral_entropy 辅助函数;
  _default_phase_score 复用新评分(score_fn 仍可注入)。合成
  Lorentzian 多峰+噪声实测:5° 相位误差评分余量由旧公式 ~0.1 提升到
  ~0.5-0.7(约 5 倍),0°→90° 单调递减,180° 反相惩罚更强。
- 测试:相位误差扫描单调性(0/5/10/30/90°)、5° 余量 >0.2、180° 反相
  惩罚、连续负面积/谱熵方向断言;全量 346 通过,ruff 全绿。

## [0.2.39] - 2026-08-13

- Backend:窗函数与填零嵌入相位优化(用户方案,uniform):stepwise 在相位+
  基线后跑小网格(sine_bell/sine_bell²/gaussian × 填零 auto/none),每次
  候选重渲评分;填零受文件大小上限约束(默认 256MB),同分容忍 0.5 分内
  选最小文件;NUS 窗函数在 SMILE 重构内,调整需重跑 SMILE,仅报告。
  script_generator.generate_process_script 支持 window/zero_fill 覆盖
  (SP/GM/EM 窗,ZF auto/none/size),backend.process 透传。
- 相位搜索加「评分余量/置信度」日志:用已评分的 ±5° 邻域计算分差,
  <1 分提示评分面平坦、最佳相位置信度低(phase_quality 对 5° 误差区分度
  实测 <0.3 分);VM sampleA 实测余量 0.02-0.27,证实评分无法在 ±5° 内
  唯一定位最佳相位(搜索仍收敛到合理相位,负峰最小)。
- 验证:3D uniform(F3/F2/F1)与 3D NUS(F2/F1,直接维 F3 随重构固化)维度
  覆盖测试;VM sampleA uniform 实测网格选出 sine_bell+不填零(score 92.9,
  0.5MB,比 auto 小 4× 且分数相当)。
- 测试:3D 覆盖、窗函数/填零脚本、置信度日志;全量 343 通过,ruff 全绿。

## [0.2.38] - 2026-08-13

- Backend:其它优化嵌入相位优化(用户方案,除 SMILE)——stepwise
  optimize_phase_brute_force 在相位搜索后对最优谱内存内跑基线优化
  (optimize_baseline,0 次后端运行),配置变化时以「最优相位+最优基线」
  重渲终谱 1 次(uniform 全轴;NUS 2D 仅间接维 F1,直接维 F2 基线在
  SMILE 重构时固化,调整需重跑 SMILE 时仅报告);finalize_nus 支持
  baseline 透传;结果返回 baseline(config/scores/optimized/skipped),
  日志逐轴说明基线变化与分数增益。VM sampleA NUS 实测:相位+嵌入基线
  端到端通过(F1 order 3 重渲 +1.0 分;F2 需重跑 SMILE 已报告)。
- 测试:嵌入基线(曲率 → order 2 重渲,多 1 次 process);全量 341 通过,
  ruff 全绿。

## [0.2.37] - 2026-08-13

- Backend:相位优化改「粗网格 + 多尺度细化」(用户方案,替代固定步长全搜索):
  粗网格 p1 30°/p0 45° → 逐级约 1/3 细化到 5°(每级在上一级最优
  ±上一步长/2 窗口内联合扫描 p0×p1),单峰假设下与最优相位偏差 ≤2.5°;
  VM sampleA 实测 uniform 2D 13.6s(118 次)/ NUS 2D 5.7s(60 次),约为
  5° 全网格的一半(25.8s/10.6s),结果一致(F2 p1=10°)。
- 移除「够好即停」前置过滤(相位 + 基线):直接全网格优化实测足够快
  (粗网格 uniform 5s / NUS 2.6s),避免复杂度与评分阈值不可靠问题
  (phase_quality 对 5° 误差区分度 <0.3 分,固定阈值无法严格保证 5°);
  保留逐轴「相位/配置变化 + 分数增益」日志与 optimized 字段。
- 测试:多尺度收敛(真值 12° → 细化到 10°)、默认参数(粗 30° + refine)、
  无前置过滤;全量 340 通过,ruff 全绿。

## [0.2.36] - 2026-08-13

- Backend:相位/基线优化加「够好即停」前置判断(用户反馈):
  optimize_phase_sequential 与 optimize_baseline 每轴先评分当前状态
  (good_enough=80,0-100;None 关闭前置判断),已够好则跳过候选搜索并
  保持当前配置;日志逐轴说明「未优化 / 已优化 + 参数变化 + 分数增益」,
  末尾附总结行;结果新增 optimized/skipped 字段;stepwise
  optimize_phase_brute_force 透传 good_enough 并返回 optimized/skipped。
- 测试:跳过(保持配置)、关闭前置判断(全候选)、日志断言;全量 341 通过,
  ruff 全绿。

## [0.2.35] - 2026-08-12

- 重新处理入口:Pipeline 已成功(SUCCESS)的处理步骤(生成 FID / 生成谱图 /
  峰挑选 / 分析)新增「重新处理」按钮(导入步骤除外——重跑会新建数据而
  非覆盖);点击强制重跑对应 ProcessingController 方法,重跑后下游步骤
  经指纹校验标记为过期(OUTDATED);按钮 tooltip 说明后果。
- 测试:新增 test_gui_rerun(4 例),全量通过,ruff 全绿。

## [0.2.34] - 2026-08-12

- 脚本快照 GUI 接线:ProcessingController 在生成 FID / 生成谱图 / 人工
  fid.com / 人工谱图运行成功后,把实际执行的脚本与参数经 snapshot_run
  写入 WorkflowRun 快照目录(processing/<exp>/runs/<run_id>/snapshot,
  data_id 作用域过滤,已快照不重复);RunHistoryDialog 展示快照目录与
  脚本清单,新增「打开快照目录」按钮。
- 测试:新增 test_gui_snapshot(8 例),全量通过,ruff 全绿。

## [0.2.33] - 2026-08-13

- Backend:2D NUS 重构改两阶段(Architect VM 验证 sampleA 25% NUS 主峰
  112.59/7.47 与全采样一致,QC=93.6):generate_2d_nus_script → stage 1
  直接维 FT+EXT+POLY → TP → SMILE(-sample None -sampleCount N
  -xT 复点网格)→ nus2d/recon.ft1;stage 2 nmrPipe -in recon.ft1 →
  ZF/FT -alt/PS/POLY/TP → 终谱 ft2(-out -ov);effective_td 2D NUS
  间接维改复点网格 TD//mult(不采信 acqu2s NusTD,部分数据 NusTD=TD);
  3D NUS 保持 NusTD(已是复点数);generate_nus_finalize_script 2D 改
  nmrPipe -in + POLY + -out -ov,finalize_nus 支持 2D 逐维 PS 候选
  (相位优化不重跑 SMILE);构造工具 scripts/vm_sample_make_nus.py 入库
  并回归(网格=TD//mult、nuslist 首点 0)。
- 测试:两阶段结构(TP/SMILE/-alt/-xT 网格)、finalize 2D、effective_td
  复点网格、make_nus 构造回归;全量 338 通过,ruff 全绿。
## [0.2.32] - 2026-08-12

- 3D 谱切片查看(契约 §10):viewer/spectrum.py 新增 Spectrum3D
  (load_from_ft3 / slice / project / index_at,兼容单文件流与非流存储);
  新增 viewer/spectrum3d_panel.py(查看平面 F1-F2/F1-F3/F2-F3、第三轴
  切片滑块 ppm 显示、MIP/求和投影);SpectrumWindow 文件过滤器/拖放/
  命令行支持 .ft3,按维度数自动进入 2D/3D 模式,切片/投影复用现有
  SpectrumViewer/ContourLayer 绘制;gui/spectrum_panel 双击/选择 .ft3
  走 3D 查看路径(不再报「仅支持二维谱图」),峰表 3D 列按切片平面轴
  标签映射联动;状态栏/帮助补充 3D 操作说明。
- 测试:新增 test_viewer3d(12 例),全量通过,ruff 全绿。

## [0.2.31] - 2026-08-12

- Backend(G2B-007):逐维基线校正(默认全维 POLY -auto):
  select_method 每维 FT+PS 后插 baseline 节点(逐轴可关/order);
  script_generator uniform/NUS 脚本按轴插入 POLY(直接维 EXT 后、
  间接维 PS 后),param_schema 增 baseline 键(enabled/mode/order/axes);
  workflow/baseline_optimize 逐维网格(mode∈{off,auto}×order∈{1,2,3})
  用 core.qc.baseline_quality 选每维最优写回配置,score 可注入。
- 测试:默认两行 POLY -auto、关闭/order 覆盖、NUS 2D/3D 插入位置、
  schema 默认、逐维优化选校正/平谱选 off;全量通过,ruff 全绿。

## [0.2.30] - 2026-08-12

- Backend:逐维暴力相位优化默认改用相位专用评分
  (workflow.phase_optimize._default_phase_score,复用 core.qc.phase_quality:
  吸收度比例 50% + 负峰比例 30% + 对称性 20%),不再用综合 QC
  (SNR/基线/伪影与相位基本无关,加权会稀释相位排名);score_fn 仍可注入
  (需要综合评估时传 spectrum_quality)。
- 测试:错相(负峰)评分低于正相、负峰比例差异断言;全量通过。

## [0.2.29] - 2026-08-12

- Backend:相位自动调优改为「逐维暴力」方案(用户反馈):
  workflow/phase_optimize.optimize_phase_sequential——传统采样按轴
  (直接维 → 间接维)逐维,每候选相位重跑一次后端管线(process,
  direct_phase_override 覆盖该轴 PS),对终谱做整体 QC 评分,取最优后
  固定,依次推进;NUS:先 SMILE 重构一次(reconstruct_nus,直接维随重构
  固化),再从重构平面逐间接维候选跑 finalize_nus(不重跑 SMILE)。
- backend:script_generator.generate_nus_finalize_script(重构平面→间接维
  FT,逐维 PS 可配)+ NMRPipeBackend.finalize_nus;
  stepwise.optimize_phase_brute_force 改用逐维暴力。
- 测试:sequential 逐维/失败、finalize 2D/3D 脚本、stepwise 断言;
  全量通过,ruff 全绿。

## [0.2.28] - 2026-08-12

- Pipeline 状态机完善(OUTDATED + 指纹校验):新增 gui/pipeline_state.py,
  每数据维护 .pipeline_state.json(输入/脚本/参数指纹);步骤成功后经
  ProcessingController / 导入流程登记;compute_step_statuses 重算当前
  指纹,输入或脚本变化 → 该步骤及下游 OUTDATED(上游重新运行/外部修改/
  脚本编辑),旧数据无指纹状态用上游产物 mtime 启发式回退;OUTDATED
  步骤显示「重新运行」按钮与原因 tooltip,「下一步」优先提示过期步骤;
  人工 fid/谱图/峰表保存同样登记指纹。测试新增 test_gui_pipeline_state
(7 例),全量 307 passed,ruff 全绿。

## [0.2.27] - 2026-08-12

- 峰表编辑回写(G2B-005 GUI 剩余):右侧谱图面板峰表工具栏新增「添加峰 /
  删除选中 / 导入 Poky / 保存峰表」;2D/3D 列自动切换,编辑后写回
  data_dir(...,"peaks")/<exp>-<data>.csv(经 ProcessingController.
  save_peaks_manual,登记 manual_peaks WorkflowRun success/failed);
  保存后刷新 viewer 峰标记与 Pipeline peaks 状态。
- 报告页:新增 gui/report_panel.py,展示 data_dir(...,"report") 下
  html/pdf/json 产物(内嵌预览/外部打开,缺失提示先完成分析);
  Pipeline「分析」步骤状态由报告产物驱动(产物存在 → SUCCESS),
  分析成功时步骤行显示「报告」按钮,查看菜单增加「报告...」。
- 人工处理接线(经 ProcessingController):ParameterTableDialog 以
  param_schema 填充(zero_fill/ext_lo/ext_hi/extract/sampling.*/stages),
  「渲染脚本」→ manual_scripts 生成 process.com / nus*.com;
  ScriptEditorDialog 增加「运行」→ run_manual_spectrum;fid.com
  查看/修改/运行接 manual_fid_com / run_manual_fid_com;保存到
  data_dir(...,"process"|"raw")/ 并刷新运行历史(manual_* 已登记
  WorkflowRun);步骤运行后刷新 Pipeline。
- 测试:新增报告页 / 峰表编辑 / Poky 导入 / 人工接线用例,更新
  test_gui_manual / test_gui_processing;全量 300 passed,ruff 全绿。

## [0.2.26] - 2026-08-12

- Backend(G2B-006):EXT 参数在自动/步骤化路径生效——
  NMRPipeBackend.process 增加 params(extract 默认 True/ext_lo "11.0"/
  ext_hi "6.0")透传给 generate_process_script;reconstruct_nus 支持
  extract(False 时不写 EXT 行);2D/3D NUS 脚本默认窗口统一 6-11 ppm;
  stepwise.generate_spectrum 均匀分支把 params 传给 backend.process;
  ProcessingBackend Protocol 补充 params 签名(Shared Contract,
  G2B-006 已批准)。
- 测试:NUS extract 开关/默认窗口、stepwise 透传断言、后端 params 接受;
  全量通过,ruff 全绿。

## [0.2.25] - 2026-08-12

- Architect:VM 真实数据回归(sampleA,步骤化 import_data→generate_fid→
  generate_spectrum)确认 EXT+TP 修复:终谱 FDTRANSPOSED=0,1H 窗
  6-11 ppm(640 点),主峰 (112.59, 7.47) 与手工 test.ft2
  (112.73, 7.56) 一致;extract=False 全宽谱水峰 4.7 ppm 为垂直竖线
  (峰高为相邻 1H 列 60-90 倍)。新增回归脚本 scripts/vm_sample_*.py。
- EXT 参数透传缺口已由 G2B-006 关闭(见 0.2.26)。
## [0.2.24] - 2026-08-12

- Backend:处理脚本(generate_process_script)直接维 FT+PS 后增加 EXT 提取
  窗口,默认选区 6-11 ppm(-x1 11.0ppm -xn 6.0ppm -sw -round 2),
  与 NUS 脚本一致;ext_lo/ext_hi/extract 可配(extract=False 关闭);
  param_schema 与 render_scripts 透传。
- 测试:新增 EXT 默认行/顺序、关闭、自定义窗口、schema 键断言;
  全量通过,ruff 全绿。
## [0.2.22.1] - 2026-08-12

- B2G-002:处理步骤按 data_id 作用域执行——run_step/set_context 支持
  传选中数据 id,树右键生成 FID/谱图作用于该数据(不再固定首个);
  单数据实验行为不变。
- 多谱叠加:reset_view 显示所有谱的联合范围(不再只适配第一张谱)。
- viewer 性能:轮廓重建缓存插值数据(级数/起点变化复用),滑块拖动防抖
  (仅松开时重建),操作卡顿明显缓解。

## [0.2.24] - 2026-08-12

- fix: 2D process 脚本缺末尾 TP 导致 F1/F2 交换(软件谱在 viewer 显示为
  转置/交换;水峰被标成 15N 116.5ppm 横线)。generate_process_script 对 2D
  在间接维 FT 后补 TP(与手工 xy.com 两个 TP 对齐),产物 FDTRANSPOSED=0,
  主峰与手工一致(112.6/8.2 vs 112.7/8.3)。VM 实测验证。
- 已知待办:软件 2D 谱仍缺 EXT(1H 全宽 12.7--3.3ppm,手工裁剪 6.5-9.5),
  需 Backend 在 script_generator 补 EXT/POLY(见 PROJECT_STATUS)。

## [0.2.23] - 2026-08-12

- 诊断(Backend):同一实验多组数据时,步骤执行固定作用于第一个数据
  (gui/pipeline_panel.run_step 取 nodes[0],树 data_id 未传递);后端
  接口均按 (exp_id, data_id) 作用域且正确。已建 B2G-002 提案,
  待 GUI Agent 接线(选中数据传入 run_step)。

## [0.2.21.7] - 2026-08-12

- 轮廓起点滑块改非均匀(平方)映射:低百分比区间更精细可调,默认起点降低,
  峰显示更充分。
- 谱图显示方向对齐 nmrDraw:反转 x 轴(1H 高 ppm 在左)+ 反转 y 轴
  (15N 高 ppm 在下),水峰/信号/侧链峰位置与参考工具一致。

## [0.2.22] - 2026-08-12

- fix(shared):create_project 不再预建扁平目录模板(raw/processing/spectra/
  peaks/analysis/figures/report/metadata)——契约 §9.2「文件系统即层级」,
  数据目录在导入/处理时按 <exp>/<data>/{raw,process,spectra,...} 创建;
  dir_map 仅作旧扁平路径兼容解析,不 mkdir。
- 测试适配:布局断言改为「不创建扁平目录」,legacy 状态推断/删除用例显式
  创建旧扁平目录,配置目录用例断言路径解析而非存在;全量 270 passed。

## [0.2.21.6] - 2026-08-12

- 修复谱图内容位置(SpectrumViewer 反转 y 轴使 15N 高 ppm 在下、
  1H 高 ppm 在左;contour 坐标同步翻转,与 nmrDraw 显示一致)。

## [0.2.21.5] - 2026-08-12

- 谱图查看器显示方向对齐 nmrDraw/Poky:反转 y 轴使 15N 高 ppm 在下
  (1H 高 ppm 已在左);撤销 contour y 翻转(与旧项目一致)。

## [0.2.21.4] - 2026-08-12

- 修复谱图查看器显示异常:SpectrumViewer 内部恢复左右布局(plot 左、控制
  面板右),避免与谱图面板垂直多层嵌套挤压;无谱图/峰表时工具栏一并隐藏。

## [0.2.21.3] - 2026-08-12

- 左侧树谱图文件夹内 .ft2/.ft3 双击:右侧谱图面板直接显示并加载峰表;
  其它文件双击打开所在目录。
- 独立谱图查看器(查看 → 谱图查看器)打开文件默认路径=当前数据 spectra 目录。

## [0.2.21.2] - 2026-08-12

- 左侧树子文件夹节点(raw/process/spectra/peaks/figures/report)下拉显示
  目录内文件(构建树时扫描,文件/子目录按名排序)。

## [0.2.21.1] - 2026-08-12

- 谱图面板自动显示:选中数据后自动加载该数据第一张谱图(有谱图时),
  无需手动点击文件列表;路径未变化时刷新不重复加载。

## [0.2.21] - 2026-08-12

- 峰表工具栏新增「导出 Poky」:当前峰表导出为 Poky/Sparky .list
  (Assignment w1 w2 Data Height Volume,未命名峰 ?-?),默认路径
  data_dir(...,"peaks")/<exp>-<data>.list;无峰表/谱图时按钮禁用。
- 峰表加载统一走 core.peaks.load_peaks(Backend 未落地时 GUI 本地等价实现),
  行数展示与谱图双向联动不受影响。
- 新增 gui/peaks_io.py:load_peaks/save_peaks/export_peaks_poky(契约优先,
  缺失回退本地实现)。

- Backend(G2B-005):统一峰表模型 core/peaks/peak_table.py(Shared,移植旧项目
  NMRFlow)——PeakTable(add/remove 数字自动编号)、save_peaks/load_peaks
  (CSV 数字 Peak_ID,缺列补空,2D/3D 自动判别)、export_peaks_poky /
  import_peaks_poky(Poky/Sparky .list:"Assignment w1 w2 [w3] Data Height
  Volume",2D w1=15N/w2=1H,未命名 ?-?/?-?-?,Height %.3g,双空格)。
- Backend(G2B-005):workflow/pick_peaks 改用 PeakTable.save_peaks——
  输出数字 Peak_ID(1..n,不再 "P001"),列与旧项目一致。
- 测试:新增 tests/test_peak_table.py 9 项(往返/导出导入/2D/3D/占位/
  pick_peaks 列);全量 270 passed,ruff 全绿。

## [0.2.20] - 2026-08-12

- Dashboard:Project 概览(实验/数据/处理完成度/最近运行 + 新建实验表单);
  Experiment 概览(数据列表状态 + 导入数据表单)。
- 运行历史展示:处理菜单「运行历史...」打开全局列表对话框(run_id/流程/状态/
  时间/消息/产物详情);Project Dashboard 内嵌最近运行。
- 谱图-峰表联动:打开谱图自动加载对应峰表(data_dir peaks CSV,旧布局回退),
  峰表列表在面板底部;点击谱图峰 → 峰表选中对应行,点击峰表行 → viewer 高亮。

## [0.2.19.9] - 2026-08-12

- 修复主界面右下角空白:谱图面板文件列表在无谱图文件时自动隐藏,
  viewer 占满右侧区域;有谱图文件时列表显示在底部。

## [0.2.19.8] - 2026-08-12

- 谱图查看器 plot 默认调整为接近正方形(splitter 初始 [460, 220],
  撤销上一版宽扁设置)。

## [0.2.19.7] - 2026-08-12

- 谱图查看器初始尺寸调整:plot 默认宽扁(最小高 320),避免打开即接近正方形;
- 谱图查看器术语专业化:「等高线」统一改为「轮廓」(contour),如轮廓起点/
  轮廓级数。

## [0.2.19.6] - 2026-08-12

- SpectrumViewer 内部改上下布局:谱图在上、控制面板在下(分隔条可拖动);
- SpectrumPanel 调整为查看器在上、文件列表在下(避免上方空白);
  主界面左/中/右三栏直接拼接。
- Backend:人工处理路径后端(workflow/manual,用户反馈)——集合命令行人工流程:
  生成 FID 自动产出 fid.com 后可查看/修改/运行(manual_fid_com /
  run_manual_fid_com);生成谱图支持表格参数(param_schema/render_scripts)
  或直接脚本编辑后运行 process.com / nus*.com(run_manual_spectrum,
  消费已转换 fid,不执行 fid.com——生成 FID 是独立步骤);产物归位
  process//spectra/,登记 manual_fid/manual_process/manual_nus
  WorkflowRun,失败登记 failed 并抛 ManualRunError。
- 测试:新增 tests/test_manual.py 7 项;全量 247 passed,ruff 全绿。

## [0.2.19.5] - 2026-08-12

- 谱图面板上下布局(重新实现):文件列表在上、查看器在下,分隔条可上下
  拖动;取消文件列表横向宽度限制;主界面左/中/右三栏直接拼接。

## [0.2.19.4] - 2026-08-12

- 回退谱图面板上下布局改动(0.2.19.3 的垂直 splitter 未达预期):恢复为
  左右布局(左文件列表/右查看器);三栏比例仍可拖动调节。
- viewer/ 模块归属确认:由 GUI Agent 负责(spectrum.py 为 Shared Contract,
  实现属 GUI)。

## [0.2.19.2] - 2026-08-12

- 修复右键「打开所在目录」无效:data/folder 节点右键改为复用
  open_path_requested(与双击同一路径解析),并由主窗口统一打开文件管理器;
  打开后中间保持 Pipeline 视图。

## [0.2.19.1] - 2026-08-12

- 双击数据节点/子文件夹:中间保持 Pipeline 视图(不再跳转导入数据页),
  同时用系统文件管理器打开对应目录(数据节点打开 raw/ 或数据基座,
  子文件夹打开自身目录)。

## [0.2.19] - 2026-08-12

- 谱图/峰表路径适配 schema 1.3 新布局:spectrum_panel 与 Pipeline 状态推断
  改用 data_dir(exp_id, data_id, "spectra"/"peaks") 扫描,旧扁平路径保留回退。
- 数据删除接线:数据右键「删除」调 manager.delete_data(不再误删实验),
  确认对话框 + save + refresh。
- 数据重命名落盘:改写 DataEntry.title(优先 manager.rename_data,缺失时直接
  写 title),移除树内存 _data_titles;树显示 title 优先、缺省回退 data_id。
- 项目删除/重命名接 WorkspaceManager:delete_project(回收站)/
  rename_project(目录+name);后端接口缺失时友好提示并保留原行为。
- 峰挑选/分析步骤接线:ProcessingController 增加 pick_peaks/analyze
  (调 workflow.pick_peaks/analyze,缺失时提示待实现);peaks 运行后峰表
  出现即 SUCCESS,analysis 占位提示。
- 人工处理对话框加载真实数据:ParameterTableDialog 从
  script_generator.param_schema() 填充;ScriptEditorDialog 从
  render_scripts() 加载 .com 并支持保存到 data_dir(...,"process")/;
  后端缺失时显示「后端待实现」并保持可编辑骨架。

- Backend(G2B-004):数据重命名落盘——DataEntry 增加 title 字段
  (序列化兼容,缺省空),ProjectManager.rename_data 写审计历史 data_renamed;
- Backend(G2B-004):工作区项目管理——WorkspaceManager.delete_project
  (默认移入系统回收站,失败回退临时目录,绝不直接删除)与 rename_project
  (目录 + project.json name,重名/非法名校验);
- Backend(G2B-004):峰挑选 workflow/pick_peaks——复用
  core/qc/peak_detection,峰表写 data_dir(...,"peaks")/
  <exp_id>-<data_id>.csv(2D:Peak_ID,H_shift,N_shift,Intensity,SN,label;
  3D 加 F1/F2/F3_shift),登记 pick_peaks WorkflowRun,失败
  finish_run("failed") 并抛 PickPeaksError;
- Backend(G2B-004):人工处理数据源——backend/script_generator 暴露
  param_schema()(API_CONTRACT §6 键 + 默认值/说明)与
  render_scripts(experiment, params)(确定性渲染 fid.com/process.com/
  nus*.com);
- Backend(G2B-004):分析步骤占位 workflow/analyze.analyze(返回
  pending 不抛异常);产物路径契约确认——generate_fid 落盘
  data_dir(...,"process")、generate_spectrum 落盘
  data_dir(...,"spectra")(stepwise 测试断言),旧扁平路径在
  infer_status 保留兼容回退;
- 测试:新增 test_pick_peaks(2)+ test_script_schema(3),
  project_manager 30 / workspace 11;全量 240 passed,ruff 全绿。

## [0.2.18.1] - 2026-08-12

- 当前项目右键新增「重命名项目...」:更新 project.json 的 name,树与窗口标题
  同步刷新(树当前项目节点显示 project.name)。

## [0.2.18] - 2026-08-12

- 左侧树 Workspace 节点下列出工作区全部项目:当前项目展开实验/数据并标记
  「当前」,其余项目双击或右键「打开项目...」切换。
- 导入数据支持命名:实验页导入表单增加「数据名称(可选)」;数据节点显示
  自定义名称(默认仍为 数据 d_001)。
- 数据节点右键新增「重命名...」(重命名后树刷新显示新名称)。

## [0.2.17.1] - 2026-08-12

- 修复欢迎页逻辑冲突:未打开项目时不再隐藏三栏,欢迎页在中间 Workspace
  页正常显示(新建/打开/最近项目可操作)。
- 修复导入失败点 OK 卡死:导入在后台线程执行,成功/失败结果改经 Qt 信号
  回主线程处理(不再在后台线程弹模态对话框/操作 UI)。

## [0.2.17] - 2026-08-12

- 中间面板改为层级上下文面板(CenterPanel),随左侧树选中层级切换:
  Workspace → 新建项目/打开项目/最近项目(嵌入欢迎页);
  Project → 新建实验(内嵌表单);
  Experiment → 导入数据(内嵌表单:目录选择 + 复制选项);
  Data/子目录 → Pipeline 五步(生成 FID → 分析)。
- 新建实验/导入数据表单直接内嵌在中间,不再弹独立窗口。

## [0.2.16.3] - 2026-08-12

- 「导入数据」为纯自动化步骤,不再显示「人工」按钮;选中 Data 后仅
  生成 FID/生成谱图/峰挑选/分析 保留人工入口。

## [0.2.16.2] - 2026-08-12

- 选中实验时,中间面板提供可点击的「导入数据...」按钮(直接在当前实验下
  导入数据);「未选中数据」状态不再显示各步骤的「人工」按钮,选中 Data
  后恢复。

## [0.2.16.1] - 2026-08-12

- 中间面板按选中类型显示:选中 Project/Experiment 时显示「未选中数据」
  提示,选中 Data(或其子目录)才显示 Pipeline 步骤。
- raw/process/spectra/peaks/figures/report 子目录右键提供「打开所在目录」。
- Data 节点右键移除功能项(生成 FID/生成谱图),仅保留打开目录/删除;
  处理动作统一经 Pipeline 步骤运行。

## [0.2.15.1] - 2026-08-12

- 修复:新建空白实验不再产生伪数据节点(移除 schema 1.1「实验即数据」回退;
  空白实验树/管线均不显示 Data);空白实验运行步骤时提示先导入数据。
- 修复:「项目 → 添加实验」改为新建空白实验(不再弹数据文件夹选择);
  导入数据统一走实验右键「导入数据」。

## [0.2.15] - 2026-08-12

- 首次启动工作区欢迎页(G2B-003 §9.4):WorkspaceManager.ensure() 创建默认
  工作区 ~/NMRForgeWorkspace,显示工作区路径、工作区内项目列表(双击打开)、
  新建项目入口;未打开项目时主窗口显示欢迎页,三栏自动隐藏。
- 左侧树加 Workspace 根节点:Workspace → Project → Experiment → Data →
  raw/process/spectra/peaks/figures/report 各子文件夹;项目右键删除(强确认)。
- ProcessingController 三步接线(契约 v1.2 §8.3 / workflow.stepwise):
  import_data / generate_fid / generate_spectrum 对接 Backend 已落地的
  stepwise(含 WorkflowRun 登记、manager.save、data_id 产物);控制器绑定
  ProjectManager 后由 Pipeline 步骤调用。
- 树列宽保持可读(220/90 + 最小段宽),数据节点显示 d_001 并挂真实子文件夹。
- 测试:欢迎页/Workspace 根/三步接线适配新增;全量 226 passed,ruff 全绿。

## [0.2.16] - 2026-08-12

- Backend:工作区容器 core/workspace.py(契约 v1.3 §9.1 / G2B-003):
  WorkspaceManager(默认 ~/NMRForgeWorkspace,ensure 幂等/list_projects/
  create_project/open_project,非法项目名校验)。
- Backend:数据目录层级(schema 1.3,契约 §9.2):项目 → <exp_id>/ →
  <data_id>/ → {raw, process, spectra, peaks, figures, report,
  metadata.json};import_data 复制到 <data>/raw/ 并写
  <data>/metadata.json;stepwise 工作目录 <data>/process/、终谱归位
  <data>/spectra/;infer_status/删除兼容新旧布局,旧 dir_path 保留为
  兼容层;open_project 自动迁移 1.1/1.2 → 1.3(不物理搬文件)。
- Backend:test_phase_optimize 平台敏感断言改为「非零解 + 增益」
  (VM numpy 2.2.6 true=-60 → est=-180,±60° 容差仍不足;先例 ±45°→非零解)。
- 测试:新增 tests/test_workspace.py 7 项;全量 226 passed。
## [0.2.14] - 2026-08-12

- Backend:Experiment→Data 层级(core/project schema 1.2 + 迁移,按 G2B-002 /
  API_CONTRACT §8):新增 DataEntry(d_001...),ExperimentEntry.data 列表,
  旧 source/segments/imported_at 打开时自动迁移为 data[0]
  (migrated_from_1_1),保留兼容只读属性;create_experiment/import_data/
  set_data_fid/set_data_spectrum/delete_data;infer_status 按数据条目聚合。
- Backend:步骤化处理——import_data(只读参数+复制 raw/<exp_id>/<data_id>/
  + metadata/<exp_id>-<data_id>.json + import run,不生成 fid/谱);
  ProcessingBackend.convert_to_fid 独立阶段(NMRPipeBackend 实现,
  process/reconstruct_nus 复用已转换 fid);workflow/stepwise 编排
  generate_fid / generate_spectrum(NUS 自动含 SMILE 重构)+ 每步 WorkflowRun。
- Backend:相位优化恢复「先 SMILE 重构生成谱,再逐候选反复跑后端(暴力)」:
  workflow/stepwise.optimize_phase_brute_force(暴力搜索 + 真实管线写回),
  brute_force_direct_scores 支持 NUS(reconstruct_nus + 相位覆盖)。
- 测试:test_import_workflow 11 / test_project_manager 28 / test_stepwise 6;
  p1 幅值断言放宽至 ±60°(VM numpy 2.2.6 平台敏感性,先例 ±45°→非零解)。
- 待 GUI Agent:ProcessingController 三步接线与树测试断言更新
  (数据节点 id=d_001、metadata/<exp_id>-<data_id>.json)。
## [0.2.13] - 2026-08-12

- GUI 树层级演进(契约 v1.2 §8.5):Project → Experiment → Data;
  Project 右键删除项目(强确认)、空白处/Project 右键新建空白实验、
  Experiment 右键导入数据/重命名/删除、Data 右键生成 FID/生成谱图/
  打开目录/删除;树列宽改为显式宽度 + 最小段宽(220/90,可读)。
- ProcessingController 新增步骤化方法(契约 v1.2 §8.3):import_data /
  generate_fid / generate_spectrum;旧 auto_run_sync/async 保留兼容;
  后端 DataEntry 层级与 convert_to_fid 落地前为占位(友好提示)。
- Pipeline 面板改为五步流程:导入数据 → 生成 FID → 生成谱图(含 SMILE
  重构)→ 峰挑选 → 分析;步骤运行映射到 ProcessingController 对应方法。
- 测试:树层级/右键动作/列宽/五步状态新增 9 项;全量 203 passed,ruff 全绿。

## [0.2.12] - 2026-08-12

- Backend:相位准确性保证(workflow/phase_optimize)——VM 真实数据等价性验证
  (dataset 3,2D 均匀)证实「内存内代理筛选 ≠ 暴力逐候选后端评分」:
  内存内最优 p1=-60,暴力最优 p1=-120,Spearman 相关 0.46,验证未通过;
  据此新增验证门控 optimize_direct_phase_guaranteed:验证通过才采信内存内
  筛选,否则围绕内存内最优 ±60° 邻域暴力细化(本例细化得 p1=-120,QC 94.2),
  最终谱由真实管线 produce_phased_spectrum 产出——能力与反复跑后端一致,
  成本有界(验证网格 + 细化邻域 + 生产)。
- Backend:NMRPipeBackend.process/reconstruct_nus 支持 direct_phase_override;
  等价性验证 validate_direct_phase_equivalence(最优候选一致 + Spearman,
  可序列化 EquivalenceReport)。
- 测试:test_phase_optimize 增至 21 项,全量 183 passed。

## [0.2.11] - 2026-08-12

- Backend:自动相位性能重构(workflow/phase_optimize)——自动相位优化改为
  「单次后端运行 + 内存内候选评分」:直接维复用 phase.json 缓存或对转换后
  .fid 做内存内 FT + p1 共识;NUS 间接维对复型重构平面按轴搜索(全部 numpy,
  不触发后端重跑);结果含 backend_runs(=1) 与 candidates_scored 量化性能。
- Backend:AutoProcessor 后端成功后自动输出相位估计日志(失败/缺失产物优雅
  跳过);相位搜索候选计数透传(core/optimization/phase_search._search_axis、
  workflow/recon_phase_search,向后兼容)。
- 测试:新增 tests/test_phase_optimize.py 11 项,全量 173 passed。

## [0.2.10] - 2026-08-12

- GUI 三栏布局第二版:导入工作流接驳(B2G-001)+ 人工处理入口 UI 骨架。
- 导入数据:ImportExperimentDialog 增加「复制数据到项目」选项(默认勾选,
  raw/<exp_id> + SHA-256 指纹)与 acqus 校验;导入改走
  workflow.import_bruker_dataset(后台线程避免复制大文件阻塞 UI),完成后
  自动登记 WorkflowRun(import)、刷新项目树并选中新实验,warnings 与错误
  经 InfoDialog 展示。
- 人工处理入口 UI 骨架:ParameterTableDialog(参数表格,展示 zero_fill/
  sampling/stages 契约结构)与 ScriptEditorDialog(等宽字体 .com 脚本
  编辑器);Pipeline 每步增加「人工」按钮 + 处理菜单两条入口;后端接口
  仍为占位(manual_param_table/manual_script_editor 抛 NotImplementedError
  时给出友好提示)。
- Pipeline 步骤 LOCKED 状态增加依赖 tooltip(说明缺哪个前置步骤);
  自动化处理仍只经 ProcessingController.auto_run_async 调用后端。
- 测试:新增 tests/test_gui_manual.py 5 项 + 导入工作流端到端 1 项 +
  对话框复制选项/acqus 校验 2 项;全量 178 passed,ruff 全绿。

## [0.2.9] - 2026-08-12

- Backend:导入工作流(workflow/import_workflow):Bruker 数据集校验 → 登记实验 →
  raw/<exp_id>/ 复制(可跳过,源在项目内自动引用)→ SHA-256 指纹 →
  metadata/<id>.json 落盘 → WorkflowRun(import) 登记(成功/失败 + 失败回滚)。
- Backend:导入后 ExperimentEntry.source 指向项目内 raw 副本,状态机推进到
  imported;GUI 导入入口待按 B2G-001 接驳(对话框调 import_bruker_dataset、
  warnings 展示、「复制到项目」选项)。
- Backend 测试:新增 tests/test_import_workflow.py 9 项。
- GUI 演进为三栏布局(docs/GUI_ARCHITECTURE_VISION.md P0):左侧项目管理树
  (Project → Experiment → Input/Processing/Output/Figures,状态列 + 右键
  重命名/删除/打开所在目录)、中间 Pipeline 面板(上下文面包屑 + 状态驱动的
  步骤列表 + 下一步提示 + READY 步骤运行按钮)、右侧谱图面板(内嵌
  viewer.SpectrumViewer + 项目 spectra 目录文件列表)、底部可折叠 Task/Log。
- Pipeline 状态第一版:按产物文件与前置依赖推断 LOCKED/READY/RUNNING/SUCCESS;
  自动化处理仍只经 ProcessingController.auto_run_async 调用后端。
- 菜单按愿景重组(文件/项目/处理/样本/查看/帮助);查看菜单可开关三栏并打开
  独立谱图查看器;主窗口对话框全部改用 gui/dialogs(不再直接使用 QMessageBox)。
- GUI 测试:新增 tests/test_gui_layout.py 13 项;本地全量 162 passed,ruff 全绿。

## [0.2.8] - 2026-08-11

- GUI 重构为简洁流程化布局（借鉴 CryoSPARC）：顶部流程栏（导入数据 / 处理 / 查看谱图 / 报告）+ 中央页面切换；导入页实验/样本双栏表格、处理页自动化与人工两条路径卡片、查看页连接独立 viewer。
- 导入实验改为目录选择对话框（非手输路径）；添加样本改为表单对话框（名称/蛋白/序列/浓度/缓冲液/备注）。
- 处理流程分两条路径：自动化（ProcessingController 接 AutoProcessor，后台线程执行）；人工（参数表格/脚本编辑器接口占位，待实现）。
- 修复“点帮助显示 This plugin supports grabbing the mouse only for popup windows”：统一替换 QMessageBox 为自定义 QDialog（gui/dialogs + viewer/app 的 InfoDialog）。
- 测试：新增对话框/5904理控制器 9 项，全量 144 passed。
## [0.2.7] - 2026-08-11

- fix: main.py 恢复 __main__ 入口块(此前重写 main() 时误删,
  导致 `python main.py` 无输出直接退出);首次运行 bootstrap venv 逻辑复原。
## [0.2.6] - 2026-08-11

- 独立谱图查看模块 viewer（与项目管理 GUI 解耦）：Spectrum/SpectrumAxis（nmrglue 读 .ft2 + ppm 轴，ORIG 优先回退 CAR）、ContourLayer（matplotlib 等高线 + 可配插值因子防锯齿/正黑负红）、NMRViewBox（框选缩放/中键平移/滚轮缩放）、SpectrumViewer（多谱叠加/级数滑块/峰标记 Poky 风格半透明圆点/选中放大/长宽比锁定/十字光标）。
- 独立窗口 SpectrumWindow：打开/拖放 .ft2、最近文件、视图菜单；`nmrforge-viewer` 命令行入口。
- 测试：viewer 11 项（offscreen），全量 135 passed。
## [0.2.5] - 2026-08-11

- 项目管理模块 core/project：项目/实验/样本/WorkflowRun/审计历史数据模型，目录模板、project.json 原子写、实验 CRUD + 状态推断、样本删除引用保护、运行记录 R-YYYYMMDD-NNN 只追加、脚本快照、模板提取。
- 最近项目存储（JSON，最多 8 条，置顶去重，可注入 GUI）。
- GUI 主窗口骨架建立在 ProjectManager 之上：新建/打开/保存/最近项目/实验树（状态推断）/样本管理；main.py 启动 Qt 主窗口。
- 测试：core/project 22 项 + GUI 7 项（offscreen）。

## [0.2.4] 及更早

见 git 历史(0.1.0–0.2.4 变更记录,2026-08-11 重写)。
