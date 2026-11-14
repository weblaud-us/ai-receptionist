"""
Call recording via LiveKit Egress → S3-compatible storage (Roadmap Task 1.11). ⚖

Off unless ENABLE_CALL_RECORDING=true. The greeting then says "This call may be recorded"
(agent/profile.py) — the minimum in US all-party-consent states and across the EU/UK.
Egress stops by itself when the room closes. Retention (RECORDING_RETENTION_DAYS) is enforced
with a bucket lifecycle rule; see docs/PRODUCTION_CHECKLIST.md.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from livekit import api

from agent.state import CallState
from config import settings

logger = logging.getLogger(__name__)


def recording_configured() -> bool:
    return bool(settings.ENABLE_CALL_RECORDING and settings.RECORDING_S3_BUCKET
                and settings.RECORDING_S3_ACCESS_KEY and settings.RECORDING_S3_SECRET)


def recording_path(state: CallState) -> str:
    return f"recordings/{state.started_at:%Y/%m/%d}/{state.call_id}.ogg"


async def start_recording(lkapi: Any, state: CallState) -> Optional[dict[str, Any]]:
    if not recording_configured():
        return None
    path = recording_path(state)
    request = api.RoomCompositeEgressRequest(
        room_name=state.call_id,
        audio_only=True,
        file_outputs=[api.EncodedFileOutput(
            file_type=api.EncodedFileType.OGG,
            filepath=path,
            s3=api.S3Upload(
                bucket=settings.RECORDING_S3_BUCKET,
                region=settings.RECORDING_S3_REGION,
                endpoint=settings.RECORDING_S3_ENDPOINT,
                access_key=settings.RECORDING_S3_ACCESS_KEY,
                secret=settings.RECORDING_S3_SECRET,
            ),
        )],
    )
    try:
        info = await lkapi.egress.start_room_composite_egress(request)
    except Exception:
        logger.warning("Could not start recording for call %s", state.call_id, exc_info=True)
        return None
    logger.info("Recording call %s (egress %s)", state.call_id, info.egress_id)
    return {"egress_id": info.egress_id, "bucket": settings.RECORDING_S3_BUCKET, "path": path}
