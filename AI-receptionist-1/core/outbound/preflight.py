"""
The launch checklist (SALES_SUITE_PLAN.md Phase 10): everything that must be true before a campaign
starts, on one screen, with what to do about each.

A `fail` stops `start` (409 preflight_failed). A `warn` is shown but doesn't: the compliance gate
still checks every lead before every call (registries, hours, line type, consent), so a stale list
or an unwashed number blocks those calls, not the campaign. The checks are recorded with the legal
sign-off when the campaign starts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from config import settings
from core.outbound import dialer, number_health, phones, registry, rules, simulate
from core.outbound import models as m
from core.outbound import offers as of


def _check(id: str, label: str, status: str, detail: str = "", fix: str = "") -> Dict[str, str]:
    return {"id": id, "label": label, "status": status, "detail": detail, "fix": fix}


async def checks(db: Any, campaign: Mapping[str, Any], *, now: Optional[datetime] = None) -> List[Dict[str, str]]:
    now = now or datetime.now(timezone.utc)
    out: List[Dict[str, str]] = []
    problems = dialer.readiness() + dialer.campaign_problems(campaign)
    tenant = await dialer.tenant_problem(db)
    if tenant:
        problems.append(tenant)
    out.append(_check("dialer", "The dialer can place calls", "fail" if problems else "pass", " ".join(problems),
                      "status" if problems else ""))

    offer_id, version = campaign.get("offer_id"), campaign.get("offer_version")
    snap = await of.get_version(db, offer_id, version) if offer_id and version else None
    if offer_id:
        doc = await db[of.OFFERS].find_one({"_id": offer_id}, {"status": 1, "name": 1})
        if not snap:
            out.append(_check("offer", "The offer version is published", "fail", f"v{version} of this offer doesn't exist.", "offers"))
        elif (doc or {}).get("status") == "archived":
            out.append(_check("offer", "The offer is in use", "fail", "The offer is archived. Unarchive it or pick another.", "offers"))
        else:
            out.append(_check("offer", "The offer version is published", "pass", f"{(doc or {}).get('name')} v{version}"))
    else:
        out.append(_check("offer", "The offer", "warn", "Uses the built-in LaudLine scripts. Move it to a published offer to "
                                                         "get test calls and version history.", "campaigns"))

    if snap:
        variants = list(campaign.get("variant_ids") or [snap["settings"]["variants"][0]["id"]])
        passed = await simulate.latest_pass(db, snap["checksum"], variants)
        if passed:
            out.append(_check("simulation", "Test calls passed", "pass",
                              f"{passed['passed']}/{passed['total']} scenarios on {passed['finished_at']:%d %b}"
                              if isinstance(passed.get("finished_at"), datetime) else f"{passed['passed']}/{passed['total']} scenarios"))
        else:
            out.append(_check("simulation", "Test calls passed", "fail" if settings.SALES_REQUIRE_SIMULATION else "warn",
                              "Run the automated test calls for this version and every chosen script, and get every "
                              "scenario to pass.", "offers"))

    leads = await db[m.LEADS].find({"campaign_id": campaign["_id"], "status": {"$in": ["queued", "callback"]}},
                                   {"country": 1, "phone": 1}).to_list(50_000)
    countries = sorted({str(lead.get("country") or "US") for lead in leads})
    out.append(_check("leads", "There are leads to call", "pass" if leads else "warn",
                      f"{len(leads)} queued" + (f" ({', '.join(countries)})" if countries else ""), "" if leads else "leads"))
    allowed = rules.allowed_countries(str(campaign.get("mode") or ""))
    outside = [c for c in countries if c not in allowed]
    if outside:
        out.append(_check("countries", "Every lead is in a country this call type may call", "warn",
                          f"Leads in {', '.join(outside)} will be blocked by the compliance gate.", "leads"))

    overview = {r["list"]: r for r in await registry.registry_overview(db, now)}
    stale = []
    for c in countries:
        for lst in rules.required_registries(str(campaign.get("mode") or ""), c):
            if lst == "au_dncr":
                continue
            row = overview.get(lst)
            if not row or row.get("stale_area_codes") or not row.get("area_codes"):
                stale.append(lst)
    out.append(_check("registries", "Do-not-call lists are fresh", "warn" if stale else "pass",
                      f"Load a fresh copy of {', '.join(sorted(set(stale)))}: calls to numbers they cover are blocked until then."
                      if stale else "", "compliance" if stale else ""))
    if "AU" in countries:
        due = await registry.needs_wash(db, [lead["phone"] for lead in leads if lead.get("country") == "AU"], "au_dncr", now)
        out.append(_check("au_wash", "Australian numbers are washed against the DNCR", "warn" if due else "pass",
                          f"{len(due)} AU number{'s' if len(due) != 1 else ''} need a wash." if due else "", "compliance" if due else ""))

    numbers = list(campaign.get("from_numbers") or [])
    resting = await number_health.rested(db, numbers, now) if numbers else set()
    number_countries = {(phones.parse(n) or phones.PhoneInfo("", "", "", ())).country for n in numbers}
    missing = [c for c in countries if c not in number_countries]
    if missing or resting:
        detail = " ".join(filter(None, [f"No local caller ID for {', '.join(missing)}." if missing else "",
                                        f"{len(resting)} number{'s are' if len(resting) != 1 else ' is'} resting (flagged as spam)."
                                        if resting else ""]))
        out.append(_check("caller_ids", "Caller IDs match the leads and are healthy", "warn", detail, "campaigns"))
    else:
        out.append(_check("caller_ids", "Caller IDs match the leads and are healthy", "pass" if numbers else "warn",
                          "" if numbers else "No caller IDs yet.", "" if numbers else "campaigns"))
    if "US" in countries:
        out.append(_check("free_caller_registry", "US numbers registered with the free caller registry", "warn",
                          "A manual step: register each US caller ID at freecallerregistry.com so carriers don't label it spam."))

    email_ok = bool(settings.SALES_EMAIL_FOOTER and settings.WEBHOOK_BASE_URL)
    out.append(_check("email", "Sales email has a legal footer and working unsubscribe links", "pass" if email_ok else "warn",
                      "" if email_ok else "Set SALES_EMAIL_FOOTER (who you are and your postal address) and WEBHOOK_BASE_URL."))
    g = m.Guardrails.model_validate(campaign.get("guardrails") or {})
    out.append(_check("guardrails", "Auto-pause guardrails are on", "pass" if g.enabled else "warn",
                      f"Pauses above {g.max_optout_rate * 100:.0f}% opt-outs, below {g.min_answer_rate * 100:.0f}% answered, "
                      f"above {g.max_error_rate * 100:.0f}% errors." if g.enabled else "Turn them on in the campaign.", ""))
    return out


def failed(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [c for c in items if c["status"] == "fail"]
