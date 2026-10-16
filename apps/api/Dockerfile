# Image shared by the API and the worker. Pinned base and uv versions; never "latest".
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY apps/api ./apps/api
# The importer reads config/sources.yaml relative to its working directory (apps/api).
COPY config ./apps/api/config
RUN uv sync --package hoy-api --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"
WORKDIR /app/apps/api
EXPOSE 8000
# The API service applies migrations before serving; the worker command is set in compose.
CMD ["sh", "-c", "alembic upgrade head && uvicorn hoy_api.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
