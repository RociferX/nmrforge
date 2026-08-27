#!/bin/csh
# uniform 2D 全复型(sampleF):直接/间接 PS 均不加 -di
cd /home/<lab-user>/Desktop/sampleF.nmrpipe
source /home/<lab-user>/pipe/com/nmrInit.linux235_64.com
nmrPipe -in 3.fid \
| nmrPipe -fn SP -off 0.3 -end 0.98 -pow 1 -c 0.5 \
| nmrPipe -fn ZF -zf -size 2048 \
| nmrPipe -fn FT \
| nmrPipe -fn EXT -x1 10.5ppm -xn 6.5ppm -sw -round 2 \
| nmrPipe -fn PS -p0 0 -p1 0 \
| nmrPipe -fn TP \
| nmrPipe -fn SP -off 0.45 -end 0.95 -pow 1 -c 0.5 \
| nmrPipe -fn ZF -size 256 \
| nmrPipe -fn FT -alt \
| nmrPipe -fn PS -p0 0 -p1 0 \
| nmrPipe -fn TP \
| pipe2xyz -out 3_full_cplx.ft2 -x
echo UNIFORM_CPLX_DONE
