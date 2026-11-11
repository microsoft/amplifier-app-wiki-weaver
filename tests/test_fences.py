"""A code fence never holds a section: a `## ` line inside one - backticks, tildes, or a fence
indented under a list item - is text, for every check that reads Current state or headings,
for the writer's input, and for the lens (wiki_weaver.sections)."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_steps import step

from wiki_weaver import checks as ck
from wiki_weaver import rollover, steps
from wiki_weaver.lens import PageType, parse_page_types

ROOT = Path(__file__).resolve().parent.parent
TYPES = parse_page_types((ROOT / "wiki_weaver" / "data" / "default_lens.md").read_text())
FM = "---\ntitle: Orchard\ntype: topics\nsources:\n  - a.md\nlast_updated: 2026-05-03\n---\n\n"
CS = "## Current state (as of 2026-05-02)\n\nAll fine.\n\n"
FENCES = {
    "backticks": "```markdown\n## Current state (as of 2026-05-01)\nwhere it stands\n```\n",
    "tildes": "~~~\n## Current state (as of 2026-05-01)\nwhere it stands\n~~~\n",
    "indented": "  ```markdown\n  ## Current state (as of 2026-05-01)\n  where it stands\n  ```\n",
}


def page(*body: str) -> str:
    return FM + "# Orchard\n\n" + CS + "## Decisions\n\n- **2026-05-01:** first.\n" + "".join(body)


def checks(tmp_path: Path, before: str, after: str) -> list[str]:
    """`run_page_checks` for one write of p.md, from ``before`` to ``after``."""
    wiki, prior = tmp_path / "wiki", tmp_path / "before"
    wiki.mkdir(exist_ok=True)
    prior.mkdir(exist_ok=True)
    (prior / "p.md").write_text(before)
    (wiki / "p.md").write_text(after)
    return ck.run_page_checks(wiki, ["p.md"], prior, {}, None, TYPES, known={})


@pytest.mark.parametrize("fence", list(FENCES.values()), ids=list(FENCES))
def test_a_fenced_current_state_never_hides_later_lines_from_the_loss_guard(
    tmp_path: Path, fence: str
):
    example = f"- **2026-05-02:** a template:\n\n{fence}"
    fifty = "\n" + "".join(f"- **2026-05-03:** decision {i}.\n" for i in range(50))
    assert checks(tmp_path, page(), page(example)) == []  # the example goes in
    assert checks(tmp_path, page(example), page(example, fifty)) == []  # fifty entries after it
    errs = checks(tmp_path, page(example, fifty), page(example))  # all fifty deleted
    assert [ck.check_kind(e) for e in errs] == ["content_loss"] and "lost 50/" in errs[0], errs


@pytest.mark.parametrize("fence", list(FENCES.values()), ids=list(FENCES))
def test_the_writer_input_strips_only_the_real_current_state(fence: str):
    example = f"- **2026-05-02:** a template:\n\n{fence}\n- **2026-05-03:** after the fence.\n"
    text = page(example)
    assert steps.strip_current_state(text) == text.replace(CS, "")


def test_current_state_checks_see_no_fenced_heading():
    draft = page("- **2026-05-02:** a draft:\n\n```markdown\n## Current state (draft)\n```\n")
    assert ck.check_current_state("p.md", draft) == []
    intro = FM + "# Orchard\n\n```markdown\n## Notes\n```\n\n" + CS + "## Decisions\n\n- x\n"
    assert ck.check_current_state("p.md", intro) == []
    real = FM + "# Orchard\n\n## Notes\n\nx\n\n" + CS + "## Decisions\n\n- x\n"
    assert ck.check_current_state("p.md", real) != []  # a real one before it counts


def test_a_fenced_heading_is_no_duplicate():
    text = page("- **2026-05-02:** the shape:\n\n```markdown\n## Decisions\n```\n")
    assert ck.check_duplicate_headings("p.md", text) == []
    assert ck.check_duplicate_headings("p.md", text + "\n## Decisions\n\n- again\n") != []


def test_a_fenced_heading_is_no_section_the_writer_added():
    types = {"topic": PageType("topics", sections=["Decisions"])}
    after = page("- **2026-05-02:** the shape:\n\n~~~\n## Notes\n~~~\n")
    assert ck.check_section_headings("p.md", page(), after, types) == []
    assert ck.check_section_headings("p.md", page(), page("\n## Notes\n\n- x\n"), types) != []


def test_history_and_the_current_state_size_ignore_fenced_headings():
    item = "- [2026-01-01 to 2026-01-31 \u00b7 3 entries](history/p--2026-01-01--2026-01-31.md)"
    text = page(f"- **2026-05-02:** how it reads:\n\n```markdown\n## History\n\n{item}\n```\n")
    assert not rollover.has_history(text)
    fenced_state = page(f"- **2026-05-02:** a template:\n\n{FENCES['backticks']}")
    assert ck.current_state_chars(fenced_state) == len("All fine.")


def test_the_lens_reads_no_page_types_from_a_fenced_example():
    example = "```markdown\n## Page types\n\n- **examples:** not a type\n```\n"
    lens = (
        f"# Lens\n\n## Purpose\n\nx\n\n{example}\n## Page types\n\n- **topics:** one per subject\n"
    )
    assert list(parse_page_types(lens)) == ["topic"]


def test_lens_check_wants_real_headings(tmp_path: Path):
    heads = ("Purpose", "What matters", "People", "Question shapes", "Page types")
    body = "".join(f"## {h}\n\nx\n\n" for h in heads)
    (tmp_path / "lens.md").write_text(body + "```markdown\n## Owner\n```\n")
    assert step(tmp_path, "lens_check") == (0, "bad")  # Owner is only in a code example
    (tmp_path / "lens.md").write_text(body + "## Owner\n\nme\n")
    assert step(tmp_path, "lens_check") == (0, "ok")
