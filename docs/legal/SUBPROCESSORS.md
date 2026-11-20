# Sub-processors

> **DRAFT for counsel review.** Version `2026-11-20`. The list the product serves at
> `GET /public/legal` (and shows owners in Legal & privacy) comes from
> `AI-receptionist-1/core/compliance/legal.py → SUBPROCESSORS`. Change both together, and give
> customers **30 days' notice** of any addition (DPA §4.2).

**Last updated:** 2026-11-20

Companies that process personal data on our behalf to provide the AI receptionist. Fill in the
hosting providers you actually use, the regions, and the transfer mechanism you rely on for each.

| Company | What for | Data | Location | When used | Transfer basis (EU/UK) |
|---|---|---|---|---|---|
| Twilio Inc. | Phone numbers, call routing (SIP), SMS | Phone numbers, call audio in transit, SMS content | United States | Always | [DPF / SCCs] |
| LiveKit, Inc. | Real-time call media and SIP bridging | Call audio in transit, call metadata | US / EU (by region) | Always | [DPF / SCCs] |
| Deepgram, Inc. | Speech-to-text (opted out of model improvement on every request) | Caller audio | United States | Always | [SCCs] |
| OpenAI, L.L.C. | Language model; call summaries (`store` off; Zero Data Retention only where approved) | Transcripts, business information | United States | Always | [DPF / SCCs] |
| Anthropic, PBC | Fallback language model | Transcripts, business information | United States | If the primary model fails | [SCCs] |
| ElevenLabs Inc. | Text-to-speech | Assistant replies (text) | United States | When the business uses an ElevenLabs voice | [SCCs] |
| Cartesia AI, Inc. | Text-to-speech | Assistant replies (text) | United States | Cartesia voice or as a fallback | [SCCs] |
| MongoDB, Inc. (Atlas) | Database hosting | All account and call data | US / EU (by region) | Always | [DPF / SCCs] |
| [Redis host] | Short-lived state (rate limits, call slots, job queue) | Identifiers, no transcripts | Same region as the database | Always | [ ] |
| [Cloud host] | Application servers | All data in processing | Same region as the database | Always | [ ] |
| [S3-compatible storage] | Call recordings | Call audio | Same region as the database | Only if recording is on | [ ] |
| Stripe, Inc. | Subscriptions and payments | Business billing details | United States | Self-serve plans | [DPF / SCCs] |
| Functional Software, Inc. (Sentry) | Error tracking | Technical identifiers; no transcripts | United States | Always | [DPF / SCCs] |
| Resend (Plus Five Five, Inc.) | Transactional e-mail | Owner e-mail, notification content | United States | Always | [SCCs] |
| Google LLC | Calendar sync | Appointment times and titles | United States | Only if the business connects Google Calendar | [DPF] |
| Meta Platforms, Inc. (WhatsApp) | WhatsApp messaging | Phone numbers, messages | United States | Only if the business connects WhatsApp | [DPF] |
| Red Guava Pty Ltd (Cliniko) | The dental practice's own diary: availability, patients, bookings | Patient name and phone number, appointment times and notes | Australia / UK / other (the practice's Cliniko region) | Only if the practice connects Cliniko | [UK: IDTA if outside the UK] |
| Dentally (Henry Schein One) | The dental practice's own diary: availability, patients, bookings | Patient name and phone number, appointment times and notes | United Kingdom | Only if the practice connects Dentally | [ ] |

Cliniko and Dentally are the practice's own systems, chosen and contracted by the practice. We send
data to them only on the practice's instruction, when it connects them.

**Hosting regions.** Australian practices can be hosted in AWS Asia Pacific (Sydney), `ap-southeast-2`,
and UK practices in AWS Europe (London), `eu-west-2`. Database, recordings and application servers
then stay in that region; speech and language models are processed where each provider runs them
(above). [Confirm availability per region before publishing.]

To be told about changes, [subscribe here / we e-mail every account owner].
