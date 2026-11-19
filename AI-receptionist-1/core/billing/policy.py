"""
Plan rules applied when an owner changes settings (Roadmap Task 5.4 "Abuse prevention").

- Transfers never go to premium-rate numbers (TRANSFER_BLOCKED_PREFIXES).
- International transfers only on plans that allow them (toll fraud: a stolen card + a transfer to an
  expensive destination is the classic attack on voice products).
- During a self-serve trial, transfers only to the owner's number verified by SMS code.
- Call recording only on plans that include it.

Platform admins bypass these (they edit through /platform). Manual-plan businesses keep the checks
on premium-rate numbers only.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from config import settings
from core.billing.plans import get_plan
from core.phone import digits
from core.tenancy.models import TenantConfig


class PolicyError(ValueError):
    pass


def _country(number: str) -> Optional[str]:
    try:
        import phonenumbers

        return phonenumbers.region_code_for_number(phonenumbers.parse(number, None))
    except Exception:
        return None


def check_transfer_number(tenant: TenantConfig, number: str) -> None:
    if not number:
        return
    e164 = "+" + digits(number)
    if len(e164) < 9:
        raise PolicyError("Transfer number must be in international format, e.g. +15125551234")
    if any(e164.startswith(p) for p in settings.transfer_blocked_prefixes):
        raise PolicyError("Transfers to premium-rate numbers aren't allowed")
    if not tenant.billing.self_serve:
        return
    plan = get_plan(tenant.billing.plan)
    country = _country(e164)
    if not (plan and plan.international_transfers) and country and tenant.country and country != tenant.country.upper():
        # US and Canada share +1 and are one calling zone.
        if not ({country, tenant.country.upper()} <= {"US", "CA"}):
            raise PolicyError("Transfers to another country need the Agency plan — contact us")
    if tenant.status == "trial":
        verified = tenant.billing.verified_phone
        if not verified or digits(verified) != digits(e164):
            raise PolicyError("During the trial, calls can only be transferred to your own number. "
                              "Verify it on the Billing page first.")


def check_changes(tenant: TenantConfig, changes: Dict[str, Any]) -> None:
    """Raise PolicyError when an owner's settings change breaks a plan rule."""
    transfer = changes.get("transfer")
    if isinstance(transfer, dict) and "number" in transfer and transfer["number"] != tenant.transfer.number:
        check_transfer_number(tenant, str(transfer.get("number") or "").strip())
    agent = changes.get("agent")
    if isinstance(agent, dict) and tenant.billing.self_serve:
        recording = agent.get("recording")
        if isinstance(recording, dict) and recording.get("enabled") and not tenant.agent.recording.enabled:
            plan = get_plan(tenant.billing.plan)
            if not (plan and plan.recording):
                raise PolicyError("Call recording is included in the Pro plan")
