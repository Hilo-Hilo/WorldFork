FROM python:3.11-slim AS base

# Build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

WORKDIR /app
ENV PYTHONPATH=/app/backend

# Copy project metadata and source before installing the editable local package.
# Hatchling needs backend/app to exist when resolving ".[dev]".
COPY pyproject.toml ./
COPY backend/ ./backend/
COPY source_of_truth/ ./source_of_truth/
COPY infra/ ./infra/

RUN uv pip install --system ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
