# 选峰与定位

选峰跑在已处理的谱上,产出峰表。检测与定位是两个阶段,各自都留档做了什么。

## 检测

`workflow/pick_peaks.py::pick_peaks` 在已处理的谱上检测峰,写出 POKY 风格的 `.list` 表,
并登记一条 `WorkflowRun`。

| 参数 | 含义 |
| --- | --- |
| `sigma_multiplier` | 以噪声估计倍数表示的阈值(缺省 35σ)。显式给值时,实际使用的值连同来源一起留档。 |
| `edge_margin_ppm` | 谱边缘的排除边距,以**物理宽度(ppm)**表示(缺省:该核线宽的三倍)。 |
| `edge_margin_points` | 以点数为单位的口子。不推荐:不同数字分辨率下不可比。 |
| `localization_method` | `"parabolic"`(缺省)或 `"gaussian"`(仅 2D)。 |
| `gaussian_roi_f1_ppm`、`gaussian_roi_f2_ppm` | Gaussian 拟合 ROI 半径(间接维 / 直接维),以物理宽度表示;缺省取配置里的 `peaks.localization`。 |
| `ref_peaks`、`ref_nuclei`、`tolerance_ppm` | 按参考峰表限定或匹配(见下)。 |

为什么边距按 ppm 定义、运行时再换算成点:填零会改变点距但不改变物理宽度,所以按 ppm 定义的
边距在填零前后覆盖同一段谱区;按点定义则会默默缩水。

### 带参考峰表约束的选峰

给了参考峰表时,只保留核种能对上的峰。2D 要求所有核种都对上;拿 2D 参考去选 3D 谱时第三维是
自由的,所以一个参考峰可能对上多个检出峰。这才让「跟着同一个峰看参数扫描」有意义,而不是
只举一两个例子。

## 定位

检测找的是采样点上最大的那个点;定位估计峰真正落在采样点之间的哪里。

| 方法 | 做法 | 维度 |
| --- | --- | --- |
| `parabolic` | 逐轴独立的三点抛物线精修 | 任意(缺省) |
| `gaussian` | 以抛物线结果为初值的二维高斯最小二乘拟合 | **仅 2D** |

值得依赖的行为:

- **对 1D/3D 谱要求 Gaussian 拟合是报错,不是静默回退。** 请求的方法与实际使用的方法都留档,
  所以「替换」永远不会看起来像你要的那套分析;
- **拟合失败退回抛物线**,并记录回退原因;
- **拟合是有意设上限的。** 逐轴拟合窗口半径可以封顶
  (`peaks.localization.gaussian_roi_max_points`),每次拟合的函数求值次数也封顶
  (`gaussian_max_nfev`)。这样细填零的网格不会把选峰变成无界优化;
- **两种方法跑在同一个检出候选上**,所以用 `localization: both` 时结果可直接比较。

每次定位都在峰表旁边写一份附件 `<峰表>.localization.json`,里面有 `requested_method`、
`actual_method`、`fallback_reason` 与逐峰记录。run 记录在 `params['localization']` 下带同样的信息。

## 峰表格式

默认交换格式是 POKY 风格的 `.list`:

```text
label    F2_ppm    F1_ppm    height    ...
```

导入导出由 `core/peaks/peak_table.py` 处理,它同时写出轴单位与从谱头推导的核种标签
(`core/peaks/axis_units.py`)。核种归属取自 NMRPipe 头槽位,不靠顺序猜。

## 怎么用

GUI:谱生成之后,流水线里的「峰挑选」步骤。峰出现在右侧表里,点一行查看器就跳到那个峰。

Python:

```python
from workflow.pick_peaks import pick_peaks

result = pick_peaks(
    manager,
    "exp_001",
    "data_001",
    localization_method="gaussian",
)
print(result["peak_count"], result["peak_path"], result["localization"])
```

脚本接口:峰定位按组合选择 `localization="parabolic" | "gaussian" | "both"` —— 见
[external-api/03-api-reference.md](external-api/03-api-reference.md)。

## 配置

```yaml
peaks:
  localization:
    method: parabolic            # parabolic(缺省)| gaussian(仅 2D)
    gaussian_roi_f1_ppm: 1.5     # 间接维 ROI 半径
    gaussian_roi_f2_ppm: 0.25    # 直接维 ROI 半径
    gaussian_roi_max_points: 0   # 0 = 不封顶;正数封顶 ROI 半宽
    gaussian_max_nfev: 200       # 每次拟合函数求值上限
```

## 边界

- 选峰需要已处理的谱,所以需要 NMRPipe;解析与导出已有峰表不需要;
- Gaussian 定位只支持 2D 是模型本身如此,不是漏做:现在的拟合就是二维模型;
- 缺省阈值是约定,不是测量结果。如果发表要依赖某个特定阈值,请显式写出来,这样留档的才是你要的值。
