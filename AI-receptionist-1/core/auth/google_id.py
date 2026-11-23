"""
Verify a "Sign in with Google" ID token (the `credential` Google Identity Services hands the landing
page). Checked locally against Google's published signing keys (cached), so no Google round trip per
visitor:

    signature (RS256, a current Google key) · aud = our client id · iss = accounts.google.com ·
    not expired · email verified

Returns the few claims we keep. Raises GoogleTokenError with a short reason otherwise.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional

import jwt

from config import settings

GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
LEEWAY_S = 30                     # clock skew between Google and us

_jwks: Optional[jwt.PyJWKClient] = None


class GoogleTokenError(ValueError):
    pass


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str                      # Google's stable account id
    email: str
    name: str
    hosted_domain: str            # Workspace domain ("" for a personal Gmail)


def _client() -> jwt.PyJWKClient:
    global _jwks
    if _jwks is None:
        # Keys rotate about weekly; cache them and re-fetch only for an unknown key id.
        _jwks = jwt.PyJWKClient(GOOGLE_CERTS_URL, cache_keys=True, lifespan=6 * 3600, timeout=5)
    return _jwks


def _decode(token: str, audience: str) -> Dict[str, Any]:
    try:
        key = _client().get_signing_key_from_jwt(token)
        return jwt.decode(token, key.key, algorithms=["RS256"], audience=audience, issuer=GOOGLE_ISSUERS,
                          leeway=LEEWAY_S, options={"require": ["exp", "iat", "sub", "aud", "iss"]})
    except jwt.ExpiredSignatureError:
        raise GoogleTokenError("Your Google sign-in has expired. Please sign in again.")
    except jwt.PyJWKClientError:
        raise GoogleTokenError("Couldn't check the Google sign-in right now. Please try again.")
    except jwt.InvalidTokenError:
        raise GoogleTokenError("That Google sign-in isn't valid.")


async def verify(credential: str) -> GoogleIdentity:
    audience = settings.google_signin_client_id
    if not audience:
        raise GoogleTokenError("Google sign-in isn't set up on this server.")
    if not credential or credential.count(".") != 2 or len(credential) > 4096:
        raise GoogleTokenError("That Google sign-in isn't valid.")
    claims = await asyncio.to_thread(_decode, credential, audience)   # key fetch is blocking I/O
    email = str(claims.get("email") or "").strip().lower()
    if not email or claims.get("email_verified") not in (True, "true"):
        raise GoogleTokenError("Please use a Google account with a verified e-mail address.")
    return GoogleIdentity(sub=str(claims["sub"]), email=email, name=str(claims.get("name") or "").strip()[:120],
                          hosted_domain=str(claims.get("hd") or "").strip().lower())
