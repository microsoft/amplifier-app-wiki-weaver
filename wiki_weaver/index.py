"""wiki-weaver corpus index builder and query layer.

Public API
----------
build_indexes(corpus_dir)           scan corpus/*.md and materialise 5 JSON
                                    indexes under <corpus>/.wiki/index/
resolve_alias(alias, alias_decls)   resolve an alias through declared map;
                                    raises CycleDetectedError on cycles
query_backlinks(corpus_dir, page)   pages that link to *page*
query_graph_neighbors(corpus_dir,   immediate out/in neighbours of *page*
    page)
query_tags(corpus_dir, tag)         pages tagged with *tag* (None → all tags)
query_properties(corpus_dir, page)  frontmatter k/v for *page*
query_resolve_citation(corpus_dir,  map page citation ordinal → source record
    page, n)

Errors
------
WikiIndexError          base
SchemaVersionError      index file uses an unexpected schema_version
PageNotFound            slug not found in the corpus
CitationNotFound        ordinal n out of range on page
CycleDetectedError      alias resolution detected a cycle

Design notes
------------
- Indexes live at <corpus>/.wiki/index/*.json, each wrapped in:
  {"schema_version":1,"built":{...},"data":{...}}
- Staleness: compare current max(mtime of corpus/*.md) to built.max_mtime.
  Tools always return data + stale flag; they never refuse a stale read.
- Frontmatter parser is a lightweight inline implementation (no pyyaml dep).
  Handles: inline lists [a, b], quoted strings, integers, booleans, plain strings.
- Wikilink regex is re-declared here (same pattern as validate_wiki.WIKILINK)
  to avoid a cross-layer import; both parse [[target|display]] correctly.
- resolve_alias is the single shared function used by both the index builder
  and query tools — tie-breaking and cycle detection live in exactly one place.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Constants ───────────────────────────────────────────────────────────────

EXPECTED_SCHEMA_VERSION: int = 1
_INDEX_SUBDIR = ".wiki/index"

# ── Errors ───────────────────────────────────────────────────────────────────


class WikiIndexError(Exception):
    """Base class for all wiki-index errors."""


class SchemaVersionError(WikiIndexError):
    """Index file uses a schema_version this code cannot read."""

    def __init__(self, index_name: str, found: int | None, expected: int) -> None:
        super().__init__(
            f"{index_name}: schema_version {found!r} != expected {expected!r}"
        )
        self.index_name = index_name
        self.found = found
        self.expected = expected


class PageNotFound(WikiIndexError):
    """Slug not found in the corpus."""

    def __init__(self, page: str) -> None:
        super().__init__(f"page not found: {page!r}")
        self.page = page


class CitationNotFound(WikiIndexError):
    """Citation ordinal n is out of range on the given page."""

    def __init__(self, page: str, n: int) -> None:
        super().__init__(f"citation {n} not found on page {page!r}")
        self.page = page
        self.n = n


class CycleDetectedError(WikiIndexError):
    """Alias resolution detected a cycle (any length, including self-loops)."""

    def __init__(self, chain: list[str]) -> None:
        super().__init__("alias cycle detected: " + " -> ".join(chain))
        self.chain = chain


# ── Parsing helpers ──────────────────────────────────────────────────────────

# Same pattern as pipeline/validate_wiki.py WIKILINK — [[target]] or [[target|display]]
# or [[target#section|display]].  Group 1 is the raw target (before | or #).
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?]]")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _slug(name: str) -> str:
    """Normalise a page name / link target to a comparison key.

    Matches the slug() function in pipeline/validate_wiki.py — single source
    of truth for slug semantics across the repo.
    """
    return name.strip().lower().replace(" ", "-").replace("_", "-")


def _parse_yaml_value(v: str) -> Any:
    """Parse a simple YAML scalar or inline list.

    Handles:
    - Inline list: [a, b, c]  or  [1, 2, 3]
    - Quoted string: "foo"  or  'bar'
    - Integer: 42
    - Boolean: true / false
    - Plain string: anything else
    """
    v = v.strip()
    # Inline list: [...]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        items: list[Any] = []
        for item in inner.split(","):
            item = item.strip().strip("\"'")
            try:
                items.append(int(item))
            except ValueError:
                items.append(item)
        return items
    # Quoted string
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1]
    # Integer
    try:
        return int(v)
    except ValueError:
        pass
    # Boolean
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    # Plain string (includes empty)
    return v


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Extract YAML frontmatter from markdown text into a dict.

    Returns {} when no frontmatter block is present.  Handles simple YAML
    (scalars, inline lists) — no multi-line values or anchors.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    result: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip().lower()
        result[key] = _parse_yaml_value(raw.strip())
    return result


def _body(text: str) -> str:
    """Return the markdown text with the frontmatter block stripped."""
    m = _FRONTMATTER_RE.match(text)
    return text[m.end() :] if m else text


def _extract_links(text: str) -> list[str]:
    """Return slugified wikilink targets from the body of a page (not frontmatter)."""
    return [_slug(t) for t in _WIKILINK_RE.findall(_body(text))]


# ── Alias resolution ─────────────────────────────────────────────────────────


def resolve_alias(alias: str, alias_decls: dict[str, str]) -> str:
    """Walk the alias_decls chain from *alias* to its terminal slug.

    Algorithm (spec §3):
        seen = []
        cur  = alias
        while cur in alias_decls:
            if cur in seen: raise CycleDetectedError(seen + [cur])
            seen.append(cur)
            cur = alias_decls[cur]
        return cur

    Raises CycleDetectedError for any cycle length (A→A, A→B→A, etc.).
    The terminal slug is returned as-is; callers decide if it's a real page.
    """
    seen: list[str] = []
    cur = alias
    while cur in alias_decls:
        if cur in seen:
            raise CycleDetectedError(seen + [cur])
        seen.append(cur)
        cur = alias_decls[cur]
    return cur


# ── Index I/O ─────────────────────────────────────────────────────────────────


def _write_index(index_dir: Path, name: str, data: Any, built: dict[str, Any]) -> None:
    """Atomically write a single index file (tmp-and-replace)."""
    envelope = {
        "schema_version": EXPECTED_SCHEMA_VERSION,
        "built": built,
        "data": data,
    }
    index_dir.mkdir(parents=True, exist_ok=True)
    path = index_dir / name
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_index(corpus_dir: Path, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read an index file and return (envelope, built_dict).

    Raises SchemaVersionError if the file's schema_version != EXPECTED_SCHEMA_VERSION.
    Raises FileNotFoundError if the index file does not exist (caller should handle
    with a helpful message directing the user to run build_indexes first).
    """
    path = corpus_dir / _INDEX_SUBDIR / name
    envelope: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    sv = envelope.get("schema_version")
    if sv != EXPECTED_SCHEMA_VERSION:
        raise SchemaVersionError(name, sv, EXPECTED_SCHEMA_VERSION)
    return envelope, envelope["built"]


