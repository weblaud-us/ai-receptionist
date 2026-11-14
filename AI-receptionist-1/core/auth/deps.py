"""
FastAPI dependencies.

    user: Principal = Depends(current_user)                      # any signed-in user
    tp: TenantPrincipal = Depends(require_tenant("staff"))        # acting inside one tenant
    tp: TenantPrincipal = Depends(require_tenant("owner"))        # owner-only (billing, team)
    admin: Principal = Depends(require_platform_admin)            # you

Tenant selection: the access token names the active tenant (`tid`). A user who belongs to several
tenants, or a platform admin, may override it per request with `X-Tenant-Id` — membership is
re-checked against the database on every override. Legacy credentials (`X-Admin-Key`, the
ADMIN_USERNAME login) are platform admins; in legacy single-hotel mode they act on the .env hotel.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from fastapi import Depends, Header, HTTPException, Request

from config import settings
from core.auth.tokens import ACCESS_TOKEN_TYPE, LEGACY_ADMIN_SUB, decode_jwt
from core.repositories import Repos
from core.repositories.repos import UsersStore
from core.tenancy import resolve_by_id
from core.tenancy.models import TenantConfig, legacy_hotel_tenant
from core.tenancy.resolver import legacy_mode
from database.mongodb import get_db

ROLE_RANK = {"staff": 1, "admin": 2, "owner": 3}
_DEFAULT_SECRETS = {"", "change-in-production"}


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    name: str = ""
    tenant_id: Optional[str] = None        # active tenant from the token
    role: Optional[str] = None             # role in that tenant
    platform_admin: bool = False
    legacy: bool = False                   # X-Admin-Key / env admin
    mfa: bool = False                      # signed in with a TOTP code this session

    def rank(self) -> int:
        return 99 if self.platform_admin else ROLE_RANK.get(self.role or "", 0)


@dataclass(frozen=True)
class TenantPrincipal:
    user: Principal
    tenant: TenantConfig
    role: str

    @property
    def tenant_id(self) -> str:
        return self.tenant.id

    def repos(self) -> Repos:
        return Repos(get_db(), self.tenant.id)

    def can(self, min_role: str) -> bool:
        return self.user.platform_admin or ROLE_RANK.get(self.role, 0) >= ROLE_RANK[min_role]


def _unauthorized(detail: str = "Authentication required") -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def _legacy_admin_key_ok(key: str) -> bool:
    return bool(key) and settings.ADMIN_SECRET_KEY not in _DEFAULT_SECRETS and \
        hmac.compare_digest(key.encode("utf-8"), settings.ADMIN_SECRET_KEY.encode("utf-8"))


def principal_from_token(payload: Dict[str, Any]) -> Optional[Principal]:
    if payload.get("typ") == ACCESS_TOKEN_TYPE and payload.get("sub"):
        return Principal(user_id=str(payload["sub"]), email=str(payload.get("email") or ""),
                         name=str(payload.get("name") or ""), tenant_id=payload.get("tid"),
                         role=payload.get("role"), platform_admin=bool(payload.get("pa")),
                         legacy=payload.get("sub") == LEGACY_ADMIN_SUB, mfa=bool(payload.get("mfa")))
    if payload.get("role") == "admin" and payload.get("sub") and "typ" not in payload:
        # Pre-Phase-2 dashboard token (utils.auth.create_access_token): the .env admin.
        return Principal(user_id=LEGACY_ADMIN_SUB, email=str(payload["sub"]), name="Administrator",
                         platform_admin=True, legacy=True)
    return None


async def current_user(
    authorization: str = Header(default=""),
    x_admin_key: str = Header(default="", alias="X-Admin-Key"),
) -> Principal:
    if authorization.lower().startswith("bearer "):
        payload = decode_jwt(authorization[7:].strip())
        principal = principal_from_token(payload) if payload else None
        if principal:
            return principal
        raise _unauthorized("Invalid or expired token")
    if _legacy_admin_key_ok(x_admin_key):
        return Principal(user_id="api-key", email="api-key", name="API key", platform_admin=True, legacy=True)
    raise _unauthorized()


def _require_mfa(user: Principal) -> None:
    """Platform admins can see every business, so they must use two-factor (Task 2.10).
    The bootstrap .env admin and X-Admin-Key are exempt — remove them once real admins exist."""
    if user.platform_admin and not user.legacy and settings.REQUIRE_ADMIN_MFA and not user.mfa:
        raise HTTPException(403, "mfa_required: set up two-factor authentication to use platform admin access")


async def _memberships(user: Principal) -> List[Dict[str, Any]]:
    if user.legacy or user.platform_admin:
        return []
    doc = await UsersStore(get_db()).get(user.user_id)
    if not doc or doc.get("disabled"):
        raise _unauthorized("Account disabled")
    return list(doc.get("memberships") or [])


async def resolve_tenant_for(user: Principal, requested: Optional[str]) -> TenantPrincipal:
    """Pick the tenant this request acts on and verify the user may act there."""
    tenant_id = (requested or "").strip() or user.tenant_id
    if not tenant_id:
        if user.platform_admin and await legacy_mode():
            return TenantPrincipal(user, legacy_hotel_tenant(), "owner")
        if user.platform_admin:
            raise HTTPException(400, "X-Tenant-Id header required for platform admins")
        raise HTTPException(403, "No business selected")

    role: Optional[str]
    _require_mfa(user)
    if user.platform_admin:
        role = "owner"
    elif requested and requested != user.tenant_id:
        role = next((m.get("role") for m in await _memberships(user) if m.get("tenant_id") == requested), None)
        if role is None:
            raise HTTPException(403, "You are not a member of that business")
    else:
        role = user.role or "staff"

    tenant = await resolve_by_id(tenant_id)
    if tenant is None:
        if tenant_id == "default" or (user.platform_admin and await legacy_mode()):
            return TenantPrincipal(user, legacy_hotel_tenant(), role or "owner")
        raise HTTPException(404, "Business not found")
    if tenant.status in ("suspended", "cancelled") and not user.platform_admin:
        raise HTTPException(403, f"This business is {tenant.status}")
    return TenantPrincipal(user, tenant, role or "staff")


def require_tenant(min_role: str = "staff") -> Callable[..., Any]:
    if min_role not in ROLE_RANK:
        raise ValueError(f"unknown role {min_role}")

    async def dependency(
        request: Request,
        user: Principal = Depends(current_user),
        x_tenant_id: str = Header(default="", alias="X-Tenant-Id"),
    ) -> TenantPrincipal:
        tp = await resolve_tenant_for(user, x_tenant_id)
        if not tp.can(min_role):
            raise HTTPException(403, f"Requires the {min_role} role")
        request.state.tenant_id = tp.tenant.id
        return tp

    return dependency


async def require_platform_admin(user: Principal = Depends(current_user)) -> Principal:
    if not user.platform_admin:
        raise HTTPException(403, "Platform administrators only")
    _require_mfa(user)
    return user
