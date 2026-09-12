# 02 · 安装与上手

## 1. 安装

NMRForge 以源码工作区方式使用(发行物只有 AppImage,见其 README):

```bash
git clone <NMRForge 仓库> ~/NMRForge        # 或使用已有的工作副本
cd ~/NMRForge
python -m venv nmrforge                     # 若还没有虚拟环境
~/NMRForge/nmrforge/bin/pip install -e .
```

> **重要**:接口包是新增的顶层包。若你之前已经装过 editable 版本,需**重跑一次
> `pip install -e .`**,否则 `import nmrforge_api` 会报 `ModuleNotFoundError`
> (editable 安装的包索引是安装时生成的)。

验证:

```bash
~/NMRForge/nmrforge/bin/python -c "import nmrforge_api as a; print(a.API_VERSION)"
# 0.1
```

真实处理需要 NMRPipe(`nmrPipe` 可执行文件,通常通过 `~/.cshrc` 提供环境)。
接口在**解析到 NMRPipe 安装目录时**会探测一次 `nmrPipe`/`smile` 版本并写进记录;
探测失败不影响运行(记录里就没有该版本)。

## 2. 准备数据

需要**Bruker 原始数据目录**:含 `acqus`,以及 `ser`(或 `fid`)/`acqu2s` 等。

```text
bmrXXXXX/1/
  acqus  acqu2s  ser  ...        ← 处理参数研究的输入
```

从公开库(BMRB、PDB 等)下载后解压即可,无需预处理。已处理过的谱(ft2/UCSF)
不能用于本接口,因为无法重跑处理。

## 3. 第一次运行(一步式)

```python
from nmrforge_api import run_parameter_study

result = run_parameter_study(
    "~/studies/hsqc_params",                  # 研究根(不存在会自动创建)
    "~/bruker_data/bmrXXXXX/1",               # 首次必须给;之后可省略
    axes={
        "window.F1.off": [0.35, 0.45, 0.55],  # 间接维窗函数
        "zero_fill": [1, 2, 4],               # 填零(数字分辨率)
    },
)

print(result.summary["delta_std_ppm"])
```

产出(研究根下):

```text
study/reference/<exp>_<data>/   参考谱、参考脚本、参考峰表、reference.json
study/runs/s0001/               每个组合:process.com + spectrum.ft2 + run.json
study/records/                  manifest / runs / peak_positions / uncertainty ...
```

## 4. 用命令行(适合集群/批处理)

```bash
python -m nmrforge_api init      --study ~/studies/hsqc_params --dataset ~/bruker_data/bmrXXXXX/1
python -m nmrforge_api reference --study ~/studies/hsqc_params
python -m nmrforge_api peaks     --study ~/studies/hsqc_params
cat > grid.yaml <<'YAML'
axes:
  "window.F1.off": [0.35, 0.45, 0.55]
  zero_fill: [1, 2, 4]
max_runs: 64
YAML
python -m nmrforge_api sweep     --study ~/studies/hsqc_params --grid grid.yaml
python -m nmrforge_api status    --study ~/studies/hsqc_params
```

## 5. 常见操作

**续跑**:同一命令重跑即可,已成功的组合不会重算(除非 `--no-resume` /
`resume=False`)。每个组合的状态在 `study/runs/<run_id>/run.json`。

**换数据集**:再传一次 `dataset=<新目录>`(或 CLI `--dataset`),参考谱会自动重建;
旧参考谱与运行记录保留在 `study/` 内(按 `exp_id_data_id` 分目录)。

**换网格**:改 `axes` 重跑。网格哈希写进 `records/manifest.json`;两次不同网格的
结果不要混在同一个 `runs/` 里比较——建议一个网格一个研究根。

**只重算汇总**(不重跑处理):

```bash
python -m nmrforge_api report --study DIR
```

**控制选峰**:默认在参考谱上自动选峰(阈值 35σ)。

```python
result = run_parameter_study(..., sigma_multiplier=25.0, max_peaks=100)
```

**用自己的峰表(可选)**:若你另有公开库指认表,传 `peaks=` 即可(会被冻结留档);
支持 Poky `.list` 或 `peak_id,H_ppm,N_ppm,...` 的 CSV。

```python
run_parameter_study(..., peaks="~/data/bmrXXXXX/reference_peaks.csv")
```

## 6. 典型耗时(参考量级)

VM 实测(2D 15N-HSQC,间接维 TD=256,uniform):参考谱(含统一相位优化)
约 30 s;每个参数组合约 5 s(复用 fid,相位已锁定);峰位测量 150 峰量级为
亚秒级。具体取决于数据规模与 NMRPipe 机器性能。
