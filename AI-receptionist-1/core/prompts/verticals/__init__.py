"""
Vertical templates (Roadmap Task 2.5). Each vertical defines: extra prompt rules, the tool set
its agent gets, onboarding defaults (example services and FAQs) and dashboard labels.

The engine is generic; these make sales, onboarding and demos specific.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.tenancy.models import TenantConfig

SCHEDULING_TOOLS: Tuple[str, ...] = (
    "check_availability", "book_appointment", "find_my_appointments",
    "reschedule_appointment", "cancel_appointment",
)
ALWAYS: Tuple[str, ...] = ("take_message", "answer_from_knowledge")


@dataclass(frozen=True)
class VerticalSpec:
    key: str
    label: str
    description: str
    rules: str                                   # appended to the base voice rules
    tools: Tuple[str, ...]
    labels: Dict[str, str] = field(default_factory=dict)      # dashboard wording
    default_services: List[Dict[str, Any]] = field(default_factory=list)
    default_faqs: List[Dict[str, str]] = field(default_factory=list)
    keyterms: Tuple[str, ...] = ()               # words STT should expect
    facts: Optional[Callable[[TenantConfig], str]] = None      # extra business-facts lines
    uses_scheduling: bool = True


def _labels(appointment="Appointment", contact="Customer", resource="Staff member", service="Service") -> Dict[str, str]:
    return {"appointment": appointment, "appointments": appointment + "s", "contact": contact,
            "contacts": contact + "s", "resource": resource, "resources": resource + "s",
            "service": service, "services": service + "s"}


from core.prompts.verticals import (  # noqa: E402
    clinic, dental, generic, home_services, hotel, professional, restaurant, salon,
)

VERTICALS: Dict[str, VerticalSpec] = {
    v.SPEC.key: v.SPEC for v in (generic, salon, home_services, restaurant, hotel, clinic, dental, professional)
}


def get_vertical(key: Optional[str]) -> VerticalSpec:
    return VERTICALS.get(key or "", VERTICALS["generic"])


def vertical_catalog() -> List[Dict[str, Any]]:
    """For the onboarding wizard: what a business can pick and what it gets."""
    return [{"key": v.key, "label": v.label, "description": v.description, "labels": v.labels,
             "default_services": v.default_services, "default_faqs": v.default_faqs}
            for v in VERTICALS.values()]
