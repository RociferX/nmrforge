# BMRB15750 HNCO：作者与自动处理脚本对比

本页对照软件1.0.4、API v1.1测量基线下实际运行的脚本；现行发行版本为软件1.0.5 / API v1.1.1，下列测量数字仍对应原基线。两侧使用同一份32×32复网格uniform原始数据。
作者侧原样执行沉积`fid.com`和`proc.com`；自动侧通过正常Bruker导入、生成FID、自动优化与完整终跑生成脚本。
它们分别转换原始数据，未把作者相位或窗函数传给自动链。
终谱和投影结果见[四路径证据](real-data-comparison.md)；原始脚本、哈希、谱头及谱宽审计见
[完整记录](3d-uniform-bmrb15750-comparison_20261009.json)。

## 转换参数

| 项目 | 作者`fid.com` | 自动`fid.com` |
| --- | --- | --- |
| 原始输入 | 同源`ser` | 同源导入副本`ser`，哈希一致 |
| 编码 | DQD / Echo-AntiEcho / States-TPPI | 相同 |
| N / T | 1024/64/64；512/32/32 | 相同 |
| H / N / C谱宽（Hz） | 12755.102 / 2500 / 3636.364 | 相同 |
| H / N / C频率（MHz） | 850.104 / 86.150 / 213.795 | 相同 |
| 数字滤波参数 | `-decim 1568 -dspfvs 20 -grpdly 67.9841461181641` | 相同参数；仅凭参数不认证实际滤波校正效果 |
| 转换选项 | `-aswap -DMX`，`-aq2D States` | `-ext -aswap -AMX`，`-aq2D Complex` |
| H / N / C CAR（ppm，脚本文本） | 4.800 / 118.100 / 178.250 | 4.819 / 118.125 / 178.251 |
| 缩放与输出 | 直接写`data/test%03d.fid` | 乘`1.95312e+00`后写`fid/test%03d.fid` |

碳维原始`SW_h=2500 Hz`与`SW×SFO1=3636.363636 Hz`冲突31.25%；现有规则采用后者，
实际转换与作者一致，没有修改原始文件。CAR是另一组参照参数，不用谱宽规则改写；比较图按实际谱头CAR差
给作者坐标加固定H +0.018999577、N +0.025001526、C +0.001007080 ppm，未逐峰拟合。
两侧强度分别归一，转换缩放常数不作为绝对峰高一致的证据。

## 逐维处理步骤

| 步骤 | 作者`proc.com` | 自动终跑`d_001_process.com` |
| --- | --- | --- |
| 直接H维时域基线 | `POLY -time` | 相同 |
| H维加窗 | `SP -size 512 -off 0.5 -end 1 -pow 2 -c 0.5` | 本次终脚本无H维SP |
| H维补零 | `ZF -auto`，处理后H为274点 | `ZF -size 2048`，处理后H为548点 |
| H维相位 | 先`PS 3/0`，再`PS -4/43 -di`；最终谱头−1°/43° | `PS 15/0 -di` |
| H维裁窗 | `EXT 10ppm..6ppm -sw` | 相同范围，另有`-round 2` |
| N维LP / 加窗 | `LP -fb`；`SP -off .5 -end .98 -pow 1 -c 1` | 无LP；`SP -off .3 -end .98 -pow 1 -c .5` |
| N维补零 / FT / 相位 | `ZF -auto`；FT；−90°/0° | `ZF -size 128`；FT；267.5°/0° |
| N处理后频域基线 | 两个方向`POLY -auto -ord 0` | 无该步骤 |
| C维首遍 | `SP .5/.98/1/.5`；ZF auto；FT alt；PS 0/0 | 无C维SP；ZF 128；FT alt；PS 2.5/0 |
| C维后续处理 | HT→逆PS/FT/ZF→LP fb→SP hdr→ZF auto→FT→PS hdr | 无该第二轮链 |
| C维位置 / 基线 | `CS -ls 3.0ppm -sw`；`POLY -auto -ord 0` | 无CS或此频域POLY |
| 完整三维输出 | `hnco.ft3`，128×128×274 | `d_001.ft3`，128×128×548 |

表中H/N/C是核身份，不按文件存储维序猜测；两侧轴顺序由各自`FDDIMORDER`解释。
作者C维CS同时更新ppm标定，不能再给比较坐标叠加3 ppm平移。
N相位267.5°与−90°相差2.5°（按360°周期），不是整轴反号。
作者脚本确有H维P1=43°；这里仅记录实际脚本差异，没有据此宣称独立非零P1实采验证完成。

两侧窗函数、LP、相位、补零及基线步骤不同，图中自动N维较宽、H/C较窄及弱负结构差异均保留。
不能只根据此表把差异归因于某一个操作，也不能把投影候选覆盖率当成已指认真峰回收率。

## 实际脚本

以下代码保留实际命令，完整记录提供SHA256；作者脚本运行前后哈希一致。
作者脚本中的注释行也按原文保留，不作为已经执行的命令。

### 作者转换：fid.com

