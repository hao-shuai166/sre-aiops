# ---- Stage 1: Builder ---- 
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies only
RUN pip install --no-cache-dir hatchling

# Copy just the build manifest and source
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Build a wheel
RUN pip wheel . --wheel-dir=/build/wheels

# ---- Stage 2: Runtime ----
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="Infrastructure Agent"
LABEL org.opencontainers.image.description="AI-Native SRE Platform — Kubernetes 智能故障诊断"
LABEL org.opencontainers.image.version="0.1.0"

# Create non-root user
RUN groupadd -r sre && useradd -r -g sre -d /app sre

WORKDIR /app

# Copy and install the built wheel
COPY --from=builder /build/wheels/*.whl /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels

# Switch to non-root user
USER sre

# Container health check — FastAPI /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

EXPOSE 8000

# Production entrypoint — no --reload, no hot-reload watcher
CMD ["uvicorn", "infrastructure_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
