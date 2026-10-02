# GUI 架构

## 目录

- gui/main_window.py:主窗口、导入/分段/批量入口、弹窗居中、内存护栏提示;
- gui/center_panel.py + panels.py:实验类型页(单个/分段/批量导入、注释表单);
- gui/pipeline_panel.py:步骤状态机与批量组;gui/pipeline_state.py:指纹与
  状态判定;
- gui/spectrum_panel.py:谱图面板(文件扫描、3D 面板、峰表、放大模式双谱比对);
- gui/project_tree.py / dashboards.py / welcome_page.py / settings.py /
  notes.py / raw_quality.py / report_panel.py / log_panel.py;
- viewer/spectrum.py(Spectrum/SpectrumAxis/Spectrum3D,Shared Contract)、
  spectrum_viewer.py、spectrum3d_panel.py、contour_layer.py、
  nmr_viewbox.py、axis_labels.py(核推断/标签)、phase_panel.py、app.py
  (独立查看器)。

## 原则

- 查看器完整/懒加载轴序须按 FDDIMORDER，与峰表同源；重复核不能凭相同标签互换。
  SpectrumAxis.orig_hz 用 None 表示缺失，0 是有效值；参考筛选跳过原因在中间报告可见。

- 双谱比对的垂直绘图区以左侧为基准等高：布局完成后对齐，窗口缩放/拖动同步；混合维度
  为两侧控制区保留最低空间。只同步几何，不联动参数，退出比对停止同步。

- 运行成功通知与普通刷新分开：`spectrum_results_changed(exp_id,data_id)` 目标在任务开始
  冻结，经 MainWindow 到 `SpectrumPanel.refresh_after_processing`，仅当前数据匹配才重读
  主谱及缓存。2D/3D 均通知；普通刷新不重复读取，失败不发成功信号。

- 滑块数值输入共用 `ui_support/numeric_inputs.py`；键入草稿仅回车确认，箭头/滑块即时调整。
- 工具栏“数据质量检查”明确分为单 FID/切片 FID 文件夹；取消只关闭当前选择器，不触发另一
  选择器。日志目标在启动时冻结为全局，后台输出不随树选择变化。
- 同步/后台 3D 加载统一走 `SpectrumPanel._display_3d_spectrum`，从投影返回时在平面和比例
  恢复后展开完整视野；同一 3D 切片原位刷新保留用户缩放。
- GUI 只通过 ProcessingController(gui/processing.py)触达后端,禁止直接
  import backend/workflow 细节;
- 不使用 QMessageBox(Windows+Qt6 鼠标抓取警告),统一自定义 QDialog;
- 3D 轴序:终谱存储序 (F2,F3,F1);加载时按 FDDIMORDER 建立数据轴→FDF
  块映射并重排到逻辑序(0.2.151),标签与 ppm 轴必须一一对应;
- 谱图面板按数据级 spectra/ 目录扫描 .ft2/.ft3(0.2.112),不假设文件名前缀。
- 放大模式的“比对”菜单跨项目枚举未删除数据的主结果谱；选中后当前谱与比对谱左右并列，
  各自持有独立 `SpectrumViewer` 和显示/3D 控制。比对为只读视图，公共文件列表与峰表仍留在右侧。

## 接口

见 docs/API_CONTRACT.md §5(ProcessingController)、§10(Spectrum3D)。
历史设计愿景:docs/GUI_ARCHITECTURE_VISION.md。
