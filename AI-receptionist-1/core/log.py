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


# ⚖ Health identifiers and dates of birth (UK GDPR Art. 9, Australian Privacy Principles).
# Dates of birth: day/month/year as people say them in AU and the UK (14/03/1987), or any date
# labelled as one ("DOB 1987-03-14"). Plain ISO dates (booking dates in our own logs) stay readable.
_DOB_DMY_RE = re.compile(r"(?<!\d)(0?[1-9]|[12]\d|3[01])[/.-](0?[1-9]|1[0-2])[/.-](19\d{2}|20[0-2]\d)(?!\d)")
_DOB_LABELLED_RE = re.compile(
    r"(?i)\b(dob|d\.o\.b\.?|date of birth|born( on)?|birthday)\b[\s:=]*"
    r"(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}(st|nd|rd|th)? \w+ \d{4})")
# NHS number: 10 digits, often written 3-3-4. Medicare: 10 digits written 4-5-1, plus an optional
# 1-digit IRN. Both have check digits, so ordinary numbers are left alone.
_NHS_RE = re.compile(r"(?<![\d+])(\d{3})[ -]?(\d{3})[ -]?(\d{4})(?!\d)")
_MEDICARE_RE = re.compile(r"(?<![\d+])(\d{4})[ -]?(\d{5})[ -]?(\d)(?:[ /-]?(\d))?(?!\d)")


def nhs_number_valid(digits: str) -> bool:
    if len(digits) != 10 or not digits.isdigit():
        return False
    check = 11 - sum(int(d) * w for d, w in zip(digits[:9], range(10, 1, -1))) % 11
    check = 0 if check == 11 else check
    return check != 10 and check == int(digits[9])


def medicare_number_valid(digits: str) -> bool:
    if len(digits) not in (10, 11) or not digits.isdigit() or digits[0] not in "23456":
        return False
    return sum(int(d) * w for d, w in zip(digits[:8], (1, 3, 7, 9, 1, 3, 7, 9))) % 10 == int(digits[8])


def _mask_health_ids(text: str) -> str:
    def nhs(m: re.Match) -> str:
        return "[NHS number]" if nhs_number_valid("".join(m.group(1, 2, 3))) else m.group(0)

    def medicare(m: re.Match) -> str:
        digits = "".join(g for g in m.groups() if g)
        return "[Medicare number]" if medicare_number_valid(digits) else m.group(0)

    text = _MEDICARE_RE.sub(medicare, text)
    return _NHS_RE.sub(nhs, text)


def redact(text: Any) -> Any:
    """Mask phone numbers, e-mails, health identifiers and dates of birth inside a string.
    Non-strings are returned unchanged."""
    if not isinstance(text, str) or not text:
        return text
    text = _DOB_LABELLED_RE.sub(lambda m: f"{m.group(1)} [date of birth]", text)
    text = _DOB_DMY_RE.sub("[date]", text)
    text = _mask_health_ids(text)
    text = _PHONE_RE.sub(_mask_phone_match, text)
    return _EMAIL_RE.sub(lambda m: f"{m.group(1)}•••@{m.group(2)}", text)


# Keys whose values are a caller's own words or health details: blanked whole in Sentry events,
# whatever they contain (free text can't be pattern-matched reliably).
SENSITIVE_KEYS = frozenset({"symptom_summary", "problem", "notes", "transcript", "summary", "dob", "date_of_birth",
                            "medical_history", "unanswered_question", "special_requests"})


def redact_obj(obj: Any) -> Any:
    """Recursively redact strings inside dicts/lists (used for Sentry events)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: ("[redacted]" if isinstance(k, str) and k.lower() in SENSITIVE_KEYS and v else redact_obj(v))
                for k, v in obj.items()}
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
