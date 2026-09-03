# Live streaming

Batch tracing sends one payload when a run ends — so a run that hangs, gets
OOM-killed, or is simply still going never appears at all. Those are the
runs you most want to see.

```python
from agentlens import AgentLens, StreamExporter

lens = AgentLens(exporter=StreamExporter("http://localhost:7430"))
```

Each span is pushed as it opens and closes, and the UI subscribes over SSE
at `/api/stream`, so the DAG draws itself node by node while the agent
works. `GET /api/live/runs` lists what's executing right now; opening a live
run shows its partial DAG immediately.

Design notes:

- **Events are best-effort, the final run is the source of truth.** The
  exporter's queue is bounded and drops oldest-first, so a slow or dead
  server costs you the live view — never the agent's memory or its data.
- **Live state is in memory and disposable.** Persistence happens on the
  run's final export. A multi-process deployment swaps the broker for Redis
  pub/sub; the interface is small enough to be a drop-in.
- **Spans arriving before `run_start` are kept**, not dropped, so a browser
  connecting mid-run still sees a coherent DAG.
- **Reconnects trust the server's snapshot**, since `EventSource` can't tell
  you whether the gap lost events.
