"""
The CI entrypoint: fail a build when eval quality regresses.

    python -m agentlens.ci gate \
      --endpoint https://agentlens.internal \
      --candidate-tag "pr-${PR_NUMBER}" \
      --baseline-tag main \
      --threshold grounding=0.85 --threshold task_completion=0.8 \
      --max-regression 0.03

Exits 0 when every check passes, 1 when any fails, and 2 on a usage or
connection problem — so an unreachable server never reads as a clean pass.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Optional

EXIT_OK, EXIT_FAILED, EXIT_ERROR = 0, 1, 2


def _post(url: str, payload: dict, api_key: Optional[str], timeout: float = 60.0) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read())


def _parse_thresholds(pairs: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError(f"Bad --threshold '{pair}'. Use metric=value, e.g. grounding=0.85.")
        name, _, value = pair.partition("=")
        try:
            out[name.strip()] = float(value)
        except ValueError:
            raise ValueError(f"Bad --threshold '{pair}': '{value}' is not a number.")
    return out


def _write_github_summary(markdown: str) -> None:
    """Render the table into the job summary when running on GitHub Actions."""
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(markdown + "\n")
    except OSError:
        pass


def _print_table(result: dict[str, Any]) -> None:
    rows = result.get("checks") or []
    width = max([len(str(c["metric"])) for c in rows] + [6])
    print(f"\n  {'metric'.ljust(width)}  branch  baseline   delta  result")
    print(f"  {'-' * width}  ------  --------  ------  ------")
    for c in rows:
        def num(v, signed=False):
            if v is None:
                return "    —"
            return f"{v:+.3f}" if signed else f"{v:.3f}"
        mark = "pass" if c["status"] == "pass" else "FAIL"
        print(f"  {str(c['metric']).ljust(width)}   {num(c.get('candidate'))}"
              f"     {num(c.get('baseline'))}  {num(c.get('delta'), True)}  {mark}"
              + (f"  ({c['detail']})" if c.get("detail") else ""))
    print()


def cmd_gate(args: argparse.Namespace) -> int:
    try:
        thresholds = _parse_thresholds(args.threshold)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR

    payload = {
        "candidate_tag": args.candidate_tag,
        "baseline_tag": args.baseline_tag,
        "thresholds": thresholds,
        "max_regression": args.max_regression,
        "min_runs": args.min_runs,
        "fail_on_error_runs": not args.allow_error_runs,
    }
    url = args.endpoint.rstrip("/") + "/api/evals/gate"

    try:
        result = _post(url, payload, args.api_key or os.getenv("AGENTLENS_API_KEY"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:400]
        print(f"error: gate request failed ({e.code}): {detail}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as e:
        # an unreachable server must never look like a passing gate
        print(f"error: could not reach AgentLens at {args.endpoint}: {e}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["summary"])
        _print_table(result)

    if result.get("markdown"):
        _write_github_summary(result["markdown"])
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(result.get("markdown", result["summary"]))
        except OSError as e:
            print(f"warning: could not write {args.output}: {e}", file=sys.stderr)

    if result["passed"]:
        return EXIT_OK
    return EXIT_OK if args.warn_only else EXIT_FAILED


def cmd_judge(args: argparse.Namespace) -> int:
    url = args.endpoint.rstrip("/") + "/api/evals/judge"
    payload = {"run_id": args.run_id, "model": args.model}
    if args.rubric:
        payload["rubrics"] = args.rubric
    try:
        result = _post(url, payload, args.api_key or os.getenv("AGENTLENS_API_KEY"))
    except Exception as e:
        print(f"error: judge request failed: {e}", file=sys.stderr)
        return EXIT_ERROR
    for s in result["scores"]:
        mark = "" if s["passed"] is None else ("pass" if s["passed"] else "FAIL")
        print(f"  {s['name']:<18} {s['value']:.2f}  {mark}  {s['comment']}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentlens.ci", description="AgentLens CI checks.")
    parser.add_argument("--endpoint", default=os.getenv("AGENTLENS_ENDPOINT", "http://localhost:7430"))
    parser.add_argument("--api-key", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("gate", help="Fail the build if eval scores regress.")
    g.add_argument("--candidate-tag", required=True, help="Tag on this branch's runs, e.g. pr-123")
    g.add_argument("--baseline-tag", default=None, help="Tag to compare against, e.g. main")
    g.add_argument("--threshold", action="append", default=[], metavar="METRIC=VALUE",
                   help="Absolute floor for a metric. Repeatable.")
    g.add_argument("--max-regression", type=float, default=0.05,
                   help="Largest allowed drop vs baseline (default: 0.05)")
    g.add_argument("--min-runs", type=int, default=1)
    g.add_argument("--allow-error-runs", action="store_true", help="Don't fail just because a run errored.")
    g.add_argument("--warn-only", action="store_true", help="Report failures but always exit 0.")
    g.add_argument("--output", default=None, help="Write the markdown summary to this file.")
    g.add_argument("--json", action="store_true", help="Print the raw result.")
    g.set_defaults(func=cmd_gate)

    j = sub.add_parser("judge", help="Score a run with the LLM judge.")
    j.add_argument("--run-id", required=True)
    j.add_argument("--rubric", action="append", default=[], help="Rubric name. Repeatable.")
    j.add_argument("--model", default="claude-sonnet-4")
    j.set_defaults(func=cmd_judge)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
