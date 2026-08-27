#!/bin/csh
# 验证:keep_complex finalize 末尾加 HT(直接维虚部由 Hilbert 变换重建)
cd /home/<lab-user>/Desktop/data/sampleB.nmrpipe
source /home/<lab-user>/pipe/com/nmrInit.linux235_64.com
xyz2pipe -in nus3d_rc/test%04d.ft1 -x \
| nmrPipe -fn SP -off 0.45 -end 0.95 -pow 2 -c 0.5 \
| nmrPipe -fn ZF -size 128 \
| nmrPipe -fn FT \
| nmrPipe -fn PS -p0 0 -p1 0 \
| nmrPipe -fn TP \
| nmrPipe -fn SP -off 0.45 -end 0.95 -pow 2 -c 0.5 \
| nmrPipe -fn ZF -size 256 \
| nmrPipe -fn FT -alt \
| nmrPipe -fn PS -p0 0 -p1 0 \
| nmrPipe -fn TP \
| nmrPipe -fn ZTP \
| nmrPipe -fn HT \
| pipe2xyz -out 28_ht.ft3 -x
echo HT_FINALIZE_DONE
