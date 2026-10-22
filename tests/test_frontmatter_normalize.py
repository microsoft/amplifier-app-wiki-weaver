"""Written pages' frontmatter is normalized before checks run.

A writer that copies a title such as "Ada : Bo Planning" into `title:` unquoted produces
YAML that yaml.safe_load rejects, and the frontmatter check then holds the source. V4
re-serializes such frontmatter with yaml.safe_dump from a tolerant key: value read, and
leaves frontmatter that already parses untouched. The check itself is unchanged.
Model-free.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from test_steps import ORCHARD, cite, fake_brief, step

from wiki_weaver import checks as ck
from wiki_weaver.sources import normalize_frontmatter, split_frontmatter

BODY = "\n# Heading\n\n- a claim\n"


def fm_page(title_line: str, extra: str = "") -> str:
    return (
        f"---\n{title_line}\ntype: source\nsources:\n  - Ada - Bo Planning__rec.md\n"
        f"last_updated: 2026-05-19\n{extra}---\n{BODY}"
    )


TITLES = [
    # (raw title line as a writer might emit it, the title it means)
    ("title: Ada : Bo Planning", "Ada : Bo Planning"),
    ("title: Ada: Bo Planning", "Ada: Bo Planning"),
    ("title: Sync: #3 notes: planning", "Sync: #3 notes: planning"),
    ("title: Retro : #4 what went well", "Retro : #4 what went well"),
    ("title: - Planning: draft", "- Planning: draft"),
    ('title: "Ada" : Bo Planning', '"Ada" : Bo Planning'),
    ("title: O'Brien: weekly", "O'Brien: weekly"),
    ('title: "unterminated: quote', '"unterminated: quote'),
]


@pytest.mark.parametrize(("line", "title"), TITLES)
def test_unparseable_title_is_requoted_and_passes_the_check(line: str, title: str):
    text = fm_page(line)
    assert split_frontmatter(text)[0] is None  # the bug: safe_load rejects it
    assert ck.check_frontmatter("p.md", text) == ["p.md: missing or unparseable frontmatter"]
    out = normalize_frontmatter(text)
    fm, _ = split_frontmatter(out)
    assert fm == {
        "title": title,
        "type": "source",
        "sources": ["Ada - Bo Planning__rec.md"],
        "last_updated": yaml.safe_load("2026-05-19"),
    }
    assert out.endswith("---\n" + BODY)  # the body is byte-for-byte unchanged
    assert ck.check_frontmatter("p.md", out) == []


def test_frontmatter_that_parses_is_left_byte_for_byte():
    for text in (
        fm_page('title: "Ada : Bo Planning"'),
        fm_page("title: Plain title"),
        fm_page("title: Issue #42"),  # parses (as "Issue"); not ours to rewrite
        "---\ntitle: x\n---\nbody\n",
    ):
        assert normalize_frontmatter(text) == text


def test_no_frontmatter_or_unreadable_lines_are_left_alone():
    assert normalize_frontmatter("# No frontmatter\n") == "# No frontmatter\n"
    weird = "---\ntitle: Ada : Bo\n  continued: [oops\n?? not a pair\n---\nbody\n"
    assert normalize_frontmatter(weird) == weird  # cannot read it -> check still fails
    assert ck.check_frontmatter("p.md", weird) == ["p.md: missing or unparseable frontmatter"]


def test_list_items_and_values_keep_their_types():
    text = (
        '---\ntitle: A : B\ntype: topics\nsources:\n  - "x: y.md"\n  - plain: item.md\n'
        "  - z.md\nlast_updated: 2026-05-19\ntags: [a, b]\ncount: 3\n---\nbody\n"
    )
    fm, body = split_frontmatter(normalize_frontmatter(text))
    assert fm is not None
    assert fm["title"] == "A : B" and fm["sources"] == ["x: y.md", "plain: item.md", "z.md"]
    assert fm["tags"] == ["a", "b"] and fm["count"] == 3
    assert str(fm["last_updated"]) == "2026-05-19" and body == "body\n"


def test_an_empty_key_followed_by_no_items_stays_empty():
    text = "---\ntitle: A : B\nsources:\ntype: x\n---\n"
    fm, _ = split_frontmatter(normalize_frontmatter(text))
    assert fm == {"title": "A : B", "sources": None, "type": "x"}


def test_steps_normalize_the_written_summary_page_before_checks(corpus: Path, tmp_path: Path):
    """The held-source shape: the writer copies a "X : Y" meeting title into the summary
    page's frontmatter unquoted. Checks now pass and the committed page parses."""
    run = tmp_path / "run"
    step(corpus, "select", str(run), "0", "-")
    step(corpus, "assemble")
    fake_brief(corpus, [])
    assert step(corpus, "page_select") == (0, "ok")
    summary = (corpus / ".wiki/work/selected.txt").read_text().split()[0]
    q = cite(corpus, "The orchard sensor rollout ships on Friday")
    (corpus / summary).write_text(
        f"---\ntitle: Orchard : Sync Planning\ntype: source\nsources:\n  - {ORCHARD}\n"
        f"last_updated: 2031-03-02\n---\n# Orchard : Sync Planning\n\n- Rollout Friday {q}\n"
    )
    assert step(corpus, "checks") == (0, "pass")
    fm, body = split_frontmatter((corpus / summary).read_text())
    assert fm is not None and fm["title"] == "Orchard : Sync Planning"
    assert body.startswith("# Orchard : Sync Planning\n")
    assert step(corpus, "commit", str(run)) == (0, "next")
    row = json.loads((corpus / ".wiki/.processed.jsonl").read_text().splitlines()[-1])
    assert row["status"] == "converged" and not row.get("failed_checks")
