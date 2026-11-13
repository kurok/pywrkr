# Builder: uv only exists here. The runtime stage has no build tooling in it.
FROM python:3.13-alpine@sha256:1a63a53928ce53d2b0baf08092a703f4840ac5dfbd61fd48802dbf48e08c801e AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc /uv /usr/local/bin/uv

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN uv build --wheel --out-dir dist/

# Runtime.
#
# 3.13 rather than 3.15-rc: the published image ran an RC interpreter, which
# has no prebuilt musllinux wheels, so aiohttp and its C extensions were
# compiled from source at build time. pyproject declares requires-python
# >= 3.10, so nothing here needs an unreleased interpreter.
#
# Both images are pinned by digest. A floating tag makes the build-provenance
# attestation attest to inputs that can change underneath it.
FROM python:3.13-alpine@sha256:1a63a53928ce53d2b0baf08092a703f4840ac5dfbd61fd48802dbf48e08c801e

LABEL org.opencontainers.image.source="https://github.com/kurok/pywrkr"
LABEL org.opencontainers.image.description="pywrkr — Python HTTP benchmarking tool"
LABEL org.opencontainers.image.licenses="MIT"

# uv is not copied here. Everything is installed with the interpreter's own
# pip, so the runtime image carries no extra tooling.
#
# The dependencies come from requirements-image.txt, pinned by version and
# sha256 (#342): --require-hashes makes pip refuse anything the index serves
# that is not the pinned artifact, and --only-binary refuses to compile, so
# the image's Python stack is a function of the repository rather than of the
# index on the day of the build. The file is compiled from pyproject.toml with
# the http2, otel, tui and yaml extras, the ones every documented CLI flag
# needs (#340); its header carries the regenerate command.
#
# The pywrkr wheel itself is built from this checkout in the builder stage, so
# it has no index hash; --no-deps keeps pip from resolving anything for it,
# and `pip check` fails the build if the pinned set does not satisfy it.
COPY requirements-image.txt /tmp/
COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir --require-hashes --only-binary :all: --no-deps \
        -r /tmp/requirements-image.txt \
    && pip install --no-cache-dir --no-deps /tmp/*.whl \
    && pip check \
    && rm /tmp/*.whl /tmp/requirements-image.txt

# A load generator has no reason to be uid 0. It opens sockets and writes
# reports; neither needs root, and this one runs on Fargate.
RUN adduser -D -u 10001 pywrkr
USER pywrkr

# The runtime user is non-root, so the working directory has to be one it can
# write to: under the base image's "/" a relative --json/--csv/--html-report
# path fails with PermissionError after the benchmark has already run.
# adduser created /home/pywrkr owned by the user, so no mkdir or chown.
WORKDIR /home/pywrkr

# --help, not --version: pywrkr has no --version flag, so that check would
# have failed on every probe and marked the container permanently unhealthy.
# This matches infra/docker/Dockerfile, and proves the entrypoint is installed
# and importable, which is the most a CLI image can usefully assert.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD pywrkr --help > /dev/null 2>&1 || exit 1

ENTRYPOINT ["pywrkr"]
