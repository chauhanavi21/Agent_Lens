"""
Async-safe run/span context propagation via contextvars.
Nesting works automatically across threads and asyncio tasks.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

from .models import AgentRun, Span

_current_run: ContextVar[Optional[AgentRun]] = ContextVar("agentlens_run", default=None)
_current_span: ContextVar[Optional[Span]] = ContextVar("agentlens_span", default=None)


def current_run() -> Optional[AgentRun]:
    """The AgentRun active in this execution context, if any."""
    return _current_run.get()


def current_span() -> Optional[Span]:
    """The Span active in this execution context, if any."""
    return _current_span.get()


def set_run(run: Optional[AgentRun]):
    return _current_run.set(run)


def reset_run(token) -> None:
    _current_run.reset(token)


def set_span(span: Optional[Span]):
    return _current_span.set(span)


def reset_span(token) -> None:
    _current_span.reset(token)
