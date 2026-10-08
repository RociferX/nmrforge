# 真实数据实测证据(公开数据 · 四条处理路径)

> **本轮换数据(2026-10-08)**:本页原先只用一套公开数据(某病毒蛋白的 2D HSQC,BMRB timedomain
> 条目)。为让证据覆盖产品实际支持的四条处理路径,并把数据来源全部换成**非病毒、非致病
> 生物来源**的公开数据,本页整体重做为**四套数据 × 四条路径**。
>
> **数字状态**:本页保留 2026-09-22 那套 2D uniform 数据的**实测数字**(它是当时真机跑出来的,
> 不重算);新增的三条路径(NUS ×2、3D uniform)的**数字列在真机运行后回填** —— 表格里标
> `待真机` 的格子就是还没跑过的,不许当成已有结论引用。换数据的动机与选型见第 0 节。

本页用**四套公开数据**,对应产品的四条处理路径。原始数据全部公开可下载,**任何人都能取同一份
数据复算**;期望峰位取自公开条目里**作者沉积的化学位移**(期望峰表本体不进仓库,只留 sha256)。

| # | 路径 | 数据 | 来源生物 | 为什么选它 |
| --- | --- | --- | --- | --- |
| ① | 2D uniform | BMRB timedomain **27493**,数据集 `Apo_CBL_0.4mM/2` | *Bacillus subtilis*(枯草芽孢杆菌,土壤细菌) | 真 Bruker `acqus/acqu2s/ser`;**自带 NMRPipe 处理的 `pdata/1`**,可比性最好 |
| ② | 2D NUS | SMILE 官方示例 2:`trosyJHH`(20% NUS 2D TROSY) | 标准蛋白样品 | **真实采集的 NUS**,Bruker `ser` + 官方采样表;**参照谱在 `*.data.tar.gz`** |
| ③ | 3D uniform | BMRB timedomain **15217**,`SR358_B600_3D_HNCO.fid` | *Bacillus subtilis*(枯草芽孢杆菌) | 真 Bruker `acqus/acqu2s/acqu3s/ser` + `pdata/1`,`pdata/2` |
| ④ | 3D NUS | ③ 的 BMRB 15217 HNCO **降采样**成 NUS(`uniform_to_nus`,真机生成) | *Bacillus subtilis* | 真 Bruker `acqus/acqu2s/acqu3s/ser`;15N 64 × 13C 80 **天然每维 ≤ 85**;取样表**本项目生成** |

## 0. 为什么换数据、怎么选的

**换数据的直接原因**:原先那套是**病毒蛋白**的 HSQC。病毒字样在对外发布、内容审查与自动化
分类里都是敏感项,而本项目需要一套能被长期公开引用、不引入无关争议的证据数据。因此本页把
数据源整体换成**非病毒、非致病**来源。

**选型的四条硬约束**(任何一条不满足就不选):

1. **生物安全中性** —— 只取细菌、人、植物、真菌或**合成/标准样品**。排除病毒、致病菌与毒素类
   (例如 *Photorhabdus luminescens* 的 Tc 毒素家族、白喉/肉毒/蓖麻毒素等一律不选)。
2. **必须是真 Bruker 时域数据** —— 目录里要有 `acqus`/`acqu2s`(`/acqu3s`)+ `ser`。这一条
   卡掉了大量看起来合适的 NESG 老条目:**它们多数是 Varian 格式**(`procpar`/`fid`,没有
   `acqus`/`ser`),喂不进 Bruker 读取器。BPTI(5307)、Z domain(5656)、*E. coli* YacG(5335)
   都属于这一类,虽然小而无害,但**格式不对**。
3. **网格必须小** —— 见下条的内存口径。3D 间接维网格越大,SMILE 重构的内存越夸张,真机会跑不动。
4. **有可比对的参照** —— 要么数据自带 `pdata/`(数据提供方自己处理的谱),要么是 NUS 且官方
   给了重建结果。这样第 1 节的「自动处理 vs 参照」才有对象可比。

### 网格为什么必须小:SMILE 的内存口径

产品自己的护栏(`backend/memory_guard.py`)按 SMILE 启动横幅标定的模型估峰值内存:

```text
峰值 ≈ 直接维点数 × 迭代FT尺寸(x) × 迭代FT尺寸(y) × 16 B × 1.06
迭代FT尺寸 = next_pow2(3 × NusTD),下限 256
```

