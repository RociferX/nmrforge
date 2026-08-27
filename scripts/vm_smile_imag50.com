#!/bin/csh
# 同迭代(50)实部 vs 虚部 SMILE 对比(sampleB)
cd /home/<lab-user>/Desktop/data/sampleB.nmrpipe
source /home/<lab-user>/pipe/com/nmrInit.linux235_64.com
mkdir -p nus3d_1_r50 nus3d_rc_r50
# step1 实部(PS 0 -di)
xyz2pipe -in 28.fid -x \
| nmrPipe -fn SP -off 0.6 -end 0.98 -pow 2 -c 0.5 \
| nmrPipe -fn ZF -zf -size 2048 \
| nmrPipe -fn FT \
| nmrPipe -fn EXT -x1 10.5ppm -xn 6.5ppm -sw -round 2 \
| nmrPipe -fn PS -p0 0 -p1 0 -di \
| pipe2xyz -out nus3d_1_r50/test%04d.ft1 -z
xyz2pipe -in nus3d_1_r50/test%04d.ft1 -x \
| nmrPipe -fn SMILE -nDim 3 \
           -sample nuslist -nThread 2 \
           -sampleCount 250 -nSigma 5 -off 0 0 -report 1 \
           -scaling 1 \
           -maxIter 50 \
           -yAlt -maxMem 6 \
           -thresh 0.95 \
| pipe2xyz -out nus3d_rc_r50/test%04d.ft1 -x
echo REAL50_DONE
