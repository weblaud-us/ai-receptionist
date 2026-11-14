"""
Public OAuth callbacks for calendar integrations (Roadmap Task 2.8).

GET /integrations/google/callback?code=…&state=…
    Google redirects the owner here after consent. The signed `state` names the tenant and the
    user who started the flow (it expires after 15 minutes), so no session cookie is needed.
    The refresh token is encrypted before it is stored, then the owner is sent back to the
    dashboard's Integrations section.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse

from config import settings
from core.integrations import google_calendar as gcal
from core.repositories import Repos
from core.services import calendar_sync
from core.tenancy import resolve_by_id
from database.mongodb import get_db

logger = logging.getLogger(__name__)

integrations_router = APIRouter(prefix="/integrations", tags=["Integrations"])


def _back(**params: str) -> RedirectResponse:
    base = (settings.DASHBOARD_URL or "").rstrip("/")
    return RedirectResponse(f"{base}/settings?{urlencode({'tab': 'integrations', **params})}", status_code=302)


@integrations_router.get("/google/callback")
async def google_callback(code: Optional[str] = Query(default=None, max_length=2000),
                          state: str = Query(default="", max_length=2000),
                          error: Optional[str] = Query(default=None, max_length=200)):
    info = gcal.read_state(state)
    if not info:
        return _back(google="invalid_link")
    if error or not code:
        return _back(google="cancelled")
    tenant = await resolve_by_id(info["tenant_id"])
    if tenant is None:
        return _back(google="unknown_business")
    try:
        tokens = await gcal.exchange_code(code)
    except gcal.GoogleError as e:
        logger.warning("Google OAuth exchange failed for %s: %s", tenant.id, e)
        return _back(google="failed")
    await calendar_sync.save_connection(tenant.id, refresh_token=tokens["refresh_token"], user_id=info["user_id"])
    await Repos(get_db(), tenant.id).audit.record(user_id=info["user_id"], action="integration.connect",
                                                  target_type="google_calendar", target_id=tenant.id)
    fresh = await resolve_by_id(tenant.id)
    if fresh is not None:
        await calendar_sync.sync_busy(fresh)
    return _back(google="connected")
