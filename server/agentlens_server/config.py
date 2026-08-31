"""Server configuration from environment variables."""

import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://agentlens:agentlens@localhost:5432/agentlens",
)
# Local dev without Postgres: DATABASE_URL=sqlite+aiosqlite:///./agentlens.db
API_KEY = os.getenv("AGENTLENS_API_KEY", "")  # empty = no auth
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

# Defense in depth for traces from SDKs you don't control (OTLP ingest, a
# third-party instrumentation). The SDK is the right place to redact — by
# the time data reaches here it has already crossed the network — but an
# ingest-side pass is better than nothing for foreign traffic.
REDACT_ON_INGEST = os.getenv("AGENTLENS_REDACT_ON_INGEST", "").lower() in ("1", "true", "yes")

# --- retention -------------------------------------------------------------
#
# Off by default. An observability store that silently deletes data is worse
# than one that grows, so turning this on has to be a deliberate act.
from .retention import policy_from_env  # noqa: E402

RETENTION_POLICY = policy_from_env(dict(os.environ))
# How often the background sweep runs. Only matters when a rule is set.
RETENTION_SWEEP_HOURS = float(os.getenv("AGENTLENS_RETENTION_SWEEP_HOURS", "6"))
