"""wiki_weaver.lib -- shared internal library for the ingest subcommands.

Houses filesystem/path helpers, atomic writes, ledger I/O, frontmatter
parsing, wikilink extraction, the deterministic slice builder, and thin git
wrappers. Not a CLI subcommand itself -- every ``wiki_weaver.ingest.*``
module imports from here.

NOTE on naming (flagged in the delivery report): ``pipeline/CLI-CONTRACT.md``
names this shared module ``wiki_weaver.common``. The task that commissioned
this package explicitly asked for ``wiki_weaver/lib.py``. Followed the
explicit instruction; the discrepancy is cosmetic (an internal module name,
not part of the CLI surface any pipeline node depends on).

Standard library only -- see docs/IMPLEMENTATION_PHILOSOPHY (stdlib first)
and the task's explicit "no third-party deps" instruction.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from wiki_weaver import bm25

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LedgerCorruptError(Exception):
    """``ledger.jsonl`` exists but at least one line fails to parse as JSON.

    Per CLI-CONTRACT.md: fail loud, never silently treat a corrupt ledger as
    "nothing pending" (that would look like a successful, empty drain and
    mask real data loss).
    """


# ---------------------------------------------------------------------------
# argparse ``type=`` helper for in-context-counter / --param CLI arguments
# ---------------------------------------------------------------------------
#
# Every pipeline .dot substitutes these values via the BARE ``$var`` form
# (never ``${var:-default}`` -- the attractor engine's substitution does not
# understand shell default-value syntax, so a ``${var:-default}`` token is
# left untouched by the engine and expanded by bash itself, which ALWAYS
# applies its own default and silently discards any ``--param`` the caller
# supplied).
#
# The bare-``$var`` form has its own failure mode: when the context key is
# absent, the engine leaves the literal ``$var`` token in place and bash's
# unset-variable expansion resolves it to an empty string. ``int_or_default``
# is the single place these defaults live -- the .dot files never encode a
# default themselves, so the shell expression and the Python default can
# never silently disagree.


def int_or_default(default: int):
    """Return an ``argparse`` ``type=`` callable: "" -> *default*, else ``int(value)``.

    Args:
        default: Value to use when the CLI receives an empty string (the
            bare-``$var`` absent-key expansion described above).

    Returns:
        A callable suitable for ``argparse.add_argument(..., type=...)``.
    """

    def _parse(value: str) -> int:
        if value == "":
            return default
        return int(value)

    return _parse


# ---------------------------------------------------------------------------
# Wiki-root path conventions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WikiRoot:
    """Every path a ``--wiki-root`` implies, per CLI-CONTRACT.md's preamble:
    ``sources/``, ``lens/``, ``wiki/``, ``AGENTS.md``, ``ledger.jsonl``,
    ``cost.jsonl``, ``log.md``, and the ephemeral ``.ai/`` directory.
    """

    root: Path

    def __init__(self, root: str | Path) -> None:
        object.__setattr__(self, "root", Path(root))

    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def lens_dir(self) -> Path:
        return self.root / "lens"

    @property
    def wiki_dir(self) -> Path:
        return self.root / "wiki"

    @property
    def ai_dir(self) -> Path:
        return self.root / ".ai"

    @property
    def ledger_path(self) -> Path:
        return self.root / "ledger.jsonl"

    @property
    def cost_path(self) -> Path:
        return self.root / "cost.jsonl"

    @property
    def log_path(self) -> Path:
        return self.root / "log.md"

    @property
    def current_source_file(self) -> Path:
        return self.ai_dir / "current_source.txt"

    @property
    def source_start_file(self) -> Path:
        return self.ai_dir / "source-start"

    @property
    def current_slice_file(self) -> Path:
        return self.ai_dir / "current_slice.json"

    @property
    def current_catalog_file(self) -> Path:
        """The index-first catalog ``build_catalog`` writes: filename, title,
        one-line summary for EVERY wiki page, no bodies (evals/compounding/
        PRECOMMIT-INDEX.md). This is what ``weave`` KNOWS EXISTS, as opposed
        to ``current_slice_file`` above, which bounds what it may READ --
        the two were conflated by the prior BM25-threshold routing rule this
        file's sibling replaces."""
        return self.ai_dir / "current_catalog.md"

    @property
    def review_guidance_file(self) -> Path:
        return self.ai_dir / "review-guidance.md"

    @property
    def review_brief_file(self) -> Path:
        """The self-contained brief ``prepare_review_brief`` writes for
        ``review_gate`` -- ``{\"source_id\": ..., \"stage\": ..., \"brief\": ...}``
        (``wiki_weaver.ingest.review_brief``). Stamped with the source id it
        was built for so a reader (``wiki_weaver.aitl.proxy_interviewer``)
        can verify freshness against ``current_source_file`` before trusting
        it -- a brief from a previous source read at the wrong gate is worse
        than no brief at all. Distinct from ``review_guidance_file`` above,
        which is the HUMAN's/proxy's own steering text written back after a
        [B] Guide decision -- this file is the input the decision is based
        on, not the output of it."""
        return self.ai_dir / "review-brief.json"

    @property
    def takeaways_brief_file(self) -> Path:
        """The self-contained brief ``wiki_weaver.ingest.takeaways_brief``
        writes for ``takeaways_gate`` -- the ONE pre-write discussion site
        the source gist describes (docs/llm-wiki-pattern.md L50: "the LLM
        reads the source, discusses key takeaways with you ... writes a
        summary page") that this pipeline never built until now. Same
        stamped ``{"source_id": ..., "stage": ..., "brief": ...}`` shape and
        freshness contract as ``review_brief_file``/``guidance_brief_file``
        above -- read by ``wiki_weaver.aitl.proxy_interviewer``, verified
        fresh against ``current_source_file`` before being trusted."""
        return self.ai_dir / "takeaways-brief.json"

    @property
    def takeaways_guidance_file(self) -> Path:
        """The answerer's OWN emphasis text for the source about to be
        woven -- written by ``wiki_weaver.ingest.persist_takeaways`` right
        after ``takeaways_gate``, read directly by ``weave`` itself (a
        plain-markdown file-tool read, the same hand-off shape
        ``review_guidance_file`` above already proves for a POST-write
        steer). Absent whenever no particular emphasis was requested
        (including an unattended auto-approve answer -- see
        ``persist_takeaways.py``'s module docstring) -- this is normal, not
        an error."""
        return self.ai_dir / "takeaways-guidance.md"

    @property
    def takeaways_answer_file(self) -> Path:
        """THE fix for a verified platform defect (see
        ``wiki_weaver.aitl.freeform_bridge``'s module docstring): environment
        variable names containing periods (e.g. the engine's own
        ``human.gate.text`` context key, upper-cased verbatim by
        ``tool_env`` to ``HUMAN.GATE.TEXT``) do not reliably survive
        ``asyncio.create_subprocess_shell``'s ``env=`` passthrough on this
        platform -- verified by direct reproduction outside the pipeline
        entirely. Every ``Interviewer`` this project constructs (regardless
        of mode) therefore writes ITS OWN freeform ``takeaways_gate`` answer
        directly to this stamped file -- ``{"source_id": ..., "stage": ...,
        "text": ...}`` -- the moment it answers, sidestepping the broken
        env-var channel AND the shell-injection risk of substituting free
        text directly into a ``tool_command`` string. ``persist_takeaways``
        reads this file (verifying freshness against ``current_source_file``,
        same discipline as every other stamped brief file) instead of
        ``HUMAN.GATE.TEXT``."""
        return self.ai_dir / "takeaways-answer.json"

    @property
    def page_plan_file(self) -> Path:
        """Arm C only (ingest-c.dot): the LLM planner's page-plan, validated
        deterministically by ``wiki_weaver.ingest.validate_plan`` before
        ``weave`` is allowed to execute it."""
        return self.ai_dir / "page-plan.json"

    @property
    def reweave_attempts_file(self) -> Path:
        return self.ai_dir / "reweave-attempts.json"

    @property
    def validation_report_file(self) -> Path:
        """``validate``'s structural findings for the CURRENT weave pass --
        ``.ai/validation-report.md``. Read by ``quarantine_brief`` (real,
        quotable evidence for why a source is being offered to review_gate
        after exhausting its re-weave attempts), same fixed relative path
        every ``ingest.dot``/``correct.dot``/``canon.dot`` caller already
        writes via ``--out``."""
        return self.ai_dir / "validation-report.md"

    @property
    def guidance_brief_file(self) -> Path:
        """The self-contained brief ``wiki_weaver.ingest.guidance_brief``
        writes for ``collect_guidance`` -- the ``collect_guidance``
        analogue of ``review_brief_file`` above (same defect class: a
        freeform gate reached with no grounding refuses rather than
        fabricates; this file is what fixes that). Stamped with the
        source id it was built for, same freshness contract as
        ``review_brief_file``."""
        return self.ai_dir / "guidance-brief.json"

    @property
    def corrections_dir(self) -> Path:
        """``lens/corrections/`` -- the second-highest-precedence lens layer
        (docs/DESIGN.md \u00a74: canon > corrections > sources). THE load-bearing
        artifact ``wiki_weaver.correct.persist_lens`` writes to (see its
        module docstring) -- durable, machine-readable, re-read on every
        subsequent ``weave`` pass (pipeline/ingest.dot's weave prompt) and,
        via ``wiki_weaver.aitl.corrections``, on every subsequent AITL proxy
        gate decision too."""
        return self.lens_dir / "corrections"

    @property
    def gate_decisions_file(self) -> Path:
        """The durable, append-only audit trail of every gate interaction
        the proxy made -- ``wiki_weaver.aitl.audit``'s
        ``.ai/gate-decisions.jsonl``. Formalized here (rather than only as
        a path built inline in ``audit.py``) so ``guidance_brief`` can read
        the reviewer's own stated reason for choosing [B] Guide without a
        new ``ingest`` -> ``aitl`` package dependency."""
        return self.ai_dir / "gate-decisions.jsonl"

    @property
    def quarantine_state_file(self) -> Path:
        """One-shot, source-id-keyed latch: has THIS source already been
        offered its one post-exhaustion review at ``review_gate``? See
        ``wiki_weaver.ingest.quarantine_brief`` module docstring for the
        termination argument this enforces. Identity-gated exactly like
        ``reweave_attempts_file`` -- a stale latch left over from a
        PREVIOUS source is reset, never silently reused."""
        return self.ai_dir / "quarantine-state.json"

    @property
    def curate_brief_file(self) -> Path:
        """The self-contained brief ``wiki_weaver.ingest.curate_brief``
        writes for ``curate_gate`` -- the ONE source-level "does this belong
        in this wiki" checkpoint (the gist's four human jobs: "curate
        sources, direct the analysis, ask good questions, and think about
        what it all means" -- the other three each already have a dedicated
        gate; curation had none until now). Same stamped
        ``{"source_id": ..., "stage": ..., "brief": ...}`` shape and
        freshness contract as ``review_brief_file``/``takeaways_brief_file``
        above -- read by ``wiki_weaver.aitl.proxy_interviewer``, verified
        fresh against ``current_source_file`` before being trusted."""
        return self.ai_dir / "curate-brief.json"

    @property
    def file_back_brief_file(self) -> Path:
        """The self-contained brief ``wiki_weaver.ask.file_back_brief``
        writes for ``ask.dot``'s ``file_back_gate`` -- the question asked
        and a gist of the answer about to be offered for filing back into
        the wiki. Unlike the ingest-side stamped briefs above (which are
        verified fresh against a per-source ``current_source_file`` across
        MANY sources in one pipeline run), ``ask.dot`` has no per-source
        resume loop of its own -- one question in, one answer out, this
        brief written fresh immediately before the gate in the SAME pass --
        so no source-id-style freshness stamp is needed here; presence and
        a non-empty ``brief`` field are the whole contract (see
        ``wiki_weaver.ask.file_back_brief``'s module docstring)."""
        return self.ai_dir / "file-back-brief.json"

    # -- DESIGN.md §5 source-kind handling (detect_kind / segment_source) --

    @property
    def current_kind_file(self) -> Path:
        """THE detected-kind breadcrumb for the current source (disk state,
        not just in-context) -- written fresh by ``detect_kind`` every time a
        source is (re)selected, and read by ``segment_source``,
        ``budget``, and ``commit`` so the ledger/cost labels reflect what was
        actually detected rather than falling back to the best-effort,
        frontmatter-only ``read_source_kind()``."""
        return self.ai_dir / "current_kind.json"

    @property
    def llm_kind_verdict_file(self) -> Path:
        """Where ``classify_kind`` (the arm-of-last-resort LLM box, reached
        only when ``detect_kind``'s own signals were ambiguous) writes its
        one-word verdict. Never routed on directly (AP-2, DESIGN.md §12.1) --
        ``detect_kind --from-verdict`` reads and validates it deterministically."""
        return self.ai_dir / "kind-verdict.txt"

    @property
    def segments_dir(self) -> Path:
        return self.ai_dir / "segments"

    @property
    def segments_manifest_file(self) -> Path:
        return self.ai_dir / "segments.json"

    @property
    def current_segment_file(self) -> Path:
        """Which segment of the CURRENT source is being processed right now
        -- the segment-granularity analogue of ``current_source_file``."""
        return self.ai_dir / "current_segment.json"

    @property
    def current_segment_content_file(self) -> Path:
        """The bounded slice of text ``retrieve_slice``/``weave`` must read
        for THIS pass -- always present once ``segment_source --select`` has
        run, whether the source was segmented or not (one uniform file, one
        uniform downstream code path -- see DESIGN.md §5)."""
        return self.ai_dir / "current-segment-content.md"

    # -- DESIGN.md §5 stream watermarking (wiki_weaver.ingest.watermark) --

    @property
    def current_source_content_file(self) -> Path:
        """Written by ``watermark`` for a ``kind=stream`` source in
        ``delta``/``full``/``watermark_reset`` mode: the bounded text
        ``segment_source`` must read INSTEAD OF the raw file under
        ``sources/`` for this pass (delta content + a small overlap window,
        or the whole source on first sight / a detected reset). Absent for
        every other kind -- ``detect_kind`` deletes any stale copy left by a
        PRIOR stream source the moment a new source is selected, so
        ``segment_source`` never accidentally reads another source's delta
        (see detect_kind.py's cleanup comment)."""
        return self.ai_dir / "current-source-content.md"

    @property
    def watermarks_path(self) -> Path:
        """Durable, per-stream-identity watermark store -- sibling to
        ``ledger.jsonl`` (persist watermarks where the ledger lives), a
        single atomically-rewritten JSON object keyed by stable identity
        (see ``wiki_weaver.ingest.watermark``), mirroring the existing
        ``wiki_index_file`` precedent below for a small mutable JSON cache
        that isn't append-only ledger data."""
        return self.root / "watermarks.json"

    @property
    def wiki_index_dir(self) -> Path:
        return self.wiki_dir / ".index"

    @property
    def wiki_index_file(self) -> Path:
        return self.wiki_index_dir / "pages.json"

    # -- pipeline/synthesize.dot (FINDINGS.md \u00a77: "no step forms a whole-corpus
    # view" / "the exit condition is a queue-drain predicate, never anything
    # about the wiki") --

    @property
    def synth_ledger_path(self) -> Path:
        """Durable, append-only record of every gap-candidate this pipeline
        has ever DECIDED on -- paged (a page was written) or declined (the
        corpus-scoped answer node found no real cross-source support, or
        structural validation never converged). Sibling to ``ledger.jsonl``
        (same durability contract: committed, read via ``lib.read_ledger`` --
        LedgerCorruptError on malformed JSONL, never silently treated as
        empty). This IS what makes ``select_gap`` a resume gate: a term
        already decided here is never proposed again, whether this run is
        continuous or was killed and restarted from ``start``."""
        return self.root / "synth-ledger.jsonl"

    @property
    def current_gap_file(self) -> Path:
        """The gap candidate ``select_gap`` chose for THIS pass: ``{term,
        source_count, source_ids}`` (see ``wiki_weaver.synthesize.find_gaps``).
        The synthesize-loop analogue of ``current_source_file``."""
        return self.ai_dir / "current_gap.json"

    @property
    def gap_context_file(self) -> Path:
        """Corpus-scoped reading list for the current gap: the wiki pages
        most relevant to the term (BM25) plus the source ids ``find_gaps``
        already knows discuss it -- written by
        ``wiki_weaver.synthesize.retrieve_context``, read by ``answer_gap``."""
        return self.ai_dir / "gap-context.json"

    @property
    def gap_answer_file(self) -> Path:
        """Written ONLY when ``answer_gap`` finds real cross-source support
        for the current gap term -- mirrors ``ask.dot``'s
        ``answer``/``.ai/answer.md`` split (exactly one of this and
        ``gap_declined_file`` exists after ``answer_gap`` runs;
        ``gap_coverage_check`` routes on which)."""
        return self.ai_dir / "gap-answer.md"

    @property
    def gap_declined_file(self) -> Path:
        """Written ONLY when ``answer_gap`` decides coverage is too thin to
        justify a page -- refuse rather than guess, same discipline as
        ``ask.dot``'s ``.ai/refusal.md``."""
        return self.ai_dir / "gap-declined.md"

    @property
    def synth_reweave_attempts_file(self) -> Path:
        """Attempt counter for THIS gap term's structural-validation retry
        loop -- the synthesize-loop analogue of ``reweave_attempts_file``,
        keyed by term (via ``current_gap_file``) instead of source id."""
        return self.ai_dir / "synth-reweave-attempts.json"

    @property
    def gap_candidates_raw_file(self) -> Path:
        """``scan_arguments``' (LLM box) raw, unvalidated candidate list --
        ``.ai/gap-candidates-raw.json`` -- read and validated by
        ``wiki_weaver.synthesize.rank_candidates`` before anything
        downstream trusts it: \"the planner cannot certify its own plan's
        syntax\" (``validate_plan.py``'s exact reasoning) applies equally
        here to \"the model cannot certify its own candidate list's
        shape.\""""
        return self.ai_dir / "gap-candidates-raw.json"

    @property
    def gap_candidates_file(self) -> Path:
        """``rank_candidates``' normalized, threshold-filtered, ranked
        candidate list -- ``.ai/gap-candidates.json`` -- what ``select_gap``
        actually reads on every pass (the synthesize-loop analogue of the
        retired ``find_gaps()`` in-process call: candidates now come from
        ONE bounded LLM pass over ``wiki/index.md`` + ``.ai/source-arguments.md``,
        cached to disk for this invocation, rather than a live n-gram
        count over ``sources/``)."""
        return self.ai_dir / "gap-candidates.json"

    @property
    def synthesis_brief_file(self) -> Path:
        """Self-contained PRE-write brief for ``synthesis_gate`` -- the ONE
        pass-level checkpoint where a human or agent proxy can shape WHICH
        ranked candidate themes actually get a page, before ``select_gap``
        ever picks one to write (see ``pipeline/synthesize.dot``'s header
        and ``wiki_weaver.synthesize.synthesis_brief``). Stamped with a
        ``signature`` over the pending candidate set (never a source_id --
        this gate has no single-source identity, unlike ``takeaways_gate``/
        ``curate_gate``) so ``persist_synthesis_guidance`` can verify the
        answer it reads was given for THIS pass's candidate list, never a
        stale prior one."""
        return self.ai_dir / "synthesis-brief.json"

    @property
    def synthesis_answer_file(self) -> Path:
        """``synthesis_gate``'s freeform answer, written directly by
        ``wiki_weaver.aitl.freeform_bridge.FreeformAnswerRecorder`` the
        moment it is given -- the SAME dotted-env-var platform workaround
        ``takeaways_answer_file`` uses (see that property's docstring and
        ``freeform_bridge``'s module docstring for the defect this works
        around). Stamped with ``signature`` (not ``source_id``) -- read by
        ``wiki_weaver.synthesize.persist_synthesis_guidance``."""
        return self.ai_dir / "synthesis-answer.json"

    @property
    def source_arguments_file(self) -> Path:
        """``wiki_weaver.synthesize.extract_source_arguments``' deterministic
        output -- ``.ai/source-arguments.md`` -- a condensed, one-extract-
        per-source-page ARGUMENT view of the corpus, read by ``scan_arguments``
        in place of ``wiki/overview.md`` (iteration-4 fix: overview.md and
        index.md are themselves organized per-source-arrival-order and
        per-entity respectively, so a theme with no entity and no shared
        title vocabulary is invisible to a scan seeded from them -- see
        pipeline/synthesize.dot's header and
        wiki_weaver.synthesize.extract_source_arguments' module docstring).
        Computed fresh every invocation (cheap, deterministic); never
        committed -- ephemeral like every other ``.ai/`` artifact."""
        return self.ai_dir / "source-arguments.md"

    # -- iteration-6 fix: detection/attribution split (see pipeline/
    # synthesize.dot's header, "ITERATION 6" section, and
    # wiki_weaver.synthesize.attribute_select / attribute_record) --

    @property
    def attribution_progress_file(self) -> Path:
        """Internal, resumable working state for the per-candidate
        attribution loop -- ``.ai/attribution-progress.json`` --
        ``{"raw_signature": <sha256 of gap-candidates-raw.json's bytes>,
        "results": [{"term", "source_ids", "claim"}, ...]}``. Owned
        entirely by ``attribute_select``/``attribute_record``; never read
        by anything downstream of the attribution loop (that's
        ``gap_candidates_attributed_file``, below). The stored
        ``raw_signature`` is what makes a same-invocation crash/resume
        cheap (partial progress survives) while a genuine new
        ``scan_arguments`` run (different raw content -> different
        signature) discards stale progress rather than silently mixing
        two different detection passes' candidates."""
        return self.ai_dir / "attribution-progress.json"

    @property
    def current_attribution_candidate_file(self) -> Path:
        """The ONE candidate ``attribute_select`` chose for this pass --
        ``{"term", "claim", "source_ids"}`` where ``source_ids`` is
        ``scan_arguments``' SEED, not a final count -- the attribution-loop
        analogue of ``current_gap_file``. Read by ``attribute_sources``."""
        return self.ai_dir / "current-attribution-candidate.json"

    @property
    def current_attribution_result_file(self) -> Path:
        """Written ONLY by ``attribute_sources`` (the LLM box): its
        corrected ``{"term", "claim", "source_ids"}`` judgment for the ONE
        candidate named in ``current_attribution_candidate_file`` -- may
        add source_ids the seed missed and drop ones that do not actually
        hold. Read and validated by ``attribute_record`` (box/tool split,
        same reasoning as ``rank_candidates`` validating ``scan_arguments``'
        own output: the model cannot certify its own attribution's shape)."""
        return self.ai_dir / "current-attribution-result.json"

    @property
    def gap_candidates_attributed_file(self) -> Path:
        """The attribution loop's FINAL artifact -- ``.ai/gap-candidates-
        attributed.json`` -- a flat JSON array of ``{"term", "source_ids",
        "claim"}``, one per unique candidate ``scan_arguments`` proposed,
        with ``source_ids`` now the ATTRIBUTED count (every source thesis
        judged individually against that one argument) rather than the
        detection pass's rough seed. This is what
        ``wiki_weaver.synthesize.rank_candidates`` reads and threshold-
        filters -- the synthesize-loop analogue of the old direct read of
        ``gap_candidates_raw_file`` before the iteration-6 attribution pass
        was inserted between detection and ranking."""
        return self.ai_dir / "gap-candidates-attributed.json"

    # -- CHUNKED DETECTION (this pass): scan_arguments measurably triages to
    # only 2-3 dominant signals when run once over the full ~44 KB source-
    # arguments.md extract, but correctly surfaces far more when scoped to a
    # small fixed-size chunk of source theses (see wiki_weaver.synthesize.
    # chunk_arguments' module docstring for the measured evidence). This is
    # a select/box/record loop for DETECTION, the same shape iteration 6
    # already applied to ATTRIBUTION -- see wiki_weaver.synthesize.
    # select_chunk / record_chunk_candidates. --

    @property
    def argument_chunks_file(self) -> Path:
        """``wiki_weaver.synthesize.chunk_arguments``' deterministic output
        -- ``.ai/argument-chunks.json`` -- a JSON array of fixed-size chunk
        document strings, each a self-contained subset of the corpus's
        source theses (same ``## <title>\\n(source_id: ...)`` section
        format as ``source_arguments_file``, just fewer sections per
        chunk). Read by ``wiki_weaver.synthesize.select_chunk`` to drive the
        per-chunk detection loop; never read directly by ``scan_arguments``
        (which reads only the ONE chunk ``select_chunk`` wrote to
        ``current_chunk_file``)."""
        return self.ai_dir / "argument-chunks.json"

    @property
    def chunk_detection_progress_file(self) -> Path:
        """Resumable working state for the per-chunk detection loop --
        ``.ai/chunk-detection-progress.json`` -- ``{"chunks_signature":
        <sha256 of argument_chunks_file's bytes>, "scanned_indices": [...],
        "current_index": <int, the chunk select_chunk most recently chose>,
        "accumulated": [{"term", "claim", "source_ids", "chunk_indices"},
        ...]}``. Same resumability discipline as
        ``attribution_progress_file`` (see that property's docstring):
        matching signature -> resume; different/absent signature -> a NEW
        ``chunk_arguments`` run produced this file, start fresh rather than
        silently mixing two different chunkings' candidates."""
        return self.ai_dir / "chunk-detection-progress.json"

    @property
    def current_chunk_file(self) -> Path:
        """The ONE chunk ``select_chunk`` chose for this pass -- a plain
        markdown document (NOT JSON), the per-chunk analogue of
        ``current_gap_file`` -- read by ``scan_arguments`` (the LLM box)
        INSTEAD OF ``source_arguments_file``, the whole-corpus extract, for
        the duration of the chunked detection loop."""
        return self.ai_dir / "current-chunk.md"

    @property
    def current_chunk_candidates_raw_file(self) -> Path:
        """Written ONLY by ``scan_arguments`` (the LLM box): its raw,
        unvalidated candidate list for the ONE chunk named in
        ``current_chunk_file`` -- the per-chunk analogue of the pre-
        chunking ``gap_candidates_raw_file``. Read and validated by
        ``wiki_weaver.synthesize.record_chunk_candidates`` (box/tool split,
        same reasoning as every other box output in this pipeline: the
        model cannot certify its own output's shape) before being unioned
        into ``chunk_detection_progress_file``'s accumulated list."""
        return self.ai_dir / "current-chunk-candidates-raw.json"