```csh
#!/bin/csh

bruk2pipe -in ./ser -bad 0.0 -aswap -DMX -decim 1568 -dspfvs 20 -grpdly 67.9841461181641  \
  -xN              1024  -yN                64  -zN                64  \
  -xT               512  -yT                32  -zT                32  \
  -xMODE            DQD  -yMODE  Echo-AntiEcho  -zMODE    States-TPPI  \
  -xSW        12755.102  -ySW         2500.000  -zSW         3636.364  \
  -xOBS         850.104  -yOBS          86.150  -zOBS         213.795  \
  -xCAR           4.800  -yCAR         118.100  -zCAR         178.250  \
  -xLAB              H1  -yLAB             N15  -zLAB             C13  \
  -ndim               3  -aq2D          States                         \
  -out ./data/test%03d.fid -verb -ov

sleep 5
```

### 自动转换：fid.com

```csh
#!/bin/csh

bruk2pipe -verb -in ./ser \
  -bad 0.0 -ext -aswap -AMX -decim 1568 -dspfvs 20 -grpdly 67.9841461181641  \
  -xN              1024  -yN                64  -zN                64  \
  -xT               512  -yT                32  -zT                32  \
  -xMODE            DQD  -yMODE Echo-AntiEcho  -zMODE States-TPPI  \
  -xSW        12755.102  -ySW         2500.000  -zSW 3636.364  \
  -xOBS         850.104  -yOBS          86.150  -zOBS         213.795  \
  -xCAR           4.819  -yCAR         118.125  -zCAR         178.251  \
  -xLAB 1H  -yLAB             15N  -zLAB             13C  \
  -ndim               3  -aq2D         Complex                         \
| nmrPipe -fn MULT -c 1.95312e+00 \
| pipe2xyz -x -out ./fid/test%03d.fid -ov
```

### 作者处理：proc.com

```csh
#!/bin/csh

xyz2pipe -in  data/test%03d.fid -x  -verb           \
| nmrPipe  -fn POLY -time                           \
| nmrPipe  -fn SP -size 512 -off 0.5 -end 1.00 -pow 2 -c 0.5  \
| nmrPipe  -fn ZF -auto                             \
| nmrPipe  -fn FT                                   \
| nmrPipe  -fn PS -p0  3.0 -p1 0                    \
| nmrPipe  -fn PS -p0 -4  -p1 43.0 -di               \
| nmrPipe  -fn EXT -x1 10.0ppm -xn 6.0ppm -sw       \
| pipe2xyz -out data/test%03d.ft3 -x -ov

xyz2pipe -in data/test%03d.ft3 -z -verb               \
| nmrPipe  -fn SP -off 0.5 -end 0.98 -pow 1 -c 0.5  \
| nmrPipe  -fn ZF -auto                             \
| nmrPipe  -fn FT -alt                              \
| nmrPipe  -fn PS -p0 0.0 -p1 0.0 -di               \
| pipe2xyz -out data/test%03d.ft3 -z -inPlace

xyz2pipe -in data/test%03d.ft3 -y -verb               \
| nmrPipe  -fn LP -fb                               \
| nmrPipe  -fn SP -off 0.5 -end 0.98 -pow 1 -c 1.0  \
| nmrPipe  -fn ZF -auto                             \
| nmrPipe  -fn FT                                   \
| nmrPipe  -fn PS -p0 -90 -p1 0 -di                   \
| nmrPipe  -fn POLY -auto -ord 0                    \
| nmrPipe  -fn TP                                   \
| nmrPipe  -fn POLY -auto -ord 0                    \
| pipe2xyz -out data/test%03d.ft3 -y -inPlace

xyz2pipe -in data/test%03d.ft3 -z -verb               \
| nmrPipe  -fn HT  -auto                            \
| nmrPipe  -fn PS  -inv -hdr                        \
| nmrPipe  -fn FT  -inv                             \
| nmrPipe  -fn ZF  -inv                             \
#| nmrPipe  -fn LP -pred 32 -ord 8                   \
| nmrPipe  -fn LP -fb                               \
| nmrPipe  -fn SP  -hdr                             \
| nmrPipe  -fn ZF  -auto                            \
| nmrPipe  -fn FT                                   \
| nmrPipe  -fn PS  -hdr -di                         \
| nmrPipe  -fn CS  -ls 3.0ppm   -sw                   \
| nmrPipe  -fn POLY -auto -ord 0                    \
| pipe2xyz -out data/test%03d.ft3 -z -inPlace

xyz2pipe -in ./data/test%03d.ft3  -y -verb  \
  > ./hnco.ft3
```

### 自动处理：d_001_process.com

```csh
#!/bin/csh
# NMRForge processing script
# experiment: d_001
xyz2pipe -in fid/test%03d.fid -x \
| nmrPipe -fn POLY -time \
| nmrPipe -fn ZF -size 2048 \
| nmrPipe -fn FT \
| nmrPipe -fn PS -p0 15 -p1 0 -di \
| nmrPipe -fn EXT -x1 10ppm -xn 6ppm -sw -round 2 \
| nmrPipe -fn TP \
| nmrPipe -fn SP -off 0.3 -end 0.98 -pow 1 -c 0.5 \
| nmrPipe -fn ZF -size 128 \
| nmrPipe -fn FT \
| nmrPipe -fn PS -p0 267.5 -p1 0 -di \
| nmrPipe -fn ZTP \
| nmrPipe -fn ZF -size 128 \
| nmrPipe -fn FT -alt \
| nmrPipe -fn PS -p0 2.5 -p1 0 -di \
| nmrPipe -fn TP \
| pipe2xyz -out d_001.ft3 -x
```
