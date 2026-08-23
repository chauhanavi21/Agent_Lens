"""
PII redaction.

Agent traces are unusually dangerous to store. A prompt is whatever the
user typed, and tool arguments are whatever the agent decided to send — so
a trace backend accumulates support-ticket text, uploaded documents, and
API credentials that nobody deliberately logged.

Redaction runs in the SDK, before export. Scrubbing server-side would still
mean the raw values crossed the network and sat in an access log; the only
place a secret is reliably contained is the process that produced it.

    lens = AgentLens(endpoint="…", redact=True)              # sane defaults
    lens = AgentLens(endpoint="…", redact=Redactor(
        policies={"email": "hash", "credit_card": "drop"},
        extra_patterns={"employee_id": r"\\bEMP-\\d{6}\\b"},
    ))

Policies per detector:
  mask  — keep enough to recognize a value, hide the rest (default)
  hash  — deterministic HMAC, so the same user correlates across runs
          without the value being stored. This is what keeps traces useful
          for audit and debugging after redaction.
  drop  — replace entirely
  allow — leave untouched
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Pattern

Policy = str  # "mask" | "hash" | "drop" | "allow"

# Keys whose values are redacted regardless of what they look like — a
# short random API key is indistinguishable from a random string, so the
# field name is the only reliable signal.
SENSITIVE_KEYS = frozenset({
    "password", "passwd", "secret", "token", "api_key", "apikey", "access_token",
    "refresh_token", "authorization", "auth", "credentials", "private_key",
    "client_secret", "session_id", "cookie", "ssn", "credit_card", "card_number",
    "cvv", "pin",
})

# Order matters: more specific patterns run first so a credit card isn't
# partially eaten by the phone-number pattern.
BUILTIN_PATTERNS: dict[str, str] = {
    "jwt": r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
    "openai_key": r"\bsk-[A-Za-z0-9_-]{16,}\b",
    "anthropic_key": r"\bsk-ant-[A-Za-z0-9_-]{16,}\b",
    "aws_key": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "github_token": r"\bgh[pousr]_[A-Za-z0-9]{16,}\b",
    "bearer": r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}",
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    "ssn": r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b",
    "credit_card": r"\b(?:\d[ -]*?){13,19}\b",
    "iban": r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
    "phone": r"(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b",
    "ipv4": r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
}

DEFAULT_POLICIES: dict[str, Policy] = {
    "jwt": "drop",
    "openai_key": "drop",
    "anthropic_key": "drop",
    "aws_key": "drop",
    "github_token": "drop",
    "bearer": "drop",
    "email": "mask",
    "ssn": "drop",
    "credit_card": "mask",
    "iban": "mask",
    "phone": "mask",
    "ipv4": "hash",
}

# Values longer than this are truncated before scanning: redaction runs on
# the agent's hot path, and a pathological input shouldn't stall it.
MAX_SCAN_CHARS = 20_000


def luhn_valid(digits: str) -> bool:
    """Real card numbers pass Luhn; order numbers and timestamps mostly don't."""
    nums = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    checksum, parity = 0, len(nums) % 2
    for i, n in enumerate(nums):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        checksum += n
    return checksum % 10 == 0


_VERSION_CONTEXT = re.compile(r"(?:version|ver\.?|v|release|build|semver|schema)\s*$", re.IGNORECASE)


def looks_like_version(text: str, start: int) -> bool:
    """
    `1.2.3.4` is a syntactically valid IP and also a very common version
    string. The surrounding words are the only thing that distinguishes
    them, so check what precedes the match.
    """
    prefix = text[max(0, start - 14) : start]
    return bool(_VERSION_CONTEXT.search(prefix.rstrip()))


def _mask(value: str, kind: str) -> str:
    """Keep just enough to recognize the value in a trace."""
    if kind == "email":
        local, _, domain = value.partition("@")
        head = local[:2] if len(local) > 2 else local[:1]
        return f"{head}{'*' * max(len(local) - len(head), 1)}@{domain}"
    digits = [c for c in value if c.isdigit()]
    if len(digits) >= 4:
        return f"[{kind}:••••{''.join(digits[-4:])}]"
    return f"[{kind}]"


