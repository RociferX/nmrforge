#!/bin/csh
# 验证:SMILE 是否接受"丢实部留虚部"数据(sampleB,低迭代)
cd /home/<lab-user>/Desktop/data/sampleB.nmrpipe
source /home/<lab-user>/pipe/com/nmrInit.linux235_64.com
mkdir -p nus3d_1_imag nus3d_rc_imag
# step1 imag:直接维 PS -p0 90 -di → 保留原虚部(转到实部)
xyz2pipe -in 28.fid -x \
| nmrPipe -fn SP -off 0.6 -end 0.98 -pow 2 -c 0.5 \
| nmrPipe -fn ZF -zf -size 2048 \
| nmrPipe -fn FT \
| nmrPipe -fn EXT -x1 10.5ppm -xn 6.5ppm -sw -round 2 \
| nmrPipe -fn PS -p0 90 -p1 0 -di \
| pipe2xyz -out nus3d_1_imag/test%04d.ft1 -z
echo STEP1_IMAG_DONE
# SMILE(低迭代,小线程,内存护栏)
xyz2pipe -in nus3d_1_imag/test%04d.ft1 -x \
| nmrPipe -fn SMILE -nDim 3 \
           -sample nuslist -nThread 2 \
           -sampleCount 250 -nSigma 5 -off 0 0 -report 1 \
           -scaling 1 \
           -maxIter 50 \
           -yAlt -maxMem 6 \
           -thresh 0.95 \
| pipe2xyz -out nus3d_rc_imag/test%04d.ft1 -x
echo SMILE_IMAG_DONE
