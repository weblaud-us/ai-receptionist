# Legal documents — drafts

> **DRAFTS FOR COUNSEL REVIEW. NOT LEGAL ADVICE. DO NOT PUBLISH AS-IS.**
> They are written to match what the product actually does, so a lawyer starts from facts rather
> than a generic template. Text in `[square brackets]` must be filled in or decided.

| File | Publish at (env var) | Accepted where |
|---|---|---|
| [TERMS.md](TERMS.md) | `TERMS_URL` | Signup checkbox (version recorded per user) |
| [PRIVACY.md](PRIVACY.md) | `PRIVACY_URL` | Signup checkbox |
| [AUP.md](AUP.md) | `AUP_URL` | Signup checkbox |
| [DPA.md](DPA.md) | `DPA_URL` | Dashboard → Legal & privacy (per business, with signer name) |
| [SUBPROCESSORS.md](SUBPROCESSORS.md) | `SUBPROCESSORS_URL` | Referenced by the DPA; live list at `GET /public/legal` |
| [SLA.md](SLA.md) | `SLA_URL` | Pro and Agency plans |
| [SECURITY.md](SECURITY.md) | `SECURITY_URL` | Shared on request during due diligence |

## Changing a published document

1. Edit the text, have counsel approve it, publish it at the same URL.
2. Bump its `version` (the publication date) in `AI-receptionist-1/core/compliance/legal.py`.
3. Material changes to the Terms or DPA: e-mail every owner at least 30 days before they take
   effect (sub-processor additions: 30 days, DPA §7). The dashboard asks owners to accept the new version.
4. Keep every past version (date-stamped PDF) — acceptance records point at version dates.

Checklist of what each market requires and what still needs a lawyer: [../COMPLIANCE.md](../COMPLIANCE.md).
