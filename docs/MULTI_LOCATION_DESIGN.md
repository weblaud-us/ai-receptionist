# Multi-location dental groups: design (plan 4.7)

Status: **proposal, for approval before any code.** Audience: whoever builds it, and the owner deciding scope.

## The need

A group with 2–50 practices wants one account: the group manager sees and configures every clinic,
reports roll up across them, and billing is one invoice with pooled minutes, while each clinic keeps its own
phone number, hours, dentists, diary (Cliniko/Dentally account) and patients.

## What already exists

- **A user can belong to several businesses.** `users.memberships[]` holds `{tenant_id, role}` per clinic,
  and the dashboard has a clinic switcher (`switchTenant`, `X-Tenant-Id`).
- **Each clinic is already a complete, isolated tenant.** It has its own number, prompt, PMS connection, data and
  retention. Isolation is enforced by `Repos` and `scoped_db`, and audit SEC-05 closed the legacy gap.
- **The `agency` plan exists as a label** ("pooled minutes, custom terms"), billed by hand.

So a group can run today as N separate tenants plus a user invited to each. What's missing: an object that ties
them together, a role that spans them, combined reporting, and combined billing.

## Proposal

### 1. `organisations` collection (platform level, not tenant-scoped)

```
{_id: "org_…", name, created_at,
 billing: {stripe_customer_id, stripe_subscription_id, plan: "group", included_minutes_pool},
 defaults: {agent, notifications, escalation, confirmation_calls}}   # optional shared settings
```

`tenants.org_id` (nullable) links a clinic to its group. **A clinic stays a tenant.** Nothing about its data,
isolation, prompt caching or PMS changes.

### 2. Org roles in memberships

`users.memberships[]` gains entries of the form `{org_id, role: "org_admin" | "org_viewer"}`. `require_tenant`
resolves the clinic in `X-Tenant-Id` and allows the call when:
- the user has a membership for that tenant (as today), **or**
- the user has an org membership for `tenant.org_id`.

`org_admin` maps to `admin` and `org_viewer` maps to `staff` for that clinic. Every request still runs against
exactly one tenant, so the isolation guarantees don't change. `audit_log` records `org:<user>` as the actor.

### 3. Group views

| Page | What it shows |
|---|---|
| **Group overview** (new) | `insights.value_report` per clinic, plus totals: calls, bookings, pipeline, emergencies, missed transfers, and the inbox's overdue count per clinic. |
| **Clinic switcher** | Lists every clinic in the org, plus clinics the user belongs to directly. |
| **Shared settings** | An org admin can push `defaults` to all clinics or selected ones. This is an explicit "apply to clinics" action, not live inheritance, so a clinic's settings are always readable on their own. |

Roll-ups read each tenant through `Repos`; there are no cross-tenant queries without a `tenant_id`.

### 4. Billing

- **One Stripe customer and subscription per org.** The subscription has a per-clinic quantity, an included-minutes
  pool, and overage on the pooled total.
- `core/billing/metering.py` sums usage across `tenants where org_id = X` for the period.
- Clinic-level caps become optional, and the org can set a cap per clinic.
- Clinics in an org don't hold their own subscription. `lifecycle.find_tenant` maps org subscription events to
  every clinic in the org.

### 5. Data, privacy, offboarding

- Patients stay per clinic. A patient of two clinics is two contacts, and there's no cross-clinic patient
  lookup. Sharing patient data between clinics in a group is a controller decision that needs its own legal basis.
- Erasure (DSR) runs per clinic. The group view can run it across all clinics as one request that is logged per
  clinic.
- Leaving a group detaches the tenant (`org_id = null`) and keeps its data. Deleting an org needs every clinic to
  be detached or offboarded first.

## Work breakdown (estimate)

| Step | Size |
|---|---|
| `organisations` model, `tenants.org_id`, platform admin screens to create an org and attach clinics | S |
| Org memberships, `require_tenant` resolution, invites to an org, tests for isolation and role mapping | M |
| Group overview, plus switcher listing org clinics | M |
| Push shared settings to clinics | S |
| Org billing: Stripe subscription with quantity and pooled metering, lifecycle mapping | L |

Ship steps 1–3 first: groups get one login and one overview, still billed per clinic or by hand. Org billing
follows once a real group's terms are agreed.

## Decisions needed

1. **Pricing:** price per clinic with pooled minutes, or a negotiated flat fee? This decides whether step 5 is
   needed at all for the first groups.
2. **Shared settings:** should shared knowledge and prices be pushed copies (proposed), or live inheritance?
3. **Cross-clinic patients:** should a group ever see one patient across clinics? The default proposed here is no.
