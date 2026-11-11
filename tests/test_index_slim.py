"""The index the brief and the writer read: index.md without the lines for source summary
pages and history volumes - lines that grow with every source, for pages neither model
picks. Shapes copied from the batch-19 index (`[share]` labels hold brackets)."""

from __future__ import annotations

from pathlib import Path

from dotparse import nodes
from test_steps import fake_brief, step

from wiki_weaver.index_slim import slim_index

ROOT = Path(__file__).resolve().parent.parent
INDEX = (
    "# Index\n\n"
    "## Initiatives\n\n"
    "- [Pibipesov](pibipesov.md) \u00b7 2026-05-07 to 2026-10-12 \u00b7 The bema's fapos makubupu.\n"
    "- [Giriden, Mim](history/giriden--2026-05-08--2026-05-18.md) \u00b7 a history zunolo\n"
    "- [Giriden notes](history/giriden-notes.md) \u00b7 luzir history/, not a zunolo's zaga\n"
    "- [Fovusekifo](fovusekifo.md) \u00b7 2026-07-08 to 2026-10-12 \u00b7 The tazele sevasi.\n"
    "\n## Weekly\n\n"
    "- [Weekly commitments and blockers](weekly-commitments.md) \u00b7 2026-05-07 to "
    "2026-10-12 \u00b7 Week by week.\n"
    "- [Ralog weeks](weekly-commitments--2026-05-07--2026-05-31-2.md) \u00b7 a zunolo by zaga\n"
    "\n## Sources\n\n"
    "- [Pibipesov bema chat (2026-05-07)](source-pibipesov-bema-chat-2026-05-07.md) \u00b7 s1 "
    "\u00b7 2026-05-07 \u00b7 Mebibivop.\n"
    "- [Bema Lagit - Kafobuvovo [liniz] (2026-09-22)](source-bema-lagit-kafobuvovo-liniz-"
    "2026-09-22-0f1e2d3c4b.md) \u00b7 s1343 \u00b7 2026-09-22 \u00b7 Sopivuf.\n"
    "- [Voma Zole Galen do [liniz] (2026-09-29)](./source-voma-zole-galen-do-2026-09-29.md) "
    "\u00b7 s1369 \u00b7 2026-09-29 \u00b7 Ligo.\n"
)
SLIM = (
    "# Index\n\n"
    "## Initiatives\n\n"
    "- [Pibipesov](pibipesov.md) \u00b7 2026-05-07 to 2026-10-12 \u00b7 The bema's fapos makubupu.\n"
    "- [Fovusekifo](fovusekifo.md) \u00b7 2026-07-08 to 2026-10-12 \u00b7 The tazele sevasi.\n"
    "\n## Weekly\n\n"
    "- [Weekly commitments and blockers](weekly-commitments.md) \u00b7 2026-05-07 to "
    "2026-10-12 \u00b7 Week by week.\n"
    "\n"
)


def test_drops_source_and_history_lines():
    assert slim_index(INDEX) == SLIM


def test_drops_empty_group_heading():
    slim = slim_index(INDEX)
    assert "## Sources" not in slim and "## Weekly" in slim
    # a heading still holding more than blank lines stays, and so does its text
    notes = "## Notes\n\nSources live in the ledger.\n- [Chat](source-chat.md) \u00b7 s1\n"
    assert slim_index(notes) == "## Notes\n\nSources live in the ledger.\n"


def test_keeps_topic_lines_and_order_byte_for_byte():
    topics = (
        "# Index\r\n\r\n## People\r\n\r\n"
        "- [Kip](non.md) \u00b7 zugutet  \r\n"
        "- [Arlo](arlo.md)\r\n"
        "\r\n## Notes\r\n\r\nSee [the lens](lens.md); [s1] tolen a source, not a page.\r\n"
        "- [Regu](regu.md)"
    )
    assert slim_index(topics) == topics


def test_multiline_item_continuations_go_with_item():
    # Not in the batch-19 index, whose items are one line each; a model may wrap one.
    text = (
        "## Pages\n\n"
        "- [Gizo chat](source-gizo-chat.md) \u00b7 s9 \u00b7 a summary that\n"
        "  nozul nola a sopusa nilo.\n"
        "- [Kip](non.md) \u00b7 zugutet, a summary that\n"
        "  reze nozul.\n"
    )
    assert slim_index(text) == (
        "## Pages\n\n- [Kip](non.md) \u00b7 zugutet, a summary that\n  reze nozul.\n"
    )


# ---------------------------------------------------------------- the steps and the prompt


def test_brief_reads_slim_copy(corpus: Path, tmp_path: Path):
    (corpus / "index.md").write_text(INDEX)
    assert step(corpus, "select", str(tmp_path / "run"), "0", "-") == (0, "source")
    assert step(corpus, "assemble") == (0, "ok")
    assert (corpus / ".wiki/work/index.md").read_text() == SLIM
    prompt = nodes(ROOT / "pipeline" / "ingest.dot")["brief"]["prompt"]
    assert (
        "Read .wiki/work/index.md if it exists: the wiki's pages; source summary and history "
        "pages are left out."
    ) in prompt
    assert "Read index.md" not in prompt and "already in index.md" not in prompt


def test_assemble_without_an_index_writes_no_copy(corpus: Path, tmp_path: Path):
    assert not (corpus / "index.md").exists()
    assert step(corpus, "select", str(tmp_path / "run"), "0", "-") == (0, "source")
    assert step(corpus, "assemble") == (0, "ok")
    assert not (corpus / ".wiki/work/index.md").exists()


def test_pages_md_index_is_slim(corpus: Path, tmp_path: Path):
    (corpus / "index.md").write_text(INDEX)
    assert step(corpus, "select", str(tmp_path / "run"), "0", "-") == (0, "source")
    assert step(corpus, "assemble") == (0, "ok")
    fake_brief(corpus, ["amplifier"])
    assert step(corpus, "page_select") == (0, "ok")
    assert (corpus / ".wiki/work/pages.md").read_text().endswith("\n\n# index.md\n\n" + SLIM)


def test_recover_ok_with_slim_index_scratch(corpus: Path):
    work = corpus / ".wiki/work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "index.md").write_text(SLIM)  # left by a run, with no journal
    assert step(corpus, "recover") == (0, "clean")
    assert not (work / "index.md").exists()
    # any other scratch with no journal still stops recovery, as before
    work.mkdir(parents=True, exist_ok=True)
    (work / "index.md").write_text(SLIM)
    (work / "context.md").write_text("context")
    assert step(corpus, "recover")[0] == 1 and (work / "context.md").exists()
