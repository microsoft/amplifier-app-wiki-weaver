"""Round-trip tests for the wiki-weaver index builder and query tools.

Fixture: eval/fixtures/wiki-min/ (6 pages + .sources.json + expected.json)

Coverage (spec §6):
  - build_indexes materialises five .wiki/index/*.json files in the fixture dir
  - backlinks tool: actual output == ground truth
  - links graph tool: spot-check neighbours
  - tags tool: actual output == ground truth
  - properties tool: frontmatter round-trips correctly
  - alias resolution: a-overview→alpha, b-overview→beta (ground truth)
  - alias cycle detection: resolving loop-a raises CycleDetectedError with the
    exact chain ["loop-a", "loop-b", "loop-a"] — never hangs
  - broken-link recording: delta→nonexistent recorded in aliases._broken
  - citation: wiki_resolve_citation("beta", 1) → source id 1

Safety:
  - build_indexes writes only under .wiki/index/ (no corpus modification)
  - test uses tmp_path copy so the committed fixture stays pristine

No LLM, no engine, no Amplifier runtime required.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repo-root path plumbing (mirrors existing eval/*.py convention)
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from wiki_weaver.index import (  # noqa: E402
    EXPECTED_SCHEMA_VERSION,
    CycleDetectedError,
    PageNotFound,
    build_indexes,
    query_backlinks,
    query_graph_neighbors,
    query_properties,
    query_resolve_citation,
    query_tags,
    resolve_alias,
)

# Private helpers are importable for testing — convention in Python.
# They are used only to construct alias_decls for the direct cycle test.
from wiki_weaver.index import _parse_frontmatter, _slug  # noqa: E402, PLC2701

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "wiki-min"
_EXPECTED = json.loads((_FIXTURE_DIR / "expected.json").read_text(encoding="utf-8"))


@pytest.fixture()
def wiki(tmp_path: Path) -> Path:
    """Copy the fixture corpus into a temp dir so tests don't pollute the repo."""
    dest = tmp_path / "wiki-min"
    shutil.copytree(_FIXTURE_DIR, dest)
    build_indexes(dest)
    return dest


# ---------------------------------------------------------------------------
# Helper: read a built index file
# ---------------------------------------------------------------------------


def _read_data(wiki_dir: Path, name: str) -> dict:
    path = wiki_dir / ".wiki" / "index" / name
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == EXPECTED_SCHEMA_VERSION, (
        f"{name}: unexpected schema_version {raw['schema_version']!r}"
    )
    return raw["data"]


# ---------------------------------------------------------------------------
# Test: index files are created with correct envelope
# ---------------------------------------------------------------------------


def test_index_files_exist_with_correct_envelope(wiki: Path) -> None:
    """All five index files exist and carry the correct schema_version."""
    for name in (
        "backlinks.json",
        "links.json",
        "tags.json",
        "properties.json",
        "aliases.json",
    ):
        path = wiki / ".wiki" / "index" / name
        assert path.exists(), f"{name} was not created"
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == EXPECTED_SCHEMA_VERSION, name
        assert "built" in raw, name
        assert "data" in raw, name
        built = raw["built"]
        assert "max_mtime" in built, name
        assert "content_hash" in built, name
        assert built["content_hash"].startswith("sha256:"), name
        assert "built_at" in built, name


# ---------------------------------------------------------------------------
# Test: backlinks match ground truth
# ---------------------------------------------------------------------------


def test_backlinks_match_ground_truth(wiki: Path) -> None:
    """wiki_backlinks() returns the exact backlinks declared in expected.json."""
    expected_bl = _EXPECTED["backlinks"]

    for page, expected_sources in expected_bl.items():
        result = query_backlinks(wiki, page)

        # The result must not be stale (we just built the indexes)
        assert result["stale"] is False, f"{page}: stale=True right after build"

        actual_slugs = sorted(item["slug"] for item in result["backlinks"])
        assert actual_slugs == sorted(expected_sources), (
            f"backlinks[{page!r}]: got {actual_slugs}, expected {sorted(expected_sources)}"
        )


# ---------------------------------------------------------------------------
# Test: full backlinks data object equals ground truth
# ---------------------------------------------------------------------------


