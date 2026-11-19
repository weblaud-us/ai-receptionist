# Phase 6 — legal and compliance: what's built and how to test it

What Phase 6 added (ROADMAP §11), how to try it locally, and how to check each item. The market
checklist and everything that still needs a lawyer: [COMPLIANCE.md](COMPLIANCE.md). Draft documents:
[legal/](legal/README.md). Production steps:
[PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md#phase-6--legal-and-compliance).

## What's in the code

| ROADMAP item | Where |
|---|---|
| §11.2 Consent revocation, any channel | `core/compliance/optout.py`. **SMS:** `POST /telephony/sms/inbound` (Twilio-signed) acts on STOP / STOPALL / UNSUBSCRIBE / CANCEL / END / QUIT / REVOKE / OPTOUT and phrases like "stop texting me"; START / UNSTOP re-subscribe; HELP answers. **WhatsApp:** the same words in a chat (`whatsapp/meta_handler.py`), except "cancel", "end", "quit" and "remove", which mean something else in a conversation. **Voice:** the Phase 4 sales opt-out listener. Every event lands in `consent_events`. A text to a business's own number opts the person out of that business. A text to the shared sender (`TWILIO_SMS_FROM`) opts them out of every business **and** puts them on the permanent sales `dnc_list` |
| Opted-out contacts get no texts | `core/notifications.notify_contact` checks `do_not_contact` before every confirmation and reminder. If the check itself fails, nothing is sent |
| §11.2 Data subject requests | `core/compliance/subjects.py`, `routers/compliance.py`. Owners: **Legal & privacy** → find a caller by number → counts → **Download their data** (JSON) or **Delete their data**. Erasure deletes contacts, messages, job requests, WhatsApp chats and guest records. On calls it removes the transcript, summary and recording and hashes the number; the call row stays for billing. On appointments and bookings it wipes the personal fields. Upcoming appointments block erasure until the owner confirms. The number can stay on a suppression list (default on). Each request is logged in `dsr_requests` with received date, due date (`DSR_DEADLINE_DAYS`) and a masked number; it shows in the business's log and on the platform **Compliance** page |
| §11.2 Retention | Transcripts: existing purge (`agent.transcript_retention_days`). **New:** recordings older than `agent.recording.retention_days` are deleted from storage per business by the tenant jobs (`core/compliance/retention.py`); the bucket lifecycle rule stays as the global backstop |
| §11.2 Emergency calls | The business facts in every prompt now carry the local emergency number (911 US/CA, 999 UK/UAE, 112 EU and fallback…) and "You are not an emergency service" (`core/prompts/builder.emergency_number`) |
| §11.2 Don't trap callers | Base prompt: "agent", "operator", "representative" or pressing 0 → `transfer_to_human`, or a message. **Pressing 0** on the keypad is caught in code (`agent/receptionist.DtmfWatcher`), so it works even when the model missed it |
| §11.8 Documents and acceptance | `core/compliance/legal.py`: one version per document; signup records which versions of terms, privacy and AUP the owner accepted (with IP and user agent) on the user; owners accept the **DPA** per business with name and title (`POST /t/legal/dpa/accept`); a newer version shows as outstanding (`GET /t/legal`). Public: `GET /public/legal` (documents + sub-processors) |
| §11.8 Sub-processor list | `legal.SUBPROCESSORS` → `GET /public/legal`, owners' **Legal & privacy** page, [legal/SUBPROCESSORS.md](legal/SUBPROCESSORS.md) |
| Production guard | `SIGNUP_ENABLED=true` in production also needs `TERMS_URL`, `PRIVACY_URL` and `DPA_URL` |
| Offboarding | The new per-business collections (`consent_events`, `owner_feedback`, `call_reviews`, `eval_cases`) are deleted with the business. `dsr_requests` is kept on purpose: it holds no personal data and is the proof requests were answered |

Already in place from earlier phases (now listed in COMPLIANCE.md): AI disclosure in the greeting,
recording notice, the outbound compliance gate, export and deletion on cancellation, audit log.

**Not built, deliberately:**
- **Cookie banner.** It belongs on the marketing site, not in this repo.
- **Automatic lead expiry** for your own sales leads. Decide the retention period first (COMPLIANCE.md §9).
- **Request intake form for callers.** Callers contact the business. The business uses the tool.
  Requests that reach you go to `PRIVACY_CONTACT_EMAIL`.

## Try it locally

```bash
cd AI-receptionist-1 && uv run uvicorn main:app --port 8002     # and npm run dev at the repo root
```

1. **Legal & privacy** (owner): the DPA shows **Action needed**. Type a name, tick, **Accept** → Accepted.
   Bump `version` for `dpa` in `core/compliance/legal.py`, restart → Action needed again.
2. Make a test call (Assistant → Test call) or insert a contact and a call for `+15125550111`.
   Find that number → counts → **Download their data** → **Delete their data** (type the number again).
   The log below shows both requests with a due date. The call is still in **Calls**, without
   transcript or number.
3. **SMS STOP** without Twilio (signature checks are on, so sign the request):

   ```bash
   uv run python - <<'EOF'
   from twilio.request_validator import RequestValidator
   import httpx, os
   url = os.environ.get("WEBHOOK_BASE_URL", "http://localhost:8002") + "/telephony/sms/inbound"
   form = {"From": "+15125550111", "To": "+15125559999", "Body": "STOP"}
   sig = RequestValidator(os.environ["TWILIO_AUTH_TOKEN"]).compute_signature(url, form)
   print(httpx.post("http://localhost:8002/telephony/sms/inbound", data=form, headers={"X-Twilio-Signature": sig}).text)
   EOF
   ```

   The reply is TwiML "You're unsubscribed…". The contact shows **Do not contact**. The platform
   **Compliance** page lists the opt-out.
4. **Press 0** on a softphone call (docs/TELEPHONY_TESTING.md): the assistant offers to connect you or take a message.

## Acceptance

| Check | How |
|---|---|
| STOP by SMS is honoured within seconds, scoped correctly, and START doesn't undo the DNC list | `tests/test_phase6_compliance.py` (runs in CI); step 3 above on staging with a real Twilio number |
| A data request can be answered in minutes, inside the legal deadline, with a record | Steps 1–2; the request log; `test_find_export_and_erase_one_caller`, `test_privacy_api_is_owner_only_logged_and_masked` |
| Nothing personal is left after erasure except the suppression entry | `test_find_export_and_erase_one_caller` |
| Recordings don't outlive the business's setting | `test_recordings_are_deleted_after_the_business_retention`; on staging, set 1 day and check the bucket the next day |
| Every caller hears the AI disclosure; recording is announced | Phase 2 tests on `greeting_text`; listen to 5 calls on staging |
| Callers can always reach a person or leave a message | Say "operator" and press 0 on a staging call, during and outside transfer hours |
| Lawyer review done for each launch market | COMPLIANCE.md: every 🧑‍⚖️ row signed off, with date and name |
| Documents published | `TERMS_URL`, `PRIVACY_URL`, `DPA_URL`, `AUP_URL`, `SUBPROCESSORS_URL` set; production boots with `SIGNUP_ENABLED=true` |
