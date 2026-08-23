"""
MCP tracing: one waterfall across the agent and the tool servers it calls.

An MCP server is, observability-wise, a peer service. The agent process
records orchestration and model rounds; the server records what the tool
actually did — the database query, the API retries, the empty response.
Without a shared trace, "the tool was slow" and "the model misread the
result" look identical from the agent side.

MCP carries W3C trace context in `params._meta` (SEP-414), so context
propagates across stdio pipes the same way it crosses HTTP. Both sides use
the same helpers here:

    # agent side — wrap the client session
    session = trace_mcp_session(lens, session, server_name="github")
    await session.call_tool("create_issue", {"title": "..."})

    # server side — decorate the tool handler
    @mcp_server_span(lens, server_name="github")
    async def create_issue(args, _meta=None): ...
"""

from __future__ import annotations

import functools
import inspect
import time
import traceback
import uuid
from typing import Any, Callable, Optional

from . import context as ctx
from .models import AgentRun, Span, SpanKind, SpanStatus, _preview

TRACEPARENT_VERSION = "00"
SAMPLED = "01"


# --------------------------------------------------------------------------- #
# W3C trace context
# --------------------------------------------------------------------------- #

def format_traceparent(trace_id: str, span_id: str, sampled: bool = True) -> str:
    """00-{32 hex trace id}-{16 hex parent span id}-{flags}"""
    tid = (trace_id.replace("-", "") + "0" * 32)[:32]
    sid = (span_id.replace("-", "") + "0" * 16)[:16]
    return f"{TRACEPARENT_VERSION}-{tid}-{sid}-{SAMPLED if sampled else '00'}"


def parse_traceparent(header: str) -> Optional[dict[str, Any]]:
    """Parse a traceparent header. Returns None when it's malformed."""
    if not header or not isinstance(header, str):
        return None
    parts = header.strip().split("-")
    if len(parts) != 4:
        return None
    version, trace_id, span_id, flags = parts
    if len(trace_id) != 32 or len(span_id) != 16:
        return None
    if set(trace_id) <= {"0"} or set(span_id) <= {"0"}:
        return None  # all-zero ids are invalid per spec
    try:
        int(trace_id, 16), int(span_id, 16)
    except ValueError:
        return None
    return {
        "version": version,
        "trace_id": trace_id,
        "parent_span_id": span_id,
        "sampled": flags.endswith("1"),
    }


def inject_context(arguments: Optional[dict] = None) -> dict:
    """
    Add trace context to an MCP tool call's arguments under `_meta`, which
    is the reserved field MCP passes through untouched. Returns the
    arguments unchanged when there's no active run to propagate.
    """
    args = dict(arguments or {})
    run, span = ctx.current_run(), ctx.current_span()
    if run is None or span is None:
        return args
    meta = dict(args.get("_meta") or {})
    meta["traceparent"] = format_traceparent(run.trace_id, span.span_id)
    meta["agentlens.run.id"] = run.run_id
    args["_meta"] = meta
    return args


def extract_context(arguments: Optional[dict] = None) -> Optional[dict[str, Any]]:
    """Read trace context out of an incoming MCP tool call's `_meta`."""
    meta = (arguments or {}).get("_meta") or {}
    parsed = parse_traceparent(meta.get("traceparent", ""))
    if parsed and meta.get("agentlens.run.id"):
        parsed["run_id"] = meta["agentlens.run.id"]
    return parsed


# --------------------------------------------------------------------------- #
# agent side: wrap an MCP client session
# --------------------------------------------------------------------------- #

