"""
Outbound AI sales dialer (Roadmap Phase 4) — everything that does not need the LiveKit runtime.

    models.py     statuses, campaign settings, collection names
    phones.py     E.164, country / state / timezone of a number
    rules.py      legal calling windows per country and US state        ⚖ counsel must confirm
    registry.py   internal do-not-call list, national / state lists, consent records
    lookup.py     line type (landline / mobile / VoIP) via Twilio Lookup
    gate.py       the compliance gate every lead passes before every dial
    importer.py   CSV → leads
    outcomes.py   what an attempt's outcome does to the lead (retry, exhaust, stop)
    dialer.py     the engine: claim leads, gate, dispatch the sales agent
    persona.py    the sales prompt and openers (A/B)
    sales_tools.py  what the sales agent can do (book a demo, e-mail, callback, opt-out)
    funnel.py     dialled → connected → human → conversation → interested → demo

The LiveKit side (dial, answering-machine detection, the conversation) is agent/sales.py.
Leads live in `sales_leads`: the tenant collection `leads` already holds the job requests a
business's receptionist captures, which is a different thing.
"""
