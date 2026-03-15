# PRism Setup Platform
# ====================
# Multi-stage build optimized for Azure Container Apps

# ── Stage 1: Install dependencies ──────────────────────────────
FROM python:3.11-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Runtime ───────────────────────────────────────────
FROM python:3.11-slim

RUN useradd -m -u 1000 prism && \
    mkdir -p /app && chown -R prism:prism /app

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=prism:prism server/ ./server/
COPY --chown=prism:prism frontend/ ./frontend/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

USER prism

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health').read()"

EXPOSE 8080

CMD uvicorn server.app:app \
    --host 0.0.0.0 \
    --port ${PORT} \
    --workers 2 \
    --log-level info
