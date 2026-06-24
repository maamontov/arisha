# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim-bookworm AS base

ARG UV_VERSION=0.5

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    PATH=/app/.venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /app

COPY pyproject.toml uv.lock* ./

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=cache,target=/root/.cache/pip \
    uv sync --no-dev --no-install-project

COPY app ./app
COPY webhook_receiver ./webhook_receiver
COPY alembic ./alembic
COPY alembic.ini ./
COPY README.md ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev

RUN chown -R nobody:nogroup /app && chmod -R 755 /app
USER nobody

FROM base AS api
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD python -c "import httpx, sys; sys.exit(0 if httpx.get('http://localhost:8000/health', timeout=2).status_code == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--loop", "uvloop", "--http", "httptools"]

FROM base AS consumer
CMD ["python", "-m", "app.messaging.consumer"]

FROM base AS outbox-relay
CMD ["python", "-m", "app.outbox.relay"]

FROM base AS webhook-receiver
EXPOSE 9000
CMD ["uvicorn", "webhook_receiver.app:app", "--host", "0.0.0.0", "--port", "9000", "--loop", "uvloop", "--http", "httptools"]
