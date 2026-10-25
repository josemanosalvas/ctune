# nvidia_oc (github.com/Dreaming-Codes/nvidia_oc, MIT): a stateless NVML command line tool that sets the clock
# offsets, the locked clocks and the power limit, used by the boot unit that re-applies the signed-off GPU settings.
# Build context: a checkout of the pinned commit (ctune.gpu.persist.COMMIT). --locked builds the exact Cargo.lock.
# The final stage holds only the binary, exported with `podman build -o type=local,dest=DIR`.
ARG RUST_IMAGE=docker.io/library/rust:1-slim-bookworm
FROM ${RUST_IMAGE} AS build
WORKDIR /src
COPY . .
RUN cargo build --release --locked

FROM scratch
COPY --from=build /src/target/release/nvidia_oc /nvidia_oc
