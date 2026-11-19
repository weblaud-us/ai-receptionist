# Forward your calls to your assistant

Your assistant's number is under **Phone numbers** in the dashboard. You can forward:

- **only calls you miss** (busy, no answer, phone off): the most popular choice;
- **all calls**: the assistant answers everything and transfers to you when needed;
- **after hours only**: switch forwarding on when you close, or use your phone system's schedule.

Then set **Settings → Which calls reach your assistant?** to match, so your Results count missed
calls correctly.

> Codes differ by carrier and plan. If a code doesn't work, search "conditional call forwarding"
> with your carrier's name, or call them. Replace `NUMBER` with your assistant's number, including
> the country code (for example `+15125550123`).

## Mobile phones (most carriers worldwide: GSM codes)

| What | Dial | Cancel |
|---|---|---|
| Forward when busy, unanswered or unreachable (recommended) | `**004*NUMBER#` | `##004#` |
| Only when busy | `**67*NUMBER#` | `##67#` |
| Only when not answered (ring 20 s first) | `**61*NUMBER*20#` | `##61#` |
| Only when unreachable / switched off | `**62*NUMBER#` | `##62#` |
| Everything | `**21*NUMBER#` | `##21#` |

These work on AT&T and T-Mobile in the US, and on most UK (EE, O2, Vodafone, Three), EU and Gulf carriers.

## Verizon (US)

| What | Dial |
|---|---|
| Forward when busy or not answered | `*71` then the 10-digit number |
| Forward everything | `*72` then the number |
| Cancel | `*73` |

## iPhone and Android settings

- **iPhone:** Settings → Phone → Call Forwarding forwards **all** calls only. To forward only missed
  calls, use the codes above.
- **Android:** Phone app → ⋮ → Settings → Calls (or Supplementary services) → Call forwarding →
  pick "When busy", "When unanswered", "When unreachable".

## Landlines and office phone systems

- **Business landline:** ask your provider for "call forward busy / no answer" to the number. Many
  providers offer it in their online portal.
- **VoIP / cloud phone systems** (RingCentral, 8x8, Vonage, Zoom Phone, Teams Phone, 3CX…): add the
  assistant's number as an external number at the end of your ring group, or as the after-hours
  destination.

## Test it

From a different phone, call your business number and don't answer. After the rings you should
hear your assistant. If you hear your old voicemail, forwarding isn't on
([troubleshooting](troubleshooting.md)).

## Transfers back to you

When a caller asks for a person, the assistant transfers the call to the number under
**Settings → Transfers**, during the hours you set. If that number forwards back to the assistant,
the call loops, so use a mobile or a direct line that doesn't forward.