按这个式子算,3D NUS 的峰值内存随间接维网格阶跃:

| 间接维网格(每维) | 迭代 FT | 峰值(直接维 168 点) | 是否安全 |
| --- | --- | --- | --- |
| ≤ 85 | 256 × 256 | ≈ 0.17 GB | ✅ 安全区 |
| 86 – 170 | 512 × 512 | ≈ 0.70 GB | ✅ 安全 |
| 171 – 292 | 1024 × 1024 | ≈ 2.78 GB | ⚠️ 贴着 **2.8 GB** 上限 |
| 更大 | 更大 | > 2.8 GB | ❌ 超出护栏 |

> 校验方式:上表不是手算的,是用 `backend/memory_guard.py::smile_iteration_ft_size` 本身逐点
> 跑出来的(85 → 256、86 → 512、170 → 512、171 → 1024),峰值按同模块的
> `MB_PER_FT_PLANE`(16 B/点)与 `FT_OVERHEAD`(1.06)代入;292 → 1024×1024 → 2.78 GB
> 与模块文档里记录的那次 sampleK 启动横幅实测一致。改护栏常量时这张表要跟着重算。

**所以「小网格」在本项目里有确定含义:3D 间接维每维控制在 85 以内** —— 这时迭代 FT 落在最小的
256×256 档,峰值不到 0.2 GB,真机上跑起来是秒级到分钟级,不会把机器压垮。

> **只有 3D 走这个公式**。2D 路径 `estimate_smile_peak_mb` 直接返回
> `MB_FLOOR_2D = 128 MB`,与网格无关 —— 所以 2D NUS 即使 NusTD 很大也很轻,不要拿上表去套 2D。

### 0.1 四套数据的**实测**网格(2026-10-08 拉取原始头部核对)

上面那段原来是「按声明推断」;现已把这四套数据的 `acqus`/`acqu2s`/`acqu3s` 真正拉下来读过
(用 `urllib`,本机 `curl` 的 schannel 取不到凭证),数字如下 —— **这是实测,不是推断**:

| 路径 | 数据 | 直接维 | 间接维实测 | 3D 峰值内存(护栏公式) | 结论 |
| --- | --- | --- | --- | --- | --- |
| ① 2D uniform | BMRB 27493 `Apo_CBL_0.4mM/2` | 1H TD=2048 | 15N TD=180(**uniform**,`NusTD=180` 但无 nuslist 语义) | 2D 路径:**128 MB** | ✅ 很轻 |
| ② 2D NUS | SMILE 例 2 `trosyJHH` | 1H TD=8192 | 15N `NusTD=740`;官方 `smile.log`:**20% NUS**,370 点,实测 **130.1 MB** | 2D 路径:**128 MB** | ✅ 很轻 |
| ③ 3D uniform | BMRB 15217 `SR358_B600_3D_HNCO.fid` | 1H TD=1024 | 15N **TD=64**、13C **TD=80** | uniform 不走 SMILE;NMRPipe 侧按点数 | ✅ **每维 ≤ 85** |
| ④ 3D NUS | SMILE 例 4 `hnco` | 1H TD=2048 | 13C **TD=3200**、15N ~171;5% NUS | **≈ 44 GB**(FT 16384×1024) | ❌ **违反「小网格」约束** |

**④ 必须换掉**。SMILE 官方示例 4 的 13C 间接维 `TD=3200`,迭代 FT 是 16384;官方自带的
`smile.log` 白纸黑字写着 **`Memory used by SMILE: 9.3 GB`**(他们那台机器上,4 线程,
9 轮迭代,1.2 分钟)。按本项目的护栏公式算是 **44 GB**,远超文档写明的 2.8 GB 上限。
这与用户「确保不要找大网格的,避免运行负载过大」的要求**直接冲突**,所以本轮把它换掉。

**换成的替代方案与取舍**:把 SMILE 其余 3D 示例的头部都扫了一遍(见下表),**没有一个**满足
「每维 ≤ 85」;最小的是 `ubiq_noesyhsqc_15N`(15N TD=80、1H TD=620)→ ≈ 1.39 GB。

