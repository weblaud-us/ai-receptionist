"""
Dashboard auth (Roadmap Task 2.10)  —  prefix /auth

POST /auth/login             e-mail + password → access token (body) + refresh token (httpOnly cookie)
POST /auth/refresh           rotate the refresh cookie → new access token
POST /auth/logout            revoke the refresh token, clear the cookie
GET  /auth/me                the signed-in user and the businesses they belong to
POST /auth/switch            change the active business
POST /auth/password/forgot   e-mail a reset link (always 200 — never reveals whether an e-mail exists)
POST /auth/password/reset    set a new password with the one-time token
POST /auth/invite/accept     join a business from an invitation link
POST /auth/mfa/verify        second sign-in step: the TOTP code (when two-factor is on)
POST /auth/mfa/setup         start two-factor setup → secret + otpauth:// URI for the authenticator app
POST /auth/mfa/enable        confirm setup with a code (returns a fresh, two-factor session)
POST /auth/mfa/disable       turn two-factor off (needs a current code)
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from config import settings
from core.auth import Principal, current_user, service
from core.integrations.email import send_email
from core.repositories.repos import UsersStore
from database.mongodb import get_db

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["Auth"])

REFRESH_COOKIE = "rcpt_refresh"


def _set_refresh(response: Response, token: Optional[str]) -> None:
    if not token:
        return
    response.set_cookie(REFRESH_COOKIE, token, max_age=settings.REFRESH_TOKEN_TTL_DAYS * 86400, httponly=True,
                        secure=settings.COOKIE_SECURE, samesite=settings.COOKIE_SAMESITE, path="/auth")


def _clear_refresh(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/auth", secure=settings.COOKIE_SECURE,
                           samesite=settings.COOKIE_SAMESITE, httponly=True)


class LoginBody(BaseModel):
    email: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=256)


class SwitchBody(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)


class RefreshBody(BaseModel):
    tenant_id: Optional[str] = Field(default=None, max_length=64)


class ForgotBody(BaseModel):
    email: str = Field(min_length=3, max_length=256)


class ResetBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=1, max_length=256)


class MfaVerifyBody(BaseModel):
    mfa_token: str = Field(min_length=10, max_length=2000)
    code: str = Field(min_length=6, max_length=8)


class MfaCodeBody(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class AcceptInviteBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: Optional[str] = Field(default=None, max_length=256)
    name: str = Field(default="", max_length=120)


@auth_router.post("/login")
async def login(body: LoginBody, request: Request, response: Response):
    result = await service.login(body.email, body.password,
                                 ip=request.client.host if request.client else "unknown",
                                 user_agent=request.headers.get("user-agent", ""))
    _set_refresh(response, result.refresh_token)
    return result.body()


@auth_router.post("/mfa/verify")
async def mfa_verify(body: MfaVerifyBody, request: Request, response: Response):
    result = await service.verify_mfa(body.mfa_token, body.code,
                                      ip=request.client.host if request.client else "unknown",
                                      user_agent=request.headers.get("user-agent", ""))
    _set_refresh(response, result.refresh_token)
    return result.body()


@auth_router.post("/mfa/setup")
async def mfa_setup(user: Principal = Depends(current_user)):
    if user.legacy:
        raise HTTPException(400, "The bootstrap .env admin can't use two-factor — create a real admin user")
    return await service.start_mfa_setup(user.user_id)


@auth_router.post("/mfa/enable")
async def mfa_enable(body: MfaCodeBody, response: Response, user: Principal = Depends(current_user),
                     rcpt_refresh: Optional[str] = Cookie(default=None)):
    if user.legacy:
        raise HTTPException(400, "The bootstrap .env admin can't use two-factor")
    result = await service.enable_mfa(user.user_id, body.code, user.tenant_id)
    await service.logout(rcpt_refresh)               # replace the pre-MFA session
    _set_refresh(response, result.refresh_token)
    return result.body()


@auth_router.post("/mfa/disable")
async def mfa_disable(body: MfaCodeBody, user: Principal = Depends(current_user)):
    await service.disable_mfa(user.user_id, body.code)
    return {"success": True}


@auth_router.post("/refresh")
async def refresh(response: Response, body: Optional[RefreshBody] = None,
                  rcpt_refresh: Optional[str] = Cookie(default=None)):
    try:
        result = await service.refresh(rcpt_refresh or "", tenant_id=body.tenant_id if body else None)
    except Exception:
        _clear_refresh(response)
        raise
    _set_refresh(response, result.refresh_token)
    return result.body()


@auth_router.post("/logout")
async def logout(response: Response, rcpt_refresh: Optional[str] = Cookie(default=None)):
    await service.logout(rcpt_refresh)
    _clear_refresh(response)
    return {"success": True}


@auth_router.get("/me")
async def me(user: Principal = Depends(current_user)):
    memberships = []
    mfa_enabled = False
    if not user.legacy:
        doc = await UsersStore(get_db()).get(user.user_id)
        if doc:
            memberships = await service._membership_view(doc)
            mfa_enabled = bool((doc.get("mfa") or {}).get("enabled"))
    return {"user": {"id": user.user_id, "email": user.email, "name": user.name,
                     "is_platform_admin": user.platform_admin, "mfa_enabled": mfa_enabled},
            "session_mfa": user.mfa, "tenant_id": user.tenant_id, "role": user.role, "memberships": memberships}


@auth_router.post("/switch")
async def switch(body: SwitchBody, response: Response, user: Principal = Depends(current_user),
                 rcpt_refresh: Optional[str] = Cookie(default=None)):
    if user.legacy:
        # The .env admin has no stored session; it switches by sending X-Tenant-Id instead.
        return {"tenant_id": body.tenant_id, "role": "owner", "access_token": None}
    result = await service.switch_tenant(user.user_id, body.tenant_id, rcpt_refresh, mfa=user.mfa)
    _set_refresh(response, result.refresh_token)
    return result.body()


@auth_router.post("/password/forgot")
async def forgot(body: ForgotBody):
    token = await service.start_password_reset(body.email)
    if token:
        link = service.dashboard_link(f"/reset-password?token={token}")
        sent = await send_email(body.email, "Reset your password",
                                f"Use this link within an hour to choose a new password:\n\n{link}\n\n"
                                "If you didn't ask for this, ignore this e-mail.")
        if not sent:
            logger.info("Password reset link created (e-mail not configured); user must ask an admin")
    return {"success": True}


@auth_router.post("/password/reset")
async def reset(body: ResetBody):
    await service.finish_password_reset(body.token, body.password)
    return {"success": True}


@auth_router.post("/invite/accept")
async def accept_invite(body: AcceptInviteBody):
    doc = await service.accept_invite(body.token, password=body.password, name=body.name)
    return {"success": True, "email": doc.get("email")}
