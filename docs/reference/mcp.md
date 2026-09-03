# MCP tracing

An MCP server is a peer service, and the interesting failures live on the
far side of the boundary. From the agent alone, "the tool was slow" and
"the model misread the result" look identical. MCP carries W3C trace
context in `params._meta`, so both sides can join one trace — across stdio
pipes as well as HTTP.

**Agent side** — wrap the client session:

```python
from agentlens import trace_mcp_session

session = trace_mcp_session(lens, session, server_name="github")
await session.call_tool("create_issue", {"title": "..."})
```

**Server side** — decorate the tool handler:

```python
from agentlens import mcp_server_span


@mcp_server_span(lens, server_name="github")
async def create_issue(arguments=None, _meta=None): ...  # spans recorded here nest inside the caller's DAG
```

The result is one waterfall:

```
issue_agent
  └── create_issue          (mcp · agent process)
        └── create_issue    (mcp · github server)
              └── github_api_post   412ms   ← the actual latency
```

Details worth knowing:

- **Either side can arrive first.** Stitching happens at read time on the
  shared trace id, so a late server run still merges and neither service
  has to know about the other's storage.
- **Server runs don't clutter the run list.** They appear inside the
  caller's DAG; pass `?include_remote=true` to list them on their own.
- **`isError` payloads count as failures.** MCP reports tool errors in the
  response body rather than by raising, which is easy to miss.
- **An unstitched server run is still readable** rather than silently
  dropped, so a server stays observable when its caller isn't instrumented.
