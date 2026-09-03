#!/bin/csh
# SMILE 直接吃无 -di 的复型直接维(1200 平面),看 Z 识别
cd /home/<lab-user>/Desktop/data/sampleB.nmrpipe
source /home/<lab-user>/pipe/com/nmrInit.linux235_64.com
mkdir -p nus3d_rc_cplx
xyz2pipe -in nus3d_1_cplx/test%04d.ft1 -x \
| nmrPipe -fn SMILE -nDim 3 \
           -sample nuslist -nThread 2 \
           -sampleCount 250 -nSigma 5 -off 0 0 -report 1 \
           -scaling 1 \
           -maxIter 50 \
           -yAlt -maxMem 6 \
           -thresh 0.95 \
| pipe2xyz -out nus3d_rc_cplx/test%04d.ft1 -x
echo SMILE_CPLX_DONE
