# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS build-base

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$VIRTUAL_ENV" \
    && pip install --no-cache-dir uv

WORKDIR /app

# Hatchling needs backend/app to exist while resolving the local package.
COPY pyproject.toml ./
COPY backend/ ./backend/
COPY source_of_truth/ ./source_of_truth/
COPY infra/ ./infra/

FROM build-base AS runtime-deps

RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install "."

FROM python:3.11-slim AS runtime-base

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/backend \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY backend/ ./backend/
COPY source_of_truth/ ./source_of_truth/
COPY infra/ ./infra/

FROM runtime-base AS runtime

COPY --from=runtime-deps /opt/venv /opt/venv

EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
