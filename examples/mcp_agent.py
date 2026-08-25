"""
Tracing an agent that calls MCP tool servers.

The payoff is attribution. When a run is slow, the merged waterfall tells
you whether the agent made too many calls, the MCP server's downstream API
was slow, or the model misread a result it received quickly — three very
different fixes that look identical from the agent side alone.

    python examples/mcp_agent.py
"""

import time

from agentlens import (
    AgentLens,
    HttpExporter,
    SpanKind,
    current_run,
    mcp_server_span,
    trace_mcp_session,
)
from agentlens.models import Span, SpanStatus

# In production these are separate processes with their own exporters.
agent_lens = AgentLens(endpoint="http://localhost:7430")
server_lens = AgentLens(endpoint="http://localhost:7430")


# --------------------------------------------------------------------------- #
# the MCP server process
# --------------------------------------------------------------------------- #


@mcp_server_span(server_lens, server_name="github")
def create_issue(arguments=None, _meta=None):
    """A tool handler. Anything traced in here nests inside the caller's DAG."""
    run = current_run()

    api = Span(name="github_api_post", kind=SpanKind.TOOL, parent_id=run.spans[0].span_id, service="github")
    time.sleep(0.4)  # the slow downstream call
    api.attributes["http.status_code"] = 201
    api.finish(SpanStatus.SUCCESS)
    run.spans.append(api)

    return {"content": [{"type": "text", "text": "issue #42 created"}], "isError": False}


@mcp_server_span(server_lens, server_name="github")
def search_issues(arguments=None, _meta=None):
    time.sleep(0.15)
    # MCP reports tool failure in the payload, not by raising
    return {"content": [{"type": "text", "text": "rate limit exceeded"}], "isError": True}


# --------------------------------------------------------------------------- #
# a stand-in for mcp.ClientSession — swap in the real one unchanged
# --------------------------------------------------------------------------- #


class FakeMCPSession:
    transport = "stdio"

    def call_tool(self, name, arguments=None):
        handler = {"create_issue": create_issue, "search_issues": search_issues}[name]
        # in reality this crosses a pipe; _meta carries the trace context
        return handler(arguments=arguments, _meta=(arguments or {}).get("_meta"))

    def list_tools(self):
        return ["create_issue", "search_issues"]


@agent_lens.trace("issue_agent", tags=["prod", "mcp"])
def issue_agent(title):
    session = trace_mcp_session(agent_lens, FakeMCPSession(), server_name="github")
    session.list_tools()
    session.call_tool("search_issues", {"q": title})  # fails, agent continues
    return session.call_tool("create_issue", {"title": title})


if __name__ == "__main__":
    print(issue_agent("DAG layout overlaps on deep trees"))
    agent_lens.exporter.flush()
    server_lens.exporter.flush()
    print()
    print("Open http://localhost:5173 — the agent run shows both sides:")
    print("  issue_agent")
    print("    ├── search_issues      (mcp · failed: rate limit)")
    print("    └── create_issue       (mcp · agent)")
    print("          └── create_issue (mcp · github server)")
    print("                └── github_api_post   400ms   ← where the time went")
