"""
AgentLens — Open Source Observability Runtime for AI Agents.

Langfuse traces LLM calls. AgentLens traces the whole agent:
the execution DAG, tool calls, retries, cost per node, and run diffs.
"""

from .context import current_run, current_span
from .evals import Score, from_ragas, score
from .exporters import ConsoleExporter, FileExporter, HttpExporter
from .models import AgentRun, LLMMetadata, Span, SpanKind, SpanStatus
from .tracer import AgentLens, BudgetExceeded, tool, trace

__version__ = "0.2.0"
__all__ = [
    "AgentLens",
    "AgentRun",
    "BudgetExceeded",
    "ConsoleExporter",
    "FileExporter",
    "HttpExporter",
    "LLMMetadata",
    "Span",
    "SpanKind",
    "SpanStatus",
    "current_run",
    "current_span",
    "from_ragas",
    "Score",
    "score",
    "tool",
    "trace",
]
