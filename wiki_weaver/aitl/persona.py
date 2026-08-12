"""wiki_weaver.aitl.persona -- load and structurally validate the AITL
proxy's character document.

LOCATION: ``lens/persona.md``. Already named in docs/DESIGN.md Sec 2's
layer list ("persona.md -- how the proxy answers on our behalf") and
already referenced by pipeline/init.dot's ``write_schema`` box node prompt
("(2) lens/persona.md describing the proxy persona for this wiki's
human-or-proxy gates"). This module is the first thing that actually reads
it. It lives in ``lens/`` (not a new top-level directory) because it is the
same category of thing as ``lens/canon/`` and ``lens/corrections/``:
human-authored, precedence-bearing intent about this specific wiki -- see
DESIGN.md Sec 4's precedence model. Persona differs from canon/corrections
in WHAT it governs (how gates get answered, not what facts are true), not
in WHERE it lives or WHO owns it.

FORMAT -- the five-slot template named in docs/DESIGN.md Sec 7's guardrails
("Satisfaction predicate lives in the persona, not the harness -- the
five-slot template: identity, capability floor, hard constraint, refusal
script, what 'done' means"). Required as ``##``-or-deeper markdown headings,
in any order, each with real (non-empty) body text:

    ## Identity
    Who this proxy stands in for; this wiki's stated purpose.

    ## Capability floor
    What the proxy may decide on its own vs. what it must always refuse.

    ## Hard constraints
    Things it must always do / must never do, regardless of the gate.

    ## Refusal script
    The conditions and (optionally) the exact wording for declining to
    answer a gate.

    ## What "done" means
    How the proxy knows an answer is sufficient to act on -- the
    satisfaction predicate a backend uses to decide "answer" vs "give_up".

Section TEXT is deliberately not validated here (that would require
judgment -- an LLM's job, not a deterministic check's). Structural
completeness is: this is cheap, free, and catches the most common failure
(a stub or half-written persona) before spending a single token.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# (human-readable label, normalized substring that must appear in some heading)
REQUIRED_SECTIONS: tuple[tuple[str, str], ...] = (
    ("Identity", "identity"),
    ("Capability floor", "capability floor"),
    ("Hard constraints", "hard constraint"),
    ("Refusal script", "refusal script"),
    ('What "done" means', "what done means"),
)

DEFAULT_PERSONA_TEMPLATE = """# Persona

## Identity

<Who this proxy stands in for. What is this wiki for?>

## Capability floor

<What the proxy may decide on its own. What it must always refuse and
leave for a real human.>

## Hard constraints

<Things the proxy must always do, or must never do, regardless of the
gate it is answering.>

## Refusal script

<When and how the proxy declines to answer -- e.g. "if you cannot point
to a specific sentence in this document that justifies the answer,
decline rather than guess."

## What "done" means

<How the proxy knows an answer is sufficient to act on.>
"""


def _normalize(text: str) -> str:
    return _NON_ALNUM_RE.sub(" ", text.lower()).strip()


def _headings(text: str) -> list[str]:
    return [_normalize(m.group(1)) for m in _HEADING_RE.finditer(text)]


@dataclass(frozen=True)
class PersonaResult:
    """Result of loading ``lens/persona.md``.

    ``ok=False`` means "do not answer from this" -- callers must fail loud,
    never fall back to a best-effort read of ``text`` (which is empty on
    failure, precisely so an ``ok`` check can never accidentally be
    skipped).
    """

    ok: bool
    path: Path
    text: str = ""
    reason: str = ""
    missing_sections: tuple[str, ...] = field(default_factory=tuple)


def persona_path(wiki_root: str | Path) -> Path:
    return Path(wiki_root) / "lens" / "persona.md"


def load_persona(wiki_root: str | Path) -> PersonaResult:
    """Load and structurally validate ``lens/persona.md``.

    A DETERMINISTIC check -- no model call. Refuses (``ok=False``) rather
    than returning a partial/best-effort persona whenever the document is
    missing, empty, or missing a required section: a proxy that answers
    from an unverified character document is exactly the invented-answer
    failure mode this whole feature exists to prevent.
    """
    path = persona_path(wiki_root)

    if not path.is_file():
        return PersonaResult(
            ok=False,
            path=path,
            reason=f"{path} does not exist -- no character document to answer from",
        )

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return PersonaResult(ok=False, path=path, reason=f"{path} is empty")

    present = _headings(text)
    missing = [label for label, needle in REQUIRED_SECTIONS if not any(needle in h for h in present)]
    if missing:
        return PersonaResult(
            ok=False,
            path=path,
            reason=(
                f"{path} is missing required section(s): {', '.join(missing)} "
                "-- see wiki_weaver.aitl.persona.REQUIRED_SECTIONS"
            ),
            missing_sections=tuple(missing),
        )

    return PersonaResult(ok=True, path=path, text=text)
