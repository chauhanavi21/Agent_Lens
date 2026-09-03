# OpenTelemetry bridge

AgentLens speaks OTLP in both directions, using the OpenTelemetry GenAI
semantic conventions (v1.41.0).

**Out** — send agent traces to any OTel backend alongside AgentLens, so they
sit beside the rest of your telemetry instead of in a silo:

```python
from agentlens import AgentLens, HttpExporter
from agentlens.otel import MultiExporter, OTLPExporter

lens = AgentLens(
    exporter=MultiExporter(
        HttpExporter("http://localhost:7430"),  # AgentLens UI
        OTLPExporter("http://localhost:4318", service_name="my-agent"),  # collector
    )
)
```

A run arrives in Grafana, Tempo, Honeycomb, Jaeger, or Datadog as a proper
span tree:

```
invoke_agent research_agent
  ├── execute_tool web_search
  ├── retrieval retrieve_docs
  └── chat claude-sonnet-4      gen_ai.usage.input_tokens=1980
```

**In** — point any OTel exporter at `/api/ingest/otlp` and traces from other
SDKs become AgentLens runs, with the DAG, diffing, and alerting on top. No
SDK swap needed. See `otel-collector-config.yaml` for a collector that fans
traces to both at once.

### Notes on the spec

Every `gen_ai.*` attribute still carries **Development** stability in the
OTel registry, so names can change without a major version bump. AgentLens
dual-emits by default: GenAI attributes plus `agentlens.*` ones, which
carry what the spec has no place for yet — retry lineage, per-call cost,
and eval scores — and are namespaced so they can't collide with a future
OTel addition. Set `dual_emit=False` for pure convention output.

Prompt and completion content is **not** exported by default, since prompts
routinely carry user data. Opt in with `capture_content=True` or
`OTEL_GENAI_CAPTURE_MESSAGE_CONTENT=true`.
