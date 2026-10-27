# gpu-burn (github.com/wilicc/gpu-burn, BSD-2-Clause), built from tools/gpu-burn/src at the commit in ctune/gpu/burn.py.
# The stages are upstream's Dockerfile with two changes: fully qualified image names (podman's short-name mode is
# "enforcing" on Fedora, so "nvidia/cuda" would prompt or fail), and sm_86 machine code embedded instead of
# PTX, so the driver never JIT-compiles it (the maintainer's fix for issue #145, README "PTX JIT"). A third change
# makes both compares bitwise: upstream ships EPSILON 0.001f and EPSILOND 1e-7, but its own comment says 0 is fine
# because cuBLAS results do not depend on accumulation order. At the matrix values here (about 2e5) 1e-7 leaves the
# low ~12 bits of every FP64 result unchecked.
ARG CUDA_VERSION=13.4.3
FROM docker.io/nvidia/cuda:${CUDA_VERSION}-devel-ubi9 AS builder
WORKDIR /build
COPY compare.cu gpu_burn-drv.cpp Makefile /build/
RUN sed -i -e 's/^#define EPSILON 0.001f$/#define EPSILON 0.0f/' -e 's/^#define EPSILOND 0.0000001$/#define EPSILOND 0.0/' compare.cu \
 && grep -q '^#define EPSILON 0.0f$' compare.cu && grep -q '^#define EPSILOND 0.0$' compare.cu
RUN make COMPUTE= NVCCFLAGS='-gencode=arch=compute_86,code=sm_86'

FROM docker.io/nvidia/cuda:${CUDA_VERSION}-runtime-ubi9
COPY --from=builder /build/gpu_burn /build/compare.fatbin /app/
WORKDIR /app
CMD ["./gpu_burn", "60"]
