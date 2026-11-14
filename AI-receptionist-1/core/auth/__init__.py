"""
Dashboard authentication and authorisation (Roadmap Task 2.10).

    passwords.py   argon2id hashing
    tokens.py      short-lived access JWT (HS256, stdlib) + opaque refresh tokens in `sessions`
    deps.py        FastAPI dependencies: current_user, require_tenant(role), require_platform_admin
    service.py     login / refresh / logout / invites / password reset

Roles inside a tenant: owner > admin > staff. Platform admins (you) pass every check and act on
a tenant by sending `X-Tenant-Id`; everything they do is written to `audit_log`.
"""

from core.auth.deps import Principal, TenantPrincipal, current_user, require_platform_admin, require_tenant

__all__ = ["Principal", "TenantPrincipal", "current_user", "require_platform_admin", "require_tenant"]
