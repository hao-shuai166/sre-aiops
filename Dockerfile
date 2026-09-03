# syntax=docker/dockerfile:1

# ============================================================================
# Stage 1: builder
#   Build the project wheel AND download every runtime dependency wheel into
#   /wheels, so Stage 2 can install fully offline (no second network round-trip
#   for fastapi / langgraph / openai / kubernetes / mcp etc.).
# ============================================================================
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Copy only the build manifest + source. Keeping this layer isolated lets
# Docker cache the dependency wheels across code-only changes.
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Collect the project wheel + all dependency wheels into /wheels.
RUN pip wheel --wheel-dir=/wheels .

# ============================================================================
# Stage 2: runtime
#   Minimal image: no build tooling, no network installs, non-root user.
# ============================================================================
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="Infrastructure Agent"
LABEL org.opencontainers.image.description="AI-Native SRE Platform — Kubernetes 智能故障诊断"
LABEL org.opencontainers.image.version="0.2.0"

# Unbuffered stdout is important for an SRE tool — logs appear immediately.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Non-root user
RUN groupadd -r sre && useradd -r -g sre -d /app sre

WORKDIR /app

# Install the project + all dependencies from the collected wheels, offline.
COPY --from=builder /wheels /tmp/wheels
RUN pip install --no-index --find-links=/tmp/wheels infrastructure-agent \
    && rm -rf /tmp/wheels

# Switch to non-root user
USER sre

# Health check — FastAPI /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

EXPOSE 8000

# Production entrypoint — no --reload, no hot-reload watcher
CMD ["uvicorn", "infrastructure_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
