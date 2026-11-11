"""Enforce invariant 4: only app/core/clock.py reads the wall clock.

Domain code takes a session date. A stray `date.today()` in a pricing path would
make results depend on when the demo runs, which is exactly what the session-date
abstraction exists to prevent.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"\b(?:datetime\.now|date\.today|utcnow|time\.time)\s*\(")

ALLOWED = {ROOT / "app" / "core" / "clock.py"}
SEARCH_DIRS = ("app", "scripts")
SKIP_NAMES = {"check_clock.py"}


def main() -> int:
    violations: list[str] = []
    for directory in SEARCH_DIRS:
        for path in sorted((ROOT / directory).rglob("*.py")):
            if path in ALLOWED or path.name in SKIP_NAMES:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if line.lstrip().startswith("#"):
                    continue
                if PATTERN.search(line):
                    rel = path.relative_to(ROOT).as_posix()
                    violations.append(f"{rel}:{lineno}: {line.strip()}")

    if violations:
        print("Wall-clock access outside app/core/clock.py:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(
            "\nUse WallClock for audit timestamps, or take a session date as a "
            "parameter for anything domain-related.",
            file=sys.stderr,
        )
        return 1

    print("clock check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
