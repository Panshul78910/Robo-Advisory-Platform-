"""`ENUM_VALUES` in models.py must match the enum types in the migration.

The values are duplicated out of necessity: SQLAlchemy cannot decode a Postgres
enum column without them, and the migration is the DDL's source of truth. A
divergence is invisible until a row is read at runtime, so it is checked here.
"""

from __future__ import annotations

import pathlib
import re

from app.db.models import ENUM_VALUES

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "001_extensions.sql"
)

CREATE_TYPE = re.compile(
    r"create\s+type\s+(\w+)\s+as\s+enum\s*\((.*?)\)\s*;", re.IGNORECASE | re.DOTALL
)


def parse_migration_enums() -> dict[str, tuple[str, ...]]:
    sql = MIGRATION.read_text(encoding="utf-8")
    return {name: tuple(re.findall(r"'([^']+)'", body)) for name, body in CREATE_TYPE.findall(sql)}


def test_the_migration_actually_declares_enums():
    """Guards the regex itself -- an empty parse would make every test vacuous."""
    assert len(parse_migration_enums()) >= 10


def test_every_migration_enum_is_declared_in_models():
    missing = set(parse_migration_enums()) - set(ENUM_VALUES)
    assert not missing, f"models.py is missing enum type(s): {sorted(missing)}"


def test_no_model_enum_is_invented():
    extra = set(ENUM_VALUES) - set(parse_migration_enums())
    assert not extra, f"models.py declares enum type(s) the migration does not: {sorted(extra)}"


def test_members_match_exactly_and_in_order():
    """Order matters: Postgres compares enums by declaration order."""
    for name, expected in parse_migration_enums().items():
        assert ENUM_VALUES[name] == expected, (
            f"{name} differs.\n  migration: {expected}\n  models.py: {ENUM_VALUES[name]}"
        )
