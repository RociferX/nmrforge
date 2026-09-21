# GUI Architecture

## Directory

- Gui/main_window.py: main window, import/segmentation/Batch entry, pop-up window in the centre, memory guard prompt;
- Gui/center_panel.py + panels.py: experiment type page (single/segmentation/Batch import, comment form);
- Gui/pipeline_panel.py: step state machine and batch group; gui/pipeline_state.py: fingerprint and
  Status determination;
- Gui/spectrum_panel.py: spectrum panel (file scan, 3D panel, peak table);
- gui/project_tree.py / dashboards.py / welcome_page.py / settings.py /
  notes.py / raw_quality.py / report_panel.py / log_panel.py;
- viewer/spectrum.py(Spectrum/SpectrumAxis/Spectrum3D,Shared Contract),
  spectrum_viewer.py, spectrum3d_panel.py, contour_layer.py,
  Nmr_viewbox.py, axis_labels.py(Kernel inference/Label), phase_panel.py, app.py.
  (standalone viewer).

## In principle

- GUI Only access the backend through ProcessingController(gui/processing.py), direct access is prohibited
  Import backend/workflow details;
- Do not use QMessageBox (Windows+Qt6 mouse capture warning), uniformly customize QDialog;
- 3D axis order: final spectrum storage order (F2, F3, F1); press FDDIMORDER when loading to create data axis -> FDF
  Blocks are mapped and rearranged into logical order (0.2.151), and the labels and ppm axes must correspond one-to-one;
- The spectrum panel scans the data level spectra/ directory.ft2/.ft3(0.2.112), without assuming file name prefixes

## Interface

See docs/API_CONTRACT.md §5(ProcessingController), §10(Spectrum3D).
Historical Design Vision:docs/GUI_ARCHITECTURE_VISION.md.
