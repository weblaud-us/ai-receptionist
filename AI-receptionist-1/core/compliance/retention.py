"""
Recording retention per business (Roadmap §11.2 "Retention").

Each business sets `agent.recording.retention_days` (default 30). The bucket lifecycle rule
(RECORDING_RETENTION_DAYS, docs/PRODUCTION_CHECKLIST.md) is the global backstop; this job enforces
the shorter per-business value, and is what the DPA promises. Transcripts have their own setting,
purged by services/tenant_jobs.purge_transcripts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict

import httpx

from config import settings
from core.integrations.s3_presign import presign
from core.tenancy.models import TenantConfig

logger = logging.getLogger(__name__)

MAX_PER_RUN = 500        # per business per tick; the next tick carries on


def storage_configured() -> bool:
    return bool(settings.RECORDING_S3_BUCKET and settings.RECORDING_S3_SECRET)


async def delete_recording_object(rec: Dict[str, Any]) -> bool:
    """Delete one recording from storage. True when it's gone (404 counts as gone)."""
    path = (rec or {}).get("path")
    if not path or not storage_configured():
        return False
    try:
        url = presign(path, method="DELETE", bucket=rec.get("bucket") or None, expires=300)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.delete(url)
        if resp.status_code < 300 or resp.status_code == 404:
            return True
        logger.warning("Deleting a recording failed: HTTP %s", resp.status_code)
    except Exception:
        logger.warning("Deleting a recording errored", exc_info=True)
    return False


async def purge_recordings(db: Any, tenant: TenantConfig, now: datetime) -> int:
    if not storage_configured():
        return 0
    cutoff = now - timedelta(days=tenant.agent.recording.retention_days)
    deleted = 0
    cursor = db.calls.find({"tenant_id": tenant.id, "recording.path": {"$exists": True},
                            "started_at": {"$lt": cutoff}}, {"call_id": 1, "recording": 1}).limit(MAX_PER_RUN)
    async for call in cursor:
        if await delete_recording_object(call.get("recording") or {}):
            await db.calls.update_one({"_id": call["_id"]}, {"$unset": {"recording": ""},
                                                             "$set": {"recording_deleted_at": now}})
            deleted += 1
    if deleted:
        logger.info("Deleted %d recordings older than %d days for %s", deleted,
                    tenant.agent.recording.retention_days, tenant.id)
    return deleted
