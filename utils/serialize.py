"""JSON-safe conversion for Mongo documents."""

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from bson import ObjectId


def to_jsonable(obj: Any) -> Any:
    """
    Recursively convert Mongo values for JSON:
    - naive datetimes are UTC in this codebase -> ISO-8601 with a trailing 'Z'
    - ObjectId -> str, Enum -> value, date -> ISO date
    """
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=timezone.utc)
        return obj.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    return obj
