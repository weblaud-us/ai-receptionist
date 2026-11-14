"""Load what a call needs about the business — once, in parallel, at call start."""

from __future__ import annotations

import asyncio
import logging

from core.prompts.builder import BusinessSnapshot
from core.prompts.verticals import get_vertical
from core.repositories import Repos
from core.tenancy.models import TenantConfig

logger = logging.getLogger(__name__)


async def load_snapshot(repos: Repos, tenant: TenantConfig) -> BusinessSnapshot:
    spec = get_vertical(tenant.vertical)
    try:
        if spec.uses_scheduling:
            services, resources, knowledge = await asyncio.gather(
                repos.services.find({"active": {"$ne": False}}, sort=[("sort", 1), ("name", 1)], limit=100),
                repos.resources.find({"active": {"$ne": False}}, sort=[("sort", 1), ("name", 1)], limit=100),
                repos.knowledge.find({"active": {"$ne": False}}, sort=[("sort", 1), ("updated_at", -1)], limit=500),
            )
        else:
            services, resources = [], []
            knowledge = await repos.knowledge.find({"active": {"$ne": False}}, sort=[("sort", 1)], limit=500)
    except Exception:
        logger.warning("Business snapshot unavailable for %s; prompt will use tenant config only", tenant.id,
                       exc_info=True)
        return BusinessSnapshot()
    return BusinessSnapshot(services=services, resources=resources, knowledge=knowledge)
