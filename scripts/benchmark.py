#!/usr/bin/env python3
"""
What does tracing actually cost?

An observability SDK sits on the hot path of the thing it observes, so
"how much overhead?" is the first question worth answering — and the honest
answer needs numbers, not adjectives.

    python scripts/benchmark.py              # the full suite
    python scripts/benchmark.py --quick      # fewer iterations
    python scripts/benchmark.py --json       # machine-readable

Method: each case is warmed up, then run in repeated batches. Results report
the **median** batch rather than the mean, because a GC pause or a scheduler
hiccup skews a mean and tells you nothing about the typical call. The p95 is
reported alongside it so tail behaviour stays visible.

The comparison that matters is against the *same function untraced*, not
against zero — the interesting number is what a span adds, not what Python
costs.
"""

from __future__ import annotations

import argparse
import gc
import json
import statistics
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

from agentlens import AgentLens, Redactor, SpanKind, score  # noqa: E402
from agentlens.exporters import Exporter  # noqa: E402


class NullExporter(Exporter):
    """
    Discards runs. Isolates *tracing* cost from *export* cost, which is a
    background thread in real use and shouldn't be attributed to the span.
    """

    def export(self, run) -> None:
        pass


class CountingExporter(Exporter):
    def __init__(self) -> None:
        self.count = 0

    def export(self, run) -> None:
        self.count += 1


@dataclass
class Result:
    name: str
    per_op_us: float
    p95_us: float
    baseline_us: Optional[float] = None
    note: str = ""

    @property
    def overhead_us(self) -> Optional[float]:
        if self.baseline_us is None:
            return None
        return self.per_op_us - self.baseline_us

    @property
    def overhead_pct(self) -> Optional[float]:
        if not self.baseline_us:
            return None
        return (self.per_op_us - self.baseline_us) / self.baseline_us * 100


@dataclass
class Suite:
    results: list[Result] = field(default_factory=list)

    def add(self, result: Result) -> Result:
        self.results.append(result)
        return result


def measure(
    fn: Callable[[], None], iterations: int, batches: int = 7, warmup: int = 2
) -> tuple[float, float]:
    """
    Time `fn` over several batches, returning (median µs/op, p95 µs/op).

    GC is disabled during timing and collected between batches: a collection
    landing inside one batch would be attributed to whatever happened to be
    running, which is noise rather than signal.
    """
    for _ in range(warmup):
        for _ in range(min(iterations, 200)):
            fn()

    timings = []
    for _ in range(batches):
        gc.collect()
        gc.disable()
        try:
            start = time.perf_counter()
            for _ in range(iterations):
                fn()
            elapsed = time.perf_counter() - start
        finally:
            gc.enable()
        timings.append(elapsed / iterations * 1_000_000)

    timings.sort()
    p95 = timings[min(int(len(timings) * 0.95), len(timings) - 1)]
    return statistics.median(timings), p95


# --------------------------------------------------------------------------- #
# workloads
# --------------------------------------------------------------------------- #


def work(n: int = 40) -> int:
    """A small unit of real work, so overhead is measured against something."""
    total = 0
    for i in range(n):
        total += i * i
    return total


def bench_bare_call(suite: Suite, iterations: int) -> Result:
    def fn():
        work()

    median, p95 = measure(fn, iterations)
    return suite.add(Result("untraced function call", median, p95, note="the baseline"))


def bench_single_span(suite: Suite, iterations: int, baseline: float) -> Result:
    lens = AgentLens(exporter=NullExporter())

    @lens.trace("agent")
    def agent():
        return work()

    median, p95 = measure(agent, iterations)
    return suite.add(Result("run with 1 span", median, p95, baseline, "@lens.trace only"))


def bench_nested_spans(suite: Suite, iterations: int, baseline: float, depth: int = 5) -> Result:
    lens = AgentLens(exporter=NullExporter())

    @lens.tool("leaf")
    def leaf():
        return work()

    layers = [leaf]
    for i in range(depth - 1):
        inner = layers[-1]
        layers.append(lens.span(f"layer_{i}", kind=SpanKind.CHAIN)(lambda inner=inner: inner()))

    top = layers[-1]

    @lens.trace("agent")
    def agent():
        return top()

    median, p95 = measure(agent, iterations)
    per_span = (median - baseline) / (depth + 1)
    return suite.add(
        Result(
            f"run with {depth + 1} spans",
            median,
            p95,
            baseline,
            f"{per_span:.1f}µs per span",
        )
    )


