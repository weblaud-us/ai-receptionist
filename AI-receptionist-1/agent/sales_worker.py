"""
The sales worker — a second LiveKit worker, only for outbound sales calls (Roadmap Task 4.4).

    python -m agent.sales_worker dev      # local development
    python -m agent.sales_worker start    # production (the `sales-worker` container)

It registers under SALES_AGENT_NAME and is only ever started by an explicit dispatch from the
dialer (core/outbound/dialer.py); it never answers an inbound call. It is a separate process from
the receptionist worker on purpose: a LiveKit worker serves one agent name, and a runaway or
suspended dialer must not be able to take capacity from customers' inbound calls.
"""

from __future__ import annotations

import logging
import math

from config import settings
from core.log import configure_logging
from core.observability import init_sentry

configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
init_sentry("sales-agent")

from livekit.agents import AgentServer, JobProcess, cli  # noqa: E402
from livekit.agents.worker import ServerEnvOption  # noqa: E402
from livekit.plugins import silero  # noqa: E402

from agent.sales import sales_caller  # noqa: E402
from core.outbound import dialer  # noqa: E402

logger = logging.getLogger("agent.sales_worker")


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server = AgentServer(
    ws_url=settings.LIVEKIT_URL or None,
    api_key=settings.LIVEKIT_API_KEY or None,
    api_secret=settings.LIVEKIT_API_SECRET or None,
    num_idle_processes=settings.LIVEKIT_NUM_IDLE_PROCESSES,
    initialize_process_timeout=settings.LIVEKIT_INITIALIZE_PROCESS_TIMEOUT,
    port=settings.LIVEKIT_WORKER_PORT,
    setup_fnc=prewarm,
    drain_timeout=settings.SALES_MAX_CALL_MINUTES * 60 + 60,     # a sales call is capped, so a short drain is enough
    load_threshold=ServerEnvOption(dev_default=math.inf, prod_default=settings.LIVEKIT_LOAD_THRESHOLD),
    job_memory_warn_mb=settings.LIVEKIT_JOB_MEMORY_WARN_MB,
    job_memory_limit_mb=settings.LIVEKIT_JOB_MEMORY_LIMIT_MB,
)
server.rtc_session(sales_caller, agent_name=settings.SALES_AGENT_NAME)


def main() -> None:
    missing = [v for v in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "OPENAI_API_KEY")
               if not getattr(settings, v)]
    if missing:
        raise SystemExit(f"Missing settings for the sales agent: {', '.join(missing)}")
    problems = dialer.readiness()
    logger.info("Sales agent '%s' (env=%s, dialer %s)", settings.SALES_AGENT_NAME, settings.APP_ENV,
                "ready" if not problems else "NOT ready: " + " ".join(problems))
    cli.run_app(server)


if __name__ == "__main__":
    main()
