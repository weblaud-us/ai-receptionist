"""
On-call escalations (plan Phase 2.3).

    GET  /public/escalations/{tenant}/{id}?sig=…   page with an "I'm handling it" button (from the alert)
    POST /public/escalations/{tenant}/{id}?sig=…   acknowledge
    GET  /t/escalations                            recent escalations with who was alerted when (staff+)
    POST /t/escalations/{id}/ack                   acknowledge from the dashboard (staff+)
    GET  /public/appointments/{tenant}/{id}.ics?sig=…   a patient's "add to calendar" link (plan 4.5)

The public link is signed (core/signed_links). GET never changes anything, so e-mail link scanners
that open every link can't acknowledge an emergency on someone's behalf.
"""

from __future__ import annotations

import html
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from core.auth import TenantPrincipal, require_tenant
from core.services import escalation
from core.tenancy import resolve_by_id
from utils.serialize import to_jsonable

escalations_router = APIRouter(prefix="/t", tags=["Escalations"])
public_escalations_router = APIRouter(prefix="/public", tags=["Public"])

_staff = require_tenant("staff")


def _page(title: str, body: str, *, button: bool = False) -> HTMLResponse:
    form = ('<form method="post"><button style="font-size:18px;padding:12px 20px;border-radius:8px;'
            'border:0;background:#0f766e;color:#fff">I\'m handling it</button></form>') if button else ""
    return HTMLResponse(
        f"<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta name='robots' content='noindex'><title>{html.escape(title)}</title></head>"
        f"<body style='font-family:system-ui;max-width:32rem;margin:3rem auto;padding:0 1rem'>"
        f"<h1 style='font-size:1.4rem'>{html.escape(title)}</h1><p>{html.escape(body)}</p>{form}</body></html>",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


async def _load(tenant_id: str, escalation_id: str, sig: str) -> tuple:
    if not escalation.verify_ack(tenant_id, escalation_id, sig):
        raise HTTPException(404, "Not found")
    tenant = await resolve_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(404, "Not found")
    from core.repositories import Repos
    from database.mongodb import get_db

    esc = await Repos(get_db(), tenant.id).escalations.find_one({"_id": escalation_id})
    if not esc:
        raise HTTPException(404, "Not found")
    return tenant, esc


@public_escalations_router.get("/escalations/{tenant_id}/{escalation_id}", response_class=HTMLResponse)
async def ack_page(tenant_id: str, escalation_id: str, sig: str = Query("", max_length=64)):
    tenant, esc = await _load(tenant_id, escalation_id, sig)
    if esc.get("status") == escalation.ACKED:
        return _page("Already being handled", f"{esc.get('acked_by') or 'Someone'} is handling this call for {tenant.name}.")
    return _page(f"Urgent call for {tenant.name}", esc.get("headline") or "Urgent dental call", button=True)


@public_escalations_router.post("/escalations/{tenant_id}/{escalation_id}", response_class=HTMLResponse)
async def ack_from_link(tenant_id: str, escalation_id: str, sig: str = Query("", max_length=64)):
    tenant, esc = await _load(tenant_id, escalation_id, sig)
    await escalation.acknowledge(tenant, escalation_id, by="on-call (alert link)")
    return _page("Thanks, it's yours", f"Nobody else will be alerted about this call for {tenant.name}. "
                                       "The patient's details are in the alert and on the dashboard.")


@public_escalations_router.get("/appointments/{tenant_id}/{appointment_id}.ics")
async def appointment_calendar(tenant_id: str, appointment_id: str, sig: str = Query("", max_length=64)):
    """Plan 4.5: the patient's "add to calendar" link from the booking confirmation."""
    from fastapi.responses import Response

    from core.repositories import Repos
    from core.services import appointment_ics
    from database.mongodb import get_db

    if not appointment_ics.verify(tenant_id, appointment_id, sig):
        raise HTTPException(404, "Not found")
    tenant = await resolve_by_id(tenant_id)
    appt = await Repos(get_db(), tenant_id).appointments.find_one({"appointment_id": appointment_id}) if tenant else None
    if tenant is None or appt is None:
        raise HTTPException(404, "Not found")
    if appt.get("status") not in ("pending", "confirmed"):
        raise HTTPException(410, "This appointment was cancelled or moved")
    return Response(appointment_ics.ics_text(tenant, appt), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{appointment_id}.ics"',
                             "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@escalations_router.get("/escalations")
async def list_escalations(tp: TenantPrincipal = Depends(_staff)):
    rows = await tp.repos().escalations.find({}, projection={"details_enc": 0}, sort=[("created_at", -1)], limit=50)
    return {"escalations": to_jsonable(rows)}


@escalations_router.post("/escalations/{escalation_id}/ack")
async def ack_from_dashboard(escalation_id: str, request: Request, tp: TenantPrincipal = Depends(_staff)):
    if not await escalation.acknowledge(tp.tenant, escalation_id, by=tp.user.name or tp.user.email or "staff"):
        raise HTTPException(409, "That escalation is already acknowledged or doesn't exist")
    await tp.repos().audit.record(user_id=tp.user.user_id, action="escalation.ack", target_type="escalation",
                                  target_id=escalation_id, diff={}, ip=request.client.host if request.client else "")
    out: Dict[str, Any] = {"success": True}
    return out
