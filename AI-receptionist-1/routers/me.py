"""
The signed-in person's own preferences  —  prefix /me

GET    /me/tutorials          which tours and getting-started lists they've finished or skipped
PUT    /me/tutorials/{key}    {"status": "done" | "skipped"}
DELETE /me/tutorials/{key}    forget it, so the tour or list shows again

Not under /auth/: the dashboards only refresh an expired session for non-/auth/ paths.
The bootstrap .env admin and the X-Admin-Key have no stored account: they get an empty map with
``persist: false`` and writes do nothing, so the dashboards never show an error for them.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel

from core import ratelimit
from core.auth import Principal, current_user
from core.auth import tutorials

me_router = APIRouter(prefix="/me", tags=["Me"])

Key = Path(..., max_length=120, pattern=tutorials.KEY_PATTERN)


class TutorialBody(BaseModel):
    status: Literal["done", "skipped"]


def _out(saved: dict, persist: bool = True) -> dict:
    return {"tutorials": saved, "persist": persist}


async def _limit(user: Principal) -> None:
    if not await ratelimit.allow_fixed(f"tutorials:{user.user_id}", 60, 60):
        raise HTTPException(429, "Too many requests. Try again in a minute.")


@me_router.get("/tutorials")
async def get_tutorials(user: Principal = Depends(current_user)):
    if user.legacy:
        return _out({}, persist=False)
    return _out(await tutorials.get(user.user_id))


@me_router.put("/tutorials/{key}")
async def mark_tutorial(body: TutorialBody, key: str = Key, user: Principal = Depends(current_user)):
    if user.legacy:
        return _out({}, persist=False)
    await _limit(user)
    return _out(await tutorials.mark(user.user_id, key, body.status))


@me_router.delete("/tutorials/{key}")
async def reset_tutorial(key: str = Key, user: Principal = Depends(current_user)):
    if user.legacy:
        return _out({}, persist=False)
    await _limit(user)
    return _out(await tutorials.reset(user.user_id, key))
