# Telephony: Twilio Elastic SIP Trunking → LiveKit

> **Start with [docs/TELEPHONY_TESTING.md](../../docs/TELEPHONY_TESTING.md).** `APP_ENV` picks the
> provider (development → free Linphone softphone, staging/production → Twilio), and
> `uv run python -m scripts.setup_telephony` creates the LiveKit trunks and dispatch rule for you.
> The JSON templates and `lk` commands below are the manual equivalent, kept as a reference and
> fallback.

Phone calls reach the voice agent like this:

```
caller ─PSTN→ Twilio number ─Elastic SIP trunk→ LiveKit inbound trunk
       → dispatch rule (room "call-…", agent LIVEKIT_AGENT_NAME) → agent/worker.py
```

Use **Elastic SIP Trunking**, not a TwiML `<Dial><Sip>` bin: the TwiML method doesn't support
transfers (SIP REFER) or outbound calls, and the agent needs both (Roadmap Task 1.2).

The JSON files here are committed **templates**: `${…}` placeholders, never real passwords.
Render them from your secret store with `envsubst`, then create the objects with the LiveKit CLI.

## 1. Twilio side (Console)

1. **Elastic SIP Trunking → Trunks → Create**: `livekit-dev` (later `livekit-prod`).
2. **Origination** (calls coming in): add an Origination URI pointing at your LiveKit project's
   SIP host (LiveKit Cloud → Settings → *SIP URI*), e.g. `sip:<project-sip-host>;transport=tcp`.
3. **Termination** (calls going out — transfers and Phase 4): pick a termination URI
   (`<name>.pstn.twilio.com`) and attach a **Credential List** (username + strong password).
   These become `SIP_OUTBOUND_USERNAME` / `SIP_OUTBOUND_PASSWORD`.
4. **Numbers**: move the number onto the trunk. A number on a trunk stops using its Voice
   webhook, so the legacy `/voice/incoming` path no longer receives its calls — expected.
   During cutover move **one** number first (Roadmap Task 1.13).
5. **Enable transfers** (needed by `transfer_to_human`):

   ```bash
   twilio api trunking v1 trunks update --sid TKxxxxxxxx \
     --transfer-mode enable-all --transfer-caller-id from-transferee
   ```

6. **Trust Hub — start now, approval takes days**: Business Profile, then SHAKEN/STIR
   A-attestation and CNAM for the numbers. Outbound calls without A-attestation show as
   "Spam Likely". Phase 4 depends on it.

## 2. LiveKit side (CLI)

```bash
lk cloud auth                     # once, links the CLI to your project

export ENVIRONMENT=dev PHONE_NUMBER=+15551234567 LIVEKIT_AGENT_NAME=receptionist
export SIP_INBOUND_USERNAME=... SIP_INBOUND_PASSWORD=...          # from the secret store
export TWILIO_TERMINATION_URI=yourname.pstn.twilio.com
export SIP_OUTBOUND_USERNAME=... SIP_OUTBOUND_PASSWORD=...

envsubst < inbound-trunk.json  > /tmp/inbound.json  && lk sip inbound create  /tmp/inbound.json
envsubst < dispatch-rule.json  > /tmp/dispatch.json && lk sip dispatch create /tmp/dispatch.json
envsubst < outbound-trunk.json > /tmp/outbound.json && lk sip outbound create /tmp/outbound.json
rm /tmp/inbound.json /tmp/dispatch.json /tmp/outbound.json
```

Save the outbound trunk ID (`ST_…`) as `LIVEKIT_SIP_OUTBOUND_TRUNK_ID`.

> ⚠ If Twilio's origination doesn't send credentials, LiveKit authenticates the inbound trunk by
> number instead: remove `auth_username` / `auth_password` from `inbound-trunk.json` (and check
> LiveKit's Twilio guide for an allowed-addresses list). Verify against the current
> [LiveKit Twilio docs](https://docs.livekit.io/telephony/accepting-calls/inbound-twilio/).

Adding a number later: add it to the Twilio trunk, then `lk sip inbound update` with the new
`numbers` list. One inbound trunk and one dispatch rule serve every number — in Phase 2 the
agent looks the business up from the dialled number.

## 3. First test call

1. Run the worker: `docker compose -f docker-compose.dev.yml up agent` (Windows) or
   `uv run python -m agent.worker dev`.
2. Call the number. The worker log shows `SIP participant attributes: [...]` — confirm
   `sip.phoneNumber` (caller) and `sip.trunkPhoneNumber` (dialled number) are present
   (Roadmap Task 1.2.c).
3. After a few calls: `uv run python -m scripts.latency_report`.

Secure trunking (TLS + SRTP) comes after the basic flow works — set the matching media
encryption on both trunks. ⚠ Verify current LiveKit/Twilio support first.