| SMILE 3D 示例 | 间接维网格 | FT | 峰值 |
| --- | --- | --- | --- |
| `ubiq_noesyhsqc_15N` | 80 / 620 | 256 × 2048 | ≈ 1.39 GB |
| `ABeta_noesyhsqc_15N` | 64 / 900 | 256 × 4096 | ≈ 2.78 GB |
| `hncoconh` | 310 / 310 | 1024 × 1024 | ≈ 2.78 GB |
| `hnco`(原选) | 3200 / 171 | 16384 × 1024 | ≈ 44 GB ❌ |
| `noesyhsqc_13C` | 48048 / 1 | 262144 × 256 | ≈ 178 GB ❌ |

**结论与处置(诚实版)**:在**公开的真实采集 3D NUS 数据**里,找不到同时满足「每维 ≤ 85」的
Bruker 数据集 —— 3D NUS 的本质就是省掉间接维采样点,官方示例为演示重建质量反而用了较大的完全网格。
所以 ④ 这一格有三条路,本页选第 3 条:

1. ~~用 SMILE 官方 `hnco`~~ —— **否决**,44 GB,直接违反用户约束;
2. 用项目自带的 `tests/fixtures/bruker/nus_3d`(NusTD 48/128)→ ≈ 0.41 GB —— **可行且最小**,
   但它是**合成夹具**(无真实 `ser`),当证据的说服力弱;
3. **用 ③ 的 BMRB 15217 HNCO(15N 64 × 13C 80,天然每维 ≤ 85)降采样成 NUS** ——
   取样表由 `nmrPipe` 的 `uniform_to_nus` 生成(或本项目 2D 路径的
   `scripts/vm_sample_make_nus.py` 同口径做法)。**本页选这条**:真 Bruker 采集数据、小网格,
   代价是**采样表由本项目生成、不是原始采集的 NUS**。

> **`scripts/vm_sample_make_nus.py` 只支持 2D**(它按 `acqu2s TD / mult` 算复点网格,不读
> `acqu3s`)。所以 ④ 的 NUS 取样必须在真机用 NMRPipe 的 `uniform_to_nus` 做(见第 5 节),
> **不能**指望那个脚本 —— 这一点原先写错了,已改正。

**性质区分(读结论前必须看清)**:

| 路径 | 数据性质 | 取样表来源 |
| --- | --- | --- |
| ① 2D uniform | 原始采集,全采样 | — |
| ② 2D NUS | **原始采集的真 NUS**(官方 `smile.log`:20%) | SMILE 官方 |
| ③ 3D uniform | 原始采集,全采样 | — |
| ④ 3D NUS | 原始采集(③)**降采样**成 NUS | **本项目生成** |

三条路径的数据来源性质不同,读结论时必须区分 —— ④ 因此也不做真值回收(第 2 节),
只用目视对照 + QC。

## 1. 谱图对比:自动处理 vs **参照处理谱**

四套数据各出一张对比图:左列是**本软件的自动处理**终谱,右列是**该数据自带的参照处理谱**
(BMRB 条目用其 `pdata/`;SMILE 示例用 `*.data.tar.gz` 里的官方处理/重建谱 —— 两者都由数据
提供方处理,不是本软件的产物)。
两列用**同一个 ppm 窗口**、同一套等高线口径(各自按自身最大值归一),所以可以直接目视比较峰形。

**这一组图不标任何峰位** —— 用来看谱图质量(峰形、相位、基线、伪影);峰位对照在第 2 节。

> 图随真机运行产出后写入本目录,文件名与命令见第 5 节。在跑之前这里**不放示意占位图**:
> 拿一张不是这四套数据跑出来的图当证据,比没有图更糟。

## 2. 真值回收:处理结果对得上**沉积化学位移**

第 1 节的图是定性对照,这一节给数字。口径(与 `workflow/truth_benchmark.py` 同源):

- **匹配**:`d = hypot(Δ1H/tol_H, Δ15N/tol_N) <= 1`,**一对一贪心最近优先**;一个检出峰只能配
  一个期望峰,被抢走的期望峰记「未检出」;只有落在谱实际覆盖的 1H/15N 窗口内的期望峰进分母。
- **参照**:沉积化学位移与谱自身参照差一个常数,按「容差逐级收紧的网格扫描」标定**一次**后
  **冻结**(标定只看匹配数,不参与选窗/选参)。
- **偶然背景**:把期望峰表**逐峰独立平移** >=5 个匹配半径(固定 seed、200 次),用同一套匹配算
  「随便摆也能配上」的比例 —— 回收率必须连背景一起读。
