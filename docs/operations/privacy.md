# PII redaction

Agent traces are unusually dangerous to store. A prompt is whatever the
user typed; tool arguments are whatever the agent decided to send. A trace
backend quietly accumulates support-ticket text, uploaded documents, and
API credentials nobody meant to log.

```python
lens = AgentLens(endpoint="…", redact=True)
```

Redaction runs **in the SDK, before export** — scrubbing at ingest would
still mean the raw values crossed the network and sat in an access log on
the way. (`AGENTLENS_REDACT_ON_INGEST=true` adds a server-side pass for
OTLP traffic from SDKs you don't control.)

### Policies

```python
from agentlens import AgentLens, Redactor

lens = AgentLens(
    endpoint="…",
    redact=Redactor(
        policies={
            "email": "hash",  # correlate a user across runs, store nothing
            "phone": "mask",  # keep a recognizable shape
            "credit_card": "drop",  # nothing survives
            "ipv4": "allow",  # internal service IPs are useful
        },
        extra_patterns={"order_id": r"\bORD-\d{8}\b"},
    ),
)
```

`hash` is the one that makes redacted traces still worth having: a
deterministic HMAC means the same customer produces the same token every
time, so you can group their runs and answer "did this user hit the bug
twice?" without the value being recoverable.

Detected out of the box: emails, phones, SSNs, credit cards, IBANs, IPv4,
JWTs, OpenAI/Anthropic/AWS/GitHub keys, and `Bearer` tokens — plus
field-name rules (`password`, `api_key`, `authorization`, …) that catch
short random secrets no pattern could.

### Accuracy

False positives are their own failure — a trace full of `[redacted]` is
useless. So detection is validated, not just matched:

- **Credit cards must pass Luhn.** `4111 1111 1111 1111` is redacted;
  order number `12345678901234567` is left alone.
- **IPv4 checks its context.** `1.2.3.4` after the word "version" is a
  version string, not an address.
- Ordinary text — dates, room numbers, semvers, error codes, code
  snippets — passes through untouched. There's a test asserting exactly
  that.

### Failing closed

- A redactor that throws **drops the field** rather than emitting raw data.
  The agent keeps running; the trace loses one value.
- Streaming events go through the same pass, or live view would bypass
  everything export protects.
- MCP server spans use the same path, so a tool server can't leak what the
  agent process redacts.
- `capture_content=False` drops inputs and outputs entirely — when content
  isn't needed, dropping beats scrubbing, since no detector catches
  everything. The DAG, timings, and status all survive.
- `redactor.scan(text)` reports what *would* be caught, for a dry run
  before you turn it on.
