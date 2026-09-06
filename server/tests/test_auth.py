"""
Authentication and scopes.

The bug that prompted this: `AGENTLENS_API_KEY` gated ingest and nothing
else, so deletion, pruning, reading traces, and creating webhook rules were
reachable with no credential at all on a server that looked protected.

The second half is scoping. A single all-powerful key has to be handed to
every agent process, and an agent — which runs arbitrary tool calls against
untrusted input — is the last thing that should be able to delete your
traces.
"""

import pytest
from agentlens_server.auth import (
    KEY_PREFIX,
    expand_scopes,
    generate_key,
    hash_key,
    matches,
    new_key_record,
    normalize_scopes,
    token_from_header,
)

pytestmark = pytest.mark.anyio


# --- primitives ----------------------------------------------------------- #


def test_keys_are_random_and_prefixed():
    keys = {generate_key() for _ in range(200)}
    assert len(keys) == 200, "generated a duplicate key"
    assert all(k.startswith(KEY_PREFIX) for k in keys)
    assert all(len(k) > 40 for k in keys), "not enough entropy"


def test_only_the_hash_is_stored():
    record, plaintext = new_key_record("agent", ["ingest"])
    assert plaintext not in record.key_hash
    assert record.key_hash == hash_key(plaintext)
    # the id is a prefix, enough to name the key without revealing it
    assert plaintext.startswith(record.key_id)
    assert len(record.key_id) < len(plaintext)


def test_matching_is_exact():
    _, plaintext = new_key_record("agent", ["ingest"])
    stored = hash_key(plaintext)
    assert matches(plaintext, stored)
    assert not matches(plaintext + "x", stored)
    assert not matches(plaintext[:-1], stored)
    assert not matches("", stored)


def test_admin_implies_the_others():
    assert expand_scopes(["admin"]) == frozenset({"admin", "read", "ingest"})
    # but not the reverse: an ingest key must not gain read
    assert expand_scopes(["ingest"]) == frozenset({"ingest"})
    assert expand_scopes(["read"]) == frozenset({"read"})


def test_unknown_scopes_are_dropped():
    assert normalize_scopes(["ingest", "superuser", "READ"]) == ["ingest", "read"]
    assert normalize_scopes([]) == []
    # a key requesting only nonsense falls back to the least authority
    record, _ = new_key_record("x", ["nonsense"])
    assert record.scopes == ["ingest"]


def test_header_parsing():
    assert token_from_header("Bearer abc123") == "abc123"
    assert token_from_header("bearer abc123") == "abc123"
    assert token_from_header("Basic abc123") is None
    assert token_from_header("abc123") is None
    assert token_from_header(None) is None
    assert token_from_header("Bearer ") is None


# --- the hole this closes ------------------------------------------------- #