- **检出与精修都用产品自己的口径**,不改候选峰、不为凑匹配换峰。

| | ① 2D uniform<br>BMRB 27493 | ② 2D NUS<br>SMILE 例 2 | ③ 3D uniform<br>BMRB 15217 HNCO | ④ 3D NUS<br>③ 降采样 |
| --- | --- | --- | --- | --- |
| 软件自判实验/采样/维度 | 待真机 | 待真机 | 待真机 | 待真机 |
| 输入指纹(文件数 / 字节 / sha256) | 待真机 | 待真机 | 待真机 | 待真机 |
| 期望峰表来源 | BMRB 条目第一方 HSQC 峰表(沉积化学位移),`filter=backbone` | **不适用**(无沉积峰表) | 同 ①,条目 15217 | **不适用**(无沉积峰表) |
| 期望峰表(sha256;行数 → 落在谱窗口内) | 待真机 | — | 待真机 | — |
| 终谱形状 | 待真机 | 待真机 | 待真机 | 待真机 |
| 全局参照平移(ppm,标定后冻结) | 待真机 | — | 待真机 | — |
| 检出峰数 | 待真机 | 待真机 | 待真机 | 待真机 |
| **紧容差回收率**(1H 0.01 / 15N 0.05 ppm) | 待真机 | — | 待真机 | — |
| 同口径偶然背景 | 待真机 | — | 待真机 | — |

> ②④ 那两列画 `—` 不是「没测」,是**这条判据对它们不成立**:SMILE 官方示例没有沉积化学位移,
> 没有独立于本软件的外部真值可用。硬要给它们编一张峰表就变成自己造真值,所以这两套只按第 1 节
> 的目视对照与第 3 节的 QC 评分呈现 —— 边界说清楚,比凑一个数字诚实。

**①③ 的真值从哪来(不许手工拼)**:BMRB 对该条目提供**第一方** HSQC 峰表接口
(`api.bmrb.io/.../simulate_hsqc?format=csv&filter=backbone`),它本身就是从该条目**沉积化学
位移**算出来的。取回后用 `scripts/bmrb_expected_to_csv.py` 转成真值脚本要的三列格式:

```bash
curl -o expected_27493_bmrb.csv \
  "https://api.bmrb.io/current/entry/27493/simulate_hsqc?format=csv&filter=backbone"
python scripts/bmrb_expected_to_csv.py --input expected_27493_bmrb.csv --entry 27493 \
    --output expected_27493.csv --report expected_27493.report.json
```

转换器只做列名映射与来源留档(写输入 sha256),**不筛「哪些峰该出现在谱里」** —— 峰是否落在
谱实际覆盖窗口内由真值基准自己按谱范围过滤(窗口外的峰进分母是冤枉人)。期望峰表本体不进仓库,
只把 sha256 与峰数填进上表。

容差阶梯固定为 `(0.01, 0.05) / (0.02, 0.10) / (0.05, 0.50) ppm`:松容差人人过关,必须有紧容差
才说明「峰位真的对上了」。档越松,同口径的偶然背景越高,所以只有紧、宽松两档能当判据,更宽松档
只用来归类。

**阈值口径(必须一起读)**:检出阈值由使用者按样品选,软件不替使用者决定。产品默认的 35σ 是
为强信号液体谱准备的默认值,它的价值是「在更多情形下都适用」,**不是**衡量某套数据的标尺。
参考流程在这类数据上选定的 12σ 只用于窗选择时的「应有峰」峰集(`workflow/window_optimize.py`),
与产品选峰默认阈值是两个独立常量,不互相覆盖。

## 3. QC 评分(四套数据)

同一口径 `sign_mode="auto"`(先判「单符号正峰 / 单符号负峰 / 正负共存」再评分);**自动处理终谱与
参照处理谱都裁到同一窗口再评分**,两行才可比:

