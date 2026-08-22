# GUI 架构

## 目录

- gui/main_window.py:主窗口、导入/分段/批量入口、弹窗居中、内存护栏提示;
- gui/center_panel.py + panels.py:实验类型页(单个/分段/批量导入、注释表单);
- gui/pipeline_panel.py:步骤状态机与批量组;gui/pipeline_state.py:指纹与
  状态判定;
- gui/spectrum_panel.py:谱图面板(文件扫描、3D 面板、峰表);
- gui/project_tree.py / dashboards.py / welcome_page.py / settings.py /
  notes.py / raw_quality.py / report_panel.py / log_panel.py;
- viewer/spectrum.py(Spectrum/SpectrumAxis/Spectrum3D,Shared Contract)、
  spectrum_viewer.py、spectrum3d_panel.py、contour_layer.py、
  nmr_viewbox.py、axis_labels.py(核推断/标签)、phase_panel.py、app.py
  (独立查看器)。

## 原则

- GUI 只通过 ProcessingController(gui/processing.py)触达后端,禁止直接
  import backend/workflow 细节;
- 不使用 QMessageBox(Windows+Qt6 鼠标抓取警告),统一自定义 QDialog;
- 3D 轴序:终谱存储序 (F2,F3,F1);加载时按 FDDIMORDER 建立数据轴→FDF
  块映射并重排到逻辑序(0.2.151),标签与 ppm 轴必须一一对应;
- 谱图面板按数据级 spectra/ 目录扫描 .ft2/.ft3(0.2.112),不假设文件名前缀。

## 接口

见 docs/API_CONTRACT.md §5(ProcessingController)、§10(Spectrum3D)。
历史设计愿景:docs/GUI_ARCHITECTURE_VISION.md。