def _is_stale(corpus_dir: Path, built: dict[str, Any]) -> bool:
    """Return True if any corpus *.md is newer than the recorded max_mtime."""
    pages = list(corpus_dir.glob("*.md"))
    if not pages:
        return False
    current_max = max(p.stat().st_mtime for p in pages)
    return current_max > built.get("max_mtime", 0.0)


def _built_iso(built: dict[str, Any]) -> str:
    """Return the build timestamp as ISO-8601 (falls back to epoch on missing key)."""
    ts = built.get("built_at", "")
    return ts if ts else datetime.fromtimestamp(0, tz=timezone.utc).isoformat()


# ── Build helpers ─────────────────────────────────────────────────────────────


def _compute_content_hash(pages: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(pages, key=lambda x: x.name):
        h.update(p.name.encode("utf-8"))
        h.update(p.read_bytes())
    return "sha256:" + h.hexdigest()


def _get_weave_commit(corpus_dir: Path) -> str | None:
    """Try to get the short HEAD commit from git; return None on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=corpus_dir,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


# ── Public: index builder ─────────────────────────────────────────────────────


def build_indexes(corpus_dir: str | Path) -> None:
    """Scan corpus/*.md and materialise five JSON index files.

    Output: <corpus>/.wiki/index/{backlinks,links,tags,properties,aliases}.json
    Each file uses the canonical envelope::

        {
          "schema_version": 1,
          "built": {
            "max_mtime": <float>,
            "content_hash": "sha256:<hex>",
            "weave_commit": "<short-sha>|null",
            "built_at": "<iso8601>"
          },
          "data": { ... }
        }

    Idempotent: re-running overwrites the indexes with fresh data.
    """
    corpus = Path(corpus_dir).expanduser().resolve()
    pages = sorted(corpus.glob("*.md"))

    # ── built metadata ───────────────────────────────────────────────────────
    max_mtime = max((p.stat().st_mtime for p in pages), default=0.0)
    content_hash = (
        _compute_content_hash(pages)
        if pages
        else "sha256:" + hashlib.sha256(b"").hexdigest()
    )
    built: dict[str, Any] = {
        "max_mtime": max_mtime,
        "content_hash": content_hash,
        "weave_commit": _get_weave_commit(corpus),
        "built_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
    }

    index_dir = corpus / ".wiki" / "index"

    # ── parse all pages ──────────────────────────────────────────────────────
    # page_data: slug -> {fm, links}
    page_data: dict[str, dict[str, Any]] = {}
    for p in pages:
        text = p.read_text(encoding="utf-8", errors="replace")
        fm = _parse_frontmatter(text)
        links = _extract_links(text)
        slug = _slug(p.stem)
        page_data[slug] = {"fm": fm, "links": links}

    known_slugs: set[str] = set(page_data.keys())

    # ── build alias_decls: alias → target page slug ──────────────────────────
    # Process in alphabetical slug order so duplicate tie-break is deterministic.
    alias_decls: dict[str, str] = {}
    for slug in sorted(page_data.keys()):
        raw_aliases = page_data[slug]["fm"].get("aliases", [])
        if isinstance(raw_aliases, str):
            raw_aliases = [raw_aliases]
        for alias_raw in raw_aliases:
            alias = _slug(str(alias_raw))
            if alias in alias_decls:
                existing = alias_decls[alias]
                winner = min(existing, slug)  # alphabetically-first slug wins
                if existing != winner:
                    warnings.warn(
                        f"Duplicate alias {alias!r}: declared by {existing!r} and "
                        f"{slug!r}; keeping {winner!r} (alphabetically first)",
                        UserWarning,
                        stacklevel=2,
                    )
                    alias_decls[alias] = winner
                else:
                    warnings.warn(
                        f"Duplicate alias {alias!r}: declared by {existing!r} and "
                        f"{slug!r}; keeping {existing!r} (alphabetically first)",
                        UserWarning,
                        stacklevel=2,
                    )
            else:
                alias_decls[alias] = slug

    # ── resolve all aliases: collect good, broken, cycle ────────────────────
    resolved_aliases: dict[str, str] = {}
    broken_alias_list: list[dict[str, str]] = []
    cycle_chains: list[list[str]] = []
    seen_cycle_sets: set[frozenset[str]] = set()

    for alias in sorted(alias_decls.keys()):
        try:
            terminal = resolve_alias(alias, alias_decls)
            if terminal in known_slugs:
                resolved_aliases[alias] = terminal
            else:
                broken_alias_list.append({"alias": alias, "target": terminal})
        except CycleDetectedError as exc:
            node_set: frozenset[str] = frozenset(exc.chain)
            if node_set not in seen_cycle_sets:
                seen_cycle_sets.add(node_set)
                cycle_chains.append(exc.chain)

    # ── full resolution map for link target lookup ───────────────────────────
    # page slugs resolve to themselves; resolved aliases override if present
    resolution_map: dict[str, str] = {s: s for s in known_slugs}
    resolution_map.update(resolved_aliases)

    # ── backlinks + links graph ───────────────────────────────────────────────
    backlinks: dict[str, list[str]] = {s: [] for s in known_slugs}
    links_out: dict[str, list[str]] = {s: [] for s in known_slugs}
    broken_links: list[dict[str, str]] = []

    for slug, info in page_data.items():
        seen_targets: set[str] = set()
        for raw_target in info["links"]:
            # Deduplicate: don't count the same (source→target) pair twice
            resolved = resolution_map.get(raw_target)
            if resolved is not None:
                pair = (slug, resolved)
                if pair not in seen_targets:
                    seen_targets.add(pair)  # type: ignore[arg-type]
                    if slug not in backlinks[resolved]:
                        backlinks[resolved].append(slug)
                    if resolved not in links_out[slug]:
                        links_out[slug].append(resolved)
            else:
                # Target doesn't resolve to any known page
                entry = {"from": slug, "target": raw_target}
                if entry not in broken_links:
                    broken_links.append(entry)

    # ── aliases.json data ─────────────────────────────────────────────────────
    aliases_data: dict[str, Any] = dict(resolved_aliases)
    aliases_data["_broken"] = broken_links  # unresolved wikilinks (no page found)
    aliases_data["_cycles"] = cycle_chains  # detected alias cycles

    # ── tags.json data ────────────────────────────────────────────────────────
    tags: dict[str, list[str]] = {}
    for slug, info in page_data.items():
        raw_tags = info["fm"].get("tags", [])
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        for tag in raw_tags:
            tag_str = str(tag).strip()
            if tag_str:
                tags.setdefault(tag_str, [])
                if slug not in tags[tag_str]:
                    tags[tag_str].append(slug)
    tags_data: dict[str, list[str]] = {
        tag: sorted(slugs) for tag, slugs in sorted(tags.items())
    }

    # ── links.json + backlinks.json data ─────────────────────────────────────
    links_data: dict[str, dict[str, list[str]]] = {
        slug: {
            "out": sorted(links_out[slug]),
            "in": sorted(backlinks[slug]),
        }
        for slug in known_slugs
    }
    backlinks_data: dict[str, list[str]] = {
        slug: sorted(bls) for slug, bls in backlinks.items()
    }

    # ── properties.json data ──────────────────────────────────────────────────
    properties_data: dict[str, dict[str, Any]] = {
        slug: dict(info["fm"]) for slug, info in page_data.items()
    }

    # ── write all five indexes ────────────────────────────────────────────────
    _write_index(index_dir, "backlinks.json", backlinks_data, built)
    _write_index(index_dir, "links.json", links_data, built)
    _write_index(index_dir, "tags.json", tags_data, built)
    _write_index(index_dir, "properties.json", properties_data, built)
    _write_index(index_dir, "aliases.json", aliases_data, built)


# ── Public: query functions ────────────────────────────────────────────────────
# Each reads its index, asserts schema_version, checks staleness, and returns
# the domain result wrapped in the common envelope:
#   { ...<result>..., "stale": bool, "built": "<iso8601>" }


def query_backlinks(corpus_dir: str | Path, page: str) -> dict[str, Any]:
    """Return pages that link to *page*.

    Returns::

        {
          "backlinks": [{"slug": "<slug>", "title": "<title-if-available>"}],
          "stale": bool,
          "built": "<iso8601>"
        }

    Raises PageNotFound if the slug is not in the index.
    """
    corpus = Path(corpus_dir).expanduser().resolve()
    envelope, built = _read_index(corpus, "backlinks.json")
    data: dict[str, list[str]] = envelope["data"]
    slug = _slug(page)
    if slug not in data:
        raise PageNotFound(page)
    bls = [{"slug": s, "title": s} for s in data[slug]]
    return {
        "backlinks": bls,
        "stale": _is_stale(corpus, built),
        "built": _built_iso(built),
    }


def query_graph_neighbors(corpus_dir: str | Path, page: str) -> dict[str, Any]:
    """Return immediate outbound and inbound neighbours of *page*.

    Returns::

        {
          "out": ["<slug>", ...],
          "in":  ["<slug>", ...],
          "stale": bool,
          "built": "<iso8601>"
        }

    No depth parameter — immediate neighbours only (spec §2).
    Raises PageNotFound if the slug is not in the index.
    """
    corpus = Path(corpus_dir).expanduser().resolve()
    envelope, built = _read_index(corpus, "links.json")
    data: dict[str, dict[str, list[str]]] = envelope["data"]
    slug = _slug(page)
    if slug not in data:
        raise PageNotFound(page)
    node = data[slug]
    return {
        "out": node["out"],
        "in": node["in"],
        "stale": _is_stale(corpus, built),
        "built": _built_iso(built),
    }


def query_tags(corpus_dir: str | Path, tag: str | None = None) -> dict[str, Any]:
    """Return pages for a specific *tag*, or a tag→count summary when tag is None.

    tag=None returns::

        { "tag": None, "tags": {"<tag>": <count>, ...}, "stale": bool, "built": ... }

    tag="<name>" returns::

        { "tag": "<name>", "pages": [{"slug": s, "title": s}, ...], "stale": ..., "built": ... }
    """
    corpus = Path(corpus_dir).expanduser().resolve()
    envelope, built = _read_index(corpus, "tags.json")
    data: dict[str, list[str]] = envelope["data"]
    stale = _is_stale(corpus, built)
    iso = _built_iso(built)
    if tag is None:
        return {
            "tag": None,
            "tags": {t: len(slugs) for t, slugs in data.items()},
            "stale": stale,
            "built": iso,
        }
    pages = [{"slug": s, "title": s} for s in data.get(tag, [])]
    return {"tag": tag, "pages": pages, "stale": stale, "built": iso}


def query_properties(corpus_dir: str | Path, page: str) -> dict[str, Any]:
    """Return all frontmatter properties for *page*.

    Returns::

        { "slug": "<slug>", "properties": {<key>: <value>, ...}, "stale": bool, "built": ... }

    Raises PageNotFound if the slug is not in the index.
    """
    corpus = Path(corpus_dir).expanduser().resolve()
    envelope, built = _read_index(corpus, "properties.json")
    data: dict[str, dict[str, Any]] = envelope["data"]
    slug = _slug(page)
    if slug not in data:
        raise PageNotFound(page)
    return {
        "slug": slug,
        "properties": data[slug],
        "stale": _is_stale(corpus, built),
        "built": _built_iso(built),
    }


def query_resolve_citation(corpus_dir: str | Path, page: str, n: int) -> dict[str, Any]:
    """Map page + citation ordinal n (1-based) to the source record.

    Citation model (spec §2 + §8):
    - The page's frontmatter ``sources`` field is a list of source IDs
      (e.g. ``sources: [1, 3]``).
    - Ordinal *n* (1-based) indexes into that list.
    - The source record is looked up by id in ``<corpus>/.sources.json``.

    Returns::

        {
          "source": {
            "id": <int>,
            "slug": "<slug>",
            "path": "_sources/<filename>",
            "title": "<title or filename>",
            "url": "<url or null>"
          },
          "stale": bool,
          "built": "<iso8601>"
        }

    Raises PageNotFound, CitationNotFound.
    Uses backlinks.json for the stale/built envelope (indexes share one build).
    """
    corpus = Path(corpus_dir).expanduser().resolve()

    # Get stale/built from backlinks.json (shared build metadata)
    _, built = _read_index(corpus, "backlinks.json")
    stale = _is_stale(corpus, built)
    iso = _built_iso(built)

    # Read the page frontmatter directly
    slug = _slug(page)
    page_file = corpus / f"{slug}.md"
    if not page_file.exists():
        raise PageNotFound(page)
    fm = _parse_frontmatter(page_file.read_text(encoding="utf-8", errors="replace"))
    sources_field = fm.get("sources", [])
    if isinstance(sources_field, int):
        sources_field = [sources_field]
    elif not isinstance(sources_field, list):
        sources_field = []

    if n < 1 or n > len(sources_field):
        raise CitationNotFound(page, n)
    source_id = int(sources_field[n - 1])

    # Look up the source record in .sources.json
    registry_path = corpus / ".sources.json"
    if not registry_path.exists():
        raise CitationNotFound(page, n)
    registry: dict[str, Any] = json.loads(registry_path.read_text(encoding="utf-8"))
    for entry in registry.get("sources", []):
        if int(entry.get("id", -1)) == source_id:
            filename = entry.get("filename", f"source{source_id}.md")
            derived_slug = _slug(Path(filename).stem)
            return {
                "source": {
                    "id": source_id,
                    "slug": derived_slug,
                    "path": f"_sources/{filename}",
                    "title": entry.get("title", filename),
                    "url": entry.get("url"),
                },
                "stale": stale,
                "built": iso,
            }

    raise CitationNotFound(page, n)