def resolve_path(wr: WikiRoot, raw: str) -> Path:
    """Resolve a ``--out``/``--append-*`` style argument.

    Absolute paths pass through; relative paths are relative to
    ``--wiki-root`` (matching how ``ingest.dot`` passes e.g.
    ``--append-ledger ledger.jsonl``).
    """
    p = Path(raw)
    return p if p.is_absolute() else wr.root / p


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Atomic writes -- tempfile.mkstemp + os.replace, per CLI-CONTRACT.md
# ---------------------------------------------------------------------------


def atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` durably: never a torn/partial file."""
    ensure_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_append_jsonl(path: Path, obj: dict) -> None:
    """Append one JSON object as a line, fsynced before returning.

    A true atomic *replace* is impossible for an append (the prior content
    must be preserved), so durability here means: the write is flushed and
    fsynced to disk before this function returns, and the process must not
    print its "done" sentinel until after this call completes -- see the
    ordering law in CLI-CONTRACT.md.
    """
    ensure_dir(path.parent)
    line = json.dumps(obj) + "\n"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def read_ledger(path: Path) -> list[dict]:
    """Read ``ledger.jsonl`` as a list of dicts.

    Missing file -> empty list (nothing has been ingested yet). Existing
    file that fails to parse as JSONL -> ``LedgerCorruptError``.
    """
    if not path.is_file():
        return []
    records: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LedgerCorruptError(f"could not read {path}: {exc}") from exc
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise LedgerCorruptError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
    return records


def _segment_status_by_source(records: list[dict]) -> dict[str, tuple[set[int], int]]:
    """Group ledger records by source_id -> (ledgered segment indices, total).

    Segment-aware extension for DESIGN.md §5 (meeting transcripts segmented
    at turn boundaries): a ledger line MAY carry ``segment_index``/
    ``segment_total``; when absent (every pre-segmentation ledger line, and
    every line written by a caller that never ran ``segment_source``, e.g.
    ``evals/arms/ingest-b.dot``/``ingest-c.dot``), it is treated as segment 1
    of 1 -- so old ledgers, and non-segmenting callers, behave exactly as
    before with zero special-casing.
    """
    result: dict[str, tuple[set[int], int]] = {}
    for rec in records:
        sid = rec.get("source_id")
        if sid is None:
            continue
        idx = rec.get("segment_index", 1)
        total = rec.get("segment_total", 1)
        indices, prior_total = result.get(sid, (set(), total))
        indices.add(idx)
        # Take the LARGEST total this source has ever claimed, not the last one
        # written. Rows for one source can disagree about segment_total the
        # moment a source is ever re-split (which generation-aware re-selection
        # would introduce), and taking the last row's value silently shrinks the
        # completion bar: rows (1,2,3 of 4) followed by a row (1 of 1) made
        # ledgered_source_ids report the source FULLY DONE on the strength of
        # segment 1 alone, retiring segment 4 without it ever being woven.
        # max() can only ever make a source look LESS complete, never more --
        # the safe direction for a gate whose job is to keep work pending.
        result[sid] = (indices, max(prior_total, total))
    return result


def ledgered_source_ids(path: Path) -> set[str]:
    """Source ids that are FULLY done -- every segment 1..segment_total has
    a ledger decision recorded (accept or skip; a source with only some of
    its segments ledgered is still pending, see select_source's resume
    gate)."""
    by_source = _segment_status_by_source(read_ledger(path))
    return {sid for sid, (indices, total) in by_source.items() if indices >= set(range(1, total + 1))}


def ledgered_segment_indices(path: Path, source_id: str) -> set[int]:
    """Which segment indices of ``source_id`` already have a ledger decision
    (accept or skip) recorded, regardless of whether the source as a whole
    is fully done yet."""
    by_source = _segment_status_by_source(read_ledger(path))
    indices, _total = by_source.get(source_id, (set(), 1))
    return indices


def already_ledgered(path: Path, source_id: str, segment_index: int = 1) -> bool:
    """Is THIS (source, segment) pair already ledgered? Defaults to segment 1
    -- the whole-source idempotency check every pre-segmentation caller
    (and every caller that never ran ``segment_source``) already relies on."""
    return segment_index in ledgered_segment_indices(path, source_id)


# ---------------------------------------------------------------------------
# Current-source breadcrumb
# ---------------------------------------------------------------------------


def read_current_source_id(wr: WikiRoot) -> str:
    path = wr.current_source_file
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist -- select_source must run first")
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise FileNotFoundError(f"{path} is empty -- select_source must run first")
    return content


def read_current_segment_index(wr: WikiRoot) -> int:
    """Which segment of the CURRENT source is being processed right now,
    per ``current_segment_file`` -- defaults to ``1`` (the pre-segmentation
    whole-source shape) when that file is absent, exactly like
    ``commit.py``'s own ``_current_segment`` helper: a caller that never ran
    ``segment_source --select`` (evals/arms/ingest-b.dot, ingest-c.dot, or
    any fresh checkout) sees unchanged, backward-compatible behavior.

    THE identity fix (docs/KNOWN_ISSUES.md #3): ``reweave_bound`` and
    ``quarantine_brief`` key their per-source state (retry counter, one-shot
    review latch) by source id ALONE. segment_source.py's own docstring
    states the design intent plainly -- "each segment is a unit of work"
    (DESIGN.md \u00a75) -- but both of those state files predate segmentation
    and were never updated to treat a segment as its own unit once
    segmentation shares one source id across many passes. The practical
    effect, confirmed against a real 73-source production run: segment 1
    of a multi-segment source consumes BOTH the shared retry budget and the
    ONE-TIME per-source review-gate rescue on its own exhaustion, so segment
    2+ of the SAME source inherits an already-spent counter (it can exhaust
    in as little as a single failed validate) and finds the rescue latch
    already burned -- auto-quarantining almost every time, regardless of
    what that segment's own weave actually wrote. Reading the segment index
    here (and folding it into both state files' identity, alongside
    source_id) restores "each segment is a unit of work" as a matter of
    accounting, not just intent: every segment gets its own retry budget and
    its own one-shot rescue, exactly like a freshly-selected source does.
    """
    path = wr.current_segment_file
    if not path.is_file():
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 1
    try:
        return int(data.get("index", 1))
    except (TypeError, ValueError):
        return 1


def read_current_kind(wr: WikiRoot) -> str:
    """The kind ``detect_kind`` actually determined for the current source
    (disk state -- see ``WikiRoot.current_kind_file``), not the best-effort,
    frontmatter-only guess ``read_source_kind()`` makes. Raises
    ``FileNotFoundError`` if ``detect_kind`` has not run yet for this source
    -- mirrors ``read_current_source_id``'s contract."""
    path = wr.current_kind_file
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist -- detect_kind must run first")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FileNotFoundError(f"{path} is not valid JSON: {exc}") from exc
    kind = data.get("kind") if isinstance(data, dict) else None
    if not kind:
        raise FileNotFoundError(f"{path} has no 'kind' field -- detect_kind must run first")
    return str(kind)


# ---------------------------------------------------------------------------
# Frontmatter -- hand-rolled, no PyYAML (task instruction: stdlib only)
#
# Format observed in the seed wiki corpus (docs/DESIGN.md's evidence base):
#     ---
#     title: Some Title
#     type: concept
#     sources: [1]
#     last_updated: 2026-07-25
#     confidence: 0.6
#     ---
#     <body>
# ---------------------------------------------------------------------------


def _parse_scalar(raw: str) -> object:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _parse_value(raw: str) -> object:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(v.strip()) for v in inner.split(",")]
    return _parse_scalar(raw)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``text`` into (frontmatter dict, body). No frontmatter -> ({}, text)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, object] = {}
    i = 1
    while i < len(lines) and lines[i].strip() != "---":
        line = lines[i]
        i += 1
        if not line.strip() or ":" not in line:
            continue
        key, _, raw_value = line.partition(":")
        meta[key.strip()] = _parse_value(raw_value)
    body_lines = lines[i + 1 :] if i < len(lines) else []
    return meta, "\n".join(body_lines)


# ---------------------------------------------------------------------------
# Wikilinks -- [[slug]] or [[slug|Display Title]]
# ---------------------------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


def extract_wikilinks(text: str) -> list[str]:
    return [m.strip() for m in _WIKILINK_RE.findall(text)]


# ---------------------------------------------------------------------------
# Source citations -- inline "NNN-Name.md" references a wiki page makes back
# to the raw source file(s) it was derived from (the ORIGINAL source id
# convention weave's prompt requires -- see ingest.dot's weave node). Cheap,
# regex-only, no additional page opens beyond whatever already opened the
# page for another reason (see refresh_page_index below, which computes this
# alongside title/links/tokens on the same read).
# ---------------------------------------------------------------------------

_INLINE_SOURCE_CITATION_RE = re.compile(r"\b\d{3}-[^\s\]\)\"']+?\.md\b")


def extract_source_citations(text: str) -> list[str]:
    """Inline ``NNN-Name.md`` source-file citations anywhere in a wiki page
    (frontmatter or body), order-preserving and deduplicated."""
    seen: dict[str, None] = {}
    for m in _INLINE_SOURCE_CITATION_RE.finditer(text):
        seen.setdefault(m.group(0), None)
    return list(seen)


# ---------------------------------------------------------------------------
# Wiki page loading (the in-memory equivalent of "wiki/.index/", built at
# call time -- DESIGN.md measured this at ~0.025ms/page, three orders of
# magnitude below one LLM pass, so no persistent index is needed for the
# ingest subcommands this package implements)
# ---------------------------------------------------------------------------


@dataclass
class WikiPage:
    id: str  # filename, e.g. "andrej-karpathy.md"
    slug: str  # filename stem, e.g. "andrej-karpathy"
    title: str
    links: list[str] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    byte_size: int = 0  # on-disk UTF-8 size -- the unit build_slice's byte budget spends


def list_wiki_pages(wiki_dir: Path) -> list[Path]:
    if not wiki_dir.is_dir():
        return []
    return sorted(p for p in wiki_dir.glob("*.md") if p.is_file())


# ---------------------------------------------------------------------------
# Unambiguous external page-path convention (the write-path bug fix, see
# ISSUE_HANDLING: 13 real content pages landed at wiki_root instead of
# wiki/). weave's file tools are rooted at --wiki-root (the pipeline cwd),
# so a BARE filename like "index.md" is genuinely ambiguous -- both
# wiki_root/index.md and wiki_root/wiki/index.md are valid resolutions of a
# relative write, and nothing forced the correct one. Every artifact weave
# reads to learn a page's identity -- the catalog (build_catalog.py) and
# the candidate slice (retrieve_slice.py) -- must render "wiki/<filename>",
# never a bare filename, and the weave prompt (ingest.dot) must say so
# explicitly. This is the single place that string is assembled so the
# three can never drift out of agreement.
# ---------------------------------------------------------------------------


def to_wiki_relpath(page_id: str) -> str:
    """``page_id`` (a bare wiki page filename, e.g. "index.md") rendered as
    the unambiguous path relative to --wiki-root: "wiki/index.md". Internal
    dict keys (catalog entries, slice bookkeeping) keep the bare filename --
    only the on-disk artifacts weave actually reads use this form."""
    return f"wiki/{page_id}"


# ---------------------------------------------------------------------------
# Root-level content-page guard -- the write-path bug's DETECTION half (see
# to_wiki_relpath above for the PREVENTION half). Every file init.persist
# legitimately places directly at wiki_root is enumerated below (AGENTS.md --
# see init/persist.py's module docstring) plus log.md (WikiRoot.log_path,
# appended by ingest.commit per the gist's append-only-log-at-root design).
# ANY OTHER ``*.md`` file appearing directly at wiki_root is always a
# content page that escaped wiki/, never legitimate scaffolding.
# ---------------------------------------------------------------------------

ROOT_LEVEL_MD_ALLOWLIST = ("log.md", "AGENTS.md")


def list_root_pages(root: Path) -> list[Path]:
    """``*.md`` files directly at ``root`` (non-recursive) -- mirrors
    ``list_wiki_pages``'s glob but scoped one level up, for detecting
    content pages that landed at wiki_root instead of wiki/."""
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*.md") if p.is_file())


def find_stray_root_pages(root: Path) -> list[Path]:
    """``*.md`` files directly at ``root`` that are NOT legitimate
    scaffolding (see ``ROOT_LEVEL_MD_ALLOWLIST``) -- the write-path bug's
    symptom: a content page weave meant to write to ``wiki/`` landing at
    ``wiki_root`` itself instead, because a bare filename is ambiguous once
    the child session's file tools are rooted at wiki_root."""
    return [p for p in list_root_pages(root) if p.name not in ROOT_LEVEL_MD_ALLOWLIST]


def load_wiki_pages(wiki_dir: Path) -> dict[str, WikiPage]:
    pages: dict[str, WikiPage] = {}
    for path in list_wiki_pages(wiki_dir):
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        title = str(meta.get("title", path.stem))
        links = extract_wikilinks(body)
        tokens = bm25.tokenize(f"{title}\n{body}")
        byte_size = len(text.encode("utf-8"))
        pages[path.name] = WikiPage(
            id=path.name, slug=path.stem, title=title, links=links, tokens=tokens, byte_size=byte_size
        )
    return pages


def count_wiki_pages(wiki_dir: Path) -> int:
    return len(list_wiki_pages(wiki_dir))


# ---------------------------------------------------------------------------
# Source reading (kind, query text for BM25)
# ---------------------------------------------------------------------------

_KIND_LINE_RE = re.compile(r"^\s*kind\s*[:=]\s*(\w+)", re.IGNORECASE)
VALID_KINDS = ("article", "meeting", "stream", "repo")


def read_explicit_kind(source_path: Path) -> str | None:
    """The EXPLICIT ``kind`` a source declares for itself, if any: a leading
    ``kind: <article|meeting|stream|repo>`` line (first 5 lines) or a
    ``kind:`` frontmatter field. Returns ``None`` -- never a guess, never a
    default -- when no explicit marker is present, so callers that must NOT
    silently default (``detect_kind``) can tell "declared" apart from
    "absent". ``read_source_kind()`` below is the best-effort wrapper that
    still defaults to ``article`` for the pre-existing ledger-label use case.
    """
    if not source_path.is_file():
        return None
    try:
        text = source_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines()[:5]:
        m = _KIND_LINE_RE.match(line)
        if m:
            return m.group(1).lower()
    meta, _ = parse_frontmatter(text)
    kind = meta.get("kind")
    return str(kind) if kind else None


def read_source_kind(source_path: Path) -> str:
    """Best-effort ``kind`` (DESIGN.md §5) for a raw source file, for the
    pre-existing ledger/cost-label use case. Prefers an explicit marker (see
    ``read_explicit_kind``); absent one, defaults to ``article`` (the common
    case for un-annotated one-shot documents).

    NOTE: this is deliberately NOT what ``detect_kind`` uses to make routing
    decisions -- a silent ``article`` default is exactly the failure mode
    DESIGN.md §5 calls out (a real transcript with no ``kind:`` line would
    default to ``article`` here, which is fine for a cost-ledger label but
    would be wrong -- and dangerous -- as a segmentation/routing decision).
    """
    kind = read_explicit_kind(source_path)
    return kind if kind is not None else "article"


def extract_query_text(source_path: Path, max_words: int = 500) -> str:
    """Title + first ~``max_words`` words of a source (or of a bounded
    segment of it -- see ``build_slice``'s ``content_path`` parameter), for
    BM25 querying."""
    if not source_path.is_file():
        return ""
    text = source_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    title = str(meta.get("title", "")) if meta else ""
    content = body if meta else text
    if not title:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                title = stripped.lstrip("#").strip()
                break
    words = content.split()
    snippet = " ".join(words[:max_words])
    return f"{title}\n{snippet}"


# ---------------------------------------------------------------------------
# The deterministic slice: BM25 top-k UNION link-graph neighbors UNION nav
# ---------------------------------------------------------------------------

NAV_PAGES = ("index.md", "overview.md")

# ---------------------------------------------------------------------------
# Byte-budgeted candidate selection -- replaces the historical FIXED top-k
# default. THE measured defect (docs/DESIGN.md, the retrieve_slice byte-
# budget fix): with a fixed COUNT, touches-per-source DECLINE as the wiki
# grows (37.5% of pages readable at 16 pages, 0.8% at 751 -- the opposite of
# the design intent, that the wiki compounds). A fixed count also starves
# small-page corpora (articles, ~14KB/page -- 6 pages is only ~84KB of
# context, far under any reasonable budget) while flooding large-page ones
# (transcripts, ~138KB/page -- 6 pages is ~828KB, likely more than the rest
# of the prompt combined). The budget that actually matters is BYTES of
# context weave must read, not a page COUNT -- so we cut the BM25-ranked
# list where the bytes run out, not where an arbitrary count does.
#
# weave's prompt also carries the bounded source segment
# (segment_source.DEFAULT_SEGMENT_BYTES = 80_000) and the whole-wiki catalog
# (build_catalog.MAX_CATALOG_BYTES = 60_000) before the slice contributes
# anything -- ~140KB of fixed overhead. DEFAULT_SLICE_BUDGET_BYTES keeps the
# slice in the same order of magnitude as that existing overhead (so it
# cannot unilaterally dominate the prompt) while leaving the total
# comfortably inside a modern LLM context window (a 150-200K-token window is
# 600KB+ of raw text). This is a CHOSEN ceiling, not a measured one -- there
# is no equivalent to the S7 BM25 recall experiment for "the right" byte
# budget. Revisit if a real corpus shows the slice starving or flooding at
# this number.
DEFAULT_SLICE_BUDGET_BYTES = 150_000

# Floor: even a wiki made entirely of huge pages (the ~138KB/page transcript
# corpus DESIGN.md itself measured) must still get SOME merge targets -- a
# strict byte budget alone could select zero or one candidate there, which
# is worse than the fixed-k defect this replaces (weave's create-vs-fold
# decision needs at least a couple of real options to compare, per entity).
# 3 is not an arbitrary floor: it is the smallest k this codebase has actual
# recall evidence for -- the S7 BM25 result (docs/DESIGN.md §3) measured
# k=3 already hitting >=90% recall on a 25-page corpus.
MIN_SLICE_CANDIDATES = 3

# Ceiling: an unbounded count on a wiki of many TINY pages could otherwise
# put hundreds of entries in the slice for a budget this size (150_000
# bytes / a ~200-byte stub page is 750 candidates). 60 keeps the slice's own
# page COUNT in the same order of magnitude as build_catalog's own listing
# ceiling (MAX_CATALOG_BYTES=60_000, roughly one short line per page) --
# past that point weave is better served by the catalog's judgment (what
# EXISTS) than by an ever-longer READ list.
MAX_SLICE_CANDIDATES = 60


def _select_candidates_by_budget(
    ranked: list[tuple[str, float]],
    byte_sizes: dict[str, int],
    budget_bytes: int,
    floor: int,
    ceiling: int,
) -> list[tuple[str, float]]:
    """Walk ``ranked`` (BM25 best-first, see ``bm25.BM25.top_k``) accepting
    candidates until the cumulative byte budget would be exceeded.

    ``floor`` guarantees at least this many candidates come back even when
    the very first one alone exceeds the whole budget (huge-page corpora).
    ``ceiling`` guarantees no more than this many come back even when the
    budget has bytes to spare (tiny-page corpora). BM25 order is never
    disturbed -- this only decides where to CUT the ranked list, so the
    best-matching pages are always the ones kept.
    """
    selected: list[tuple[str, float]] = []
    used_bytes = 0
    for doc_id, score in ranked:
        if len(selected) >= ceiling:
            break
        page_bytes = byte_sizes.get(doc_id, 0)
        if len(selected) >= floor and used_bytes + page_bytes > budget_bytes:
            break
        selected.append((doc_id, score))
        used_bytes += page_bytes
    return selected


def build_slice(
    wr: WikiRoot,
    source_id: str,
    k: int | None = None,
    *,
    budget_bytes: int = DEFAULT_SLICE_BUDGET_BYTES,
    floor: int = MIN_SLICE_CANDIDATES,
    ceiling: int = MAX_SLICE_CANDIDATES,
    content_path: Path | None = None,
) -> dict:
    """THE scale fix (DESIGN.md §3, S7 BM25 result) -- now byte-budgeted.

    slice = BM25-ranked candidates, cut at a BYTE budget (not a fixed count)
            UNION one-hop link-graph neighbors of those hits
            UNION always-include nav pages (index.md, overview.md)

    ``k``, when given (an explicit ``--k`` override -- see
    retrieve_slice.py), reproduces the ORIGINAL fixed-top-k behavior
    exactly: BM25 top-``k``, no byte budget, no floor/ceiling. This is the
    DEPRECATED path, kept only so existing callers/tests can still pin an
    exact count.

    ``k=None`` (the default) is the fix described above: BM25-ranked
    candidates are accepted until ``budget_bytes`` of cumulative page size
    is exhausted, with ``floor`` guaranteeing a few candidates even when
    pages are huge and ``ceiling`` preventing runaway counts when pages are
    tiny. See ``DEFAULT_SLICE_BUDGET_BYTES``/``MIN_SLICE_CANDIDATES``/
    ``MAX_SLICE_CANDIDATES`` above for the justification of each number.

    ``content_path``, when given, is queried INSTEAD of the raw source file
    -- this is how DESIGN.md §5 segmentation flows through: once
    ``segment_source --select`` has bounded a huge source down to one
    segment, retrieve_slice must query on that bounded text, not the
    original (possibly 200KB+) file. Defaults to ``None`` (query the raw
    source directly), which is the exact pre-segmentation behavior every
    existing caller (and both eval arms) still gets unchanged.
    """
    pages = load_wiki_pages(wr.wiki_dir)
    source_path = content_path if content_path is not None else wr.sources_dir / source_id
    query_tokens = bm25.tokenize(extract_query_text(source_path))

    corpus_tokens = {pid: page.tokens for pid, page in pages.items()}
    ranker = bm25.BM25(corpus_tokens)

    if not pages:
        hits: list[tuple[str, float]] = []
    elif k is not None:
        hits = ranker.top_k(query_tokens, k)
    else:
        ranked_all = ranker.top_k(query_tokens, len(pages))
        byte_sizes = {pid: page.byte_size for pid, page in pages.items()}
        hits = _select_candidates_by_budget(ranked_all, byte_sizes, budget_bytes, floor, ceiling)

    hit_ids = [pid for pid, _score in hits]
    hit_set = set(hit_ids)

    neighbor_ids: set[str] = set()
    for pid in hit_ids:
        page = pages[pid]
        for slug in page.links:
            target = f"{slug}.md"
            if target in pages:
                neighbor_ids.add(target)
        for other_id, other_page in pages.items():
            if page.slug in other_page.links:
                neighbor_ids.add(other_id)
    neighbor_ids -= hit_set

    nav_ids = {name for name in NAV_PAGES if name in pages}

    ordered: list[str] = []

    def _add(pid: str) -> None:
        if pid not in ordered:
            ordered.append(pid)

    for pid in hit_ids:
        _add(pid)
    for pid in sorted(neighbor_ids):
        _add(pid)
    for pid in sorted(nav_ids):
        _add(pid)

    return {
        "source_id": source_id,
        # The ACTUAL number of BM25 candidates selected -- with an explicit
        # --k override this equals k (min'd against corpus size, unchanged
        # from the original behavior); in the default budget-driven path
        # there is no single requested count to report, so this is the
        # count the budget/floor/ceiling logic actually produced.
        "k": len(hits),
        "pages": ordered,
        "bm25_hits": [{"page": pid, "score": score} for pid, score in hits],
    }


# ---------------------------------------------------------------------------
# Header/body splitting -- shared by segment_source (DESIGN.md §5 turn/
# heading-safe segmentation) and watermark (DESIGN.md §5 stream delta:
# watermark comparisons must be done on the BODY only, never the header --
# see watermark.py's module docstring for why the header is NOT append-only
# stable across re-exports of the same stream identity).
# ---------------------------------------------------------------------------


def split_header(text: str) -> tuple[str, str]:
    """(header block including its trailing '---' + blank line, body).

    Every real source in the target corpus (and CLI-CONTRACT.md's own
    ``kind:`` convention) uses a leading metadata block terminated by a bare
    ``---`` line. Absent that delimiter (kind=article/repo with no header
    convention), there is no header to carry and the whole text is body.
    """
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip() == "---":
            end = i + 1
            if end < len(lines) and lines[end].strip() == "":
                end += 1
            return "".join(lines[:end]), "".join(lines[end:])
    return "", text


# ---------------------------------------------------------------------------
# Structural validation (deterministic -- see wiki_weaver.ingest.validate)
# ---------------------------------------------------------------------------


def find_structural_issues(wiki_dir: Path) -> list[str]:
    """Broken links, missing required frontmatter fields, orphan pages."""
    pages = load_wiki_pages(wiki_dir)
    if not pages:
        return []

    issues: list[str] = []
    incoming: dict[str, int] = dict.fromkeys(pages, 0)
    required_fields = ("title", "type")

    for pid, page in pages.items():
        text = (wiki_dir / pid).read_text(encoding="utf-8")
        meta, _ = parse_frontmatter(text)
        for required in required_fields:
            if not str(meta.get(required, "")).strip():
                issues.append(f"schema: {pid} missing required frontmatter field '{required}'")
        for slug in page.links:
            # A wikilink may target a specific section of a page --
            # [[page-slug#Section Heading]] -- exactly like the file-level
            # link, just with a fragment. The PAGE is what exists on disk;
            # the fragment is not a filename component. Strip it before
            # checking existence/counting incoming links, or every
            # section-anchored link to a real, existing page is reported as
            # a false-positive "broken link" forever (see docs/KNOWN_ISSUES.md
            # #3 -- this exact false positive, present since before any
            # ingest ran, made validate() return non-zero for EVERY source in
            # a 73-source production run, not just the ones with a genuine
            # structural problem).
            target = f"{slug.split('#', 1)[0]}.md"
            if target not in pages:
                issues.append(f"broken link: {pid} -> [[{slug}]] (target page does not exist)")
            else:
                incoming[target] = incoming.get(target, 0) + 1

    for pid in pages:
        if pid not in NAV_PAGES and incoming.get(pid, 0) == 0:
            issues.append(f"orphan: {pid} has no incoming wikilinks")

    return issues


# ---------------------------------------------------------------------------
# Persistent page index (wiki/.index/pages.json) -- incremental maintenance
# so wiki_weaver.ask.load_index does not need to open every page on every
# call (CLI-CONTRACT.md: "index-first ... must NOT open every page in the
# wiki"). Only pages that are new or whose mtime changed since the last
# refresh are actually read and re-tokenized; unchanged pages are served
# straight from the cached index entry.
# ---------------------------------------------------------------------------


def load_page_index(wr: WikiRoot) -> dict[str, dict]:
    """Read the persisted per-page index. Missing or corrupt -> ``{}``
    (the caller rebuilds incrementally from an empty index, which is
    equivalent to a cold-start full build)."""
    path = wr.wiki_index_file
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def refresh_page_index(wr: WikiRoot) -> dict[str, dict]:
    """Incrementally refresh ``wiki/.index/pages.json``.

    Only pages that are new or whose mtime differs from the cached entry
    are opened and re-tokenized; everything else is served from the
    existing cache entry untouched. Returns ``{page_id: {title, links,
    tokens, mtime, cited_sources, frontmatter_sources}}`` and persists the
    result (no-op write if nothing changed since the tracked state is
    already durable).

    ``cited_sources`` (inline ``NNN-Name.md`` references, see
    ``extract_source_citations``) and ``frontmatter_sources`` (the raw
    ``sources:`` frontmatter value, if present) are computed on the SAME
    read that already opens a changed page for title/links/tokens -- no
    additional file opens, so ``wiki_weaver.ask.load_index`` can surface
    each candidate page's cited source files for free (DESIGN.md's hybrid
    query flow: the wiki for orientation, the cited sources for the words).
    """
    index = load_page_index(wr)
    pages = list_wiki_pages(wr.wiki_dir)
    known_ids = {p.name for p in pages}

    changed = False
    for path in pages:
        pid = path.name
        mtime = path.stat().st_mtime
        entry = index.get(pid)
        if entry is not None and entry.get("mtime") == mtime:
            continue  # unchanged since last refresh -- do not reopen this page
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        title = str(meta.get("title", path.stem))
        links = extract_wikilinks(body)
        tokens = bm25.tokenize(f"{title}\n{body}")
        cited_sources = extract_source_citations(text)
        frontmatter_sources = meta.get("sources", [])
        if not isinstance(frontmatter_sources, list):
            frontmatter_sources = [frontmatter_sources]
        index[pid] = {
            "title": title,
            "links": links,
            "tokens": tokens,
            "mtime": mtime,
            "cited_sources": cited_sources,
            "frontmatter_sources": frontmatter_sources,
        }
        changed = True

    for stale_id in set(index) - known_ids:
        del index[stale_id]
        changed = True

    if changed:
        ensure_dir(wr.wiki_index_dir)
        atomic_write_text(wr.wiki_index_file, json.dumps(index) + "\n")

    return index


# ---------------------------------------------------------------------------
# git helpers -- thin, best-effort wrappers
# ---------------------------------------------------------------------------


def git_available(root: Path) -> bool:
    return (root / ".git").exists()


def git_init_if_absent(root: Path) -> None:
    """``git init`` if ``root`` has no ``.git`` yet -- idempotent (per
    ``init.persist``'s contract: "git init if .git absent (idempotent)")."""
    if git_available(root):
        return
    ensure_dir(root)
    _run_git(root, "init", "-q")


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def git_show_head(root: Path, relpath: str) -> str | None:
    """``git show HEAD:<relpath>`` or ``None`` if there is no such blob at HEAD."""
    result = _run_git(root, "show", f"HEAD:{relpath}")
    if result.returncode != 0:
        return None
    return result.stdout


def git_changed_wiki_files(root: Path, wiki_dir_name: str = "wiki") -> set[str]:
    """Basenames of wiki pages modified or newly added in the working tree
    relative to HEAD (tracked modifications + untracked new files)."""
    if not git_available(root):
        return set()
    changed: set[str] = set()

    modified = _run_git(root, "diff", "--name-only", "--", wiki_dir_name)
    if modified.returncode == 0:
        changed.update(Path(p).name for p in modified.stdout.splitlines() if p.strip())

    staged = _run_git(root, "diff", "--staged", "--name-only", "--", wiki_dir_name)
    if staged.returncode == 0:
        changed.update(Path(p).name for p in staged.stdout.splitlines() if p.strip())

    untracked = _run_git(root, "ls-files", "--others", "--exclude-standard", "--", wiki_dir_name)
    if untracked.returncode == 0:
        changed.update(Path(p).name for p in untracked.stdout.splitlines() if p.strip())

    return changed


def count_pages_touched(root: Path, wiki_dir: Path) -> int:
    return len(git_changed_wiki_files(root))


def git_changed_wiki_files_detail(root: Path, wiki_dir_name: str = "wiki") -> tuple[set[str], set[str]]:
    """Split of ``git_changed_wiki_files`` into (created, updated) basenames.

    ``created`` = pages that did not exist at HEAD -- untracked new files, plus
    staged adds (``git diff --staged --diff-filter=A``). ``updated`` = tracked
    pages modified, staged or not. The touches_per_source metric (DESIGN.md's
    headline number -- see pipeline/ingest.dot's weave prompt and its entity
    decomposition) needs this split, not just the total ``count_pages_touched``
    gives: it is the created/updated ratio that shows whether ingest is
    building new landing zones or folding into existing ones.

    A path can surface from more than one of the three underlying git queries
    (e.g. staged-added AND still unstaged-modified again before this check
    runs); ``created`` wins any such overlap -- on net, it is still a page
    that did not exist before this source.
    """
    if not git_available(root):
        return set(), set()

    created: set[str] = set()
    updated: set[str] = set()

    untracked = _run_git(root, "ls-files", "--others", "--exclude-standard", "--", wiki_dir_name)
    if untracked.returncode == 0:
        created.update(Path(p).name for p in untracked.stdout.splitlines() if p.strip())

    staged = _run_git(root, "diff", "--staged", "--name-status", "--", wiki_dir_name)
    if staged.returncode == 0:
        for line in staged.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            status, name = parts[0], parts[-1]
            base = Path(name).name
            (created if status.startswith("A") else updated).add(base)

    modified = _run_git(root, "diff", "--name-only", "--", wiki_dir_name)
    if modified.returncode == 0:
        for p in modified.stdout.splitlines():
            if p.strip():
                updated.add(Path(p).name)

    updated -= created  # each page counts once; new-page wins over "also modified"
    return created, updated


def count_pages_touched_detail(root: Path, wiki_dir: Path) -> dict:
    """``{"created": n, "updated": n, "total": n}`` for the current working tree."""
    created, updated = git_changed_wiki_files_detail(root)
    return {"created": len(created), "updated": len(updated), "total": len(created) + len(updated)}


def summarize_ledger_touches(ledger_path: Path) -> dict:
    """Run-level ``touches_per_source`` summary from ``ledger.jsonl``'s accept rows.

    DESIGN.md's headline metric: mean wiki pages touched per ingested source,
    plus the created/updated split -- the number that shows whether a source
    is being decomposed into the several pages it is actually about (weave's
    per-entity placement decision), rather than one binary create-vs-fold
    call per source. A pure read of durable ledger state, so it can be
    recomputed after every accept and is always consistent with the ledger
    on disk -- no counter of its own to drift out of sync.
    """
    if not ledger_path.is_file():
        return {"sources": 0, "mean_touched": 0.0, "total_created": 0, "total_updated": 0}

    sources = 0
    total_touched = 0
    total_created = 0
    total_updated = 0
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("decision") != "accept":
            continue
        sources += 1
        total_touched += int(record.get("pages_touched", 0) or 0)
        total_created += int(record.get("pages_created", 0) or 0)
        total_updated += int(record.get("pages_updated", 0) or 0)

    return {
        "sources": sources,
        "mean_touched": round(total_touched / sources, 2) if sources else 0.0,
        "total_created": total_created,
        "total_updated": total_updated,
    }


def git_clean_and_checkout(root: Path, relpath: str) -> None:
    """Revert working-tree edits under ``relpath``: discard tracked
    modifications and remove newly created untracked files. Best-effort --
    a wiki with no prior commit has nothing to revert to, which is fine."""
    _run_git(root, "checkout", "--", relpath)
    _run_git(root, "clean", "-fd", "--", relpath)


def git_add_all(root: Path) -> None:
    _run_git(root, "add", "-A")


def git_has_staged_changes(root: Path) -> bool:
    result = _run_git(root, "diff", "--staged", "--quiet")
    return result.returncode != 0


def git_commit(root: Path, message: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", message],
        capture_output=True,
        text=True,
        check=True,
    )


def commit_if_staged(root: Path, message: str) -> bool:
    """``git add -A && git commit`` guarded by ``git diff --staged --quiet``.

    No-op (no phantom commit) when there is nothing to commit. Returns
    whether a commit was made.
    """
    git_add_all(root)
    if git_has_staged_changes(root):
        git_commit(root, message)
        return True
    return False
