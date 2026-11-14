from core.prompts.verticals import ALWAYS, SCHEDULING_TOOLS, VerticalSpec, _labels

SPEC = VerticalSpec(
    key="generic",
    label="Other local business",
    description="Appointments, questions and messages for any small business.",
    rules="""# This business
- Answer questions from the business facts. For anything not there, offer to take a message.
- To book: ask what for, then the day and time they'd like, then offer the nearest open times.""",
    tools=SCHEDULING_TOOLS + ALWAYS,
    labels=_labels(),
    default_services=[{"name": "Appointment", "duration_min": 30, "buffer_min": 0}],
    default_faqs=[
        {"question": "Where are you located?", "answer": "See the address on file."},
        {"question": "How do I pay?", "answer": "We take cards and cash."},
    ],
)
