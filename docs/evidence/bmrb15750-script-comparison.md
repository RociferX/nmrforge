# BMRB 15750 HNCO: Comparison of Author and Automatic Processing Scripts

This page compares the scripts actually run with the software 1.0.4 / API v1.1 measurement baseline.
The current release is software 1.0.5 / API v1.1.1; the measured values below retain their original
baseline. Both sides use the same
32×32 complex-grid uniform raw data. The author branch runs the deposited `fid.com` and `proc.com`
unchanged; the automatic branch generates its scripts through normal Bruker import, FID conversion,
automatic optimization, and a complete final run. Each branch converts the raw data independently;
the author's phases and window functions were not passed to the automatic workflow.
Final spectra and projection results are in the [four-route evidence](real-data-comparison.md);
the raw scripts, hashes, spectrum headers, and sweep-width audit are in the
[complete record](3d-uniform-bmrb15750-comparison_20261009.json).

## Conversion parameters

| Item | Author `fid.com` | Automatic `fid.com` |
| --- | --- | --- |
| Raw input | Source `ser` | Imported copy of source `ser`; hashes match |
| Encoding | DQD / Echo-AntiEcho / States-TPPI | Same |
| N / T | 1024/64/64; 512/32/32 | Same |
| H / N / C sweep width (Hz) | 12755.102 / 2500 / 3636.364 | Same |
| H / N / C frequency (MHz) | 850.104 / 86.150 / 213.795 | Same |
| Digital-filter parameters | `-decim 1568 -dspfvs 20 -grpdly 67.9841461181641` | Same parameters; parameters alone do not certify actual filter correction |
| Conversion options | `-aswap -DMX`, `-aq2D States` | `-ext -aswap -AMX`, `-aq2D Complex` |
| H / N / C CAR (ppm, script text) | 4.800 / 118.100 / 178.250 | 4.819 / 118.125 / 178.251 |
| Scaling and output | Write directly to `data/test%03d.fid` | Multiply by `1.95312e+00`, then write to `fid/test%03d.fid` |

The carbon-dimension raw `SW_h=2500 Hz` conflicts with `SW×SFO1=3636.363636 Hz` by 31.25%; the
current rule uses the latter. The actual conversion therefore agrees with the author script, and
the raw file was not modified. CAR is a separate reference parameter and is not rewritten by the
sweep-width rule. For the comparison figure, the author's coordinates receive fixed whole-axis
shifts of H +0.018999577, N +0.025001526, and C +0.001007080 ppm, derived from the actual spectrum
header CAR differences. No per-peak fitting was used. Each side's intensity is normalized
separately, so the conversion scale factor is not evidence of absolute peak-height equivalence.

## Processing steps by dimension

| Step | Author `proc.com` | Automatic final `d_001_process.com` |
| --- | --- | --- |
| Direct H time-domain baseline | `POLY -time` | Same |
| H apodization | `SP -size 512 -off 0.5 -end 1 -pow 2 -c 0.5` | No H `SP` in this final script |
| H zero filling | `ZF -auto`; 274 H points after processing | `ZF -size 2048`; 548 H points after processing |
| H phase | First `PS 3/0`, then `PS -4/43 -di`; final header −1°/43° | `PS 15/0 -di` |
| H extraction | `EXT 10ppm..6ppm -sw` | Same range, with additional `-round 2` |
| N linear prediction / apodization | `LP -fb`; `SP -off .5 -end .98 -pow 1 -c 1` | No LP; `SP -off .3 -end .98 -pow 1 -c .5` |
| N zero filling / FT / phase | `ZF -auto`; FT; −90°/0° | `ZF -size 128`; FT; 267.5°/0° |
| N frequency-domain baseline after processing | `POLY -auto -ord 0` in both directions | No such step |
| First C pass | `SP .5/.98/1/.5`; auto ZF; alternate FT; PS 0/0 | No C `SP`; ZF 128; alternate FT; PS 2.5/0 |
| Later C processing | HT → inverse PS/FT/ZF → LP fb → SP hdr → auto ZF → FT → PS hdr | No second pass |
| C position / baseline | `CS -ls 3.0ppm -sw`; `POLY -auto -ord 0` | No `CS` or this frequency-domain `POLY` |
| Complete 3D output | `hnco.ft3`, 128×128×274 | `d_001.ft3`, 128×128×548 |

H/N/C denote nuclei, not inferred storage order; each side's axis order is interpreted from its own
`FDDIMORDER`. The author C-dimension `CS` also updates ppm calibration, so a further 3 ppm must not
be added to the comparison coordinates. The N phase 267.5° differs from −90° by 2.5° modulo 360°;
it is not a whole-axis sign inversion. The author script has H-dimension P1=43°. This page records
the actual script difference only; it does not claim that an independent acquired nonzero-P1
validation has been completed.

The two branches differ in windowing, linear prediction, phase, zero filling, and baseline steps.
The broader N dimension, narrower H/C dimensions, and weak negative-structure differences remain in
the figures. These differences cannot be attributed to a single operation based on this table, and
projection candidate coverage is not recovery of assigned true peaks.

## Actual scripts

The commands below are the actual scripts; the complete record provides their SHA256 hashes. The
author script hashes were unchanged before and after execution. Commented lines in the author script
are preserved verbatim and are not commands that ran.

### Author conversion: `fid.com`

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

### Automatic conversion: `fid.com`

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

### Author processing: `proc.com`

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

### Automatic processing: `d_001_process.com`

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
