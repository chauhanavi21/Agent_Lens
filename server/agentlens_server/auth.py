"""
Authentication and scopes.

Two problems with what came before.

The first is a plain hole: `AGENTLENS_API_KEY` gated ingest and nothing
else. Deletion, pruning, reading traces, and creating alert rules were all
reachable without a credential — so a server that looked protected wasn't.

The second is subtler and survives fixing the first. A single key that does
everything has to be handed to every agent process that reports traces. That
key can also delete every run you have. An agent is the most exposed thing
in the system — it runs arbitrary tool calls against untrusted input — and
giving it authority to wipe your observability is backwards.

So keys carry scopes:

    ingest   write traces, spans, scores          (what an agent needs)
    read     read runs, analytics, the stream     (a dashboard, a CI gate)
    admin    delete, prune, manage keys and rules (a human)

`admin` implies the others. An agent gets `ingest` and can't read anyone
else's traces or delete anything.

Keys are stored hashed. A trace store is a database of prompts, and a
credential sitting in plaintext next to it means one SQL injection reads
both. The plaintext is shown once at creation and never again.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Iterable, Optional

# Scope names, ordered from least to most authority.
SCOPE_INGEST = "ingest"
SCOPE_READ = "read"
SCOPE_ADMIN = "admin"

ALL_SCOPES = (SCOPE_INGEST, SCOPE_READ, SCOPE_ADMIN)

# admin implies everything; the others imply only themselves
SCOPE_IMPLIES: dict[str, frozenset[str]] = {
    SCOPE_ADMIN: frozenset(ALL_SCOPES),
    SCOPE_READ: frozenset({SCOPE_READ}),
    SCOPE_INGEST: frozenset({SCOPE_INGEST}),
}

# Prefix makes a leaked key greppable in logs and identifiable in a paste.
KEY_PREFIX = "agl_"
# Shown alongside the hash so a key can be named in a UI without storing it.
DISPLAY_CHARS = 8


def generate_key() -> str:
    """A new plaintext key. 32 bytes of urandom, URL-safe."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(plaintext: str) -> str:
    """
    SHA-256 of the key.

    Deliberately not a slow KDF: these are 256-bit random tokens, not
    passwords, so there's no dictionary to attack and stretching would only
    add latency to every request. The threat is database disclosure, which a
    plain hash already covers for a high-entropy secret.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def display_hint(plaintext: str) -> str:
    """The leading characters, for naming a key you can no longer read."""
    return plaintext[: len(KEY_PREFIX) + DISPLAY_CHARS]


def normalize_scopes(scopes: Iterable[str]) -> list[str]:
    """Keep only known scopes, in a stable order, without duplicates."""
    requested = {s.strip().lower() for s in scopes if s and s.strip()}
    return [s for s in ALL_SCOPES if s in requested]


def expand_scopes(scopes: Iterable[str]) -> frozenset[str]:
    """What a key can actually do, following implications."""
    granted: set[str] = set()
    for scope in scopes:
        granted |= SCOPE_IMPLIES.get(scope, frozenset())
    return frozenset(granted)


def token_from_header(authorization: Optional[str]) -> Optional[str]:
    """Pull the bearer token out of an Authorization header."""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def matches(plaintext: str, stored_hash: str) -> bool:
    """
    Constant-time comparison.

    A plain `==` on a hash leaks its prefix through timing. It's a marginal
    attack against a hash of a random token, but the fix costs nothing and
    the habit matters more than this instance.
    """
    return hmac.compare_digest(hash_key(plaintext), stored_hash)


@dataclass(frozen=True)
class Principal:
    """Who is making a request, and what they may do."""

    key_id: str
    name: str
    scopes: frozenset[str]
    is_legacy: bool = False

    def can(self, scope: str) -> bool:
        return scope in self.scopes

    @property
    def label(self) -> str:
        return f"{self.name} ({'legacy shared key' if self.is_legacy else self.key_id})"


# When no keys are configured at all, every request runs as this. Open by
# default is the right call for a tool people run on a laptop before they
# run it anywhere else — but it's stated plainly rather than implied.
ANONYMOUS = Principal(
    key_id="anonymous",
    name="unauthenticated",
    scopes=frozenset(ALL_SCOPES),
    is_legacy=False,
)


def legacy_principal() -> Principal:
    """
    The old `AGENTLENS_API_KEY`, kept working.

    It grants every scope, because that's what it effectively granted
    before — narrowing it silently would break every deployment that
    already ships traces with it. The upgrade path is to mint scoped keys
    and retire it, which the docs say and `/api/auth/keys` supports.
    """
    return Principal(
        key_id="legacy",
        name="AGENTLENS_API_KEY",
        scopes=frozenset(ALL_SCOPES),
        is_legacy=True,
    )


@dataclass
class KeyRecord:
    """A stored key, as the database holds it."""

    key_id: str
    name: str
    key_hash: str
    scopes: list[str]
    created_at: float
    last_used_at: Optional[float] = None
    revoked: bool = False

    def to_principal(self) -> Principal:
        return Principal(
            key_id=self.key_id,
            name=self.name,
            scopes=expand_scopes(self.scopes),
        )


def new_key_record(name: str, scopes: Iterable[str]) -> tuple[KeyRecord, str]:
    """
    Mint a key. Returns the record to store and the plaintext to show once.

    The plaintext is never persisted, so losing it means minting a new one —
    which is the correct trade for a credential that reads production
    prompts.
    """
    plaintext = generate_key()
    normalized = normalize_scopes(scopes) or [SCOPE_INGEST]
    record = KeyRecord(
        key_id=display_hint(plaintext),
        name=name or "unnamed key",
        key_hash=hash_key(plaintext),
        scopes=normalized,
        created_at=time.time(),
    )
    return record, plaintext
