"""
Repositories (Roadmap Task 2.3): the only way Phase 2 code touches a tenant's collections.

    repos = Repos(get_db(), tenant.id)
    await repos.services.find({"active": True})

Every query, insert and update made through a `TenantRepo` carries `tenant_id` — it cannot be
forgotten, and a query that tries to set another tenant_id is overridden. CI greps for direct
`db.<collection>` access outside this package and the legacy hotel modules.
"""

from core.repositories.base import TenantRepo
from core.repositories.repos import Repos

__all__ = ["TenantRepo", "Repos"]
