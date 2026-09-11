# syntax=docker/dockerfile:1

# ============================================================================
# Stage 1: frontend-builder — build the Vue console (Node 22)
# ============================================================================
FROM node:22-slim AS frontend-builder

WORKDIR /web

# Install deps first (layer cache: package.json alone rarely changes)
COPY frontend/package.json frontend/package-lock.json ./
# npmmirror: registry.npmjs.org is unreachable from most CN networks.
RUN npm install --registry=https://registry.npmmirror.com --no-audit --no-fund

# Build the production bundle
COPY frontend/ ./
RUN npm run build

# ============================================================================
# Stage 2: backend-builder — project wheel + all dependency wheels
# ============================================================================
FROM python:3.12-slim AS backend-builder

# ---------------------------------------------------------------------------
# PyPI 镜像源 — 默认阿里云（国内可达）。
# 原因：官方 pypi.org 在部分网络（尤其国内）不可达，builder 阶段必须联网
# 下载 hatchling 构建依赖 + 所有运行时依赖 wheel，否则报
# "Could not find a version that satisfies the requirement hatchling"。
# 若你的网络可直接访问 pypi.org，可覆盖：
#   docker build --build-arg PIP_INDEX_URL=https://pypi.org/simple .
# ---------------------------------------------------------------------------
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}

WORKDIR /build

# Copy only the build manifest + source. Keeping this layer isolated lets
# Docker cache the dependency wheels across code-only changes.
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Collect the project wheel + all dependency wheels into /wheels.
RUN pip wheel --wheel-dir=/wheels .

# ============================================================================
# Stage 3: runtime — offline install, frontend bundle, non-root
# ============================================================================
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="Infrastructure Agent"
LABEL org.opencontainers.image.description="AI-Native SRE Platform — Kubernetes 智能故障诊断"
LABEL org.opencontainers.image.version="0.2.0"

# Unbuffered stdout is important for an SRE tool — logs appear immediately.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Frontend bundle location inside the container (main.py reads this)
    FRONTEND_DIST=/app/frontend/dist

# Non-root user
RUN groupadd -r sre && useradd -r -g sre -d /app sre

WORKDIR /app

# Install the project + all dependencies from the collected wheels, offline.
COPY --from=backend-builder /wheels /tmp/wheels
RUN pip install --no-index --find-links=/tmp/wheels infrastructure-agent \
    && rm -rf /tmp/wheels

# Frontend console (built Vue bundle) — served by FastAPI at /
COPY --from=frontend-builder --chown=sre:sre /web/dist /app/frontend/dist

# Switch to non-root user
USER sre

# Health check — FastAPI /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

EXPOSE 8000

# Production entrypoint — no --reload, no hot-reload watcher
CMD ["uvicorn", "infrastructure_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
