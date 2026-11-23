# Backend images (Roadmap Task 3.3). One Dockerfile, one target per process:
#
#   docker build --target api    -t receptionist-api .      # FastAPI: webhooks, dashboard API, call page
#   docker build --target worker -t receptionist-worker .   # LiveKit voice worker (one process per call)
#   docker build --target jobs   -t receptionist-jobs .     # arq job worker: reminders, digests, WhatsApp replies
#   docker build --target dev    -t receptionist-dev .      # + test/lint tools, for docker-compose
#
# All three production targets share the `runtime` layers, so they build once and differ only in
# CMD and HEALTHCHECK. They run as a non-root user and read every setting from the environment.
# Local stack: ../docker-compose.yml. Production stack: ../deploy/docker-compose.prod.yml.

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS base
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
# Model files the LiveKit plugins need (VAD, turn detector) are fetched at build time, so a
# container never downloads anything before it can take its first call.
RUN python -m livekit.agents download-files
COPY . .
RUN useradd --system --uid 10001 --create-home app && mkdir -p temp_audio && chown -R app temp_audio
USER app
# Set by CI (--build-arg GIT_SHA=…): tags Sentry releases.
ARG GIT_SHA=""
ENV GIT_SHA=$GIT_SHA

FROM runtime AS api
EXPOSE 8002
# One process per container; scale with replicas (state is in Redis). Trusts X-Forwarded-For only
# from the reverse proxy on the private network (audit SEC-01) — never publish this port directly.
ENV FORWARDED_ALLOW_IPS="127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/readyz', timeout=3)"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002", "--proxy-headers", "--no-server-header", \
     "--timeout-graceful-shutdown", "20"]

FROM runtime AS jobs
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD arq --check jobs.worker.WorkerSettings
CMD ["python", "-m", "jobs"]

# Last = the default target, as before Phase 3.
FROM runtime AS worker
ENV LIVEKIT_WORKER_PORT=8081
EXPOSE 8081
# LiveKit's worker health endpoint answers 503 once the worker has failed to (re)connect to LiveKit
# or its inference process (turn detector) has died, so this is more than a port check (audit SRE-03).
HEALTHCHECK --interval=15s --timeout=4s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/', timeout=3)"
# SIGTERM = drain: stop taking calls, wait up to LIVEKIT_DRAIN_TIMEOUT for live ones. The platform's
# stop grace period must be at least that long (stop_grace_period in the compose files).
STOPSIGNAL SIGTERM
CMD ["python", "-m", "agent.worker", "start"]
