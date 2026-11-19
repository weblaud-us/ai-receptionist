"""
Logging that doesn't leak.

- One line per record on stdout. JSON in staging/production (LOG_FORMAT=json), readable text
  in development. systemd / the container platform collects stdout — no log files.
- Every record carries request_id, call_id and tenant_id from contextvars (ids only, never names).
- A redaction filter masks phone numbers and e-mail addresses in every message.
- Transcripts and message bodies are logged only at DEBUG. DEBUG is never on in production.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any, Optional

request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)
call_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("call_id", default=None)
tenant_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("tenant_id", default=None)

CONTEXT_KEYS = ("request_id", "call_id", "tenant_id")


def set_tenant(tenant_id: Optional[str]) -> None:
    """Tag every log line from here on (this request, call or job step) with the business id."""
    tenant_id_var.set(tenant_id or None)

# 9–15 contiguous digits, optionally prefixed with '+' (E.164 and national formats).
# Contiguous only, so dates (2026-11-14) and booking IDs (HTL1A2B3C4D) are left alone.
_PHONE_RE = re.compile(r"(?<![\w+])\+?\d{9,15}(?!\w)")
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")


def _mask_phone_match(m: re.Match) -> str:
    raw = m.group(0)
    plus = "+" if raw.startswith("+") else ""
    d = raw.lstrip("+")
    head = d[:3] if len(d) > 9 else ""
    return f"{plus}{head}{'•' * (len(d) - len(head) - 4)}{d[-4:]}"


def redact(text: Any) -> Any:
    """Mask phone numbers and e-mails inside a string. Non-strings are returned unchanged."""
    if not isinstance(text, str) or not text:
        return text
    text = _PHONE_RE.sub(_mask_phone_match, text)
    return _EMAIL_RE.sub(lambda m: f"{m.group(1)}•••@{m.group(2)}", text)


def redact_obj(obj: Any) -> Any:
    """Recursively redact strings inside dicts/lists (used for Sentry events)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact_obj(v) for v in obj)
    return obj


class RedactionFilter(logging.Filter):
    """Formats the message once, masks PII in it, and attaches request/call context."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed %-args: keep the raw template
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        record.request_id = request_id_var.get()
        record.call_id = call_id_var.get()
        record.tenant_id = tenant_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in CONTEXT_KEYS:
            value = getattr(record, key, None)
            if value:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        ctx = " ".join(
            f"{k}={getattr(record, k)}" for k in CONTEXT_KEYS if getattr(record, k, None)
        )
        return f"{line} [{ctx}]" if ctx else line


_NOISY = ("pymongo", "httpx", "httpcore", "openai._base_client", "urllib3", "websockets")


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Install a single stdout handler on the root logger. Safe to call more than once."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactionFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(getattr(logging, (level or "INFO").upper(), logging.INFO))

    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
