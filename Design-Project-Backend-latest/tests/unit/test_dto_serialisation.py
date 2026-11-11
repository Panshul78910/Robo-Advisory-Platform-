"""No Decimal may reach the frontend as a JSON string.

Pydantic renders a bare `Decimal` as `"0"`, not `0`. The frontend would then
have to parse it, and JavaScript arithmetic on an unparsed string silently
produces concatenation rather than addition. Every money, unit, price and weight
field must therefore be declared `Money` or `OptMoney`.
"""

from __future__ import annotations

import decimal
import importlib
import inspect
import json
import pkgutil
import typing
from decimal import Decimal

import pytest

from app.api.dto.common import Dto

D = Decimal


def all_dto_classes() -> list[type[Dto]]:
    """Every Dto subclass, including those declared inside routers."""
    import app.api

    found: dict[str, type[Dto]] = {}
    for info in pkgutil.walk_packages(app.api.__path__, prefix="app.api."):
        module = importlib.import_module(info.name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Dto) and obj is not Dto:
                found[f"{obj.__module__}.{obj.__name__}"] = obj
    return sorted(found.values(), key=lambda c: f"{c.__module__}.{c.__name__}")


def _mentions_decimal(annotation) -> bool:
    if annotation is Decimal:
        return True
    return any(_mentions_decimal(a) for a in typing.get_args(annotation))


def test_the_scan_finds_the_dtos():
    """Guards the discovery itself -- an empty list would make this vacuous."""
    classes = all_dto_classes()
    assert len(classes) >= 10
    assert any(c.__name__ == "HoldingRowDto" for c in classes)


@pytest.mark.parametrize("dto", all_dto_classes(), ids=lambda c: f"{c.__name__}")
def test_no_decimal_field_serialises_as_a_string(dto):
    decimal_fields = [
        name for name, field in dto.model_fields.items() if _mentions_decimal(field.annotation)
    ]
    if not decimal_fields:
        pytest.skip("no Decimal fields")

    # Build the model with a distinctive value in every field it will accept.
    values = {}
    for name, field in dto.model_fields.items():
        if name in decimal_fields:
            values[name] = D("12.34")
        elif field.is_required():
            values[name] = _placeholder(field.annotation)
    instance = dto.model_validate(values)

    payload = json.loads(instance.model_dump_json(by_alias=True))
    offenders = [
        key for key, value in payload.items() if isinstance(value, str) and _looks_numeric(value)
    ]
    assert not offenders, (
        f"{dto.__name__} serialises {offenders} as JSON string(s). "
        "Declare them `Money` or `OptMoney` from app.api.dto.common."
    )


def _looks_numeric(value: str) -> bool:
    try:
        decimal.Decimal(value)
    except decimal.InvalidOperation:
        return False
    return True


def _placeholder(annotation):
    """Smallest value satisfying a required field, so the model can be built."""
    import datetime as _dt

    origin = typing.get_origin(annotation)
    if origin in (list, set, tuple):
        return []
    if origin is dict:
        return {}
    for candidate, value in (
        (bool, True),
        (int, 0),
        (str, "x"),
        (_dt.date, _dt.date(2026, 1, 5)),
    ):
        if annotation is candidate:
            return value
    args = [a for a in typing.get_args(annotation) if a is not type(None)]
    if args:
        return _placeholder(args[0])
    return "x"


def test_money_and_optmoney_produce_numbers():
    from app.api.dto.common import Money, OptMoney

    class Sample(Dto):
        required: Money
        optional: OptMoney = None

    payload = json.loads(Sample(required=D("52.11")).model_dump_json())
    assert payload["required"] == 52.11
    assert isinstance(payload["required"], float)
    assert payload["optional"] is None
