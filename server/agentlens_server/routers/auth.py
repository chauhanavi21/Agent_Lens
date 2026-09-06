import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import ALL_SCOPES, Principal, new_key_record, normalize_scopes
from ..config import API_KEY
from ..db import get_session
from ..dependencies import require, resolve_principal
from ..models import ApiKeyRow

router = APIRouter(tags=["auth"])


class CreateKeyRequest(BaseModel):
    name: str = Field(description="What this key is for, e.g. 'prod research agent'")
    scopes: list[str] = Field(default_factory=lambda: ["ingest"])


class KeyOut(BaseModel):
    key_id: str
    name: str
    scopes: list[str]
    created_at: float
    last_used_at: float | None = None
    revoked: bool = False


@router.get("/auth/whoami")
async def whoami(principal: Principal = Depends(resolve_principal)):
    """
    What the presented key can do.

    Useful when a request 403s and you're not sure which key the agent is
    actually sending.
    """
    return {
        "key_id": principal.key_id,
        "name": principal.name,
        "scopes": sorted(principal.scopes),
        "legacy_shared_key": principal.is_legacy,
        "note": (
            "This is the legacy AGENTLENS_API_KEY, which grants every scope. "
            "Mint scoped keys and retire it — an agent only needs 'ingest'."
        )
        if principal.is_legacy
        else None,
    }


@router.get("/auth/scopes")
async def list_scopes():
    """The available scopes, for building a key."""
    return {
        "scopes": [
            {"name": "ingest", "grants": "write runs, span events, and scores"},
            {"name": "read", "grants": "read runs, analytics, evals, and the live stream"},
            {"name": "admin", "grants": "delete and prune runs, manage alert rules and API keys"},
        ],
        "note": "admin implies read and ingest",
    }


@router.get("/auth/keys", response_model=list[KeyOut])
async def list_keys(
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require("admin")),
):
    rows = (await session.execute(select(ApiKeyRow).order_by(ApiKeyRow.created_at.desc()))).scalars().all()
    return [
        KeyOut(
            key_id=r.key_id,
            name=r.name,
            scopes=r.scopes or [],
            created_at=r.created_at,
            last_used_at=r.last_used_at,
            revoked=r.revoked,
        )
        for r in rows
    ]


@router.post("/auth/keys", status_code=201)
async def create_key(
    req: CreateKeyRequest,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require("admin")),
):
    """
    Mint a key. The plaintext is returned **once** and never stored.

    Losing it means minting another, which is the right trade for a
    credential that can read production prompts.
    """
    scopes = normalize_scopes(req.scopes)
    if not scopes:
        raise HTTPException(
            status_code=422,
            detail=f"No valid scopes given. Choose from: {', '.join(ALL_SCOPES)}.",
        )

    record, plaintext = new_key_record(req.name, scopes)
    session.add(
        ApiKeyRow(
            key_id=record.key_id,
            name=record.name,
            key_hash=record.key_hash,
            scopes=record.scopes,
            created_at=record.created_at,
            revoked=False,
        )
    )
    await session.commit()

    return {
        "key": plaintext,
        "key_id": record.key_id,
        "name": record.name,
        "scopes": record.scopes,
        "warning": "This is the only time the key is shown. Store it now.",
    }


@router.delete("/auth/keys/{key_id}", status_code=200)
async def revoke_key(
    key_id: str,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require("admin")),
):
    """
    Revoke a key.

    Revoked rather than deleted, so `last_used_at` survives — after a leak,
    "when was this last used?" is the first question.
    """
    row = await session.get(ApiKeyRow, key_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No key '{key_id}'.")
    if row.key_id == principal.key_id:
        raise HTTPException(
            status_code=409,
            detail="That's the key you're authenticating with; revoking it would lock you out.",
        )
    row.revoked = True
    await session.commit()
    return {"revoked": key_id, "revoked_at": time.time()}


@router.get("/auth/status")
async def auth_status(session: AsyncSession = Depends(get_session)):
    """
    Whether this server is protected. Deliberately unauthenticated — an
    operator needs to be able to discover that their server is wide open.
    """
    keys = (
        (
            await session.execute(
                select(ApiKeyRow).where(ApiKeyRow.revoked == False)  # noqa: E712
            )
        )
        .scalars()
        .all()
    )
    protected = bool(API_KEY) or bool(keys)
    return {
        "protected": protected,
        "scoped_keys": len(keys),
        "legacy_shared_key": bool(API_KEY),
        "warning": None
        if protected
        else "This server has no API keys configured — anyone who can reach it can read and delete your traces.",
    }
