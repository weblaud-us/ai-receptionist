"""Error tracking (Sentry), shared by the API and the LiveKit worker."""

import logging
from typing import Any

from config import settings
from core.log import redact_obj

logger = logging.getLogger(__name__)


def _before_send(event: Any, hint: Any) -> Any:
    # ⚖ never ship caller phone numbers / e-mails to a third party
    return redact_obj(event)


def init_sentry(component: str) -> bool:
    """Initialise Sentry when SENTRY_DSN is set. Returns True if enabled."""
    if not settings.SENTRY_DSN:
        return False
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed")
        return False

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.APP_ENV,
        release=settings.GIT_SHA or None,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
        # ⚖ Stack-frame variables would carry tool arguments (symptoms, names, numbers) to Sentry.
        include_local_variables=False,
        before_send=_before_send,
        before_send_transaction=_before_send,
    )
    sentry_sdk.set_tag("component", component)
    logger.info("Sentry enabled for %s", component)
    return True


def capture(exc: BaseException, **context: Any) -> None:
    try:
        import sentry_sdk

        with sentry_sdk.new_scope() as scope:
            for k, v in context.items():
                scope.set_extra(k, v)
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass
