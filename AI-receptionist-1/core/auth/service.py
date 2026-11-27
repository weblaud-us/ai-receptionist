"""
Auth flows (Roadmap Task 2.10): login, refresh, logout, switch tenant, invites, password reset.

Login rate limits (per IP and per account) are shared by every API replica through Redis
(core.ratelimit); without Redis they are kept in this process's memory.
The pre-Phase-2 `.env` admin (ADMIN_USERNAME / ADMIN_PASSWORD) still signs in, as a platform
admin, so an existing deployment keeps working until real users exist.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from config import settings
from core import crypto, ratelimit
from core.auth import totp
from core.auth.passwords import hash_password, needs_rehash, password_problem, verify_password
from core.auth.tokens import (
    LEGACY_ADMIN_SUB, MFA_TOKEN_TYPE, access_token, decode_jwt, mfa_challenge_token, new_opaque_token, token_hash,
)
from core.repositories.repos import TenantsStore, UsersStore
from core.tenancy.ids import new_id
from database.mongodb import get_db

logger = logging.getLogger(__name__)

_DEFAULT_SECRETS = {"", "change-in-production"}
MAX_FAILS_PER_IP, MAX_FAILS_PER_ACCOUNT, FAIL_WINDOW_S = 10, 5, 15 * 60
RESET_TTL = timedelta(hours=1)
INVITE_TTL = timedelta(days=7)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class LoginResult:
    access_token: str
    expires_in: int
    refresh_token: Optional[str]
    user: Dict[str, Any]                  # public view
    tenant_id: Optional[str]
    role: Optional[str]
    memberships: List[Dict[str, Any]]

    def body(self) -> Dict[str, Any]:
        return {"access_token": self.access_token, "token_type": "bearer", "expires_in": self.expires_in,
                "user": self.user, "tenant_id": self.tenant_id, "role": self.role,
                "memberships": self.memberships}


@dataclass
class MfaChallenge:
    """Password accepted; a TOTP code is still needed. No session is created yet."""
    mfa_token: str
    user: Dict[str, Any]
    refresh_token: Optional[str] = None

    def body(self) -> Dict[str, Any]:
        return {"mfa_required": True, "mfa_token": self.mfa_token, "access_token": None}


# ── Rate limiting ─────────────────────────────────────────────────────────────

def _ip_key(ip: str) -> str:
    return f"login:ip:{ip}"


def _account_key(email: str) -> str:
    return f"login:acct:{email[:254]}"


async def _check_limits(ip: str, email: str) -> None:
    if (await ratelimit.count(_ip_key(ip), FAIL_WINDOW_S) >= MAX_FAILS_PER_IP
            or await ratelimit.count(_account_key(email), FAIL_WINDOW_S) >= MAX_FAILS_PER_ACCOUNT):
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")


async def _fail(ip: str, email: str) -> HTTPException:
    await ratelimit.hit(_ip_key(ip), FAIL_WINDOW_S)
    await ratelimit.hit(_account_key(email), FAIL_WINDOW_S)
    return HTTPException(401, "Invalid e-mail or password")


def reset_rate_limits() -> None:
    """Forget this process's counters (tests)."""
    ratelimit.memory.reset()


# ── Helpers ───────────────────────────────────────────────────────────────────

def public_user(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": doc["_id"], "email": doc.get("email"), "name": doc.get("name", ""),
            "is_platform_admin": bool(doc.get("is_platform_admin")),
            "sales_role": "admin" if doc.get("is_platform_admin") else (doc.get("sales_role") or None),
            "mfa_enabled": bool((doc.get("mfa") or {}).get("enabled")),
            "email_verified": bool(doc.get("email_verified_at")) or bool(doc.get("is_platform_admin"))}


async def _membership_view(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    tenants = TenantsStore(get_db())
    out = []
    for m in doc.get("memberships") or []:
        t = await tenants.get(m["tenant_id"])
        if t:
            out.append({"tenant_id": t["_id"], "name": t.get("name"), "vertical": t.get("vertical"),
                        "status": t.get("status"), "role": m.get("role"), "resource_id": m.get("resource_id")})
    return out


def _legacy_admin_ok(username: str, password: str) -> bool:
    if not settings.ADMIN_PASSWORD or settings.SECRET_KEY in _DEFAULT_SECRETS:
        return False
    return hmac.compare_digest(username.encode("utf-8"), settings.ADMIN_USERNAME.encode("utf-8")) and \
        hmac.compare_digest(password.encode("utf-8"), settings.ADMIN_PASSWORD.encode("utf-8"))


async def _issue(doc: Dict[str, Any], tenant_id: Optional[str], *, with_refresh: bool = True,
                 user_agent: str = "", mfa: bool = False, signed_in_at: Optional[datetime] = None) -> LoginResult:
    memberships = await _membership_view(doc)
    role: Optional[str] = None
    if tenant_id:
        role = next((m["role"] for m in memberships if m["tenant_id"] == tenant_id), None)
        if role is None and not doc.get("is_platform_admin"):
            raise HTTPException(403, "You are not a member of that business")
    elif memberships:
        tenant_id, role = memberships[0]["tenant_id"], memberships[0]["role"]
    if doc.get("is_platform_admin"):
        role = role or "owner"
    token, ttl = access_token(user_id=doc["_id"], email=doc.get("email", ""), tenant_id=tenant_id, role=role,
                              platform_admin=bool(doc.get("is_platform_admin")), name=doc.get("name", ""), mfa=mfa)
    refresh = None
    if with_refresh:
        refresh = new_opaque_token()
        await get_db().sessions.insert_one({
            "_id": new_id("ses"), "token_hash": token_hash(refresh), "user_id": doc["_id"], "tenant_id": tenant_id,
            "created_at": utcnow(), "expires_at": utcnow() + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS),
            "user_agent": user_agent[:200], "mfa": mfa,
            # Refresh rotates the row (created_at = last active); this keeps the original sign-in.
            "signed_in_at": signed_in_at or utcnow(),
        })
    return LoginResult(token, ttl, refresh, public_user(doc), tenant_id, role, memberships)


# ── Flows ─────────────────────────────────────────────────────────────────────

async def login(email: str, password: str, *, ip: str = "unknown", user_agent: str = "") -> LoginResult | MfaChallenge:
    email = (email or "").strip().lower()
    await _check_limits(ip, email)
    users = UsersStore(get_db())
    doc = await users.by_email(email) if "@" in email else None
    if doc and not doc.get("disabled") and doc.get("password_hash") and verify_password(password, doc["password_hash"]):
        await ratelimit.clear(_account_key(email))
        updates: Dict[str, Any] = {"last_login_at": utcnow()}
        if needs_rehash(doc["password_hash"]):
            updates["password_hash"] = hash_password(password)
        await users.update(doc["_id"], {"$set": updates})
        if (doc.get("mfa") or {}).get("enabled"):
            return MfaChallenge(mfa_challenge_token(doc["_id"]), public_user(doc))
        logger.info("Login ok for user %s", doc["_id"])
        return await _issue(doc, None, user_agent=user_agent)

    if not doc and _legacy_admin_ok(email, password):
        legacy = {"_id": LEGACY_ADMIN_SUB, "email": settings.ADMIN_USERNAME, "name": "Administrator",
                  "is_platform_admin": True, "memberships": []}
        token, ttl = access_token(user_id=LEGACY_ADMIN_SUB, email=settings.ADMIN_USERNAME, tenant_id=None,
                                  role="owner", platform_admin=True, name="Administrator")
        return LoginResult(token, ttl, None, public_user(legacy), None, "owner", [])

    raise await _fail(ip, email)


async def refresh(refresh_token: str, *, tenant_id: Optional[str] = None) -> LoginResult:
    """Rotate: the presented refresh token is revoked and a new pair issued."""
    if not refresh_token:
        raise HTTPException(401, "Not signed in")
    db = get_db()
    session = await db.sessions.find_one({"token_hash": token_hash(refresh_token)})
    if not session or (_aware(session.get("expires_at")) or utcnow()) < utcnow():
        raise HTTPException(401, "Session expired — sign in again")
    await db.sessions.delete_one({"_id": session["_id"]})
    doc = await UsersStore(db).get(session["user_id"])
    if not doc or doc.get("disabled"):
        raise HTTPException(401, "Account disabled")
    return await _issue(doc, tenant_id or session.get("tenant_id"), mfa=bool(session.get("mfa")),
                        user_agent=session.get("user_agent") or "",
                        signed_in_at=_aware(session.get("signed_in_at")) or _aware(session.get("created_at")))


