"""
AgentLens — Open Source Observability Runtime for AI Agents.

Langfuse traces LLM calls. AgentLens traces the whole agent:
the execution DAG, tool calls, retries, cost per node, and run diffs.
"""

from .context import current_run, current_span
from .evals import Score, from_ragas, score
from .exporters import ConsoleExporter, FileExporter, HttpExporter
from .mcp import (
    extract_context,
    format_traceparent,
    inject_context,
    mcp_server_span,
    parse_traceparent,
    trace_mcp_session,
)
from .models import AgentRun, LLMMetadata, Span, SpanKind, SpanStatus
from .redaction import Redactor, default_redactor
from .replay import Cassette, InputMismatch, ReplayedError, ReplayMiss, divergence, replay
from .streaming import StreamExporter
from .tracer import AgentLens, BudgetExceeded, tool, trace

__version__ = "0.3.0"
__all__ = [
    "AgentLens",
    "AgentRun",
    "BudgetExceeded",
    "ConsoleExporter",
    "FileExporter",
    "HttpExporter",
    "StreamExporter",
    "LLMMetadata",
    "Span",
    "SpanKind",
    "SpanStatus",
    "current_run",
    "Cassette",
    "current_span",
    "default_redactor",
    "Redactor",
    "divergence",
    "InputMismatch",
    "replay",
    "ReplayedError",
    "ReplayMiss",
    "extract_context",
    "format_traceparent",
    "inject_context",
    "mcp_server_span",
    "parse_traceparent",
    "trace_mcp_session",
    "from_ragas",
    "Score",
    "score",
    "tool",
    "trace",
]
