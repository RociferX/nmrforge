# 快速开始

本页带你从 v1.0.0 走到第一个可用的结果:可以直接用 Linux AppImage(自带环境,界面中英运行时切换),
也可以按源码安装。下面两条路都走一遍。

## 路线 A —— 源码安装与 GUI

```bash
git clone https://github.com/RociferX/nmrforge.git
cd nmrforge
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[test]"
python main.py
```

真实处理需要在同一台机器上安装 NMRPipe;SMILE 随它提供。nmrForge 不捆绑、也不下载这两个
引擎。如果它们不在,数据检查仍然可用,程序会明确报出处理能力不可用。

第一次打开程序,先看 **`帮助 → 使用教程`**:一份从导入数据走到峰表的完整走查,界面语言换了正文也跟着换。

将来的 AppImage 路径及其额外的 PySide6/Qt 分发检查,预留在
维护者私有仓库里的发布检查清单。

## 路线 B —— 开发者:不装 NMRPipe 也能检查数据集

这一步全用纯 Python:Bruker 参数解析、维度判定、实验判定、采样判定,以及读取时域数据。
不需要安装 NMRPipe。

```bash
git clone <this repository>
cd nmrForge
pip install -e ".[test]"
```

造一个小合成数据集(文件头加一段合成 FID,明确标注不是真实数据),
或者把走查脚本指向你自己的某个 Bruker 数据集目录:

```bash
python examples/make_synthetic_dataset.py --out example_data/hsqc_2d
python examples/quickstart.py example_data/hsqc_2d
```

走查脚本会按顺序打印:

1. 找到了哪些参数文件(`acqus`/`acqu2s`/`acqu3s`);
2. 维度数,以及逐维的核、角色、TD 与扫宽;
3. 判定出的实验类型及依据;
4. 采样判定(uniform / NUS / uncertain)及依据;
5. 后端将使用的时域存储布局;
6. 是否找到 NMRPipe 与 SMILE,以及接下来该跑的确切命令。

如果第 6 步报「NMRPipe NOT FOUND」,那么处理暂时不可用 —— 但数据理解与 QC 仍然可用。
这条边界是有意为之:nmrForge 拒绝假装自己能在没有引擎的情况下处理。
想看维度处理与 NUS 判定,可以试 3D 例子:

```bash
python examples/make_synthetic_dataset.py --out example_data/hnca_3d --ndim 3 --nuclei 13C,15N,1H --nus
python examples/quickstart.py example_data/hnca_3d
```

## 配置 NMRPipe

后端按这个顺序查找 NMRPipe(`backend/nmrpipe_finder.py`):

1. 配置里的 `backend.nmrpipe.path`(一个目录或一个可执行文件);
2. `backend.nmrpipe.nmrpipe_bin`;
3. `PATH`;
4. `csh` 环境,也就是终端 source 过 NMRPipe 环境之后的样子;
5. 常见安装位置。

`nmrforge_data/config/nmrforge.yaml` 保存默认值;要做机器本地覆盖,就把它复制成
`nmrforge_data/config/nmrforge.local.yaml`(该文件被 git 忽略,因此机器路径永远不会进仓库):

```yaml
backend:
  nmrpipe:
    path: /opt/NMRPipe/nmrbin.linux212_64
```

## 跑处理路径

开发者可以在命令行驱动同一套引擎;完整参考见
[external-api/04-cli-reference.md](external-api/04-cli-reference.md):

```bash
python -m nmrforge_api init      --study ./study --dataset /path/to/bruker/dataset
python -m nmrforge_api reference --study ./study
python -m nmrforge_api peaks     --study ./study
python -m nmrforge_api sweep     --study ./study --grid grid.yaml --reference study/reference.json
```

一个「study」是一个自包含的根目录。参考谱、它的脚本与峰表只冻结一次;
此后参数组合都以该参考为基准评估,每个组合各自保留自己的脚本、候选谱、峰表、运行记录与告警。

## 结果在哪里

- [external-api/06-outputs-and-records.md](external-api/06-outputs-and-records.md) ——
  研究目录布局:`manifest.json`、`runs.json`、`peak_positions.csv`、`uncertainty.csv`、
  逐运行的脚本与谱图。
- [qc-system.md](qc-system.md) —— 质量指标的含义,以及怎么读报告。
- [processing-model.md](processing-model.md) —— 处理方案是怎么构建出来的。

## 下一步

- [installation.md](installation.md) —— Linux AppImage 与源码安装两条路
- [external-dependencies.md](external-dependencies.md) —— NMRPipe/SMILE
- [troubleshooting.md](troubleshooting.md) | [faq.md](faq.md)