async def logout(refresh_token: Optional[str]) -> None:
    if refresh_token:
        await get_db().sessions.delete_one({"token_hash": token_hash(refresh_token)})


async def switch_tenant(user_id: str, tenant_id: str, refresh_token: Optional[str], *, mfa: bool = False,
                        user_agent: str = "") -> LoginResult:
    doc = await UsersStore(get_db()).get(user_id)
    if not doc or doc.get("disabled"):
        raise HTTPException(401, "Account disabled")
    old = await _session_for(refresh_token)
    if refresh_token:
        await logout(refresh_token)
    return await _issue(doc, tenant_id, mfa=mfa, user_agent=user_agent or (old or {}).get("user_agent") or "",
                        signed_in_at=_aware((old or {}).get("signed_in_at")))


async def _session_for(refresh_token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not refresh_token:
        return None
    return await get_db().sessions.find_one({"token_hash": token_hash(refresh_token)})


# ── Two-factor (TOTP) ─────────────────────────────────────────────────────────

async def _use_code(user_id: str, secret_enc: str, code: str) -> bool:
    """True once per code: the step is recorded atomically, so a replayed code (or two requests
    racing with the same one) fails (audit SEC-02)."""
    step = totp.matched_step(crypto.decrypt(secret_enc), code)
    if step is None:
        return False
    res = await get_db().users.update_one(
        {"_id": user_id, "$or": [{"mfa.last_step": {"$lt": step}}, {"mfa.last_step": {"$exists": False}}]},
        {"$set": {"mfa.last_step": step}})
    return res.modified_count == 1


async def verify_mfa(mfa_token: str, code: str, *, ip: str = "unknown", user_agent: str = "") -> LoginResult:
    payload = decode_jwt(mfa_token or "")
    if not payload or payload.get("typ") != MFA_TOKEN_TYPE:
        raise HTTPException(401, "Sign-in expired — enter your password again")
    doc = await UsersStore(get_db()).get(str(payload["sub"]))
    if not doc or doc.get("disabled") or not (doc.get("mfa") or {}).get("secret_enc"):
        raise HTTPException(401, "Sign-in expired — enter your password again")
    email = doc.get("email", "")
    await _check_limits(ip, email)
    if not await _use_code(doc["_id"], doc["mfa"]["secret_enc"], code):
        raise await _fail(ip, email)
    await ratelimit.clear(_account_key(email))
    logger.info("Login ok (with two-factor) for user %s", doc["_id"])
    return await _issue(doc, None, user_agent=user_agent, mfa=True)


async def start_mfa_setup(user_id: str) -> Dict[str, str]:
    doc = await UsersStore(get_db()).get(user_id)
    if not doc:
        raise HTTPException(404, "User not found")
    secret = totp.new_secret()
    await get_db().users.update_one({"_id": user_id}, {"$set": {"mfa.pending_secret_enc": crypto.encrypt(secret)}})
    return {"secret": secret, "otpauth_uri": totp.otpauth_uri(secret, doc.get("email", user_id))}


async def enable_mfa(user_id: str, code: str, tenant_id: Optional[str]) -> LoginResult:
    db = get_db()
    doc = await UsersStore(db).get(user_id)
    pending = ((doc or {}).get("mfa") or {}).get("pending_secret_enc")
    if not doc or not pending:
        raise HTTPException(400, "Start two-factor setup first")
    step = totp.matched_step(crypto.decrypt(pending), code)
    if step is None:
        raise HTTPException(400, "That code didn't match. Check your phone's clock and try the newest code.")
    await db.users.update_one({"_id": user_id}, {"$set": {"mfa.enabled": True, "mfa.secret_enc": pending,
                                                          "mfa.enabled_at": utcnow(), "mfa.last_step": step},
                                                 "$unset": {"mfa.pending_secret_enc": ""}})
    doc = await UsersStore(db).get(user_id) or doc
    return await _issue(doc, tenant_id, mfa=True)


async def disable_mfa(user_id: str, code: str) -> None:
    db = get_db()
    doc = await UsersStore(db).get(user_id)
    secret = ((doc or {}).get("mfa") or {}).get("secret_enc")
    if not doc or not secret:
        raise HTTPException(400, "Two-factor isn't on")
    if not await _use_code(user_id, secret, code):
        raise HTTPException(400, "That code didn't match")
    await db.users.update_one({"_id": user_id}, {"$set": {"mfa": {"enabled": False}}})


# ── Account self-service (the signed-in user's own name, password, e-mail, sessions) ──

REAUTH_MAX_FAILS = 5
EMAIL_CHANGE_TTL = timedelta(hours=24)


def _reauth_key(user_id: str) -> str:
    return f"reauth:{user_id}"


async def _active_user(user_id: str) -> Dict[str, Any]:
    doc = await UsersStore(get_db()).get(user_id)
    if not doc or doc.get("disabled"):
        raise HTTPException(404, "User not found")
    return doc


async def _check_current_password(doc: Dict[str, Any], password: str) -> None:
    """Re-authentication for sensitive changes. 400 (not 401) so the dashboard doesn't treat a typo
    as an expired session; failures share a per-account limit so it can't be used to guess."""
    key = _reauth_key(doc["_id"])
    if await ratelimit.count(key, FAIL_WINDOW_S) >= REAUTH_MAX_FAILS:
        raise HTTPException(429, "Too many wrong passwords. Try again in a few minutes.")
    if not doc.get("password_hash") or not verify_password(password or "", doc["password_hash"]):
        await ratelimit.hit(key, FAIL_WINDOW_S)
        raise HTTPException(400, "Your current password isn't right")
    await ratelimit.clear(key)


async def update_profile(user_id: str, *, name: str) -> Dict[str, Any]:
    await _active_user(user_id)
    await UsersStore(get_db()).update(user_id, {"$set": {"name": name.strip()[:120]}})
    return public_user(await _active_user(user_id))


async def change_password(user_id: str, current_password: str, new_password: str, *,
                          keep_refresh_token: Optional[str] = None) -> int:
    """Set a new password; every other session is signed out. Returns how many were."""
    doc = await _active_user(user_id)
    await _check_current_password(doc, current_password)
    problem = password_problem(new_password)
    if problem:
        raise HTTPException(400, problem)
    if verify_password(new_password, doc["password_hash"]):
        raise HTTPException(400, "Choose a password you aren't using now")
    db = get_db()
    await db.users.update_one({"_id": user_id}, {"$set": {"password_hash": hash_password(new_password),
                                                          "password_changed_at": utcnow()}})
    await db.password_resets.delete_many({"user_id": user_id})        # an old reset link must not undo this
    return await revoke_other_sessions(user_id, keep_refresh_token)


def _clean_email(email: str) -> str:
    email = (email or "").strip().lower()
    local, _, domain = email.partition("@")
    if not local or "." not in domain or " " in email or len(email) > 254:
        raise HTTPException(400, "Enter a valid e-mail address")
    return email


async def start_email_change(user_id: str, password: str, new_email: str) -> Dict[str, str]:
    """Checks the password and that the address is free → a one-time token for a link sent to the
    NEW address. Nothing changes until that link is opened."""
    doc = await _active_user(user_id)
    await _check_current_password(doc, password)
    new_email = _clean_email(new_email)
    if new_email == (doc.get("email") or "").lower():
        raise HTTPException(400, "That's already your e-mail")
    users = UsersStore(get_db())
    if await users.by_email(new_email):
        raise HTTPException(409, "Another account already uses that e-mail")
    db = get_db()
    await db.email_changes.delete_many({"user_id": user_id})         # only the newest request counts
    token = new_opaque_token()
    await db.email_changes.insert_one({"token_hash": token_hash(token), "user_id": user_id, "new_email": new_email,
                                       "old_email": doc.get("email"), "created_at": utcnow(),
                                       "expires_at": utcnow() + EMAIL_CHANGE_TTL})
    return {"token": token, "new_email": new_email}


async def pending_email_change(user_id: str) -> Optional[str]:
    row = await get_db().email_changes.find_one({"user_id": user_id})
    if not row or (_aware(row.get("expires_at")) or utcnow()) < utcnow():
        return None
    return row.get("new_email")


async def cancel_email_change(user_id: str) -> None:
    await get_db().email_changes.delete_many({"user_id": user_id})


async def finish_email_change(token: str) -> Dict[str, Any]:
    """Apply the change from the link. Returns {"user": doc, "old_email": ...}."""
    db = get_db()
    row = await db.email_changes.find_one({"token_hash": token_hash(token or "")})
    if not row or (_aware(row.get("expires_at")) or utcnow()) < utcnow():
        raise HTTPException(400, "This link is invalid or has expired — ask for a new one in your account settings")
    doc = await _active_user(row["user_id"])
    other = await UsersStore(db).by_email(row["new_email"])
    if other and other["_id"] != doc["_id"]:
        await db.email_changes.delete_many({"user_id": doc["_id"]})
        raise HTTPException(409, "Another account started using that e-mail — choose a different one")
    try:
        await db.users.update_one({"_id": doc["_id"]}, {"$set": {"email": row["new_email"],
                                                                 "email_verified_at": utcnow()}})
    except DuplicateKeyError:                                        # unique index: lost a race
        raise HTTPException(409, "Another account started using that e-mail — choose a different one")
    await db.email_changes.delete_many({"user_id": doc["_id"]})
    await db.password_resets.delete_many({"user_id": doc["_id"]})
    return {"user": await _active_user(doc["_id"]), "old_email": row.get("old_email") or doc.get("email")}


async def list_sessions(user_id: str, current_refresh_token: Optional[str]) -> List[Dict[str, Any]]:
    current = token_hash(current_refresh_token) if current_refresh_token else None
    rows = await get_db().sessions.find({"user_id": user_id}).to_list(length=200)
    out = []
    for s in rows:
        if (_aware(s.get("expires_at")) or utcnow()) < utcnow():
            continue
        out.append({"id": s["_id"], "user_agent": s.get("user_agent") or "", "mfa": bool(s.get("mfa")),
                    "signed_in_at": _aware(s.get("signed_in_at")) or _aware(s.get("created_at")),
                    "last_active_at": _aware(s.get("created_at")),
                    "current": current is not None and s.get("token_hash") == current})
    out.sort(key=lambda s: (not s["current"], -(s["last_active_at"] or utcnow()).timestamp()))
    return out


async def revoke_session(user_id: str, session_id: str) -> None:
    res = await get_db().sessions.delete_one({"_id": session_id, "user_id": user_id})
    if res.deleted_count == 0:
        raise HTTPException(404, "That session has already ended")


async def revoke_other_sessions(user_id: str, keep_refresh_token: Optional[str]) -> int:
    query: Dict[str, Any] = {"user_id": user_id}
    if keep_refresh_token:
        query["token_hash"] = {"$ne": token_hash(keep_refresh_token)}
    res = await get_db().sessions.delete_many(query)
    return res.deleted_count


async def create_user(*, email: str, password: Optional[str], name: str = "", tenant_id: Optional[str] = None,
                      role: str = "owner", platform_admin: bool = False,
                      resource_id: Optional[str] = None) -> Dict[str, Any]:
    email = email.strip().lower()
    users = UsersStore(get_db())
    if await users.by_email(email):
        raise HTTPException(409, "A user with that e-mail already exists")
    if password is not None:
        problem = password_problem(password)
        if problem:
            raise HTTPException(400, problem)
    doc = {"_id": new_id("usr"), "email": email, "name": name.strip()[:120],
           "password_hash": hash_password(password) if password else None,
           "memberships": [_membership(tenant_id, role, resource_id)] if tenant_id else [],
           "is_platform_admin": platform_admin, "mfa": {"enabled": False}, "disabled": False,
           "email_verified_at": None, "last_login_at": None}
    await users.insert(doc)
    return doc


def _membership(tenant_id: str, role: str, resource_id: Optional[str] = None) -> Dict[str, Any]:
    """A dentist's membership names the practitioner (a `resources` row) whose day they see."""
    m: Dict[str, Any] = {"tenant_id": tenant_id, "role": role}
    if role == "dentist" and resource_id:
        m["resource_id"] = resource_id
    return m


async def add_membership(user_id: str, tenant_id: str, role: str, resource_id: Optional[str] = None) -> None:
    db = get_db()
    await db.users.update_one({"_id": user_id}, {"$pull": {"memberships": {"tenant_id": tenant_id}}})
    await db.users.update_one({"_id": user_id},
                              {"$push": {"memberships": _membership(tenant_id, role, resource_id)}})


async def remove_membership(user_id: str, tenant_id: str) -> None:
    await get_db().users.update_one({"_id": user_id}, {"$pull": {"memberships": {"tenant_id": tenant_id}}})


async def start_password_reset(email: str, *, ttl: timedelta = RESET_TTL) -> Optional[str]:
    """Returns the one-time token (the caller e-mails the link). Silent for unknown e-mails.
    `ttl`: 1 hour for a reset; longer (INVITE_TTL) for a new owner's welcome e-mail."""
    doc = await UsersStore(get_db()).by_email(email)
    if not doc or doc.get("disabled"):
        return None
    token = new_opaque_token()
    await get_db().password_resets.insert_one({"token_hash": token_hash(token), "user_id": doc["_id"],
                                               "expires_at": utcnow() + ttl, "created_at": utcnow()})
    return token


async def finish_password_reset(token: str, new_password: str) -> None:
    problem = password_problem(new_password)
    if problem:
        raise HTTPException(400, problem)
    db = get_db()
    row = await db.password_resets.find_one({"token_hash": token_hash(token)})
    if not row or (_aware(row.get("expires_at")) or utcnow()) < utcnow():
        raise HTTPException(400, "This link is invalid or has expired")
    await db.password_resets.delete_one({"_id": row["_id"]})
    await db.users.update_one({"_id": row["user_id"]}, {"$set": {
        "password_hash": hash_password(new_password), "email_verified_at": utcnow()}})
    await db.sessions.delete_many({"user_id": row["user_id"]})      # sign out everywhere


async def create_invite(*, tenant_id: str, email: str, role: str, invited_by: str,
                        resource_id: Optional[str] = None) -> str:
    token = new_opaque_token()
    await get_db().invites.insert_one({"token_hash": token_hash(token), "tenant_id": tenant_id,
                                       "email": email.strip().lower(), "role": role, "invited_by": invited_by,
                                       "resource_id": resource_id if role == "dentist" else None,
                                       "expires_at": utcnow() + INVITE_TTL, "created_at": utcnow()})
    return token


async def accept_invite(token: str, *, password: Optional[str], name: str = "") -> Dict[str, Any]:
    db = get_db()
    row = await db.invites.find_one({"token_hash": token_hash(token)})
    if not row or (_aware(row.get("expires_at")) or utcnow()) < utcnow():
        raise HTTPException(400, "This invitation is invalid or has expired")
    users = UsersStore(db)
    doc = await users.by_email(row["email"])
    if doc:
        await add_membership(doc["_id"], row["tenant_id"], row["role"], row.get("resource_id"))
    else:
        if not password:
            raise HTTPException(400, "Choose a password")
        doc = await create_user(email=row["email"], password=password, name=name, tenant_id=row["tenant_id"],
                                role=row["role"], resource_id=row.get("resource_id"))
        await db.users.update_one({"_id": doc["_id"]}, {"$set": {"email_verified_at": utcnow()}})
    await db.invites.delete_one({"_id": row["_id"]})
    return doc


def dashboard_link(path: str) -> str:
    base = (settings.DASHBOARD_URL or "").rstrip("/")
    return f"{base}{path}" if base else path


# ── E-mail verification (Phase 5 self-serve signup) ──────────────────────────

VERIFY_TTL = timedelta(days=3)


async def start_email_verification(user_id: str) -> str:
    token = new_opaque_token()
    db = get_db()
    await db.email_verifications.delete_many({"user_id": user_id})
    await db.email_verifications.insert_one({"token_hash": token_hash(token), "user_id": user_id,
                                             "expires_at": utcnow() + VERIFY_TTL, "created_at": utcnow()})
    return token


async def finish_email_verification(token: str) -> Dict[str, Any]:
    db = get_db()
    row = await db.email_verifications.find_one({"token_hash": token_hash(token or "")})
    if not row or (_aware(row.get("expires_at")) or utcnow()) < utcnow():
        raise HTTPException(400, "This link is invalid or has expired — sign in and send a new one")
    await db.email_verifications.delete_many({"user_id": row["user_id"]})
    await db.users.update_one({"_id": row["user_id"]}, {"$set": {"email_verified_at": utcnow()}})
    doc = await UsersStore(db).get(row["user_id"])
    return doc or {}


async def issue_session(doc: Dict[str, Any], tenant_id: Optional[str], *, user_agent: str = "") -> LoginResult:
    """Sign a just-created user in (self-serve signup)."""
    return await _issue(doc, tenant_id, user_agent=user_agent)
