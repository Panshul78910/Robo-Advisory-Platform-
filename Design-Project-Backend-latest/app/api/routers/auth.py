"""Login. Verifies a password against `app_credential` and mints this service's
own access token. See `app/core/auth.py` for why Supabase Auth is not used."""

from __future__ import annotations

import sqlalchemy as sa
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.dto.common import Dto
from app.api.dto.requests import LoginRequest, RegisterRequest
from app.core.auth import Principal, get_principal, issue_token
from app.core.errors import Conflict, Unauthorized, ValidationError
from app.db import models as m
from app.db.session import get_connection

router = APIRouter(tags=["auth"])
hasher = PasswordHasher()


class LoginResponse(Dto):
    accessToken: str
    tokenType: str = "bearer"
    expiresIn: int
    role: str
    displayName: str
    clientId: str | None = None


class MeResponse(Dto):
    userId: str
    role: str
    displayName: str
    clientId: str | None = None
    clientCode: str | None = None


def _next_client_code(last: str | None) -> str:
    if not last:
        return "C001"
    digits = "".join(ch for ch in last if ch.isdigit())
    return f"C{int(digits or 0) + 1:03d}"


@router.post("/auth/register", response_model=LoginResponse)
async def register(
    body: RegisterRequest, conn: AsyncConnection = Depends(get_connection)
) -> LoginResponse:
    """A.1(1): create a client account. The caller then completes /profile."""
    email = body.email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ValidationError("Enter a valid email address.")
    full_name = " ".join(body.full_name.split())

    existing = (
        await conn.execute(
            sa.text("select 1 from app_user where lower(email) = :email"),
            {"email": email},
        )
    ).first()
    if existing:
        raise Conflict("An account with that email already exists. Sign in instead.")

    last_code = (
        await conn.execute(
            sa.text("select client_code from client order by client_code desc limit 1")
        )
    ).scalar_one_or_none()
    client_code = _next_client_code(last_code)

    user_id = (
        await conn.execute(
            sa.text(
                "insert into app_user (email, role, display_name)"
                " values (:email, 'client', :name) returning id"
            ),
            {"email": email, "name": full_name},
        )
    ).scalar_one()
    await conn.execute(
        sa.text("insert into app_credential (user_id, password_hash) values (:uid, :hash)"),
        {"uid": user_id, "hash": hasher.hash(body.password)},
    )
    client_id = (
        await conn.execute(
            sa.text("insert into client (user_id, client_code) values (:uid, :code) returning id"),
            {"uid": user_id, "code": client_code},
        )
    ).scalar_one()
    await conn.execute(
        sa.text(
            "insert into client_profile (client_id, full_name, email) values (:cid, :name, :email)"
        ),
        {"cid": client_id, "name": full_name, "email": email},
    )

    token, ttl = issue_token(user_id, "client", email)
    return LoginResponse(
        accessToken=token,
        expiresIn=ttl,
        role="client",
        displayName=full_name,
        clientId=str(client_id),
    )


@router.post("/auth/login", response_model=LoginResponse)
async def login(
    body: LoginRequest, conn: AsyncConnection = Depends(get_connection)
) -> LoginResponse:
    row = (
        (
            await conn.execute(
                sa.text(
                    "select a.id, cr.password_hash, a.role, a.display_name,"
                    "       c.id as client_id"
                    "  from app_user a"
                    "  left join app_credential cr on cr.user_id = a.id"
                    "  left join client c on c.user_id = a.id"
                    " where lower(a.email) = lower(:email)"
                ),
                {"email": body.email},
            )
        )
        .mappings()
        .first()
    )

    # One message for both "no such user" and "wrong password": distinguishing
    # them would confirm which accounts exist.
    invalid = Unauthorized("Email or password is incorrect.")
    if row is None or not row["password_hash"]:
        raise invalid
    try:
        hasher.verify(row["password_hash"], body.password)
    except (VerifyMismatchError, VerificationError) as exc:
        raise invalid from exc

    token, ttl = issue_token(row["id"], row["role"], body.email)
    return LoginResponse(
        accessToken=token,
        expiresIn=ttl,
        role=row["role"],
        displayName=row["display_name"],
        clientId=str(row["client_id"]) if row["client_id"] else None,
    )


@router.get("/auth/me", response_model=MeResponse)
async def me(
    principal: Principal = Depends(get_principal),
    conn: AsyncConnection = Depends(get_connection),
) -> MeResponse:
    code = None
    if principal.client_id:
        code = (
            await conn.execute(
                sa.select(m.client.c.client_code).where(m.client.c.id == principal.client_id)
            )
        ).scalar_one_or_none()
    return MeResponse(
        userId=str(principal.user_id),
        role=principal.role,
        displayName=principal.display_name,
        clientId=str(principal.client_id) if principal.client_id else None,
        clientCode=code,
    )