@pytest.fixture
async def keyed_client(client):
    """A client on a server with one admin key, plus helpers to mint more."""
    # the first key is mintable because no keys exist yet, which is how a
    # fresh server bootstraps itself
    created = await client.post("/api/auth/keys", json={"name": "admin", "scopes": ["admin"]})
    assert created.status_code == 201
    admin = created.json()["key"]

    async def mint(name, scopes):
        response = await client.post(
            "/api/auth/keys",
            json={"name": name, "scopes": scopes},
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert response.status_code == 201, response.text
        return response.json()["key"]

    return client, admin, mint


async def test_destructive_endpoints_reject_an_unauthenticated_caller(keyed_client, make_run):
    """
    The regression this guards: these all returned 2xx with no credential.
    """
    client, admin, _ = keyed_client
    auth = {"Authorization": f"Bearer {admin}"}
    run = make_run()
    await client.post("/api/ingest/run", json=run, headers=auth)

    unauthenticated = [
        ("GET", "/api/runs", None),
        ("GET", f"/api/runs/{run['run_id']}", None),
        ("DELETE", f"/api/runs/{run['run_id']}", None),
        ("POST", "/api/runs/prune", {"max_runs_per_agent": 0, "dry_run": True}),
        (
            "POST",
            "/api/alerts/rules",
            {
                "name": "x",
                "field": "status",
                "op": "eq",
                "value": "error",
                "webhook_url": "https://example.invalid",
            },
        ),
        ("POST", "/api/analytics/reindex", None),
        ("GET", "/api/analytics/spans", None),
    ]
    for method, path, body in unauthenticated:
        response = await client.request(method, path, json=body)
        assert response.status_code == 401, f"{method} {path} allowed an anonymous caller"


async def test_an_ingest_key_cannot_delete_anything(keyed_client, make_run):
    """
    The point of scoping. An agent holds this key; a compromised agent must
    not be able to wipe the traces that would show what it did.
    """
    client, admin, mint = keyed_client
    agent_key = await mint("prod agent", ["ingest"])
    agent = {"Authorization": f"Bearer {agent_key}"}

    run = make_run()
    assert (await client.post("/api/ingest/run", json=run, headers=agent)).status_code == 201

    forbidden = [
        ("DELETE", f"/api/runs/{run['run_id']}", None),
        ("POST", "/api/runs/prune", {"max_runs_per_agent": 0, "dry_run": False}),
        ("POST", "/api/auth/keys", {"name": "escalate", "scopes": ["admin"]}),
        ("GET", "/api/runs", None),
    ]
    for method, path, body in forbidden:
        response = await client.request(method, path, json=body, headers=agent)
        assert response.status_code == 403, f"an ingest key was allowed to {method} {path}"

    # and the run is still there
    assert (
        await client.get(f"/api/runs/{run['run_id']}", headers={"Authorization": f"Bearer {admin}"})
    ).status_code == 200


async def test_a_read_key_cannot_write_or_delete(keyed_client, make_run):
    client, admin, mint = keyed_client
    read_key = await mint("dashboard", ["read"])
    reader = {"Authorization": f"Bearer {read_key}"}

    await client.post("/api/ingest/run", json=make_run(), headers={"Authorization": f"Bearer {admin}"})

    assert (await client.get("/api/runs", headers=reader)).status_code == 200
    assert (await client.get("/api/analytics/spans", headers=reader)).status_code == 200

    assert (await client.post("/api/ingest/run", json=make_run(), headers=reader)).status_code == 403
    assert (
        await client.post("/api/runs/prune", json={"max_runs_per_agent": 0}, headers=reader)
    ).status_code == 403


async def test_admin_can_do_everything(keyed_client, make_run):
    client, admin, _ = keyed_client
    auth = {"Authorization": f"Bearer {admin}"}
    run = make_run()

    assert (await client.post("/api/ingest/run", json=run, headers=auth)).status_code == 201
    assert (await client.get("/api/runs", headers=auth)).status_code == 200
    assert (await client.delete(f"/api/runs/{run['run_id']}", headers=auth)).status_code == 200


async def test_an_invalid_key_is_rejected(keyed_client):
    client, _, _ = keyed_client
    response = await client.get("/api/runs", headers={"Authorization": "Bearer agl_not-a-real-key"})
    assert response.status_code == 401
    assert "Invalid API key" in response.json()["detail"]


async def test_a_revoked_key_stops_working(keyed_client, make_run):
    client, admin, mint = keyed_client
    auth = {"Authorization": f"Bearer {admin}"}
    doomed = await mint("temporary", ["ingest"])

    assert (
        await client.post("/api/ingest/run", json=make_run(), headers={"Authorization": f"Bearer {doomed}"})
    ).status_code == 201

    keys = (await client.get("/api/auth/keys", headers=auth)).json()
    key_id = next(k["key_id"] for k in keys if k["name"] == "temporary")
    assert (await client.delete(f"/api/auth/keys/{key_id}", headers=auth)).status_code == 200

    assert (
        await client.post("/api/ingest/run", json=make_run(), headers={"Authorization": f"Bearer {doomed}"})
    ).status_code == 401


async def test_you_cannot_revoke_the_key_you_are_using(keyed_client):
    """Locking yourself out of your own server is a bad afternoon."""
    client, admin, _ = keyed_client
    auth = {"Authorization": f"Bearer {admin}"}
    keys = (await client.get("/api/auth/keys", headers=auth)).json()
    own = next(k["key_id"] for k in keys if k["name"] == "admin")

    response = await client.delete(f"/api/auth/keys/{own}", headers=auth)
    assert response.status_code == 409
    assert "lock you out" in response.json()["detail"]


async def test_the_plaintext_key_is_shown_once_and_never_again(keyed_client):
    client, admin, _ = keyed_client
    auth = {"Authorization": f"Bearer {admin}"}

    created = (
        await client.post("/api/auth/keys", json={"name": "once", "scopes": ["ingest"]}, headers=auth)
    ).json()
    assert created["key"].startswith(KEY_PREFIX)

    listed = (await client.get("/api/auth/keys", headers=auth)).json()
    serialized = str(listed)
    assert created["key"] not in serialized, "the plaintext key was stored and returned"


async def test_whoami_reports_the_callers_scopes(keyed_client):
    client, admin, mint = keyed_client
    agent_key = await mint("agent", ["ingest"])

    me = (await client.get("/api/auth/whoami", headers={"Authorization": f"Bearer {agent_key}"})).json()
    assert me["scopes"] == ["ingest"]
    assert me["name"] == "agent"


async def test_auth_status_warns_about_an_open_server(client):
    """An operator has to be able to discover that their server is wide open."""
    status = (await client.get("/api/auth/status")).json()
    assert status["protected"] is False
    assert "anyone who can reach it" in status["warning"]

    await client.post("/api/auth/keys", json={"name": "admin", "scopes": ["admin"]})
    status = (await client.get("/api/auth/status")).json()
    assert status["protected"] is True
    assert status["warning"] is None


async def test_an_unconfigured_server_stays_open(client, make_run):
    """
    With no keys at all the server is open — the right default for something
    you run locally first. It has to be a deliberate, stated choice.
    """
    assert (await client.post("/api/ingest/run", json=make_run())).status_code == 201
    assert (await client.get("/api/runs")).status_code == 200
