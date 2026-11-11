"""Invariant 5: a client id can never be passed into a repository.

This is the structural half of A.1's access control and the reason the D.4
cross-client exfiltration attack cannot succeed: there is no parameter through
which a different client id could be expressed, however the model is persuaded.
"""

from __future__ import annotations

import dataclasses
import inspect
import uuid

import pytest

from app.core.auth import Principal
from app.core.errors import Forbidden
from app.db.repositories.base import AdminRepository, ClientScopedRepository
from app.db.repositories.client import ClientRepository
from app.db.repositories.portfolio import PortfolioRepository

SCOPED_REPOSITORIES = [ClientRepository, PortfolioRepository]

#: Names that would let a caller choose whose data to read.
FORBIDDEN_PARAMS = {"client_id", "clientid", "client", "user_id", "userid", "owner_id", "principal"}


def public_methods(cls):
    for name, fn in inspect.getmembers(cls, predicate=inspect.isfunction):
        if not name.startswith("_"):
            yield name, fn


@pytest.mark.parametrize("repo_cls", SCOPED_REPOSITORIES, ids=lambda c: c.__name__)
def test_no_public_method_accepts_a_client_id(repo_cls):
    offenders = [
        f"{repo_cls.__name__}.{name}({param})"
        for name, fn in public_methods(repo_cls)
        for param in inspect.signature(fn).parameters
        if param.lower() in FORBIDDEN_PARAMS and param != "self"
    ]
    assert not offenders, (
        f"these methods let the caller choose the client, defeating principal scoping: {offenders}"
    )


@pytest.mark.parametrize("repo_cls", SCOPED_REPOSITORIES, ids=lambda c: c.__name__)
def test_scoped_repository_rejects_a_principal_without_a_client(repo_cls):
    admin = Principal(user_id=uuid.uuid4(), role="admin", display_name="Ops", client_id=None)
    with pytest.raises(Forbidden):
        repo_cls(conn=None, principal=admin)


def test_admin_repository_rejects_a_client_principal():
    client = Principal(
        user_id=uuid.uuid4(), role="client", display_name="Aaron", client_id=uuid.uuid4()
    )
    with pytest.raises(Forbidden):
        AdminRepository(conn=None, principal=client)


def test_bound_client_id_is_not_writable_from_outside():
    """Name mangling keeps the bound id out of reach of ordinary attribute writes."""
    cid = uuid.uuid4()
    principal = Principal(user_id=uuid.uuid4(), role="client", display_name="A", client_id=cid)
    repo = ClientScopedRepository(conn=None, principal=principal)

    assert repo._client_id == cid
    with pytest.raises(AttributeError):
        repo._client_id = uuid.uuid4()  # property has no setter
    assert repo._client_id == cid


def test_principal_is_immutable():
    principal = Principal(
        user_id=uuid.uuid4(), role="client", display_name="A", client_id=uuid.uuid4()
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        principal.client_id = uuid.uuid4()  # type: ignore[misc]
