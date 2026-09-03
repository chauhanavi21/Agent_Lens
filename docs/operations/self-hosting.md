# Self-hosting

### Environment variables

| Variable            | Default                                                        | Description                          |
| ------------------- | -------------------------------------------------------------- | ------------------------------------ |
| `DATABASE_URL`      | `postgresql+asyncpg://agentlens:agentlens@postgres:5432/agentlens` | Postgres connection (SQLite works for dev) |
| `AGENTLENS_API_KEY` | `""` (no auth)                                                 | Require this key on ingest requests  |
| `AGENTLENS_HASH_SECRET` | `agentlens`                                                | Salt for redaction fingerprints      |
| `AGENTLENS_RETENTION_DAYS` | _(off)_                                                 | Drop runs older than this            |
| `AGENTLENS_RETENTION_MAX_RUNS_PER_AGENT` | _(off)_                                   | Keep the newest N runs per agent     |
| `AGENTLENS_PROTECT_TAGS` | `keep`                                                    | Tags retention never deletes         |
| `AGENTLENS_REDACT_ON_INGEST` | `false`                                               | Scrub foreign OTLP traces server-side |
| `CORS_ORIGINS`      | `http://localhost:5173`                                        | Comma-separated allowed origins      |
| `VITE_API_URL`      | `""` (demo mode)                                               | UI → server URL                      |

### Production checklist

- [ ] Set `AGENTLENS_API_KEY` to a strong random string
- [ ] Use a managed Postgres (RDS, Supabase, Neon)
- [ ] Put the server behind nginx/Caddy with TLS
- [ ] Set `CORS_ORIGINS` to your UI domain only
- [ ] Mount a persistent volume for Postgres data

# Alerts

Rules are declarative, stored server-side, and evaluated on every finished
run as a background task — a slow or broken webhook can never delay ingest
or fail the agent's export.

```bash
curl -X POST http://localhost:7430/api/alerts/rules -H 'Content-Type: application/json' -d '{
  "name": "Runs over budget",
  "field": "total_cost_usd",
  "op": "gt",
  "value": "0.50",
  "run_name": "research_agent",
  "webhook_url": "https://hooks.slack.com/services/…"
}'
```

Testable fields: `status`, `total_cost_usd`, `total_tokens`, `duration_ms`,
`span_count`, `error_span_count`, `retry_count`, `name`, `min_score`,
`failed_score_count`, and any named metric via `score:<name>`.
Operators: `gt`, `gte`, `lt`, `lte`, `eq`, `neq`, `contains`.

Payloads are Slack-compatible (`text`) and carry a structured `alert` object
for generic consumers. `POST /api/alerts/rules/{id}/test` sends a sample so
you can confirm the webhook works before you rely on it. Every firing is
recorded at `GET /api/alerts/events` with its delivery status.
