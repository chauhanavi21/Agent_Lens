"""
Request authentication.

One dependency factory, used by every router, so a new endpoint has to make
a deliberate choice about who may call it. The previous arrangement checked
the key inline in two handlers and nowhere else, which is exactly how
deletion ended up unauthenticated.
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import (
    ANONYMOUS,
    Principal,
    expand_scopes,
    hash_key,
    legacy_principal,
    matches,
    token_from_header,
)
from .config import API_KEY
from .db import get_session
from .models import ApiKeyRow


async def _has_stored_keys(session: AsyncSession) -> bool:
    count = (
        await session.execute(
            select(func.count(ApiKeyRow.key_id)).where(ApiKeyRow.revoked == False)  # noqa: E712
        )
    ).scalar()
    return bool(count)


async def resolve_principal(
    authorization: Optional[str] = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """
    Identify the caller.

    With no credentials configured at all the server is open — the right
    default for something you run locally before you run it anywhere else.
    The moment a key exists, everything requires one.
    """
    token = token_from_header(authorization)
    configured = bool(API_KEY) or await _has_stored_keys(session)

    if not configured:
        return ANONYMOUS

    if not token:
        raise HTTPException(
            status_code=401,
            detail="This server requires an API key. Send it as: Authorization: Bearer <key>",
        )

    if API_KEY and matches(token, hash_key(API_KEY)):
        return legacy_principal()

    rows = (
        (
            await session.execute(
                select(ApiKeyRow).where(ApiKeyRow.revoked == False)  # noqa: E712
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        if matches(token, row.key_hash):
            # last-used is best-effort telemetry for spotting a stale key;
            # a write failure here must not fail the request
            try:
                row.last_used_at = time.time()
                await session.commit()
            except Exception:
                await session.rollback()
            return Principal(
                key_id=row.key_id,
                name=row.name,
                scopes=expand_scopes(row.scopes or []),
            )

    raise HTTPException(status_code=401, detail="Invalid API key.")


def require(scope: str):
    """
    Dependency that demands a scope.

        @router.delete("/runs/{run_id}")
        async def delete_run(..., principal: Principal = Depends(require("admin"))):
    """

    async def dependency(principal: Principal = Depends(resolve_principal)) -> Principal:
        if not principal.can(scope):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"This endpoint needs the '{scope}' scope; "
                    f"your key has {sorted(principal.scopes) or 'none'}."
                ),
            )
        return principal

    return dependency
