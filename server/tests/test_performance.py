"""
Performance guards.

Not benchmarks — `scripts/benchmark.py` is for measuring. These are loose
ceilings that catch an accidental order-of-magnitude regression, the kind
where someone adds a `deepcopy` to the span path and nobody notices until
production. The limits are deliberately generous (several times the measured
value) because CI runners are shared and noisy; a tight bound here would
just be a flaky test.
"""

import gc
import statistics
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sdk"))

from agentlens import AgentLens, Redactor, SpanKind  # noqa: E402
from agentlens.redaction import TRIGGER_HINTS  # noqa: E402


class NullExporter:
    def export(self, run):
        pass


def per_op_us(fn, iterations=2000, batches=5):
    """Median of several batches, so one scheduler hiccup can't fail a build."""
    for _ in range(200):
        fn()
    timings = []
    for _ in range(batches):
        gc.collect()
        start = time.perf_counter()
        for _ in range(iterations):
            fn()
        timings.append((time.perf_counter() - start) / iterations * 1_000_000)
    return statistics.median(timings)


def test_untraced_passthrough_is_nearly_free():
    """
    A decorated function with no active run is what a library author's users
    pay when they haven't instrumented anything. It must stay trivial.
    """
    lens = AgentLens(exporter=NullExporter())

    @lens.tool("orphan")
    def orphan():
        return 1

    assert per_op_us(orphan) < 20, "the pass-through path got expensive"


def test_span_overhead_stays_in_microseconds():
    lens = AgentLens(exporter=NullExporter())

    @lens.tool("step")
    def step():
        return 1

    @lens.trace("agent")
    def agent():
        for _ in range(5):
            step()
        return "done"

    # measured ~80µs for six spans; 10x that is still far below one LLM call
    assert per_op_us(agent, iterations=500) < 800


def test_redaction_pre_filter_short_circuits_prose():
    """
    The fast path is the reason redaction is affordable on every export.
    If it stops working, cost jumps roughly 12x and nothing else fails.
    """
    redactor = Redactor()
    prose = (
        "The agent decided to summarize the retrieved documents rather than "
        "answer directly, because the question asked for a comparison across "
        "sources and no single passage covered it."
    )
    assert not TRIGGER_HINTS.search(prose), "this fixture must have no trigger characters"
    assert per_op_us(lambda: redactor.redact_text(prose)) < 25


@pytest.mark.parametrize(
    "secret",
    [
        "jane.doe@acme.com",
        "(555) 123-4567",
        "123-45-6789",
        "4111 1111 1111 1111",
        "10.2.0.14",
        "sk-proj-abcdefghij1234567890",
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_abcdefghijklmnop1234",
        "Bearer abcdefghijklmnop",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.abcdefghijk",
        "GB33BUKB20201555555555",
    ],
)
def test_pre_filter_never_skips_a_real_secret(secret):
    """
    The fast path is only sound if every detector's matches contain a trigger
    hint. This is the check that keeps a performance optimization from
    becoming a data leak.
    """
    assert TRIGGER_HINTS.search(secret), f"the pre-filter would skip {secret!r}"

    redactor = Redactor()
    assert secret not in redactor.redact_text(f"the value is {secret} in this message")


def test_custom_patterns_disable_the_pre_filter():
    """A user's pattern may match text containing no trigger hint at all."""
    redactor = Redactor(extra_patterns={"codename": r"\bPROJECT-[A-Z]+\b"})
    assert redactor._fast_path_safe is False

    text = "the operation is PROJECT-BLUEBIRD, no digits anywhere here"
    assert not TRIGGER_HINTS.search(text)
    assert "PROJECT-BLUEBIRD" not in redactor.redact_text(text)


def test_memory_per_run_stays_bounded():
    """Runs are retained until export; a leak here shows up as RSS growth."""
    import tracemalloc

    lens = AgentLens(exporter=NullExporter())

    @lens.span("step", kind=SpanKind.LLM)
    def step(i):
        return {"result": i}

    @lens.trace("agent")
    def agent():
        for i in range(10):
            step(i)
        return "done"

    agent()
    gc.collect()
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    for _ in range(200):
        agent()
    after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()

    kb_per_run = (after - before) / 1024 / 200
    # measured ~6 KB for an 11-span run
    assert kb_per_run < 40, f"{kb_per_run:.1f} KB per run — spans got heavy"


def test_finished_runs_are_not_retained():
    """
    The tracer must not hold a reference after export, or a long-lived
    process accumulates every run it ever traced.
    """
    import weakref

    captured = []

    class Weak:
        def export(self, run):
            captured.append(weakref.ref(run))

    lens = AgentLens(exporter=Weak())

    @lens.trace("agent")
    def agent():
        return "done"

    agent()
    gc.collect()
    assert captured[0]() is None, "the SDK is still holding a finished run"
