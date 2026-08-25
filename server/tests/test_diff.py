import pytest
from agentlens_server.diff import diff_runs

pytestmark = pytest.mark.anyio


def _run(rid, status, score_value, duration, extra_span=False):
    spans = [
        {
            "span_id": f"{rid}1",
            "parent_id": None,
            "name": "agent",
            "kind": "agent",
            "status": status,
            "started_at": 1,
            "ended_at": 2,
            "duration_ms": duration,
        },
        {
            "span_id": f"{rid}2",
            "parent_id": f"{rid}1",
            "name": "search",
            "kind": "tool",
            "status": "success",
            "started_at": 1.1,
            "ended_at": 1.2,
            "duration_ms": 100,
        },
    ]
    if extra_span:
        spans.append(
            {
                "span_id": f"{rid}3",
                "parent_id": f"{rid}1",
                "name": "rerank",
                "kind": "tool",
                "status": "success",
                "started_at": 1.3,
                "ended_at": 1.4,
                "duration_ms": 100,
            }
        )
    return {
        "run_id": rid,
        "name": "agent",
        "status": status,
        "duration_ms": duration,
        "total_tokens": 100,
        "total_cost_usd": 0.01,
        "scores": [{"name": "faithfulness", "value": score_value, "passed": score_value >= 0.85}],
        "spans": spans,
    }


def test_identical_runs_report_equivalent():
    d = diff_runs(_run("a", "success", 0.9, 500), _run("b", "success", 0.9, 500))
    assert d["summary"]["verdict"].startswith("Runs are structurally")
    assert d["summary"]["changed"] == 0


def test_quality_regression_leads_the_verdict():
    """A score drop is the headline even when the DAG is identical."""
    d = diff_runs(_run("a", "success", 0.92, 500), _run("b", "success", 0.61, 500))
    assert d["summary"]["verdict"].startswith("Quality dropped")
    assert d["scores"][0]["delta"] < 0


def test_status_flip_names_the_deepest_span():
    a = _run("a", "success", 0.9, 500)
    b = _run("b", "error", 0.9, 500)
    b["spans"][1]["status"] = "error"
    d = diff_runs(a, b)
    # the root also flips, but the useful answer is the leaf that caused it
    assert "agent.search" in d["summary"]["verdict"]


def test_added_and_removed_spans():
    d = diff_runs(_run("a", "success", 0.9, 500), _run("b", "success", 0.9, 500, extra_span=True))
    assert d["summary"]["added"] == 1
    assert d["added"][0]["path"] == "agent.rerank#0"
    assert d["summary"]["removed"] == 0


def test_latency_change_needs_to_be_meaningful():
    small = diff_runs(_run("a", "success", 0.9, 500), _run("b", "success", 0.9, 520))
    assert small["summary"]["changed"] == 0, "a 4% shift is noise, not a finding"

    large = diff_runs(_run("a", "success", 0.9, 500), _run("b", "success", 0.9, 1500))
    assert large["summary"]["changed"] == 1
