"""wiki_weaver.ingest.retract -- F4 (GOAL-followups.md): a gate answerer's
bounded, auditable way to remove or absorb an existing page. Covers the
directive parser (extract_retract_directive), the mutation+ledger+commit
core (retract_page), the CLI, and -- critically -- that a retract ledger
row can never be mistaken for a source's own accept/skip/quarantine/
declined decision (no ``source_id`` key -- see module docstring)."""

from __future__ import annotations

import json

import pytest

from wiki_weaver.ingest import retract
from wiki_weaver.lib import WikiRoot, already_ledgered, ledgered_source_ids

# ---------------------------------------------------------------------------
# extract_retract_directive -- parsing
# ---------------------------------------------------------------------------


def test_extract_retract_delete_directive():
    directive, remaining = retract.extract_retract_directive("RETRACT: joe-njenga.md")
    assert directive == retract.RetractDirective(page="joe-njenga.md", mode="delete", target=None)
    assert remaining == ""


def test_extract_absorb_directive():
    directive, remaining = retract.extract_retract_directive("ABSORB: joe-njenga.md INTO: agent-permissions.md")
    assert directive == retract.RetractDirective(page="joe-njenga.md", mode="absorb", target="agent-permissions.md")
    assert remaining == ""


def test_extract_directive_case_insensitive_and_wiki_prefixed():
    directive, _ = retract.extract_retract_directive("absorb: wiki/joe-njenga.md into: wiki/agent-permissions.md")
    assert directive == retract.RetractDirective(page="joe-njenga.md", mode="absorb", target="agent-permissions.md")


def test_extract_directive_preserves_surrounding_guidance_text():
    text = "Emphasize the caching tradeoffs section.\nRETRACT: joe-njenga.md\nAlso mention the retry budget."
    directive, remaining = retract.extract_retract_directive(text)
    assert directive == retract.RetractDirective(page="joe-njenga.md", mode="delete", target=None)
    assert "caching tradeoffs" in remaining
    assert "retry budget" in remaining
    assert "RETRACT" not in remaining


def test_extract_no_directive_returns_none_and_original_text():
    text = "Just emphasize the caching tradeoffs section."
    directive, remaining = retract.extract_retract_directive(text)
    assert directive is None
    assert remaining == text


def test_extract_rejects_retract_with_into_clause_as_ambiguous():
    with pytest.raises(retract.RetractDirectiveError, match="ambiguous"):
        retract.extract_retract_directive("RETRACT: joe-njenga.md INTO: agent-permissions.md")


def test_extract_rejects_absorb_without_into_clause():
    with pytest.raises(retract.RetractDirectiveError, match="missing its target"):
        retract.extract_retract_directive("ABSORB: joe-njenga.md")


def test_extract_rejects_more_than_one_directive_per_answer():
    text = "RETRACT: joe-njenga.md\nABSORB: other-page.md INTO: target.md"
    with pytest.raises(retract.RetractDirectiveError, match="exactly one page action"):
        retract.extract_retract_directive(text)


def test_extract_rejects_absorb_into_itself():
    with pytest.raises(retract.RetractDirectiveError, match="itself"):
        retract.extract_retract_directive("ABSORB: joe-njenga.md INTO: joe-njenga.md")


@pytest.mark.parametrize(
    "bad_name",
    [
        "../escape.md",
        "sub/dir.md",
        "no-extension",
        "..",
    ],
)
def test_extract_rejects_unsafe_or_malformed_page_names(bad_name):
    with pytest.raises(retract.RetractDirectiveError):
        retract.extract_retract_directive(f"RETRACT: {bad_name}")


# ---------------------------------------------------------------------------
# retract_page -- delete mode
# ---------------------------------------------------------------------------


def _seed_wiki_with_git(wiki_root) -> WikiRoot:
    wiki_root.add_page("index.md", "---\ntitle: Index\ntype: index\n---\n\n# Index\n")
    if pytest.importorskip("shutil").which("git"):
        wiki_root.init_git()
        wiki_root.commit_all("seed")
    return WikiRoot(wiki_root.root)