| 数据 | 谱 | 综合分 | 判定 | 信噪比 | 相位 | 基线 | 伪影 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ① 2D uniform | 自动处理终谱 | 待真机 | 待真机 | 待真机 | 待真机 | 待真机 | 待真机 |
| ① 2D uniform | 数据自带 `pdata/1` | 待真机 | 待真机 | 待真机 | 待真机 | 待真机 | 待真机 |
| ② 2D NUS | 自动处理终谱 | 待真机 | 待真机 | 待真机 | 待真机 | 待真机 | 待真机 |
| ② 2D NUS | 官方重建谱 | 待真机 | 待真机 | 待真机 | 待真机 | 待真机 | 待真机 |
| ③ 3D uniform | 自动处理终谱 | 待真机 | 待真机 | 待真机 | 待真机 | 待真机 | 待真机 |
| ③ 3D uniform | 数据自带 `pdata/1` | 待真机 | 待真机 | 待真机 | 待真机 | 待真机 | 待真机 |
| ④ 3D NUS | 自动处理终谱 | 待真机 | 待真机 | 待真机 | 待真机 | 待真机 | 待真机 |
| ④ 3D NUS | 官方重建谱 | 待真机 | 待真机 | 待真机 | 待真机 | 待真机 | 待真机 |

相位评分看全谱正负质量分布,回收率看指定峰位上有没有峰,两者不矛盾。**引用 QC 分数请连口径
(含窗口)一起给。**

## 4. 自动处理参数(四条路径各一份)

产物里每套数据都有转换脚本 `process/fid.com`(`bruk2pipe` + `nusExpand` 按采样表展开)与终谱
脚本。NUS 两条路径另有 SMILE 重构段。

| 数据 | 直接维处理链 | 间接维处理链 |
| --- | --- | --- |
| ① 2D uniform | 待真机 | 待真机 |
| ② 2D NUS | 待真机(含 SMILE) | 待真机 |
| ③ 3D uniform | 待真机 | 待真机 |
| ④ 3D NUS | 待真机(含 SMILE) | 待真机 |

每个参数由哪一步决定(产品的处理日志逐项写了同样的归属):

| 参数 | 由哪一步决定 |
| --- | --- |
| 窗函数 `SP -off/-end/-pow/-c` | **窗选择**(按参考谱评分择优,`workflow/window_optimize.py`) |
| 零填 `ZF -size` | 零填候选规则(auto:按 TD 与内存定;NUS 直接维 1×TD) |
| 相位 `PS -p0/-p1` | 相位优化(直接维先定,间接维再搜一轮;NUS 走显示层搜索) |
| 基线 `POLY -ord N -auto` | 基线优化(`workflow/baseline_optimize.py`:逐轴在 mode × 阶数里评分择优) |
| SMILE 参数 | NUS 重构(`nSigma`/`thresh`/`nthread`/`-maxMem` 按当前可用内存实时设定) |

## 5. 复现命令

四套数据都按同一套脚本跑,只是数据集不同。**在装有 NMRPipe/SMILE 的 Linux 机器上**执行。

### 5.1 取数据(全部公开)

```bash
# ① 2D uniform:BMRB 27493(Apo-bsCopL)。整条目单文件 tar(82 MB,含全部 6 个滴定条件):
curl -O https://bmrb.io/ftp/pub/bmrb/entry_directories/bmr27493/timedomain_data/N15_Chemical_shift_titration_of_bsCopl.tar
#    只要 Apo 这一个条件时,直接抓那个目录(目录 URL 不是 tar,必须用 wget -r 或浏览器逐文件下):
wget -r -np -nH --cut-dirs=6 \
  https://bmrb.io/ftp/pub/bmrb/entry_directories/bmr27493/timedomain_data/BMRB_Deposition/Apo_CBL_0.4mM/2/

# ③ 3D uniform:BMRB 15217 YkvR HNCO。这一条有**单文件 tar**,不必递归抓目录(推荐):
curl -O https://bmrb.io/ftp/pub/bmrb/entry_directories/bmr15217/timedomain_data/SR358_BMRBid_15217.tar
#    (744 MB,含条目全部 17 套数据;只要 HNCO 就用上面的 wget -r 单抓,省流量)
wget -r -np -nH --cut-dirs=6 \
  https://bmrb.io/ftp/pub/bmrb/entry_directories/bmr15217/timedomain_data/SR358_B600_3D_HNCO.fid/

# ② 2D NUS:SMILE 官方示例 2(20% NUS 2D TROSY)。三个包各有用途,都要:
curl -O https://spin.niddk.nih.gov/bax/software/smile/trosyJHH.ser.tar.gz    # 74 MB  原始 ser
curl -O https://spin.niddk.nih.gov/bax/software/smile/trosyJHH.2D.tar.gz    # 67 KB  采样表+脚本
curl -O https://spin.niddk.nih.gov/bax/software/smile/trosyJHH.data.tar.gz  # 129 MB 参照谱(关键)
# ④ 3D NUS:不用另外下载 —— 就地把 ③ 降采样。取样表用 NMRPipe 的 uniform_to_nus 生成:
#    (先按 ③ 的目录做出 15N 64 / 13C 80 的完全网格,再按目标 NUS 百分比抽点)
#    nmrPipe -in <③ HNCO 的 test.fid> -fn ... | uniform_to_nus -nusAmount 25 -out nuslist ...
#    注意:scripts/vm_sample_make_nus.py 只支持 2D,3D 这一步必须在真机用 NMRPipe 做。
```

