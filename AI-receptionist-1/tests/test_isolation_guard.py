"""
CI guard for Roadmap Task 2.3.1: Phase 2 code reaches tenant data only through repositories.

Direct `db.<collection>` / `get_db().<collection>` access is allowed only for platform-level
collections (tenants, users, sessions, invites, password resets, the phone-number routing table).
Handles from `scoped_db()` are named `sdb` so this check can tell them apart. Legacy hotel modules
are scoped differently (`scoped_db`, `HotelToolExecutor._scope`) and are covered by the isolation
tests instead.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PHASE2_FILES = [
    *sorted((ROOT / "core" / "services").glob("*.py")),
    *[p for p in sorted((ROOT / "core" / "tools").glob("*.py")) if p.name not in ("hotel.py", "__init__.py")],
    ROOT / "core" / "notifications.py",
    ROOT / "routers" / "tenant.py",
    ROOT / "routers" / "platform.py",
    ROOT / "agent" / "tools.py",
    ROOT / "agent" / "profile.py",
]
PLATFORM = {"tenants", "users", "sessions", "invites", "password_resets", "phone_numbers", "calls"}
DIRECT = re.compile(r"\b(?:get_db\(\)|\bdb)\.(\w+)\.(?:find|find_one|insert_one|insert_many|update_one|update_many|"
                    r"delete_one|delete_many|count_documents|aggregate|distinct)\b")


def test_phase2_modules_use_repositories():
    offenders = []
    for path in PHASE2_FILES:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in DIRECT.finditer(line):
                if m.group(1) not in PLATFORM:
                    offenders.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not offenders, "Use core.repositories (tenant-scoped) instead:\n" + "\n".join(offenders)


def test_platform_calls_aggregate_is_the_only_cross_tenant_read():
    # /platform/tenants aggregates calls across tenants on purpose (it groups by tenant_id).
    text = (ROOT / "routers" / "platform.py").read_text(encoding="utf-8")
    assert text.count("db.calls.") == 1 and '"_id": "$tenant_id"' in text
