# Trace replay

A production failure is usually not reproducible: the search API returns
something else now, the rate limit cleared, the model is nondeterministic.
Replay pins the *outside world* to what it actually returned, then lets
your code run for real against it.

```python
from agentlens import Cassette, replay


def test_bug_471():
    cassette = Cassette.load("fixtures/bug-471.json")
    with replay(cassette):
        result = qa_agent("capital of France")
    assert "no source" in result
```

Pull a cassette straight from the server:

```bash
curl localhost:7430/api/runs/<run_id>/cassette > fixtures/bug-471.json
```

### What gets replayed, and what doesn't

Tool, LLM, retrieval, and MCP spans are served from the recording. Agent,
chain, and custom spans **execute normally**. That split is the whole
design — replaying the reasoning too would just be playing back a
transcript, and what you want is today's code meeting yesterday's inputs.

### Guardrails

- **Changed inputs are an error, not a silent reuse.** If your fix alters
  what a step sends, replay raises `InputMismatch` rather than serving a
  recording nobody knows applies to the new arguments. Opt out with
  `match_inputs=False` if you mean it.
- **Strict by default.** An unrecorded call raises `ReplayMiss` instead of
  quietly reaching the network — otherwise a deterministic regression test
  turns flaky again the moment someone adds a call.
- **Recorded failures replay as failures**, so the bug reproduces before
  you prove the fix.
- **Unused recordings are reported.** Making fewer calls than the original
  is a divergence worth seeing.
- **Replayed spans are labelled** in the DAG, so nobody mistakes a replay
  for real traffic.
- `divergence(original, replayed)` names the first step where the two runs
  parted ways.

Recording full outputs is opt-in (`AgentLens(record_outputs=True)`) since it
costs storage; without it a cassette falls back to truncated previews and
flags itself `truncated`.
