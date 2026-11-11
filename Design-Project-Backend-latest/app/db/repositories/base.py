"""Principal-scoped repository base.

Invariant 5, and the structural half of A.1's access control: a repository is
constructed with a Principal and binds that principal's client id itself. No
public method takes a client id, so there is no path by which an LLM-produced or
request-body-supplied identifier can reach a query -- persuading the model to ask
about another client cannot succeed, because the id is not expressible.

Admin access is a different class (`AdminRepository`), reachable only through the
`require_admin` dependency.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.auth import Principal
from app.core.errors import Forbidden


class ClientScopedRepository:
    def __init__(self, conn: AsyncConnection, principal: Principal):
        if principal.client_id is None:
            raise Forbidden("This data is scoped to client accounts.")
        self._conn = conn
        self._principal = principal
        self.__client_id = principal.client_id

    @property
    def _client_id(self) -> uuid.UUID:
        """Read-only, set once at construction. Never a method parameter."""
        return self.__client_id


class AdminRepository:
    def __init__(self, conn: AsyncConnection, principal: Principal):
        if not principal.is_admin:
            raise Forbidden("Administrator access is required.")
        self._conn = conn
        self._principal = principal
