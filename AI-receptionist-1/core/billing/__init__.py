"""
Billing and plans (Roadmap Phase 5).

    plans       plan catalogue, Stripe price ids, plan → limits
    costs       cost per call-minute and margin per plan (Task 5.1)
    stripe_api  Stripe REST calls + webhook signature check
    lifecycle   Stripe webhook events → tenant status / limits (idempotent by event id)
    usage       usage from usage_events, the call gate (ok / suspended / cap)
    metering    overage → Stripe meter events, exactly once
    policy      plan rules on settings changes (transfers, recording)
    jobs        the billing tick: alerts, metering, suspension, offboarding
"""