def bench_llm_span(suite: Suite, iterations: int, baseline: float) -> Result:
    """Token extraction and cost estimation run on every LLM span."""
    lens = AgentLens(exporter=NullExporter())

    class Response:
        model = "gpt-4o"
        usage = type("U", (), {"prompt_tokens": 1200, "completion_tokens": 340})()

    @lens.llm_call("chat", model="gpt-4o", provider="openai")
    def chat(prompt):
        work()
        return Response()

    @lens.trace("agent")
    def agent():
        return chat("summarize this document")

    median, p95 = measure(agent, iterations)
    return suite.add(Result("run with 1 LLM span", median, p95, baseline, "usage + cost estimation"))


def bench_redaction(suite: Suite, iterations: int) -> tuple[Result, Result]:
    """Redaction scans every string field, so its cost scales with content."""
    redactor = Redactor()
    prose = (
        "The agent decided to summarize the retrieved documents rather than "
        "answer directly, because the question asked for a comparison across "
        "sources and no single passage covered it."
    )
    clean = (
        "The deployment finished at 14:32 on 2026-08-24 in region us-east-1. "
        "Latency was 240ms across 1,204 requests with no errors reported."
    )
    dirty = (
        "Contact jane.doe@acme.com or call (555) 123-4567 about order ORD-4821. "
        "Card 4111 1111 1111 1111 was charged. Host 10.2.0.14 responded in 240ms. "
        "Key sk-proj-abcdefghij1234567890 rotated."
    )

    median_prose, p95_prose = measure(lambda: redactor.redact_text(prose), iterations)
    suite.add(
        Result("redact prose, no trigger chars", median_prose, p95_prose, note="pre-filter short-circuits")
    )
    median_clean, p95_clean = measure(lambda: redactor.redact_text(clean), iterations)
    median_dirty, p95_dirty = measure(lambda: redactor.redact_text(dirty), iterations)

    return (
        suite.add(
            Result(
                "redact 140 chars with digits",
                median_clean,
                p95_clean,
                note="detectors all run, find nothing",
            )
        ),
        suite.add(
            Result("redact 200 chars, 6 secrets", median_dirty, p95_dirty, note="every detector fires")
        ),
    )


def bench_traced_with_redaction(suite: Suite, iterations: int, baseline: float) -> Result:
    lens = AgentLens(exporter=NullExporter(), redact=True)

    @lens.tool("lookup")
    def lookup(email):
        work()
        return {"email": email, "phone": "(555) 123-4567"}

    @lens.trace("agent")
    def agent():
        return lookup("jane.doe@acme.com")

    median, p95 = measure(agent, iterations)
    return suite.add(Result("2 spans + redaction on export", median, p95, baseline))


def bench_scores(suite: Suite, iterations: int, baseline: float) -> Result:
    lens = AgentLens(exporter=NullExporter())

    @lens.trace("agent")
    def agent():
        work()
        score("faithfulness", 0.91, threshold=0.85)
        score("relevancy", 0.88, threshold=0.80)
        return 1

    median, p95 = measure(agent, iterations)
    return suite.add(Result("1 span + 2 scores", median, p95, baseline))


def bench_untraced_passthrough(suite: Suite, iterations: int, baseline: float) -> Result:
    """
    A decorated function called with no active run. This is what a library
    author pays when their user hasn't instrumented anything — it should be
    nearly free.
    """
    lens = AgentLens(exporter=NullExporter())

    @lens.tool("orphan")
    def orphan():
        return work()

    median, p95 = measure(orphan, iterations)
    return suite.add(Result("decorated, no active run", median, p95, baseline, "pass-through path"))


def bench_serialization(suite: Suite, iterations: int) -> Result:
    """Turning a finished run into the wire payload."""
    lens = AgentLens(exporter=NullExporter())

    captured = []

    class Capture(Exporter):
        def export(self, run):
            captured.append(run)

    lens.exporter = Capture()

    @lens.tool("step")
    def step(i):
        return {"result": i, "items": list(range(10))}

    @lens.trace("agent")
    def agent():
        for i in range(10):
            step(i)
        return "done"

    agent()
    run = captured[0]
    median, p95 = measure(lambda: json.dumps(run.to_dict(), default=str), iterations)
    return suite.add(Result("serialize an 11-span run", median, p95, note="to JSON, for export"))


