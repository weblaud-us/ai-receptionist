# The morning dashboard (Roadmap Task 3.7)

One Grafana dashboard, one row per question. Every panel is a PromQL query against the API's
`/metrics`; paste them into Grafana panels (Prometheus data source).

| Panel | Query | Good |
|---|---|---|
| Active calls | `max(receptionist_calls_active)` | below total capacity |
| Calls in the last hour by outcome | `sum by (outcome) (receptionist_calls_last_hour)` | mostly booked / message / faq |
| Calls by channel | `sum by (channel) (receptionist_calls_last_hour)` | — |
| Response latency p50 / p95 | `max by (quantile) (receptionist_turn_latency_seconds{kind="plain"})` | p50 ≤ 1.0 s, p95 ≤ 1.8 s |
| Tool-turn latency p50 / p95 | `max by (quantile) (receptionist_turn_latency_seconds{kind="tool"})` | p95 ≤ 3 s |
| Greeting time-to-first-audio p95 | `max(receptionist_greeting_latency_seconds{quantile="0.95"})` | ≤ 1.5 s |
| Provider errors (last hour) | `sum by (kind) (receptionist_provider_errors_last_hour)` | 0 |
| Fallback activations (last hour) | `sum by (kind) (receptionist_provider_fallbacks_last_hour)` | 0 |
| Transfer success | `sum by (result) (receptionist_transfers_last_hour)` | no `failed` |
| Calls ending in < 10 s | `sum(receptionist_calls_short_last_hour)` | a handful at most |
| API requests / errors | `sum by (status) (rate(http_requests_total[5m]))` | no 5xx |
| API p95 latency | `histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))` | ≤ 300 ms |
| Mongo / Redis up | `receptionist_dependency_up` | 1 |
| Dead jobs | `receptionist_jobs_dead` | 0 |

Cost per call minute and gross margin per tenant arrive with billing (Phase 5, `usage_events`).
Per-call detail (transcript, per-turn latency) is in the dashboard's Calls page and
`python -m scripts.latency_report`.
