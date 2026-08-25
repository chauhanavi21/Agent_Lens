import pytest
from agentlens_server.gate import evaluate, to_markdown
from agentlens_server.judge import BUILTIN_RUBRICS, judge_run, parse_judge_response

pytestmark = pytest.mark.anyio


# --- pure gate logic ------------------------------------------------------ #


def _runs(values, status="success"):
    return [{"status": status, "scores": [{"name": k, "value": v} for k, v in d.items()]} for d in values]


def test_gate_passes_a_clean_branch():
    base = _runs([{"grounding": 0.92}, {"grounding": 0.90}])
    result = evaluate(_runs([{"grounding": 0.93}]), base, thresholds={"grounding": 0.8})
    assert result["passed"] is True


def test_gate_catches_regression_above_the_floor():
    """The case a fixed threshold alone never catches."""
    base = _runs([{"grounding": 0.92}])
    result = evaluate(_runs([{"grounding": 0.86}]), base, thresholds={"grounding": 0.85}, max_regression=0.03)
    assert result["passed"] is False
    assert "regressed" in result["failures"][0]


def test_gate_catches_absolute_floor_breach():
    result = evaluate(_runs([{"grounding": 0.4}]), None, thresholds={"grounding": 0.85})
    assert result["passed"] is False
    assert "below floor" in result["failures"][0]


def test_gate_fails_on_lost_coverage():
    """A metric the baseline measured but this branch stopped producing."""
    base = _runs([{"grounding": 0.9, "task_completion": 0.9}])
    result = evaluate(_runs([{"grounding": 0.9}]), base)
    assert result["passed"] is False
    assert "not scored" in result["failures"][0]


def test_gate_fails_on_errored_runs():
    result = evaluate(_runs([{"grounding": 0.99}], status="error"), None)
    assert result["passed"] is False
    assert "errored" in result["failures"][0]


def test_gate_requires_minimum_runs():
    result = evaluate([], None, min_runs=1)
    assert result["passed"] is False


def test_markdown_renders_every_check():
    base = _runs([{"grounding": 0.92}])
    md = to_markdown(evaluate(_runs([{"grounding": 0.80}]), base, max_regression=0.03))
    assert "AgentLens eval gate" in md
    assert "grounding" in md and "❌" in md


# --- judge ---------------------------------------------------------------- #


def test_judge_parses_messy_model_output():
    """Models wrap JSON in fences and prose often enough that strict
    parsing would fail runs for cosmetic reasons."""
    rubrics = [BUILTIN_RUBRICS["grounding"]]
    for raw in [
        '{"scores": {"grounding": {"value": 0.4, "reason": "invented a date"}}}',
        '```json\n{"scores": {"grounding": 0.4}}\n```',
        'Here is my assessment:\n{"scores": {"grounding": {"value": 0.4}}}\nHope that helps.',
    ]:
        parsed = parse_judge_response(raw, rubrics)
        assert parsed["grounding"]["value"] == 0.4


def test_judge_clamps_and_rejects_junk():
    rubrics = [BUILTIN_RUBRICS["grounding"]]
    assert parse_judge_response('{"scores": {"grounding": 7}}', rubrics)["grounding"]["value"] == 1.0

    with pytest.raises(ValueError):
        parse_judge_response("no json at all", rubrics)
    with pytest.raises(ValueError):
        parse_judge_response('{"scores": {"unrelated": 0.5}}', rubrics)


def test_judge_scores_use_the_shared_shape():
    run = {
        "name": "qa",
        "status": "success",
        "duration_ms": 900,
        "total_tokens": 100,
        "total_cost_usd": 0.01,
        "spans": [],
    }
    scores = judge_run(
        run, ["grounding"], provider=lambda _p: '{"scores": {"grounding": {"value": 0.4, "reason": "x"}}}'
    )
    assert scores[0]["source"] == "llm_judge"
    assert scores[0]["passed"] is False  # 0.4 is below grounding's 0.85 threshold
    assert set(scores[0]) >= {"name", "value", "source", "threshold", "passed", "comment"}


def test_unknown_rubric_is_rejected():
    with pytest.raises(ValueError, match="Unknown rubric"):
        judge_run({"spans": []}, ["not_a_rubric"], provider=lambda _p: "{}")


# --- gate over the API ---------------------------------------------------- #


async def test_gate_endpoint(client, make_run):
    await client.post("/api/ingest/run", json=make_run(tags=["main"], faithfulness=0.93))
    await client.post("/api/ingest/run", json=make_run(tags=["pr-1"], faithfulness=0.60))

    result = (
        await client.post(
            "/api/evals/gate", json={"candidate_tag": "pr-1", "baseline_tag": "main", "max_regression": 0.05}
        )
    ).json()
    assert result["passed"] is False
    assert "markdown" in result


async def test_gate_on_unknown_tag_is_404_not_a_pass(client):
    r = await client.post("/api/evals/gate", json={"candidate_tag": "nope"})
    assert r.status_code == 404


async def test_judge_endpoint_reports_missing_key(client, make_run, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    run = make_run()
    await client.post("/api/ingest/run", json=run)
    r = await client.post("/api/evals/judge", json={"run_id": run["run_id"]})
    assert r.status_code == 503
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]