def bench_memory(iterations: int = 500) -> dict[str, float]:
    """How much memory does a retained run cost? Runs are held until export."""
    lens = AgentLens(exporter=NullExporter())
    kept = []

    class Keep(Exporter):
        def export(self, run):
            kept.append(run)

    lens.exporter = Keep()

    @lens.tool("step")
    def step(i):
        return {"result": i}

    @lens.trace("agent")
    def agent():
        for i in range(10):
            step(i)
        return "done"

    gc.collect()
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    for _ in range(iterations):
        agent()
    after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()

    total_kb = (after - before) / 1024
    return {
        "runs": iterations,
        "spans_per_run": 11,
        "kb_per_run": round(total_kb / iterations, 2),
        "bytes_per_span": round(total_kb * 1024 / (iterations * 11)),
    }


def bench_throughput() -> dict[str, float]:
    """Sustained runs per second on one core, tracing only."""
    exporter = CountingExporter()
    lens = AgentLens(exporter=exporter)

    @lens.tool("step")
    def step(i):
        return i

    @lens.trace("agent")
    def agent():
        for i in range(5):
            step(i)
        return "done"

    gc.collect()
    deadline = time.perf_counter() + 1.0
    count = 0
    while time.perf_counter() < deadline:
        agent()
        count += 1

    return {"runs_per_second": count, "spans_per_second": count * 6}


# --------------------------------------------------------------------------- #

# A representative LLM call, for scale. Overhead as a percentage of a 1.2µs
# no-op reads like a catastrophe and means nothing; the honest comparison is
# against the work an agent actually does between spans.
REFERENCE_LLM_CALL_MS = 800


def render(suite: Suite, memory: dict, throughput: dict) -> None:
    width = max(len(r.name) for r in suite.results) + 2
    header = (
        f"{'case'.ljust(width)}{'median'.rjust(9)}{'p95'.rjust(9)}"
        f"{'added'.rjust(10)}{'of an LLM call'.rjust(16)}   note"
    )
    print(f"\n{header}")
    print("-" * len(header))

    for r in suite.results:
        added = f"{r.overhead_us:+.1f}µs" if r.overhead_us is not None else ""
        share = ""
        if r.overhead_us is not None and r.overhead_us > 0:
            fraction = r.overhead_us / (REFERENCE_LLM_CALL_MS * 1000) * 100
            share = f"{fraction:.4f}%"
        print(
            f"{r.name.ljust(width)}{r.per_op_us:8.2f}µ"[:-1].rjust(9)
            + f"{r.p95_us:8.2f}µ"[:-1].rjust(9)
            + added.rjust(10)
            + share.rjust(16)
            + f"   {r.note}"
        )

    print(
        f"\nmemory      {memory['kb_per_run']} KB per {memory['spans_per_run']}-span run "
        f"(~{memory['bytes_per_span']} bytes/span, held until export)"
    )
    print(
        f"throughput  {throughput['runs_per_second']:,} runs/sec, "
        f"{throughput['spans_per_second']:,} spans/sec on one core"
    )
    print(f'\n"of an LLM call" is the added cost as a share of one {REFERENCE_LLM_CALL_MS}ms model call.')


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure AgentLens tracing overhead.")
    parser.add_argument("--quick", action="store_true", help="Fewer iterations")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args()

    n = 2_000 if args.quick else 20_000
    suite = Suite()

    baseline = bench_bare_call(suite, n).per_op_us
    bench_untraced_passthrough(suite, n, baseline)
    bench_single_span(suite, n, baseline)
    bench_nested_spans(suite, n // 2, baseline)
    bench_llm_span(suite, n // 2, baseline)
    bench_scores(suite, n // 2, baseline)
    bench_traced_with_redaction(suite, n // 4, baseline)
    bench_redaction(suite, n // 2)
    bench_serialization(suite, n // 10)

    memory = bench_memory(200 if args.quick else 500)
    throughput = bench_throughput()

    if args.json:
        print(
            json.dumps(
                {
                    "python": sys.version.split()[0],
                    "results": [asdict(r) | {"overhead_us": r.overhead_us} for r in suite.results],
                    "memory": memory,
                    "throughput": throughput,
                },
                indent=2,
            )
        )
    else:
        print(f"AgentLens tracing overhead — Python {sys.version.split()[0]}")
        print("median of 7 batches, GC disabled during timing")
        render(suite, memory, throughput)
        print("\nExport happens on a background thread and is excluded here:")
        print("these numbers are what the agent's own thread pays.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