def test_backlinks_index_equals_ground_truth(wiki: Path) -> None:
    """The raw backlinks.json data dict equals expected.json['backlinks']."""
    data = _read_data(wiki, "backlinks.json")
    expected = _EXPECTED["backlinks"]
    for slug, expected_bls in expected.items():
        assert slug in data, f"backlinks.json missing slug {slug!r}"
        assert sorted(data[slug]) == sorted(expected_bls), (
            f"backlinks.json[{slug!r}]: got {data[slug]!r}, expected {expected_bls!r}"
        )


# ---------------------------------------------------------------------------
# Test: tags match ground truth
# ---------------------------------------------------------------------------


def test_tags_match_ground_truth(wiki: Path) -> None:
    """wiki_tags() returns the exact tag→pages mapping in expected.json."""
    expected_tags = _EXPECTED["tags"]

    # Tag summary (no tag argument)
    summary = query_tags(wiki)
    assert summary["tag"] is None
    assert summary["stale"] is False
    for tag, expected_slugs in expected_tags.items():
        assert summary["tags"].get(tag) == len(expected_slugs), (
            f"tag_summary[{tag!r}]: got {summary['tags'].get(tag)}, "
            f"expected {len(expected_slugs)}"
        )

    # Per-tag lookup
    for tag, expected_slugs in expected_tags.items():
        result = query_tags(wiki, tag)
        assert result["tag"] == tag
        actual_slugs = sorted(item["slug"] for item in result["pages"])
        assert actual_slugs == sorted(expected_slugs), (
            f"tags[{tag!r}]: got {actual_slugs}, expected {sorted(expected_slugs)!r}"
        )


# ---------------------------------------------------------------------------
# Test: tags index equals ground truth
# ---------------------------------------------------------------------------


def test_tags_index_equals_ground_truth(wiki: Path) -> None:
    """The raw tags.json data dict equals expected.json['tags']."""
    data = _read_data(wiki, "tags.json")
    expected = _EXPECTED["tags"]
    assert dict(data) == {tag: sorted(slugs) for tag, slugs in expected.items()}, (
        f"tags.json data mismatch.\nActual:   {data}\nExpected: {expected}"
    )


# ---------------------------------------------------------------------------
# Test: link graph neighbours
# ---------------------------------------------------------------------------


def test_graph_neighbors_alpha(wiki: Path) -> None:
    """alpha links OUT to [beta, gamma]; receives links IN from [beta]."""
    result = query_graph_neighbors(wiki, "alpha")
    assert result["stale"] is False
    assert sorted(result["out"]) == ["beta", "gamma"], f"alpha out: {result['out']!r}"
    assert result["in"] == ["beta"], f"alpha in: {result['in']!r}"


def test_graph_neighbors_gamma(wiki: Path) -> None:
    """gamma has no out-links; receives a link from alpha."""
    result = query_graph_neighbors(wiki, "gamma")
    assert result["out"] == []
    assert result["in"] == ["alpha"]


# ---------------------------------------------------------------------------
# Test: properties round-trip
# ---------------------------------------------------------------------------


def test_properties_alpha(wiki: Path) -> None:
    """Properties for alpha include type, tags, aliases, sources."""
    result = query_properties(wiki, "alpha")
    assert result["slug"] == "alpha"
    props = result["properties"]
    assert props.get("type") == "concept"
    assert "x" in props.get("tags", [])
    assert "a-overview" in props.get("aliases", [])


def test_properties_page_not_found(wiki: Path) -> None:
    """Querying a non-existent page raises PageNotFound."""
    with pytest.raises(PageNotFound):
        query_properties(wiki, "nonexistent-page-xyz")


# ---------------------------------------------------------------------------
# Test: alias resolution matches ground truth
# ---------------------------------------------------------------------------


def test_aliases_index_equals_ground_truth(wiki: Path) -> None:
    """Resolved aliases in aliases.json match expected.json['aliases']."""
    data = _read_data(wiki, "aliases.json")
    expected = _EXPECTED["aliases"]
    for alias, expected_target in expected.items():
        assert data.get(alias) == expected_target, (
            f"aliases[{alias!r}]: got {data.get(alias)!r}, expected {expected_target!r}"
        )


# ---------------------------------------------------------------------------
# Test: broken-link recording
# ---------------------------------------------------------------------------


def test_broken_links_recorded(wiki: Path) -> None:
    """delta→nonexistent is recorded in aliases.json._broken."""
    data = _read_data(wiki, "aliases.json")
    broken = data.get("_broken", [])
    expected_broken = _EXPECTED["broken_links"]

    assert len(broken) >= len(expected_broken), (
        f"_broken should have at least {len(expected_broken)} entry, got {broken!r}"
    )

    # Verify each expected broken link is present
    for expected_entry in expected_broken:
        found = any(
            entry.get("from") == expected_entry["from"]
            and entry.get("target") == expected_entry["target"]
            for entry in broken
        )
        assert found, (
            f"Expected broken link {expected_entry!r} not found in _broken={broken!r}"
        )