> **三个包分别是什么(容易搞错)**:`*.ser.tar.gz` 是 Bruker `ser` 原始数据(②的包里是「全采样
> 与伪 NUS 两份 ser」);`*.2D.tar.gz` 很小,只有**采样表与官方处理/重建脚本**,里面**没有谱**;
> 真正含**参照谱**的是 `*.data.tar.gz`(官方说明:「conventionally processed and SMILE
> reconstructed data」)—— 第 1 节的右列参照谱就取自它,**别只下前两个**。
>
> ② 的 NUS 比例以官方 `smile.log` 为准:**`NUS sparsity: 20.0%`**(370 个采样点),
> 与官方页面写的 "20% NUS" 一致。头部 `acqu2s` 的 `NusTD=740` 是**声明的网格行数**,
> 而 `nuslist` 里的索引最大到 1849 —— 两者不是同一口径(索引是交织后的位置),
> **不要把 370/740 当成采样率**。官方该次运行的实测内存是 **130.1 MB**,与本项目
> `MB_FLOOR_2D = 128 MB` 的 2D 下限吻合(见第 0.1 节)。
>
> **④ 为什么不下 SMILE 的 `hnco`**:它的 13C 间接维 `TD=3200`、峰值约 44 GB,违反本轮
> 「小网格」约束(第 0.1 节有实测与理由)。④ 改为就地把 ③ 降采样,所以**不需要**那 2.8 GB 的包。
>
> **BMRB 取数要点**:条目页面上的目录索引能列出内容,但那些 URL 是**目录**;`wget -r`(或浏览器
> 逐文件下载)才拿得到 `acqus`/`acqu2s`/`ser`/`pdata`。数据集完整性用第 2 节报出的输入指纹
> (文件数 / 字节 / sha256)核对 —— 指纹对不上就不是同一份数据,别比。
>
> 以上清单与文件字节数取自 SMILE 官方示例页(<https://spin.niddk.nih.gov/bax/software/smile/>,
> 2018-04-05 更新)的 "SMILE NUS Examples" 一节;该页共 12 个示例,**本页只选用示例 2**,
> 其余示例(含 3D HNCO)网格过大,已在第 0.1 节逐条列出实测值说明为何不用。

### 5.1b 生成期望峰表(BMRB 那一侧的真值)

①②③④ 里只有 BMRB 的两套有沉积化学位移,真值回收(第 2 节)也**只对这两套**成立。峰表由
BMRB 的**第一方** HSQC 峰表接口导出(它本身就是从该条目沉积化学位移算出来的),再转成真值
脚本要的三列格式 —— **不要手工拼峰表**,那等于自己造真值:

```bash
# 取 BMRB 的第一方峰表(公开,无需登录)
curl -o expected_27493_bmrb.csv \
  "https://api.bmrb.io/current/entry/27493/simulate_hsqc?format=csv&filter=backbone"
curl -o expected_15217_bmrb.csv \
  "https://api.bmrb.io/current/entry/15217/simulate_hsqc?format=csv&filter=backbone"

# 转成 peak_id,H_ppm,N_ppm,并留下来源记录(输入 sha256 / 条目号 / 峰数)
python scripts/bmrb_expected_to_csv.py \
    --input expected_27493_bmrb.csv --entry 27493 \
    --output expected_27493.csv --report expected_27493.report.json
python scripts/bmrb_expected_to_csv.py \
    --input expected_15217_bmrb.csv --entry 15217 \
    --output expected_15217.csv --report expected_15217.report.json
```

`peak_id` 形如 `27493_5_VAL`(条目_残基号_残基名),所以匹配明细里的 `expected_id` 能直接对回
沉积条目。**期望峰表本体不进仓库**(与旧页口径一致):只把上表的 sha256 与峰数写进第 2 节。

