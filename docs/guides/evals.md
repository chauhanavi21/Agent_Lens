# Evals

Score a run inline, or attach results from an eval suite afterwards.

```python
from agentlens import score


@lens.trace("qa_agent")
def qa_agent(question):
    answer = generate(question)
    score("faithfulness", 0.86, source="ragas", threshold=0.85)
    return answer
```

```python
# post-hoc, from a nightly eval harness
from agentlens import from_ragas
from ragas import evaluate

result = evaluate(dataset, metrics=[faithfulness, answer_relevancy])
lens.score_run(run_id, from_ragas(result), source="ragas", thresholds={"faithfulness": 0.85})
```

A score below its threshold is marked failed. That failure shows on the run,
appears in a diff as a quality regression, and can trip an alert rule via the
`score:<name>`, `min_score`, or `failed_score_count` fields — so a quality
drop pages you the same way an error does. See `examples/eval_agent.py`.

# LLM-as-judge and the CI gate

Ragas covers what you can compute. A judge covers what you can only
describe — did the agent actually answer, did it invent a tool result, did
it give up early. The judge reads the **execution trace**, not just the
final string, so it can see the retry loop that produced the answer.

```bash
curl -X POST localhost:7430/api/evals/judge \
  -d '{"run_id": "…", "rubrics": ["grounding", "task_completion"]}'
```

Built-in rubrics: `task_completion`, `tool_correctness`, `grounding`,
`efficiency`, `error_handling`. Judged scores are stored in the same shape
as inline and Ragas scores, so trends, diffs, and alerts treat them
identically.

### Gating a pull request

```bash
python -m agentlens.ci gate \
  --candidate-tag "pr-42" --baseline-tag main \
  --threshold grounding=0.85 --max-regression 0.03
```

```
1 eval check(s) failed: grounding: regressed -0.0700 (limit -0.0300)

  metric           branch  baseline   delta  result
  ---------------  ------  --------  ------  ------
  grounding         0.850     0.920  -0.070  FAIL  (regressed -0.0700)
  task_completion   0.905     0.900  +0.005  pass
```

The gate asks two questions, and the second is the one that earns its keep:

1. **Absolute** — is any metric below its floor? Catches a branch that was
   always bad.
2. **Relative** — did any metric drop more than `--max-regression` against
   the baseline? Catches a branch that made things *worse* while still
   passing every fixed threshold. `0.92 → 0.86` clears a 0.85 floor and is
   exactly the drift nobody notices until it's three releases old.

Also enforced: a metric the baseline measured but this branch stopped
producing fails as lost coverage, and errored runs fail by default.

Exit codes are `0` pass, `1` failed checks, `2` usage or connection error —
so an unreachable server can never read as a clean gate. See
`.github/workflows/eval-gate.yml` for a workflow that runs the suite, gates
the PR, and comments the table back.
