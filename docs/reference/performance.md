# Performance

"How much does tracing cost?" is the first question worth asking an
observability SDK, so there's a suite that answers it:

```bash
python scripts/benchmark.py
```

Measured on one core, Python 3.12, median of seven batches with GC disabled
during timing:

| Case | Median | Added | Share of one 800ms LLM call |
| --- | ---: | ---: | ---: |
| Untraced function call (baseline) | 1.2µs | — | — |
| Decorated, no active run | 1.5µs | +0.2µs | 0.0000% |
| Run with 1 span | 22µs | +21µs | 0.0026% |
| Run with 6 spans | 78µs | +77µs | 0.0096% |
| Run with 1 LLM span | 45µs | +44µs | 0.0055% |
| 2 spans + redaction | 120µs | +119µs | 0.0149% |

**~13µs per span**, ~560 bytes per span held until export, and **12,600
runs/sec** (75,900 spans/sec) sustained on a single core. Export runs on a
background thread and is excluded — these are what the agent's own thread
pays.

The comparison that matters is the last column. Overhead as a percentage of
a 1.2µs no-op reads like a catastrophe and means nothing; against the work
an agent actually does between spans, a fully traced six-span run costs
about a hundredth of a percent of one model call.

### What optimization actually taught me

Redaction dominated the export path at ~50µs per string. Two attempts:

- **Combining all 13 detectors into one regex alternation** — no measurable
  gain, so it was reverted. It was added complexity buying nothing.
- **A trigger-character pre-filter** — every built-in detector's matches
  contain a digit, an `@`, or one of a few literals, so one cheap check up
  front lets ordinary prose skip every detector. **56µs → 4µs**, a 12x win
  on the common case.

The pre-filter is only sound because that over-approximation holds, so
there's a test asserting every secret type contains a trigger hint, and
custom patterns disable the fast path rather than being guessed at. A
performance optimization that silently becomes a data leak is the worst
possible trade.
