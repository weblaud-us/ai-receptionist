# Legal documents — drafts

> **Superseded as the source of truth.** The published texts are now edited in the platform dashboard
> (**Legal documents**) and shown on the marketing site at `/legal/<key>` (terms, privacy, subscription,
> cookies, dpa, aup, subprocessors, sla, security). These drafts seeded them
> (`AI-receptionist-1/core/compliance/legal_seed/`). A document with `[square brackets]` left can't be
> published: fill them in there. Publishing a *material* change gives it a new version and asks owners
> to accept again; every published text is kept (`legal_document_versions`). See
> `AI-receptionist-1/core/compliance/legal_docs.py`.

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

1. Edit the text in Platform → Legal documents, have counsel approve it.
2. Publish it there; tick "material change" when customers must accept it again (the version becomes the date).
3. Material changes to the Terms or DPA: e-mail every owner at least 30 days before they take
   effect (sub-processor additions: 30 days, DPA §7). The dashboard asks owners to accept the new version.
4. Keep every past version (date-stamped PDF) — acceptance records point at version dates.

Checklist of what each market requires and what still needs a lawyer: [../COMPLIANCE.md](../COMPLIANCE.md).
