"""Load what a call needs about the business — once, in parallel, at call start."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from core.prompts.builder import BusinessSnapshot
from core.prompts.verticals import get_vertical
from core.repositories import Repos
from core.tenancy.models import TenantConfig

logger = logging.getLogger(__name__)

NOTICE_DAYS_AHEAD = 14          # closures and staff away up to two weeks out are worth mentioning


async def load_snapshot(repos: Repos, tenant: TenantConfig) -> BusinessSnapshot:
    spec = get_vertical(tenant.vertical)
    try:
        notices: List[Dict[str, Any]] = []
        services: List[Dict[str, Any]] = []
        resources: List[Dict[str, Any]] = []
        if spec.uses_scheduling:
            now = datetime.now(timezone.utc)
            services, resources, knowledge, notices = await asyncio.gather(
                repos.services.find({"active": {"$ne": False}}, sort=[("sort", 1), ("name", 1)], limit=100),
                repos.resources.find({"active": {"$ne": False}}, sort=[("sort", 1), ("name", 1)], limit=100),
                repos.knowledge.find({"active": {"$ne": False}}, sort=[("sort", 1), ("updated_at", -1)], limit=500),
                repos.exceptions.find({"announce": True, "end": {"$gt": now},
                                       "start": {"$lt": now + timedelta(days=NOTICE_DAYS_AHEAD)}},
                                      sort=[("start", 1)], limit=10),
            )
        else:
            knowledge = await repos.knowledge.find({"active": {"$ne": False}}, sort=[("sort", 1)], limit=500)
    except Exception:
        logger.warning("Business snapshot unavailable for %s; prompt will use tenant config only", tenant.id,
                       exc_info=True)
        return BusinessSnapshot()
    return BusinessSnapshot(services=services, resources=resources, knowledge=knowledge, notices=notices)