@dataclass
class Redactor:
    """
    Scrubs strings and nested structures. Compiled once and reused, since
    this runs on every span.
    """

    policies: dict[str, Policy] = field(default_factory=dict)
    extra_patterns: dict[str, str] = field(default_factory=dict)
    sensitive_keys: frozenset[str] = SENSITIVE_KEYS
    hash_secret: Optional[str] = None
    default_policy: Policy = "mask"
    _compiled: list[tuple[str, Pattern[str]]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        merged = {**BUILTIN_PATTERNS, **self.extra_patterns}
        # Span inputs and outputs are previews — strings, not structures —
        # so field-name rules have to work on text as well, or a secret
        # named in a dict escapes the moment it's stringified.
        keys = "|".join(sorted(self.sensitive_keys, key=len, reverse=True))
        self._kv_pattern = re.compile(
            rf"""(?P<key>['"]?\b(?:{keys})\b['"]?)      # the field name
                 (?P<sep>\s*[:=]\s*)                    # : or =
                 (?P<val>'[^']*'|"[^"]*"|[^\s,;}}\)\]]+)  # quoted or bare value""",
            re.IGNORECASE | re.VERBOSE,
        )
        self.policies = {**DEFAULT_POLICIES, **self.policies}
        for name in self.extra_patterns:
            self.policies.setdefault(name, self.default_policy)
        # extra patterns first: a caller's domain-specific rule should win
        # over a generic builtin that might partially match the same text
        order = list(self.extra_patterns) + [k for k in BUILTIN_PATTERNS if k not in self.extra_patterns]
        self._compiled = [
            (name, re.compile(merged[name]))
            for name in order
            if self.policies.get(name, self.default_policy) != "allow"
        ]
        if self.hash_secret is None:
            self.hash_secret = os.getenv("AGENTLENS_HASH_SECRET", "agentlens")

    # -- primitives ----------------------------------------------------- #

    def fingerprint(self, value: str, kind: str = "") -> str:
        """
        Deterministic HMAC. The same input always yields the same token, so
        you can still answer "did this user hit the bug twice?" without the
        value being recoverable from the trace.
        """
        digest = hmac.new(
            (self.hash_secret or "agentlens").encode(),
            value.encode("utf-8", "replace"),
            hashlib.sha256,
        ).hexdigest()[:12]
        return f"[{kind or 'redacted'}:{digest}]"

    def _apply(self, kind: str, value: str) -> str:
        policy = self.policies.get(kind, self.default_policy)
        if policy == "allow":
            return value
        if policy == "drop":
            return f"[{kind}:redacted]"
        if policy == "hash":
            return self.fingerprint(value, kind)
        return _mask(value, kind)

    # -- public API ----------------------------------------------------- #

    def redact_text(self, text: str) -> str:
        if not text:
            return text
        if len(text) > MAX_SCAN_CHARS:
            text = text[:MAX_SCAN_CHARS] + "…[truncated before redaction]"

        def kv_replace(m: re.Match[str]) -> str:
            raw = m.group("val").strip("'\"")
            key = m.group("key").strip("'\"").lower()
            return f"{m.group('key')}{m.group('sep')}{self.fingerprint(raw, key)}"

        text = self._kv_pattern.sub(kv_replace, text)

        for kind, pattern in self._compiled:
            def replace(m: re.Match[str], kind=kind) -> str:
                value = m.group(0)
                # a long digit run is only a card if it passes Luhn;
                # otherwise it's an order id, a timestamp, or an ISBN
                if kind == "credit_card" and not luhn_valid(value):
                    return value
                if kind == "ipv4" and looks_like_version(m.string, m.start()):
                    return value
                return self._apply(kind, value)

            text = pattern.sub(replace, text)
        return text

    def redact_value(self, value: Any, key: str = "", depth: int = 0) -> Any:
        """Walk a nested structure, redacting by field name and by content."""
        if depth > 12:
            return value
        if key and key.lower().replace("-", "_") in self.sensitive_keys:
            return self.fingerprint(str(value), key.lower())
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            return {k: self.redact_value(v, str(k), depth + 1) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact_value(v, key, depth + 1) for v in value]
        return value

    def redact_span(self, span: Any) -> None:
        """
        Scrub a span in place, just before export. Never raises: a
        redaction bug must not become an agent outage — and it must not
        silently emit raw data either, so a failure drops the field.
        """
        for attr in ("inputs", "outputs", "error"):
            try:
                current = getattr(span, attr, None)
                if isinstance(current, str) and current:
                    setattr(span, attr, self.redact_text(current))
            except Exception:
                setattr(span, attr, "[redaction failed: field dropped]")

        try:
            if getattr(span, "attributes", None):
                span.attributes = self.redact_value(span.attributes)
        except Exception:
            span.attributes = {"redaction": "failed: attributes dropped"}

        llm = getattr(span, "llm", None)
        if llm is not None:
            for attr in ("prompt_preview", "response_preview"):
                try:
                    current = getattr(llm, attr, "")
                    if current:
                        setattr(llm, attr, self.redact_text(current))
                except Exception:
                    setattr(llm, attr, "[redaction failed: field dropped]")

    def redact_run(self, run: Any) -> Any:
        """Scrub every span in a run, plus run-level error text."""
        try:
            if getattr(run, "error", None):
                run.error = self.redact_text(run.error)
            for span in getattr(run, "spans", []) or []:
                self.redact_span(span)
        except Exception:
            pass
        return run

    def scan(self, text: str) -> dict[str, int]:
        """What would be redacted, and how much. Useful for a dry run."""
        found: dict[str, int] = {}
        for kind, pattern in self._compiled:
            matches = [
                m for m in pattern.findall(text or "")
                if kind != "credit_card" or luhn_valid(m if isinstance(m, str) else "")
            ]
            if matches:
                found[kind] = len(matches)
        return found


def default_redactor() -> Redactor:
    return Redactor()


def build_redactor(spec: Any) -> Optional[Redactor]:
    """Accept True, a Redactor, or a dict of policies."""
    if spec is None or spec is False:
        return None
    if spec is True:
        return default_redactor()
    if isinstance(spec, Redactor):
        return spec
    if isinstance(spec, dict):
        return Redactor(policies=spec)
    raise TypeError("redact must be True, a Redactor, or a dict of policies.")
