"""Build identity stamped onto every decision (C.6).

`prompt_version` combines the commit with a hash of the prompt templates, so
editing a template without committing still produces a distinguishable version --
otherwise two materially different decisions could claim the same provenance.
"""

from __future__ import annotations

import functools
import hashlib
import pathlib
import subprocess

PROMPTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "prompts"


@functools.lru_cache(maxsize=1)
def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=PROMPTS_DIR.parent,
        )
        return out.stdout.strip() or "nogit"
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return "nogit"


@functools.lru_cache(maxsize=1)
def prompt_templates() -> dict[str, str]:
    """All prompt templates, read once at startup."""
    if not PROMPTS_DIR.is_dir():
        return {}
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(PROMPTS_DIR.glob("*.md"))}


#: Suffixes that mark a prompt as an alternative of another, not a prompt in its
#: own right: `system_minimal.md` is a variant of `system.md`.
VARIANT_SUFFIXES = ("minimal",)


def _resolved_templates() -> dict[str, str]:
    """The templates actually in use, after applying `PROMPT_VARIANT`.

    Variant files are resolved here rather than at the call site so that
    `prompt_version` covers what was really sent. With the default variant the
    digest is taken over exactly the same base files as before, so promptVersion
    is unchanged; switching variant changes it, which is what makes a D.1(2)
    before/after capture self-documenting instead of relying on memory.
    """
    from app.core.config import get_settings

    everything = prompt_templates()
    base = {
        name: body
        for name, body in everything.items()
        if not any(name.endswith(f"_{suffix}") for suffix in VARIANT_SUFFIXES)
    }
    variant = get_settings().prompt_variant
    if variant != "full":
        for name in list(base):
            alternative = everything.get(f"{name}_{variant}")
            if alternative is not None:
                base[name] = alternative
    return base


def prompt_version() -> str:
    digest = hashlib.sha256()
    for name, body in sorted(_resolved_templates().items()):
        digest.update(name.encode())
        digest.update(body.encode())
    return f"{git_sha()}-{digest.hexdigest()[:8]}"


def template(name: str) -> str:
    try:
        return _resolved_templates()[name]
    except KeyError:  # pragma: no cover - a missing template is a packaging bug
        raise FileNotFoundError(f"prompt template {name!r} not found in {PROMPTS_DIR}") from None