### 5.2 真机跑四条路径

```bash
# 逐套数据跑「导入 → 生成 FID → 生成谱图」,输出聚合 JSON(不含路径/样品名)
nmrforge/bin/python scripts/vm_realdata_report.py \
    --dataset <Bruker 数据集目录> --tag "<匿名标签>" --root <临时研究根> --repeats 3 \
    --json <报告 JSON>

# 真值基准(第 2 节;输出只有聚合量 + 匹配明细 CSV)
nmrforge/bin/python scripts/vm_truth_benchmark.py \
    --dataset <Bruker 数据集目录> --expected <期望峰表 CSV> --tag "<匿名标签>" \
    --root <临时研究根> --thresholds 12 --json <报告 JSON> --matches <匹配明细 CSV>

# 对比图 + 真值图(--case 可重复四次;2D 用 .ft2、3D 用 .ft3)
nmrforge/bin/python scripts/vm_four_path_figure.py \
    --case "2D uniform,<自动谱>,<参照谱>,<qc自动.json>,<qc参照.json>" \
    --case "2D NUS,<自动谱>,<参照谱>,<qc自动.json>,<qc参照.json>" \
    --case "3D uniform,<自动谱>,<参照谱>,<qc自动.json>,<qc参照.json>" \
    --case "3D NUS,<自动谱>,<参照谱>,<qc自动.json>,<qc参照.json>" \
    --out docs/evidence/four-path-compare_<日期>.png

# 有沉积峰表的案例(①②③ 这类)另外出真值回收图(点标注 + 残差 + 回收率)
nmrforge/bin/python scripts/vm_truth_figure.py \
    --case "<标签>,<谱.ft2>,<期望峰表 CSV>,<匹配明细 CSV>,<报告 JSON>" \
    --out docs/evidence/truth_recovery_<日期>.png

# QC 评分(第 3 节):同一口径给任意一条谱打分。
# 每条谱单独跑;--json 的产物正是上面 vm_four_path_figure.py 要读的 qc*.json,
# 所以先跑 QC、再出图。
nmrforge/bin/python scripts/vm_qc_score.py \
    --spectrum <自动谱.ft2/.ft3> --label "auto" --json <qc自动.json>
nmrforge/bin/python scripts/vm_qc_score.py \
    --spectrum <参照谱 或 pdata/1 目录> --label "reference" --json <qc参照.json>
```

**执行顺序**:5.1 取数 → 5.1b 生成期望峰表(①③ 才需要)→ 逐套跑 `vm_realdata_report.py`
→ ①③ 跑 `vm_truth_benchmark.py` → 逐谱跑 `vm_qc_score.py --json` → 最后出两张图。
图依赖前置产物,顺序反了会读不到文件。

**②④ 没有真值行**:SMILE 示例没有沉积化学位移,第 2 节对它们**不适用**,以第 1 节的目视对照与
第 3 节的 QC 为准 —— 这一条边界不许含糊。`vm_truth_figure.py` 因此只对 ①③ 跑。

## 6. 能说明什么

- **四条处理路径都有公开数据上的对照**:2D/3D × uniform/NUS 各自有「自动处理 vs 数据提供方处理」
  的谱图对照与 QC 评分,数据来源全部公开、非病毒、可自行下载复算。
- **判据在软件之外**:第 2 节用的是**已发表沉积化学位移**做外部真值,不是自己跟自己比;并且附带
  同口径的偶然匹配背景,回收率必须连背景一起读。
- **网格是刻意选小的**:3D 两条路径的间接维网格落在 SMILE 内存护栏的安全区(见第 0 节),这样
  证据是「在一台普通机器上跑得出来的」,而不是只有大内存工作站才能复现。
- **这次验证要说明的是处理程序的有效性**,不是为了得出科学结论:拿处理结果去做别的科学问题,
  属于使用者自己的事,与本软件无关。
- 软件本身的边界:处理与留档自洽,不替用户下科学结论。

**本页不覆盖**:旧版曾有的「维护者另用十几套暂无法公开的数据验证过」属维护者自述、无法仅凭
快照复算,本页不再引用它作为证据;能复算的只有公开数据 + 随仓库发布的脚本。四套之外的其他实验
类型与采样方式的验证,完成后按同样口径补。
