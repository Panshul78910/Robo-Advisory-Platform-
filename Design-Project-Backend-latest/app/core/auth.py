"""JWT verification and the Principal every repository is scoped to.

This service is its own identity provider. `POST /api/auth/login` verifies a
password against `app_credential` and mints an HS256 token signed with JWT_SECRET;
`get_principal` verifies it and loads the role. Supabase Auth is not involved,
and no Supabase project API key is needed anywhere in this codebase.

If the team later adopts hosted Supabase Auth, this is the module that changes,
and the change is not cosmetic: current Supabase projects sign access tokens
asymmetrically and publish a JWKS endpoint, so verification becomes "fetch and
cache the public key, verify RS256/ES256" rather than a shared HS256 secret. The
`Principal` shape and every repository above it stay as they are.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from dataclasses import dataclass
from typing import Literal

import jwt
import sqlalchemy as sa
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.clock import WallClock
from app.core.config import get_settings
from app.core.errors import Forbidden, Unauthorized
from app.db import models as m
from app.db.session import get_connection

Role = Literal["client", "admin"]


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated identity. `client_id` is the sole source of data scope."""

    user_id: uuid.UUID
    role: Role
    display_name: str
    client_id: uuid.UUID | None  # None for admins

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def require_client_id(self) -> uuid.UUID:
        if self.client_id is None:
            raise Forbidden("This endpoint is for client accounts.")
        return self.client_id


def issue_token(user_id: uuid.UUID, role: Role, email: str | None = None) -> tuple[str, int]:
    s = get_settings()
    now = WallClock.now()
    exp = now + _dt.timedelta(seconds=s.jwt_ttl_seconds)
    token = jwt.encode(
        {
            "sub": str(user_id),
            "aud": s.jwt_audience,
            "role": role,
            "email": email,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        },
        s.jwt_secret,
        algorithm="HS256",
    )
    return token, s.jwt_ttl_seconds


def _bearer(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized()
    return token


async def get_principal(
    request: Request,
    conn: AsyncConnection = Depends(get_connection),
) -> Principal:
    s = get_settings()
    try:
        claims = jwt.decode(
            _bearer(request),
            s.jwt_secret,
            algorithms=["HS256"],
            audience=s.jwt_audience,
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Your session has expired. Please sign in again.") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized() from exc

    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise Unauthorized() from exc

    row = (
        (
            await conn.execute(
                sa.select(
                    m.app_user.c.role, m.app_user.c.display_name, m.client.c.id.label("client_id")
                )
                .select_from(m.app_user.outerjoin(m.client, m.client.c.user_id == m.app_user.c.id))
                .where(m.app_user.c.id == user_id)
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Unauthorized()

    return Principal(
        user_id=user_id,
        role=row["role"],
        display_name=row["display_name"],
        client_id=row["client_id"],
    )


async def require_client(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.client_id is None:
        raise Forbidden("This endpoint is for client accounts.")
    return principal


async def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_admin:
        raise Forbidden("Administrator access is required.")
    return principal