def test_retract_delete_removes_page_and_ledgers_distinct_decision(wiki_root):
    wr = _seed_wiki_with_git(wiki_root)
    wiki_root.add_page("joe-njenga.md", "---\ntitle: Joe Njenga\ntype: concept\n---\n\nA person.\n")

    directive = retract.RetractDirective(page="joe-njenga.md", mode="delete", target=None)
    record = retract.retract_page(wr, directive, requested_during_source_id="017-source.txt")

    assert not (wr.wiki_dir / "joe-njenga.md").exists()
    assert record["decision"] == "retract"
    assert record["mode"] == "delete"
    assert record["page"] == "joe-njenga.md"
    assert record["target_page"] is None
    assert record["requested_during_source_id"] == "017-source.txt"

    ledger_lines = wr.ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(ledger_lines) == 1
    on_disk = json.loads(ledger_lines[0])
    assert on_disk["decision"] == "retract"
    # THE distinction from every other decision value: never "source_id".
    assert "source_id" not in on_disk


def test_retract_delete_writes_log_entry(wiki_root):
    wr = _seed_wiki_with_git(wiki_root)
    wiki_root.add_page("joe-njenga.md", "# Joe Njenga\n")

    directive = retract.RetractDirective(page="joe-njenga.md", mode="delete", target=None)
    retract.retract_page(wr, directive)

    log_text = wr.log_path.read_text(encoding="utf-8")
    assert "retract | joe-njenga.md" in log_text
    assert "Removed outright" in log_text


