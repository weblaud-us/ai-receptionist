from core.prompts.verticals import ALWAYS, SCHEDULING_TOOLS, VerticalSpec, _labels

SPEC = VerticalSpec(
    key="professional",
    label="Law, accounting or consulting office",
    description="Intake, consultation booking, strict no-advice rule, messages for the right person.",
    rules="""# Professional office rules
- You handle intake and scheduling only. Never give legal, tax or financial advice, never
  comment on the merits of a matter, and never confirm whether someone is a client.
- For a new enquiry collect: name, callback number, the general nature of the matter in one
  sentence, and how soon they need help. Then book a consultation or take a message.
- Existing clients asking about their matter → take a message for the person handling it.
- Be discreet: don't read details back that the caller didn't give you.""",
    tools=SCHEDULING_TOOLS + ALWAYS,
    labels=_labels(appointment="Consultation", contact="Client", resource="Professional", service="Consultation type"),
    default_services=[
        {"name": "Initial consultation", "duration_min": 30, "buffer_min": 15},
        {"name": "Meeting", "duration_min": 60, "buffer_min": 15},
    ],
    default_faqs=[
        {"question": "Is the first consultation free?", "answer": "The initial 30-minute consultation is free."},
        {"question": "What areas do you practise in?", "answer": "See the services list."},
    ],
    keyterms=("consultation", "retainer", "intake"),
)