class TracedMCPSession:
    """
    Wraps an MCP ClientSession so every tool call becomes an MCP span with
    trace context injected. Any method not listed here passes straight
    through to the underlying session.
    """

    TRACED = {"call_tool", "list_tools", "read_resource", "list_resources", "get_prompt"}

    def __init__(self, lens, session: Any, server_name: str = "mcp"):
        self._lens = lens
        self._session = session
        self._server_name = server_name

    def __getattr__(self, item):
        attr = getattr(self._session, item)
        if item not in self.TRACED or not callable(attr):
            return attr
        return self._wrap(item, attr)

    def _open(self, method: str, tool_name: str, args: Any) -> tuple[Optional[Span], Any]:
        run = ctx.current_run()
        if run is None:
            return None, None
        parent = ctx.current_span()
        # `service` names the process that recorded the span. This one was
        # recorded by the agent, so the target server belongs in the MCP
        # attributes — otherwise client and server spans are indistinguishable
        # once they're stitched into the same DAG.
        span = Span(
            name=tool_name or method,
            kind=SpanKind.MCP,
            parent_id=parent.span_id if parent else None,
        )
        span.attributes.update({
            "mcp.method.name": method,
            "mcp.server.name": self._server_name,
            "mcp.transport": getattr(self._session, "transport", "") or "",
        })
        if tool_name:
            span.attributes["mcp.tool.name"] = tool_name
        span.inputs = _preview(args)
        run.spans.append(span)
        return span, ctx.set_span(span)

    def _close(self, span: Span, token, result: Any, error: Optional[BaseException]) -> None:
        if error is not None:
            span.finish(SpanStatus.ERROR, error="".join(traceback.format_exception(error)).strip())
        else:
            # MCP signals tool failure in the payload, not by raising
            is_error = bool(
                result.get("isError") if isinstance(result, dict) else getattr(result, "isError", False)
            )
            span.outputs = _preview(result)
            if is_error:
                span.attributes["mcp.tool.is_error"] = True
                span.finish(SpanStatus.ERROR, error=_preview(result, 300))
            else:
                span.finish(SpanStatus.SUCCESS)
        ctx.reset_span(token)

    def _wrap(self, method: str, fn: Callable) -> Callable:
        def tool_name_of(args, kwargs) -> str:
            if method != "call_tool":
                return ""
            return (args[0] if args else kwargs.get("name", "")) or ""

        def inject(args, kwargs):
            """
            Inject trace context *after* the client span is open, so the
            server's parent is the MCP call itself rather than whatever
            span happened to be current when the wrapper was entered.
            """
            if method != "call_tool":
                return args, kwargs
            if len(args) > 1 and isinstance(args[1], dict):
                return (args[0], inject_context(args[1])) + tuple(args[2:]), kwargs
            return args, {**kwargs, "arguments": inject_context(kwargs.get("arguments"))}

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                span, token = self._open(method, tool_name_of(args, kwargs), {"args": args, "kwargs": kwargs})
                if span is None:
                    return await fn(*args, **kwargs)
                args, kwargs = inject(args, kwargs)
                span.inputs = _preview({"args": args, "kwargs": kwargs})
                try:
                    result = await fn(*args, **kwargs)
                    self._close(span, token, result, None)
                    return result
                except BaseException as e:
                    self._close(span, token, None, e)
                    raise

            return async_wrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            span, token = self._open(method, tool_name_of(args, kwargs), {"args": args, "kwargs": kwargs})
            if span is None:
                return fn(*args, **kwargs)
            args, kwargs = inject(args, kwargs)
            span.inputs = _preview({"args": args, "kwargs": kwargs})
            try:
                result = fn(*args, **kwargs)
                self._close(span, token, result, None)
                return result
            except BaseException as e:
                self._close(span, token, None, e)
                raise

        return wrapper


def trace_mcp_session(lens, session: Any, server_name: str = "mcp") -> TracedMCPSession:
    """Wrap an MCP ClientSession so its calls join the current agent run."""
    return TracedMCPSession(lens, session, server_name)


# --------------------------------------------------------------------------- #
# server side: record what the tool actually did
# --------------------------------------------------------------------------- #

def mcp_server_span(
    lens,
    server_name: str = "mcp-server",
    tool_name: Optional[str] = None,
    meta_arg: str = "_meta",
):
    """
    Decorate an MCP server's tool handler. Reads trace context from the
    incoming call and exports the server's work as a run that the AgentLens
    server stitches into the caller's DAG — so the agent's waterfall shows
    the database query inside the tool, not just "tool took 4s".

    Works standalone too: with no incoming context it starts its own trace,
    so the server stays observable when called by a client that doesn't
    propagate.
    """

    def decorator(fn: Callable) -> Callable:
        name = tool_name or fn.__name__

        def _start(kwargs) -> tuple[AgentRun, Span]:
            incoming = extract_context(kwargs.get(meta_arg) and {"_meta": kwargs[meta_arg]} or kwargs)
            run = AgentRun(name=f"{server_name}.{name}", tags=["mcp-server", server_name])
            if incoming:
                run.trace_id = incoming["trace_id"]
                run.metadata["remote_parent_id"] = incoming["parent_span_id"]
                run.metadata["caller_run_id"] = incoming.get("run_id")
            root = Span(name=name, kind=SpanKind.MCP, service=server_name)
            root.remote_parent_id = incoming["parent_span_id"] if incoming else None
            root.attributes.update({
                "mcp.tool.name": name,
                "mcp.server.name": server_name,
                "mcp.side": "server",
            })
            run.spans.append(root)
            return run, root

        def _finish(run: AgentRun, root: Span, rt, st, result, error):
            if error is None:
                root.outputs = _preview(result)
                root.finish(SpanStatus.SUCCESS)
                run.finish(SpanStatus.SUCCESS)
            else:
                tb = "".join(traceback.format_exception(error)).strip()
                root.finish(SpanStatus.ERROR, error=tb)
                run.finish(SpanStatus.ERROR, error=str(error))
            ctx.reset_span(st)
            ctx.reset_run(rt)
            try:
                # same sanitizing path as the tracer, so an MCP server
                # can't leak what the agent process redacts
                export = getattr(lens, "_export", None)
                if export is not None:
                    export(run)
                else:
                    lens.exporter.export(run)
            except Exception:
                pass

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                run, root = _start(kwargs)
                root.inputs = _preview({"args": args, "kwargs": kwargs})
                rt, st = ctx.set_run(run), ctx.set_span(root)
                try:
                    result = await fn(*args, **kwargs)
                    _finish(run, root, rt, st, result, None)
                    return result
                except BaseException as e:
                    _finish(run, root, rt, st, None, e)
                    raise

            return async_wrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            run, root = _start(kwargs)
            root.inputs = _preview({"args": args, "kwargs": kwargs})
            rt, st = ctx.set_run(run), ctx.set_span(root)
            try:
                result = fn(*args, **kwargs)
                _finish(run, root, rt, st, result, None)
                return result
            except BaseException as e:
                _finish(run, root, rt, st, None, e)
                raise

        return wrapper

    return decorator