# ---------------------------------------------------------------------------
# Test: cycle detection — MUST raise CycleDetectedError with exact chain
# ---------------------------------------------------------------------------


def _build_alias_decls_from_corpus(corpus_dir: Path) -> dict[str, str]:
    """Reconstruct the alias_decls map from frontmatter (mirrors build_indexes logic)."""
    alias_decls: dict[str, str] = {}
    for p in sorted(corpus_dir.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        slug = _slug(p.stem)
        for alias_raw in fm.get("aliases", []):
            alias = _slug(str(alias_raw))
            if alias not in alias_decls:
                alias_decls[alias] = slug
    return alias_decls


def test_cycle_detection_raises_with_exact_chain(wiki: Path) -> None:
    """resolve_alias("loop-a") raises CycleDetectedError(chain=["loop-a","loop-b","loop-a"])."""
    alias_decls = _build_alias_decls_from_corpus(wiki)

    with pytest.raises(CycleDetectedError) as exc_info:
        resolve_alias("loop-a", alias_decls)

    chain = exc_info.value.chain
    assert chain == ["loop-a", "loop-b", "loop-a"], (
        f"Expected cycle chain ['loop-a','loop-b','loop-a'], got {chain!r}"
    )


def test_cycle_detection_loop_b_also_raises(wiki: Path) -> None:
    """resolve_alias("loop-b") also raises CycleDetectedError (never hangs)."""
    alias_decls = _build_alias_decls_from_corpus(wiki)
    with pytest.raises(CycleDetectedError):
        resolve_alias("loop-b", alias_decls)


def test_cycles_recorded_in_index(wiki: Path) -> None:
    """The aliases index _cycles list contains the loop-a/loop-b cycle chain."""
    data = _read_data(wiki, "aliases.json")
    cycles = data.get("_cycles", [])
    expected_cycles = _EXPECTED["cycles"]

    # We expect at least one cycle recorded
    assert len(cycles) >= 1, f"Expected at least one cycle, got {cycles!r}"

    # The first expected cycle must appear verbatim
    expected_chain = expected_cycles[0]
    assert expected_chain in cycles, (
        f"Expected cycle chain {expected_chain!r} not in _cycles={cycles!r}"
    )


# ---------------------------------------------------------------------------
# Test: citation resolution
# ---------------------------------------------------------------------------


def test_resolve_citation_beta_1(wiki: Path) -> None:
    """wiki_resolve_citation('beta', 1) returns source id=1 from .sources.json."""
    result = query_resolve_citation(wiki, "beta", 1)

    assert result["stale"] is False
    source = result["source"]
    assert source["id"] == 1, f"Expected source id 1, got {source['id']!r}"
    # URL and other fields come from the fixture .sources.json
    assert source.get("url") == "https://example.com/source1"


def test_citation_out_of_range_raises(wiki: Path) -> None:
    """Ordinal 2 on beta (only 1 source) raises CitationNotFound."""
    from wiki_weaver.index import CitationNotFound

    with pytest.raises(CitationNotFound):
        query_resolve_citation(wiki, "beta", 2)


def test_citation_no_sources_raises(wiki: Path) -> None:
    """Ordinal 1 on alpha (sources:[]) raises CitationNotFound."""
    from wiki_weaver.index import CitationNotFound

    with pytest.raises(CitationNotFound):
        query_resolve_citation(wiki, "alpha", 1)


# ---------------------------------------------------------------------------
# Test: staleness detection
# ---------------------------------------------------------------------------


def test_stale_flag_after_corpus_modification(wiki: Path, tmp_path: Path) -> None:
    """After touching a corpus file, stale=True is returned without re-building."""
    import time

    # Ensure mtime advances (some filesystems have 1s resolution)
    time.sleep(0.01)
    new_page = wiki / "newcomer.md"
    new_page.write_text(
        "---\ntitle: Newcomer\ntype: note\nsources: []\n---\n\nNew page.\n",
        encoding="utf-8",
    )

    result = query_backlinks(wiki, "alpha")
    assert result["stale"] is True, (
        "Expected stale=True after adding a page without rebuilding"
    )
