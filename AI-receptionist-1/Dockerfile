# Backend image: the LiveKit voice worker (default) or the API (override the command).
#   docker build -t receptionist-backend .
#   docker run --env-file .env receptionist-backend                          # voice worker
#   docker run --env-file .env -p 8002:8002 receptionist-backend \
#       uvicorn main:app --host 0.0.0.0 --port 8002 --proxy-headers          # API
# Local development: docker-compose.dev.yml (target "dev" adds test/lint tools).

FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./

FROM base AS dev
RUN uv sync --frozen --no-install-project
COPY . .
CMD ["python", "-m", "agent.worker", "dev"]

FROM base AS runtime
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN useradd --system --uid 10001 app && mkdir -p temp_audio && chown -R app temp_audio
USER app
CMD ["python", "-m", "agent.worker", "start"]
