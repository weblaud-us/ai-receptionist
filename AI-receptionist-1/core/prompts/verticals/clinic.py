from core.prompts.verticals import ALWAYS, SCHEDULING_TOOLS, VerticalSpec, _labels

SPEC = VerticalSpec(
    key="clinic",
    label="Clinic or therapy practice",
    description="Appointments, new-patient intake, no medical advice. ⚠ US healthcare needs signed BAAs first.",
    rules="""# Clinic rules
- You schedule and answer practical questions only. Never give medical, dental or clinical advice,
  never discuss test results or diagnoses. For anything clinical, offer a message to the practice.
- If someone describes an emergency (chest pain, trouble breathing, heavy bleeding, a severe
  allergic reaction), tell them to hang up and call the local emergency number now.
- Ask whether they're a new or existing patient, then the appointment type, then preferred day
  and time. Collect full name and date of birth only when the facts say to.
- Keep every reply short and calm; do not repeat personal details back more than needed.""",
    tools=SCHEDULING_TOOLS + ALWAYS,
    labels=_labels(appointment="Appointment", contact="Patient", resource="Provider", service="Appointment type"),
    default_services=[
        {"name": "New patient visit", "duration_min": 45, "buffer_min": 10},
        {"name": "Follow-up", "duration_min": 20, "buffer_min": 5},
        {"name": "Cleaning", "duration_min": 45, "buffer_min": 10},
    ],
    default_faqs=[
        {"question": "Which insurance do you accept?", "answer": "Please ask the front desk; we'll have someone call you back."},
        {"question": "What should I bring to my first visit?", "answer": "Photo ID, insurance card and a list of current medications."},
    ],
    keyterms=("appointment", "cleaning", "follow-up", "new patient", "insurance"),
)
