# Data lifecycle

A trace store that only grows eventually gets deleted by whoever pays for
the disk — but deleting traces is also how you lose the run someone was
about to investigate. So retention is **off by default** and conservative
when on.

```bash
AGENTLENS_RETENTION_DAYS=30                    # drop runs older than 30 days
AGENTLENS_RETENTION_MAX_RUNS_PER_AGENT=1000    # keep the newest N per agent
AGENTLENS_PROTECT_TAGS=keep,incident           # never touch these
```

Or on demand — note that `dry_run` defaults to true, because deletion is
irreversible and the safe path should be what you get by forgetting a
parameter:

```bash
curl -X POST localhost:7430/api/runs/prune \
  -d '{"older_than_days": 30, "dry_run": true}'
```

Every selected run comes back with the reason it was chosen. Design points:

- **Count limits are per agent, not global.** "Keep the last 1000 runs" on a
  system where one agent runs 100x more often silently erases the quiet
  agent's entire history — usually the one you're debugging.
- **Deleting a run follows its trace forward.** An MCP server's run is only
  meaningful stitched into its caller, so it goes too. The reverse never
  happens: pruning a tool server's history can't destroy the agent traces
  referencing it.
- **A zero or negative retention value is ignored**, since it would mean
  "delete everything" and nobody types that on purpose.
- **The sweep runs shortly after startup**, not one interval later — a
  server restarting every few hours would otherwise never reach its first
  sweep.

Long histories page with a cursor rather than an offset (`/api/runs/page`):
with offsets, runs arriving at the head while you scroll shift every
subsequent page and you see duplicates.
