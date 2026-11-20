# Eval cases from real calls

One JSON file per case, replayed against the real LLM by `tests/evals/test_case_evals.py`
(`RUN_EVALS=1`). Files are written by `python -m scripts.export_eval_cases` from cases approved in
the platform app (Transcript review → Make eval case → Approve), or by hand.

```json
{
  "id": "evc_…",                      // file name without .json
  "title": "What went wrong, in a few words",
  "vertical": "salon",
  "tenant": {                          // the business as it was configured
    "name": "…", "vertical": "salon", "timezone": "America/Chicago", "country": "US",
    "currency": "USD", "description": "", "hours": [{"dow": 0, "open": "09:00", "close": "17:00"}],
    "agent": {"name": "Aria", "tone": "warm"},
    "services": [{"name": "Haircut", "duration_min": 45, "price": {"amount": 4500, "currency": "USD", "display": "from $45"}}],
    "knowledge": [{"question": "…", "answer": "…"}],
    "dental": {"accepts_nhs": false},  // optional: dental, links, answering_mode, policies,
    "links": {"booking": "https://…"}  //   emergency_instructions are copied onto the business
  },
  "caller_turns": ["what the caller said, turn 1", "turn 2"],
  "expect": {
    "intent": "What a good assistant does in its final reply (the judge's rubric)",
    "must_call": ["take_message"],     // tool names: core/tools/registry.py
    "must_not_call": ["book_appointment"]
  }
}
```

Rules:
- **No personal data.** Cases are redacted when created (numbers, e-mails, the caller's name), but
  read every file before committing it.
- One behaviour per case. A failing case should tell you exactly what broke.
- The `seed_*` cases are hand-written examples of the failures the pilot review looks for
  (`seed_dental_*`: the dental vertical's price, NHS, emergency and after-hours guardrails).
