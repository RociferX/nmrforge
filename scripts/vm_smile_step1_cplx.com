#!/bin/csh
# step1 不带 -di:直接维保留复型(实部+虚部),用于校验两个通道
cd /home/<lab-user>/Desktop/data/sampleB.nmrpipe
source /home/<lab-user>/pipe/com/nmrInit.linux235_64.com
mkdir -p nus3d_1_cplx
xyz2pipe -in 28.fid -x \
| nmrPipe -fn SP -off 0.6 -end 0.98 -pow 2 -c 0.5 \
| nmrPipe -fn ZF -zf -size 2048 \
| nmrPipe -fn FT \
| nmrPipe -fn EXT -x1 10.5ppm -xn 6.5ppm -sw -round 2 \
| nmrPipe -fn PS -p0 0 -p1 0 \
| pipe2xyz -out nus3d_1_cplx/test%04d.ft1 -z
echo STEP1_CPLX_DONE
