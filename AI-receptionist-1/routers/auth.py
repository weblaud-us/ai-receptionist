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

Account settings (the signed-in user's own account; not for the bootstrap .env admin):
GET    /auth/account                 what the settings page shows (pending e-mail change, dates)
PATCH  /auth/profile                 change your display name
POST   /auth/password/change         current + new password; signs out every other session
POST   /auth/email/change            password + new address -> confirmation link to the NEW address
DELETE /auth/email/change            cancel a pending change
POST   /auth/email/confirm           open the link -> the address changes; the old one is told
GET    /auth/sessions                where you're signed in
DELETE /auth/sessions/{id}           sign one of them out
POST   /auth/sessions/revoke-others  sign out everywhere else
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


class ProfileBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class ChangeEmailBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    new_email: str = Field(min_length=3, max_length=254)


class TokenBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)


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
    email_verified = user.legacy
    sales_role = "admin" if user.platform_admin else None
    if not user.legacy:
        doc = await UsersStore(get_db()).get(user.user_id)
        if doc:
            memberships = await service._membership_view(doc)
            mfa_enabled = bool((doc.get("mfa") or {}).get("enabled"))
            email_verified = bool(doc.get("email_verified_at")) or bool(doc.get("is_platform_admin"))
            if not user.platform_admin and not doc.get("disabled"):
                sales_role = doc.get("sales_role") or None
    return {"user": {"id": user.user_id, "email": user.email, "name": user.name,
                     "is_platform_admin": user.platform_admin, "sales_role": sales_role, "mfa_enabled": mfa_enabled,
                     "email_verified": email_verified, "legacy": user.legacy},
            "session_mfa": user.mfa, "tenant_id": user.tenant_id, "role": user.role, "memberships": memberships}


@auth_router.post("/switch")
async def switch(body: SwitchBody, request: Request, response: Response, user: Principal = Depends(current_user),
                 rcpt_refresh: Optional[str] = Cookie(default=None)):
    if user.legacy:
        # The .env admin has no stored session; it switches by sending X-Tenant-Id instead.
        return {"tenant_id": body.tenant_id, "role": "owner", "access_token": None}
    result = await service.switch_tenant(user.user_id, body.tenant_id, rcpt_refresh, mfa=user.mfa,
                                         user_agent=request.headers.get("user-agent", ""))
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


# ── Account settings ─────────────────────────────────────────────────────────

def _own_account(user: Principal) -> str:
    """The bootstrap .env admin (and the X-Admin-Key) have no stored account to change."""
    if user.legacy:
        raise HTTPException(400, "The bootstrap admin is set in the server's .env file. Create a real admin "
                                 "user (scripts/create_admin.py) to manage an account here.")
    return user.user_id


@auth_router.get("/account")
async def account(user: Principal = Depends(current_user)):
    """What the account settings page shows that the session token doesn't carry."""
    if user.legacy:
        return {"legacy": True, "email": user.email, "name": user.name, "pending_email": None,
                "created_at": None, "last_login_at": None, "password_changed_at": None}
    doc = await UsersStore(get_db()).get(user.user_id)
    if not doc:
        raise HTTPException(404, "User not found")
    return {"legacy": False, "email": doc.get("email"), "name": doc.get("name", ""),
            "pending_email": await service.pending_email_change(user.user_id),
            # Mongo hands back naive UTC; send it with the zone so the browser shows local time.
            "created_at": service._aware(doc.get("created_at")),
            "last_login_at": service._aware(doc.get("last_login_at")),
            "password_changed_at": service._aware(doc.get("password_changed_at"))}


@auth_router.patch("/profile")
async def update_profile(body: ProfileBody, user: Principal = Depends(current_user)):
    return {"user": await service.update_profile(_own_account(user), name=body.name)}


@auth_router.post("/password/change")
async def change_password(body: ChangePasswordBody, user: Principal = Depends(current_user),
                          rcpt_refresh: Optional[str] = Cookie(default=None)):
    user_id = _own_account(user)
    signed_out = await service.change_password(user_id, body.current_password, body.new_password,
                                               keep_refresh_token=rcpt_refresh)
    logger.info("Password changed for user %s (%d other sessions signed out)", user_id, signed_out)
    await send_email(user.email, "Your LaudLine password was changed",
                     "The password for your LaudLine account was just changed, and any other devices were "
                     "signed out.\n\nIf this wasn't you, reset your password now and tell your administrator.")
    return {"success": True, "signed_out": signed_out}


@auth_router.post("/email/change")
async def change_email(body: ChangeEmailBody, user: Principal = Depends(current_user)):
    user_id = _own_account(user)
    started = await service.start_email_change(user_id, body.password, body.new_email)
    link = service.dashboard_link(f"/confirm-email?token={started['token']}")
    emailed = await send_email(started["new_email"], "Confirm your new e-mail for LaudLine",
                               "Open this link within 24 hours to use this address for your LaudLine "
                               f"account:\n\n{link}\n\nIf you didn't ask for this, ignore this e-mail.")
    if not emailed:
        if settings.is_development:
            logger.info("E-mail change link (development, e-mail not sent): %s", link)
        else:
            await service.cancel_email_change(user_id)
            raise HTTPException(503, "We couldn't send the confirmation e-mail. Try again later.")
    return {"success": True, "pending_email": started["new_email"], "emailed": emailed}


@auth_router.delete("/email/change")
async def cancel_email_change(user: Principal = Depends(current_user)):
    await service.cancel_email_change(_own_account(user))
    return {"success": True}


@auth_router.post("/email/confirm")
async def confirm_email_change(body: TokenBody):
    done = await service.finish_email_change(body.token)
    new_email = done["user"].get("email")
    logger.info("E-mail changed for user %s", done["user"]["_id"])
    if done["old_email"] and done["old_email"] != new_email:
        await send_email(done["old_email"], "Your LaudLine e-mail was changed",
                         f"Your LaudLine account now signs in with {new_email}.\n\n"
                         "If this wasn't you, contact your administrator straight away.")
    return {"success": True, "email": new_email}


@auth_router.get("/sessions")
async def sessions(user: Principal = Depends(current_user), rcpt_refresh: Optional[str] = Cookie(default=None)):
    return {"sessions": await service.list_sessions(_own_account(user), rcpt_refresh)}


@auth_router.post("/sessions/revoke-others")
async def revoke_other_sessions(user: Principal = Depends(current_user),
                                rcpt_refresh: Optional[str] = Cookie(default=None)):
    user_id = _own_account(user)
    if not rcpt_refresh:
        raise HTTPException(400, "This browser's session wasn't found. Sign in again first.")
    return {"success": True, "signed_out": await service.revoke_other_sessions(user_id, rcpt_refresh)}


@auth_router.delete("/sessions/{session_id}")
async def revoke_session(session_id: str, user: Principal = Depends(current_user)):
    await service.revoke_session(_own_account(user), session_id)
    return {"success": True}
