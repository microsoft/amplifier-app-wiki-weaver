"""wiki_weaver.aitl.corrections -- load standing corrections
(``lens/corrections/``) into the AITL proxy's reasoning context.

THE GAP THIS CLOSES: ``weave`` (``pipeline/ingest.dot``) already reads
EVERY file under ``lens/corrections/`` on every pass -- its prompt says
explicitly "standing corrections in lens/corrections/ must not be silently
reintroduced." The AITL proxy (``wiki_weaver.aitl.proxy_interviewer
.ProxyInterviewer``), however, has only ever read ``lens/persona.md``. A
correction filed via ``pipeline/correct.dot`` (``wiki_weaver.correct
.persist_lens``'s durable output) therefore fixed the wiki page (effect 1)
but had NO path to inform any FUTURE proxy gate decision (effect 2) --
exactly the gap a human reported: "this feedback should make future
agent-in-the-loop decisions better informed," and it structurally could
not, because the proxy never read the file the fix was written to.

DESIGN CHOICE -- extend what the proxy reads; do NOT route corrections
into ``lens/persona.md``:

1. ``lens/persona.md`` is a small, structurally-checked, five-slot document
   (``wiki_weaver.aitl.persona.REQUIRED_SECTIONS``): identity, capability
   floor, hard constraints, refusal script, what "done" means. A
   correction is none of those five things -- it is dated evidence, not
   character. Folding an unbounded, growing set of corrections into one of
   those five sections would either violate the template (which is section
   text is deterministically checked for, not free growth) or force
   ``persist_lens`` to rewrite ``persona.md`` on every correction -- two
   independent writers of the same file (a human editing their persona by
   hand, and this pipeline appending to it programmatically) is exactly the
   kind of shared-mutable-state drift risk this project's own case studies
   warn against.
2. ``lens/corrections/`` is ALREADY the single source of truth for this
   content -- ``persist_lens.py`` writes there, ``weave`` already reads
   from there. Reading the SAME directory for the proxy keeps exactly ONE
   durable location per fact, instead of two copies (persona.md and
   corrections/) that could silently diverge.
3. "Corrections are text, re-read every run, not training" (the design's
   own explicit rejection of a training pipeline --
   ``pipeline/CLI-CONTRACT.md``) is satisfied literally: this loader
   re-reads ``lens/corrections/`` from disk on every single gate call,
   never caches across calls, never mutates ``persona.md`` or any model
   weight.

SCOPE: every correction file is loaded and included in FULL, regardless of
its recorded ``scope:`` frontmatter (``page`` vs ``general`` --
``wiki_weaver.correct.persist_lens.build_correction_content``) -- mirroring
``weave``'s own behavior: it also reads the whole directory unfiltered on
every pass, because a page-scoped correction may still be exactly what a
later, unrelated-looking source needs to respect. The frontmatter
distinction exists for a human auditing ``lens/corrections/`` (or a future
``wiki-weaver review``), not to filter what the proxy is shown.

FAIL-SOFT, NOT FAIL-LOUD: unlike ``lens/persona.md`` (whose absence blocks
EVERY gate -- ``wiki_weaver.aitl.persona.load_persona``), a missing or
empty ``lens/corrections/`` directory is a normal, common state (no
correction has ever been filed for this wiki) and must NOT block a gate. A
single malformed/unreadable correction file is skipped (best-effort) rather
than failing the whole load -- one bad file must never silently take down
visibility of every OTHER correction, and must never block a gate that has
nothing to do with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wiki_weaver.lib import WikiRoot

# Same discipline as build_catalog.py's MAX_CATALOG_BYTES: never let an
# unbounded corpus of corrections silently balloon the proxy's prompt.
# Truncation is explicit and visible (a trailing marker), never silent.
MAX_CORRECTIONS_BYTES = 20_000


@dataclass(frozen=True)
class CorrectionsResult:
    """``text`` is ``""`` (not ``None``) when there is nothing to load -- an
    empty result is a normal, common state, not a failure. ``count`` is how
    many correction files were actually included (post-truncation);
    ``truncated`` is ``True`` iff at least one correction had to be dropped
    to stay within the byte budget."""

    text: str
    count: int
    truncated: bool


def _read_correction(path: Path) -> str | None:
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return content or None


def load_corrections(wiki_root: str | Path, max_bytes: int = MAX_CORRECTIONS_BYTES) -> CorrectionsResult:
    """Read every ``*.md`` under ``lens/corrections/``, stable-ordered
    (alphabetical by filename -- the filename is a content hash, so this
    ordering carries no meaning beyond determinism, but determinism itself
    matters: the same corrections directory must always render the same
    prompt text, for the same reason ``build_catalog``'s stable ordering
    does).

    A missing directory, or one with zero readable files, is NOT an error
    -- returns an empty result. See module docstring for the fail-soft
    contract this implements.
    """
    corrections_dir = WikiRoot(wiki_root).corrections_dir
    if not corrections_dir.is_dir():
        return CorrectionsResult(text="", count=0, truncated=False)

    paths = sorted(p for p in corrections_dir.glob("*.md") if p.is_file())
    blocks: list[str] = []
    used_bytes = 0
    included = 0
    truncated = False
    for path in paths:
        content = _read_correction(path)
        if content is None:
            continue  # unreadable/empty file -- skip, never fail the whole load
        block = f"### Correction ({path.stem})\n\n{content}\n"
        block_bytes = len(block.encode("utf-8"))
        if used_bytes + block_bytes > max_bytes:
            truncated = True
            break
        blocks.append(block)
        used_bytes += block_bytes
        included += 1

    text = "\n".join(blocks)
    if truncated:
        text += f"\n... TRUNCATED: additional correction(s) omitted (exceeded {max_bytes} bytes) ...\n"
    return CorrectionsResult(text=text, count=included, truncated=truncated)
