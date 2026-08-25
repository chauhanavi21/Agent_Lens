"""
Trace replay: re-run an agent against recorded tool outputs.

A production failure is usually not reproducible — the search API returns
different results, the rate limit has cleared, the model is nondeterministic.
Replay pins the *outside world* to what it actually returned during the
failing run, then lets your agent code run for real against it.

That split is the whole design. Tool, LLM, retrieval, and MCP spans are
served from the recording; agent, chain, and custom spans execute normally.
Replaying the reasoning too would just be playing back a transcript — you
want today's code meeting yesterday's inputs.

    from agentlens import Cassette, replay

    def test_regression():
        cassette = Cassette.load("fixtures/run-9f2a.json")
        with replay(cassette):
            result = research_agent("who won the 2026 election")
        assert "unavailable" not in result
"""

from __future__ import annotations

import json
import os
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

# Span kinds served from the recording. Everything else runs for real.
DEFAULT_REPLAYED_KINDS = frozenset({"tool", "llm", "retrieval", "mcp"})

# Where the tracer stashes a full, JSON-round-trippable output when
# record_outputs is on. `outputs` itself is a truncated preview, which is
# fine for a UI and useless for replay.
REPLAY_OUTPUT_KEY = "agentlens.replay.output"
REPLAY_ERROR_KEY = "agentlens.replay.error"


class ReplayMiss(LookupError):
    """A span asked for during replay that the recording doesn't contain."""


class ReplayedError(Exception):
    """Re-raised in place of a failure the original run recorded."""


@dataclass
class RecordedCall:
    name: str
    kind: str
    output: Any
    error: Optional[str]
    inputs: str = ""
    truncated: bool = False
    duration_ms: Optional[float] = None


@dataclass
class Cassette:
    """
    Recorded side effects from one run, keyed by span name and call order.

    Order-based matching mirrors how the diff engine aligns runs: the second
    `web_search` in the recording answers the second `web_search` in the
    replay. It's the right default because agent code is usually
    deterministic in its call sequence given fixed inputs — and when it
    isn't, that divergence is itself the finding.
    """

    run_id: str = ""
    name: str = ""
    calls: dict[str, list[RecordedCall]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    _cursor: dict[str, int] = field(default_factory=dict, repr=False)

    # -- construction --------------------------------------------------- #

    @classmethod
    def from_run(cls, run: dict[str, Any], kinds: frozenset[str] = DEFAULT_REPLAYED_KINDS) -> Cassette:
        calls: dict[str, list[RecordedCall]] = {}
        spans = sorted(run.get("spans") or [], key=lambda s: s.get("started_at") or 0)
        for span in spans:
            if span.get("kind") not in kinds:
                continue
            if span.get("retry_of"):
                # a retried attempt is part of the recorded sequence: the
                # agent will make the same call again and should see the
                # same failure it saw the first time
                pass
            attrs = span.get("attributes") or {}
            error = attrs.get(REPLAY_ERROR_KEY) or span.get("error")
            has_full = REPLAY_OUTPUT_KEY in attrs
            calls.setdefault(span["name"], []).append(
                RecordedCall(
                    name=span["name"],
                    kind=span.get("kind", "custom"),
                    output=attrs.get(REPLAY_OUTPUT_KEY, span.get("outputs", "")),
                    error=error,
                    inputs=span.get("inputs", ""),
                    # an error recording is complete on its own; only a
                    # missing full output means we're down to a preview
                    truncated=not has_full and not error,
                    duration_ms=span.get("duration_ms"),
                )
            )
        return cls(
            run_id=run.get("run_id", ""),
            name=run.get("name", ""),
            calls=calls,
            metadata={
                "status": run.get("status"),
                "total_tokens": run.get("total_tokens"),
                "total_cost_usd": run.get("total_cost_usd"),
                "recorded_spans": sum(len(v) for v in calls.values()),
            },
        )

    @classmethod
    def load(cls, path: str) -> Cassette:
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Cassette:
        return cls(
            run_id=data.get("run_id", ""),
            name=data.get("name", ""),
            metadata=data.get("metadata", {}),
            calls={
                name: [RecordedCall(**c) for c in items] for name, items in (data.get("calls") or {}).items()
            },
        )

    @classmethod
    def fetch(cls, endpoint: str, run_id: str, api_key: Optional[str] = None) -> Cassette:
        """Pull a cassette straight from a running AgentLens server."""
        url = endpoint.rstrip("/") + f"/api/runs/{run_id}/cassette"
        headers = {}
        if api_key or os.getenv("AGENTLENS_API_KEY"):
            headers["Authorization"] = f"Bearer {api_key or os.getenv('AGENTLENS_API_KEY')}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as res:
            return cls.from_dict(json.loads(res.read()))

    # -- serialization -------------------------------------------------- #

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "name": self.name,
            "metadata": self.metadata,
            "calls": {
                name: [
                    {
                        "name": c.name,
                        "kind": c.kind,
                        "output": c.output,
                        "error": c.error,
                        "inputs": c.inputs,
                        "truncated": c.truncated,
                        "duration_ms": c.duration_ms,
                    }
                    for c in items
                ]
                for name, items in self.calls.items()
            },
        }

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)

    # -- playback ------------------------------------------------------- #

    def reset(self) -> None:
        self._cursor.clear()

    def next_call(self, name: str) -> RecordedCall:
        index = self._cursor.get(name, 0)
        recorded = self.calls.get(name)
        if not recorded:
            raise ReplayMiss(f"'{name}' was never called in the recorded run.")
        if index >= len(recorded):
            raise ReplayMiss(
                f"'{name}' was called {index + 1} time(s) during replay but only "
                f"{len(recorded)} time(s) in the recording."
            )
        self._cursor[name] = index + 1
        return recorded[index]

    def unused(self) -> dict[str, int]:
        """Calls in the recording the replay never made — a divergence signal."""
        return {
            name: len(items) - self._cursor.get(name, 0)
            for name, items in self.calls.items()
            if len(items) - self._cursor.get(name, 0) > 0
        }

    @property
    def span_count(self) -> int:
        return sum(len(v) for v in self.calls.values())


