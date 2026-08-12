"""wiki_weaver.aitl.persona -- structural validation of lens/persona.md.

Deterministic, no-LLM checks: missing/empty/structurally incomplete personas
must fail loud (ok=False) BEFORE anything downstream ever asks a model to
reason from them.
"""

from __future__ import annotations

from pathlib import Path

from wiki_weaver.aitl.persona import load_persona, persona_path

FIXTURES = Path(__file__).parent / "fixtures"


def _install(wiki_root: Path, fixture_name: str) -> None:
    text = (FIXTURES / fixture_name).read_text(encoding="utf-8")
    target = persona_path(wiki_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_missing_persona_fails_loud(tmp_path):
    result = load_persona(tmp_path)

    assert result.ok is False
    assert result.text == ""
    assert "does not exist" in result.reason


def test_empty_persona_fails_loud(tmp_path):
    path = persona_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("   \n\n  ", encoding="utf-8")

    result = load_persona(tmp_path)

    assert result.ok is False
    assert "empty" in result.reason


def test_structurally_incomplete_persona_fails_loud(tmp_path):
    _install(tmp_path, "persona-missing-sections.md")

    result = load_persona(tmp_path)

    assert result.ok is False
    assert "Refusal script" in result.reason
    assert "Refusal script" in result.missing_sections
    assert 'What "done" means' in result.missing_sections
    # Sections that WERE present must not be reported missing.
    assert "Identity" not in result.missing_sections


def test_valid_persona_loads(tmp_path):
    _install(tmp_path, "persona-valid.md")

    result = load_persona(tmp_path)

    assert result.ok is True
    assert result.reason == ""
    assert result.missing_sections == ()
    assert "on-call" in result.text


def test_heading_match_is_order_and_depth_insensitive(tmp_path):
    """Sections may appear in any order and at any heading depth -- only
    their presence (by normalized substring) is required."""
    text = (
        '### What "Done" Means\nCite the document.\n\n'
        "# Hard Constraints\nNever guess.\n\n"
        "## refusal script\nDecline if ungrounded.\n\n"
        "## Capability Floor\nRoutine calls only.\n\n"
        "## IDENTITY\nStands in for the team.\n"
    )
    path = persona_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

    result = load_persona(tmp_path)

    assert result.ok is True
