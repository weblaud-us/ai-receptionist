"""
Legal and compliance in the product (Roadmap Phase 6, §11). Market rules and the documents themselves
are in docs/COMPLIANCE.md and docs/legal/; this package is the part code must enforce.

    legal.py      versions of the published documents, sub-processor list, who accepted what
    optout.py     consent revocation from any channel (SMS STOP, WhatsApp, the voice opt-out) → one list
    subjects.py   data subject requests: find / export / erase everything about one phone number
    retention.py  per-business recording retention (transcripts: services/tenant_jobs.purge_transcripts)

Already enforced elsewhere: AI disclosure in the greeting (TenantConfig.greeting_text), recording
notice (same), emergency rule and "ask for a person" in the base prompt (core/prompts/builder.py),
the outbound compliance gate (core/outbound/gate.py), offboarding deletion (core/services/offboarding.py).
"""
