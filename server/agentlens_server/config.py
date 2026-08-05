"""Server configuration from environment variables."""

import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://agentlens:agentlens@localhost:5432/agentlens",
)
# Local dev without Postgres: DATABASE_URL=sqlite+aiosqlite:///./agentlens.db
API_KEY = os.getenv("AGENTLENS_API_KEY", "")  # empty = no auth
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
