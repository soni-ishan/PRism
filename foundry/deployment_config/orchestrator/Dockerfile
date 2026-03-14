# PRism Orchestrator Dockerfile
# =============================
# Multi-stage build for Python FastAPI application
# Optimized for Azure Container Apps

# ──────────────────────────────────────────────────────────────
# Stage 1: Builder - Install dependencies
# ──────────────────────────────────────────────────────────────
FROM python:3.11-slim as builder

WORKDIR /build

RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ──────────────────────────────────────────────────────────────
# Stage 2: Runtime - Create minimal image
# ──────────────────────────────────────────────────────────────
FROM python:3.11-slim

RUN useradd -m -u 1000 prism && \
    mkdir -p /app && \
    chown -R prism:prism /app

WORKDIR /app

COPY --from=builder /install /usr/local

COPY --chown=prism:prism agents/ ./agents/
COPY --chown=prism:prism foundry/ ./foundry/
COPY --chown=prism:prism mcp_servers/ ./mcp_servers/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    WORKERS=4

USER prism

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()"

EXPOSE 8000

CMD uvicorn agents.orchestrator.server:app \
    --host 0.0.0.0 \
    --port ${PORT} \
    --workers ${WORKERS} \
    --log-level info \
    --no-access-log