def test_retract_delete_commits_when_git_available(wiki_root):
    if not pytest.importorskip("shutil").which("git"):
        pytest.skip("git not available")
    wr = _seed_wiki_with_git(wiki_root)
    wiki_root.add_page("joe-njenga.md", "# Joe Njenga\n")
    wiki_root.commit_all("add joe-njenga")

    directive = retract.RetractDirective(page="joe-njenga.md", mode="delete", target=None)
    retract.retract_page(wr, directive)

    import subprocess

    log = subprocess.run(
        ["git", "-C", str(wr.root), "log", "--oneline", "-1"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "retract" in log
    assert "joe-njenga.md" in log

    status = subprocess.run(
        ["git", "-C", str(wr.root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert status.strip() == ""  # everything committed, nothing left staged/dirty


def test_retract_delete_fails_loud_when_page_missing(wiki_root):
    wr = _seed_wiki_with_git(wiki_root)
    directive = retract.RetractDirective(page="does-not-exist.md", mode="delete", target=None)

    with pytest.raises(FileNotFoundError, match="does-not-exist.md"):
        retract.retract_page(wr, directive)

    assert not wr.ledger_path.exists()  # no partial ledger row on failure


# ---------------------------------------------------------------------------
# retract_page -- absorb mode
# ---------------------------------------------------------------------------


def test_retract_absorb_merges_content_and_removes_page(wiki_root):
    wr = _seed_wiki_with_git(wiki_root)
    wiki_root.add_page(
        "joe-njenga.md",
        "---\ntitle: Joe Njenga\ntype: concept\n---\n\nJoe argues per-tool permission scoping matters.\n",
    )
    wiki_root.add_page(
        "agent-permissions.md",
        "---\ntitle: Agent Permissions\ntype: argument\n---\n\nExisting argument body.\n",
    )

    directive = retract.RetractDirective(page="joe-njenga.md", mode="absorb", target="agent-permissions.md")
    record = retract.retract_page(wr, directive, requested_during_source_id="018-source.txt")

    assert not (wr.wiki_dir / "joe-njenga.md").exists()
    target_text = (wr.wiki_dir / "agent-permissions.md").read_text(encoding="utf-8")
    assert "Existing argument body." in target_text
    assert "Absorbed from joe-njenga.md" in target_text
    assert "Joe argues per-tool permission scoping matters." in target_text

    assert record["decision"] == "retract"
    assert record["mode"] == "absorb"
    assert record["target_page"] == "agent-permissions.md"


def test_retract_absorb_fails_loud_when_target_missing(wiki_root):
    wr = _seed_wiki_with_git(wiki_root)
    wiki_root.add_page("joe-njenga.md", "# Joe Njenga\n")

    directive = retract.RetractDirective(page="joe-njenga.md", mode="absorb", target="does-not-exist.md")

    with pytest.raises(FileNotFoundError, match="does-not-exist.md"):
        retract.retract_page(wr, directive)

    # Nothing mutated on failure -- fail loud, not a partial absorb.
    assert (wr.wiki_dir / "joe-njenga.md").exists()
    assert not wr.ledger_path.exists()


def test_retract_is_not_idempotent_second_call_fails_loud(wiki_root):
    """Deliberately NOT idempotent (unlike commit.py's accept/skip/
    quarantine/declined) -- a page already gone on a second invocation is
    a genuine error, never a silent no-op (see module docstring / F4's
    fail-loud requirement)."""
    wr = _seed_wiki_with_git(wiki_root)
    wiki_root.add_page("joe-njenga.md", "# Joe Njenga\n")
    directive = retract.RetractDirective(page="joe-njenga.md", mode="delete", target=None)

    retract.retract_page(wr, directive)
    with pytest.raises(FileNotFoundError):
        retract.retract_page(wr, directive)


# ---------------------------------------------------------------------------
# Blast-radius / bookkeeping isolation: a retract row must never be
# mistaken for a source's own ledger decision.
# ---------------------------------------------------------------------------


def test_retract_ledger_row_never_affects_source_segment_bookkeeping(wiki_root):
    wr = _seed_wiki_with_git(wiki_root)
    wiki_root.add_page("joe-njenga.md", "# Joe Njenga\n")

    # A real source is mid-flight (segment 1 of 2, not yet ledgered).
    directive = retract.RetractDirective(page="joe-njenga.md", mode="delete", target=None)
    retract.retract_page(wr, directive, requested_during_source_id="017-source.txt")

    # The retract row must not make select_source/commit.py think
    # "017-source.txt" has any ledgered decision of its own.
    assert "017-source.txt" not in ledgered_source_ids(wr.ledger_path)
    assert not already_ledgered(wr.ledger_path, "017-source.txt", segment_index=1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_delete_mode(wiki_root, capsys):
    wr = _seed_wiki_with_git(wiki_root)
    wiki_root.add_page("joe-njenga.md", "# Joe Njenga\n")

    code = retract.main(["--wiki-root", str(wr.root), "--page", "joe-njenga.md"])

    assert code == 0
    assert not (wr.wiki_dir / "joe-njenga.md").exists()
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["mode"] == "delete"


def test_cli_absorb_mode(wiki_root, capsys):
    wr = _seed_wiki_with_git(wiki_root)
    wiki_root.add_page("joe-njenga.md", "# Joe Njenga\n")
    wiki_root.add_page("agent-permissions.md", "# Agent Permissions\n\nBody.\n")

    code = retract.main(
        [
            "--wiki-root",
            str(wr.root),
            "--page",
            "joe-njenga.md",
            "--target",
            "agent-permissions.md",
            "--requested-during-source",
            "018-source.txt",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["mode"] == "absorb"
    assert payload["target_page"] == "agent-permissions.md"
    assert payload["requested_during_source_id"] == "018-source.txt"


def test_cli_fails_loud_on_missing_page(wiki_root, capsys):
    wr = _seed_wiki_with_git(wiki_root)

    code = retract.main(["--wiki-root", str(wr.root), "--page", "does-not-exist.md"])

    assert code == 1
    assert "does-not-exist.md" in capsys.readouterr().err


def test_cli_rejects_absorb_into_itself(wiki_root, capsys):
    wr = _seed_wiki_with_git(wiki_root)
    wiki_root.add_page("joe-njenga.md", "# Joe Njenga\n")

    code = retract.main(["--wiki-root", str(wr.root), "--page", "joe-njenga.md", "--target", "joe-njenga.md"])

    assert code == 1
    assert "itself" in capsys.readouterr().err
    assert (wr.wiki_dir / "joe-njenga.md").exists()  # untouched -- rejected before any mutation
