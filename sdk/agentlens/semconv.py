"""
OpenTelemetry GenAI semantic convention mapping.

The GenAI conventions model an agent run as a span tree — invoke_agent at
the root, with chat and execute_tool spans beneath it — which is the shape
AgentLens already records. This module is the translation table.

Every gen_ai.* attribute still carries Development stability in the OTel
registry, so attribute names can move without a major version bump. Rather
than betting on one spelling, the exporter can dual-emit: GenAI attributes
plus AgentLens-native ones under the agentlens.* namespace, which will never
collide with a future OTel addition.
"""

from __future__ import annotations

from typing import Any, Optional

from .models import Span, SpanKind, SpanStatus

# Semantic convention version this mapping targets.
SEMCONV_VERSION = "1.41.0"

# SpanKind -> gen_ai.operation.name
OPERATION_NAME = {
    SpanKind.AGENT: "invoke_agent",
    SpanKind.LLM: "chat",
    SpanKind.TOOL: "execute_tool",
    SpanKind.RETRIEVAL: "retrieval",
    SpanKind.CHAIN: "invoke_workflow",
    SpanKind.CUSTOM: "invoke_workflow",
}

# OTel SpanKind enum values (proto): 0 unspecified, 1 internal, 2 server,
# 3 client, 4 producer, 5 consumer. LLM calls leave the process; the rest
# are internal orchestration.
OTEL_SPAN_KIND = {
    SpanKind.LLM: 3,
    SpanKind.TOOL: 3,
    SpanKind.RETRIEVAL: 3,
}

# OTel status codes: 0 unset, 1 ok, 2 error.
STATUS_CODE = {
    SpanStatus.SUCCESS: 1,
    SpanStatus.ERROR: 2,
    SpanStatus.CANCELLED: 2,
    SpanStatus.PAUSED: 2,
    SpanStatus.RUNNING: 0,
}

# Provider name -> gen_ai.system value. Falls back to the raw provider.
SYSTEM_ALIASES = {
    "openai": "openai",
    "azure": "az.ai.openai",
    "anthropic": "anthropic",
    "bedrock": "aws.bedrock",
    "vertex": "gcp.vertex_ai",
    "gemini": "gcp.gemini",
    "cohere": "cohere",
    "mistral": "mistral_ai",
    "ollama": "ollama",
}


def infer_system(provider: str, model: str) -> str:
    """Best-effort gen_ai.system from an explicit provider or the model name."""
    p = (provider or "").lower()
    for key, value in SYSTEM_ALIASES.items():
        if key in p:
            return value
    m = (model or "").lower()
    if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3"):
        return "openai"
    if "claude" in m:
        return "anthropic"
    if "gemini" in m:
        return "gcp.gemini"
    if "llama" in m or "mixtral" in m or "mistral" in m:
        return "mistral_ai" if "mistral" in m else "ollama"
    return provider or "_OTHER"


def span_name(span: Span) -> str:
    """
    Convention span naming: '{operation} {target}'. A chat span names the
    model, a tool span names the tool, an agent span names the agent.
    """
    op = OPERATION_NAME.get(span.kind, "invoke_workflow")
    if span.kind == SpanKind.LLM and span.llm and span.llm.model:
        return f"{op} {span.llm.model}"
    return f"{op} {span.name}"


def span_attributes(
    span: Span,
    run_name: str,
    run_id: str,
    dual_emit: bool = True,
    capture_content: bool = False,
) -> dict[str, Any]:
    """
    Build the attribute map for one span.

    `capture_content` gates prompt and response text. Content is off by
    default: prompts routinely carry user data, and a trace backend is a
    poor place to discover you've been storing it.
    """
    attrs: dict[str, Any] = {
        "gen_ai.operation.name": OPERATION_NAME.get(span.kind, "invoke_workflow"),
        "gen_ai.agent.name": run_name,
        "gen_ai.conversation.id": run_id,
    }

    if span.kind == SpanKind.TOOL:
        attrs["gen_ai.tool.name"] = span.name
        attrs["gen_ai.tool.type"] = "function"
        attrs["gen_ai.tool.call.id"] = span.span_id

    if span.llm:
        llm = span.llm
        attrs["gen_ai.system"] = infer_system(llm.provider, llm.model)
        if llm.model:
            attrs["gen_ai.request.model"] = llm.model
            attrs["gen_ai.response.model"] = llm.model
        if llm.input_tokens:
            attrs["gen_ai.usage.input_tokens"] = llm.input_tokens
        if llm.output_tokens:
            attrs["gen_ai.usage.output_tokens"] = llm.output_tokens
        if llm.temperature is not None:
            attrs["gen_ai.request.temperature"] = llm.temperature
        if capture_content:
            if llm.prompt_preview:
                attrs["gen_ai.input.messages"] = llm.prompt_preview
            if llm.response_preview:
                attrs["gen_ai.output.messages"] = llm.response_preview

    if span.error:
        attrs["error.type"] = span.error.strip().splitlines()[-1][:200] if span.error else "error"

    if dual_emit:
        # AgentLens-native attributes: namespaced so they can't collide with
        # a future gen_ai.* addition, and they carry what the spec has no
        # place for — retry lineage and per-call cost.
        attrs["agentlens.span.kind"] = span.kind.value
        # the convention names an LLM span after the model; keep the author's
        # own name so a round trip through OTLP is lossless
        attrs["agentlens.span.name"] = span.name
        attrs["agentlens.run.id"] = run_id
        if span.retry_of:
            attrs["agentlens.retry_of"] = span.retry_of
        if span.llm and span.llm.cost_usd:
            attrs["agentlens.cost.usd"] = round(span.llm.cost_usd, 6)

    return attrs


def run_attributes(run: Any, dual_emit: bool = True) -> dict[str, Any]:
    """Resource-level attributes describing the whole run."""
    attrs: dict[str, Any] = {
        "gen_ai.agent.name": run.name,
        "gen_ai.conversation.id": run.run_id,
    }
    if dual_emit:
        attrs.update(
            {
                "agentlens.run.id": run.run_id,
                "agentlens.run.status": run.status.value,
                "agentlens.run.total_tokens": run.total_tokens,
                "agentlens.run.total_cost_usd": run.total_cost_usd,
            }
        )
        if run.tags:
            attrs["agentlens.run.tags"] = ",".join(run.tags)
        for s in getattr(run, "scores", []) or []:
            name = s.name if hasattr(s, "name") else s["name"]
            value = s.value if hasattr(s, "value") else s["value"]
            attrs[f"agentlens.score.{name}"] = value
    return attrs


def status_for(span: Span) -> tuple[int, Optional[str]]:
    code = STATUS_CODE.get(span.status, 0)
    message = None
    if code == 2 and span.error:
        message = span.error.strip().splitlines()[-1][:300]
    return code, message