class InputMismatch(ReplayMiss):
    """A recorded call was reached with different arguments than were recorded."""


@dataclass
class ReplaySession:
    cassette: Cassette
    kinds: frozenset[str] = DEFAULT_REPLAYED_KINDS
    strict: bool = True
    match_inputs: bool = True
    hits: list[str] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)
    mismatches: list[dict[str, str]] = field(default_factory=list)

    def should_replay(self, kind: str) -> bool:
        return kind in self.kinds

    def resolve(self, name: str, inputs: str = "") -> RecordedCall:
        try:
            call = self.cassette.next_call(name)
        except ReplayMiss:
            self.misses.append(name)
            raise

        # Serving a recorded output for arguments that were never sent is a
        # lie: nobody knows what that API or model would have returned. If
        # your change alters what a step receives, that step needs a fresh
        # recording.
        if self.match_inputs and call.inputs and inputs and call.inputs != inputs:
            self.mismatches.append({"name": name, "recorded": call.inputs[:200], "replayed": inputs[:200]})
            if self.strict:
                raise InputMismatch(
                    f"'{name}' was called with different arguments than the recording.\n"
                    f"  recorded: {call.inputs[:160]}\n"
                    f"  replayed: {inputs[:160]}\n"
                    "Re-record this run, or pass match_inputs=False to reuse the old output anyway."
                )
        self.hits.append(name)
        return call

    def report(self) -> dict[str, Any]:
        """What matched, what didn't, and what the recording still holds."""
        return {
            "run_id": self.cassette.run_id,
            "hits": len(self.hits),
            "misses": self.misses,
            "unused": self.cassette.unused(),
            "input_mismatches": self.mismatches,
            "diverged": bool(self.misses) or bool(self.cassette.unused()) or bool(self.mismatches),
        }


_session: ContextVar[Optional[ReplaySession]] = ContextVar("agentlens_replay", default=None)


def current_session() -> Optional[ReplaySession]:
    return _session.get()


@contextmanager
def replay(
    cassette: Cassette,
    kinds: frozenset[str] = DEFAULT_REPLAYED_KINDS,
    strict: bool = True,
    match_inputs: bool = True,
) -> Iterator[ReplaySession]:
    """
    Serve recorded side effects for the duration of the block.

    strict=True raises ReplayMiss on an unrecorded call — the safe default
    for a test, since silently reaching the network turns a deterministic
    regression test back into a flaky one. strict=False falls through to
    the real function, which is useful while adapting an old cassette.
    """
    cassette.reset()
    session = ReplaySession(cassette=cassette, kinds=kinds, strict=strict, match_inputs=match_inputs)
    token = _session.set(session)
    try:
        yield session
    finally:
        _session.reset(token)


# --------------------------------------------------------------------------- #
# divergence
# --------------------------------------------------------------------------- #


def divergence(original: dict[str, Any], replayed: dict[str, Any]) -> dict[str, Any]:
    """
    Compare a replayed run against the run it was recorded from.

    Same inputs, same recorded world — so any structural difference is the
    code's doing. That's the useful signal: "this fix changes the path taken
    at step 4" or "this refactor added two extra search calls".
    """

    def sequence(run: dict[str, Any]) -> list[tuple[str, str]]:
        spans = sorted(run.get("spans") or [], key=lambda s: s.get("started_at") or 0)
        return [(s["name"], s.get("kind", "custom")) for s in spans]

    a, b = sequence(original), sequence(replayed)
    first_diff = None
    for i in range(max(len(a), len(b))):
        left = a[i] if i < len(a) else None
        right = b[i] if i < len(b) else None
        if left != right:
            first_diff = {
                "index": i,
                "original": left[0] if left else None,
                "replayed": right[0] if right else None,
            }
            break

    status_changed = original.get("status") != replayed.get("status")
    return {
        "identical": first_diff is None and not status_changed,
        "first_divergence": first_diff,
        "original_status": original.get("status"),
        "replayed_status": replayed.get("status"),
        "original_spans": len(a),
        "replayed_spans": len(b),
        "summary": _divergence_summary(first_diff, status_changed, original, replayed),
    }


def _divergence_summary(first_diff, status_changed, original, replayed) -> str:
    parts = []
    if status_changed:
        parts.append(f"Status changed: {original.get('status')} → {replayed.get('status')}.")
    if first_diff:
        parts.append(
            f"Execution diverged at step {first_diff['index']}: "
            f"{first_diff['original'] or '(nothing)'} → {first_diff['replayed'] or '(nothing)'}."
        )
    if not parts:
        return "Replay took the identical path through the same recorded world."
    return " ".join(parts)
