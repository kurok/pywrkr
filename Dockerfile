FROM python:3.13-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN pip install --no-cache-dir build && python -m build --wheel

FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/kurok/pywrkr"
LABEL org.opencontainers.image.description="pywrkr — Python HTTP benchmarking tool"
LABEL org.opencontainers.image.licenses="MIT"

COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

ENTRYPOINT ["pywrkr"]
