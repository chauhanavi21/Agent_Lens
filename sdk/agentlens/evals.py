"""
Attach evaluation scores to runs and spans.

Two ways in. Inside a traced function, score the live run:

    from agentlens import score
    score("faithfulness", 0.86, source="ragas")

Or score after the fact from a harness, keyed by run_id:

    lens.score_run(run_id, {"faithfulness": 0.86, "answer_relevancy": 0.91},
                   source="ragas")

A score carries an optional threshold. When the value falls below it the
score is marked failed, which surfaces in the UI and can trip an alert
rule — that's how a quality regression becomes a notification rather than
something you notice a week later.
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from . import context as ctx


@dataclass
class Score:
    name: str
    value: float
    source: str = "custom"  # "ragas" | "custom" | "human" | …
    threshold: Optional[float] = None
    comment: str = ""
    span_id: Optional[str] = None  # None = whole-run score
    recorded_at: float = field(default_factory=time.time)

    @property
    def passed(self) -> Optional[bool]:
        if self.threshold is None:
            return None
        return self.value >= self.threshold

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "source": self.source,
            "threshold": self.threshold,
            "passed": self.passed,
            "comment": self.comment,
            "span_id": self.span_id,
            "recorded_at": self.recorded_at,
        }


def score(
    name: str,
    value: float,
    source: str = "custom",
    threshold: Optional[float] = None,
    comment: str = "",
    on_span: bool = False,
) -> Optional[Score]:
    """
    Record a score on the run active in this context. Returns None (and does
    nothing) when called outside a traced run, so instrumented code stays
    safe to call from anywhere.
    """
    run = ctx.current_run()
    if run is None:
        return None
    span = ctx.current_span() if on_span else None
    s = Score(
        name=name,
        value=float(value),
        source=source,
        threshold=threshold,
        comment=comment,
        span_id=span.span_id if span else None,
    )
    run.scores.append(s)
    return s


def score_run_payload(
    run_id: str,
    scores: dict[str, float],
    source: str = "custom",
    thresholds: Optional[dict[str, float]] = None,
    comment: str = "",
) -> dict[str, Any]:
    thresholds = thresholds or {}
    return {
        "run_id": run_id,
        "scores": [
            Score(
                name=k, value=float(v), source=source, threshold=thresholds.get(k), comment=comment
            ).to_dict()
            for k, v in scores.items()
        ],
    }


def post_scores(endpoint: str, payload: dict, api_key: Optional[str] = None, timeout: float = 5.0) -> bool:
    """POST scores to a running server. Returns False rather than raising."""
    url = endpoint.rstrip("/") + "/api/ingest/scores"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return 200 <= res.status < 300
    except Exception:
        return False


def from_ragas(result: Any, threshold: Optional[float] = None) -> dict[str, float]:
    """
    Normalize a Ragas evaluation result into a flat {metric: value} dict.
    Accepts a dict, an object with .scores, or a pandas-style .to_pandas().
    """
    if isinstance(result, dict):
        raw = result
    elif hasattr(result, "scores"):
        raw = result.scores
        if isinstance(raw, list) and raw:  # per-sample list → mean each metric
            keys = raw[0].keys()
            return {k: sum(float(r[k]) for r in raw) / len(raw) for k in keys}
    elif hasattr(result, "to_pandas"):
        df = result.to_pandas()
        num = df.select_dtypes("number")
        return {c: float(num[c].mean()) for c in num.columns}
    else:
        raise TypeError(f"Can't read Ragas scores from {type(result).__name__}.")
    return {k: float(v) for k, v in raw.items() if isinstance(v, (int, float))}
