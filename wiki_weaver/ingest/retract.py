"""wiki_weaver.ingest.retract -- give a gate answerer a bounded, auditable
way to remove OR absorb a page that already exists in the wiki (F4,
GOAL-followups.md).

THE DEFECT THIS FIXES: ``weave`` creates and updates pages -- there was no
path to remove one. During the human baseline run (E1), a page named
``joe-njenga.md`` (a biography page -- exactly the kind of noun page this
wiki is not for) leaked in at source 016. The human asked for it to be
removed TWICE, at source 017 and again at source 018:

    "a page named after a PERSON is never correct in this wiki ... Delete
    joe-njenga.md ..."
    "Standing instruction, still in force: no page named after a person, a
    vendor, a product, or a piece of hardware. joe-njenga.md is still
    present from source 016 and should not be."

It was still there at the end of the run. A reviewer who catches a mistake
one source too late had no recourse at all.

DESIGN CHOICE -- absorb, not just delete (see GOAL-followups.md's F4
guidance): reading what the human actually said twice, the underlying
argument the offending source made was legitimate content -- what they
wanted was for the WIKI to stop having a page named after a person, not
necessarily for the material to vanish. A bare delete risks doing exactly
the thing this wiki's own worst failure mode already does twice over
(silently losing real content) -- so this module supports BOTH primitives:

  - ``mode="delete"``: remove the page outright. Use when the page is pure
    noise with nothing worth keeping (the human's literal "delete" word).
  - ``mode="absorb"``: append the page's body into an EXISTING target page
    under a clearly-marked "Absorbed from" heading, then remove the
    now-empty container. Use when the content is legitimate but placed
    wrong (the human's actual intent, read in context -- "authors get
    cited inside argument pages; they do not get their own pages").

Both share identical ledger/log/commit mechanics -- see ``retract_page``
below -- distinguished only by the ``mode`` field, same discipline
``commit.py`` already uses for its ``accept``/``skip``/``quarantine``/
``declined`` decision values.

AUDITABLE, LIKE ``declined``/``quarantine``: every retraction appends a
``{"decision": "retract", ...}`` row to the SAME ``ledger.jsonl`` every
other gate decision lands in -- a distinct decision value, not a silent
filesystem change. Deliberately carries NO ``source_id`` key (the source-
segment-tracking convention every other row uses) -- ``lib
._segment_status_by_source`` skips any record whose ``source_id`` is
absent, so a retraction can never be mistaken for that source's own
accept/skip/quarantine/declined decision, and can never corrupt
``ledgered_source_ids``/``already_ledgered``'s segment-completion
bookkeeping. Provenance (which source's gate prompted this, if any) is
still carried, under the deliberately DIFFERENT field name
``requested_during_source_id`` -- present for a human reading the ledger,
invisible to every function that groups rows by ``source_id``.

REVERSIBLE IN PRINCIPLE: exactly like every other durable decision in this
codebase, a retraction is its own single git commit (guarded by
``commit_if_staged``, same as ``commit.py``) -- ``git revert`` undoes
exactly this action and nothing else.

FAIL LOUD, NEVER A SILENT NO-OP (see GOAL-followups.md's F4 constraints):
unlike ``commit.py``'s accept/skip/quarantine/declined, this is
deliberately NOT idempotent. If the named page does not exist, or an
absorb target does not exist, this raises -- it does not print "already
done" and return success. A silent no-op here is exactly the failure mode
that let ``joe-njenga.md`` survive two explicit removal requests.

BOUNDED BLAST RADIUS: exactly one page is ever removed, and (absorb only)
exactly one existing target page is ever appended to -- never created,
never a third page touched. A directive naming more than one page action,
or a page/target name that could reach outside ``wiki/`` (a path
separator, ``..``, a missing ``.md`` extension), is rejected before
anything is mutated. This module never rewrites links FROM other pages
INTO the removed page -- ``wiki_weaver.ingest.validate``'s existing broken-
link/orphan detector already surfaces that as an ordinary structural
finding on the next pass, exactly as it does for any other edit.

TWO CALLING CONVENTIONS:

  1. Direct CLI, for a human/operator running this against a wiki-root
     directly, at any time, independent of any pipeline run:
         python3 -m wiki_weaver.ingest.retract --wiki-root <path> \\
             --page <page.md> [--target <target.md>] \\
             [--requested-during-source <source-id>]
     ``--target`` present -> absorb; absent -> delete.

  2. Embedded in a freeform gate answer, parsed by
     ``extract_retract_directive`` and executed by whichever node persists
     that answer -- wired into ``wiki_weaver.ingest.persist_takeaways``
     (the exact gate the human's two requests above were made at), which
     needed ZERO ``pipeline/ingest.dot`` changes since ``persist_takeaways``
     already runs unconditionally after every ``takeaways_gate`` answer.
     Directive syntax, one dedicated line anywhere in the freeform text:

         RETRACT: <page.md>
         ABSORB: <page.md> INTO: <target.md>

     Case-insensitive keyword, `wiki/`-prefixed or bare filenames both
     accepted. Any text before/after the directive line is ordinary
     takeaways guidance and is preserved (see ``extract_retract_directive``
     below).

Usage (direct CLI):
    python3 -m wiki_weaver.ingest.retract --wiki-root <path> --page <page.md>
    python3 -m wiki_weaver.ingest.retract --wiki-root <path> \\
        --page <page.md> --target <target.md>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from wiki_weaver.lib import (
    WikiRoot,
    atomic_append_jsonl,
    atomic_write_text,
    commit_if_staged,
    ensure_dir,
    git_available,
    parse_frontmatter,
)


class RetractDirectiveError(ValueError):
    """A RETRACT:/ABSORB: directive was present in freeform text but
    malformed, ambiguous, or violates the bounded blast-radius contract
    (see module docstring). Raised, never silently swallowed -- a
    directive an answerer typed that gets quietly ignored is exactly the
    defect this module exists to fix. A caller parsing gate text (e.g.
    ``persist_takeaways.main``) should let this propagate as a hard,
    fail-loud error, same discipline every other freeform-capture tool in
    this codebase already applies to a malformed/stub answer.
    """


@dataclass(frozen=True)
class RetractDirective:
    """One bounded page action: remove ``page`` outright, or absorb its
    body into ``target`` (an EXISTING page) and then remove ``page``.
    Both fields are bare ``wiki/``-relative filenames (e.g.
    ``"joe-njenga.md"``), never a path -- see ``_normalize_page_name``.
    """

    page: str
    mode: str  # "delete" | "absorb"
    target: str | None = None


# One directive line, anywhere in the text: RETRACT: <page> or
# ABSORB: <page> INTO: <target>. Anchored per-line (MULTILINE) so it can
# appear alongside ordinary freeform guidance text without needing to be
# the only content of the answer.
_DIRECTIVE_LINE_RE = re.compile(
    r"^[ \t]*(RETRACT|ABSORB)[ \t]*:[ \t]*(\S+?)(?:[ \t]+INTO[ \t]*:[ \t]*(\S+))?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


def _normalize_page_name(raw: str) -> str:
    """Strip an optional leading ``wiki/`` (the convention every artifact
    weave reads renders pages in -- see ``lib.to_wiki_relpath``) and
    reject anything that could reach outside ``wiki_dir`` -- this is the
    blast-radius enforcement at the parsing boundary, before any file is
    ever touched."""
    name = raw.strip()
    name = name.removeprefix("wiki/")
    if "/" in name or "\\" in name or ".." in name or not name.endswith(".md") or name in (".md",):
        raise RetractDirectiveError(
            f"'{raw}' is not a bare wiki page filename (expected e.g. 'some-page.md' or 'wiki/some-page.md')"
        )
    return name


def extract_retract_directive(text: str) -> tuple[RetractDirective | None, str]:
    """Scan freeform gate ``text`` for AT MOST ONE ``RETRACT:``/``ABSORB:``
    directive line. Returns ``(directive_or_None, remaining_text)`` --
    ``remaining_text`` is ``text`` with the directive line removed
    (stripped), so ordinary guidance in the same answer survives untouched.

    Raises ``RetractDirectiveError`` (never silently drops the request) when:
      - more than one directive line is found (bounded to one page action
        per answer -- see module docstring's blast-radius contract),
      - ``RETRACT:`` is given WITH an ``INTO:`` clause (ambiguous -- use
        ``ABSORB:`` to merge),
      - ``ABSORB:`` is given WITHOUT an ``INTO:`` clause (nothing to
        absorb into),
      - the page or target name is not a bare wiki filename, or
      - absorb's target is the same page as the one being retracted.
    """
    matches = list(_DIRECTIVE_LINE_RE.finditer(text))
    if not matches:
        return None, text
    if len(matches) > 1:
        raise RetractDirectiveError(
            f"found {len(matches)} RETRACT/ABSORB directive lines in one answer -- "
            "exactly one page action is allowed per answer; submit them one at a time"
        )

    match = matches[0]
    keyword = match.group(1).upper()
    page_raw, target_raw = match.group(2), match.group(3)
    page = _normalize_page_name(page_raw)

    if keyword == "RETRACT":
        if target_raw is not None:
            raise RetractDirectiveError(
                f"'RETRACT: {page_raw} INTO: {target_raw}' is ambiguous -- use "
                f"'ABSORB: {page_raw} INTO: {target_raw}' to merge, or 'RETRACT: {page_raw}' "
                "alone to delete outright"
            )
        directive = RetractDirective(page=page, mode="delete", target=None)
    else:  # ABSORB
        if target_raw is None:
            raise RetractDirectiveError(
                f"'ABSORB: {page_raw}' is missing its target -- use 'ABSORB: {page_raw} INTO: <target-page.md>'"
            )
        target = _normalize_page_name(target_raw)
        if target == page:
            raise RetractDirectiveError(f"cannot absorb '{page}' into itself")
        directive = RetractDirective(page=page, mode="absorb", target=target)

    remaining = (text[: match.start()] + text[match.end() :]).strip()
    return directive, remaining


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC"


def _log_date() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _append_log(log_path: Path, line: str) -> None:
    existing = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    ensure_dir(log_path.parent)
    atomic_write_text(log_path, existing + line)


def _page_title(text: str, fallback_stem: str) -> str:
    meta, _body = parse_frontmatter(text)
    title = meta.get("title")
    return str(title) if title else fallback_stem


def retract_page(
    wr: WikiRoot,
    directive: RetractDirective,
    requested_during_source_id: str | None = None,
) -> dict:
    """Execute ONE bounded page removal/absorption and record it durably:
    mutate the wiki, append the ledger row, append the log.md line, one
    git commit. Returns the ledger record that was appended.

    FAIL LOUD, deliberately NOT idempotent (see module docstring): raises
    ``FileNotFoundError`` if ``directive.page`` does not exist, or (absorb
    only) if ``directive.target`` does not exist. A page already gone on a
    second invocation is treated as a genuine error, not "already done."
    """
    page_path = wr.wiki_dir / directive.page
    if not page_path.is_file():
        raise FileNotFoundError(f"{page_path} does not exist -- nothing to {directive.mode}")

    if directive.mode == "absorb":
        if directive.target is None:
            raise ValueError("absorb mode requires a target page")  # pragma: no cover -- guarded at parse time
        target_path = wr.wiki_dir / directive.target
        if not target_path.is_file():
            raise FileNotFoundError(f"{target_path} (absorb target) does not exist")

        page_text = page_path.read_text(encoding="utf-8")
        _meta, page_body = parse_frontmatter(page_text)
        title = _page_title(page_text, Path(directive.page).stem)
        target_text = target_path.read_text(encoding="utf-8")

        merged = (
            target_text.rstrip("\n") + f"\n\n## Absorbed from {directive.page} ({title})\n\n" + page_body.strip() + "\n"
        )
        atomic_write_text(target_path, merged)
    elif directive.mode != "delete":
        raise ValueError(f"unknown retract mode: {directive.mode!r}")  # pragma: no cover -- guarded at parse time

    page_path.unlink()

    record = {
        "decision": "retract",
        "mode": directive.mode,
        "page": directive.page,
        "target_page": directive.target,
        "requested_during_source_id": requested_during_source_id,
        "timestamp": _timestamp(),
    }
    atomic_append_jsonl(wr.ledger_path, record)

    if directive.mode == "absorb":
        log_note = f"Content absorbed into {directive.target}; original page removed."
        heading_suffix = f" -> {directive.target}"
    else:
        log_note = "Removed outright -- no absorb target given."
        heading_suffix = ""
    provenance_note = f" Requested during {requested_during_source_id}." if requested_during_source_id else ""
    _append_log(
        wr.log_path,
        f"## [{_log_date()}] retract | {directive.page}{heading_suffix}\n\n"
        f"{log_note}{provenance_note} ({_timestamp()})\n\n",
    )

    if git_available(wr.root):
        commit_message = (
            f"wiki-weaver: retract {directive.page} (absorbed into {directive.target})"
            if directive.mode == "absorb"
            else f"wiki-weaver: retract {directive.page} (deleted)"
        )
        commit_if_staged(wr.root, commit_message)

    return record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.retract")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--page", required=True, help="bare wiki filename to remove, e.g. joe-njenga.md")
    parser.add_argument(
        "--target",
        default=None,
        help="if given, absorb --page's content into this EXISTING page before removing --page; "
        "if omitted, --page is deleted outright",
    )
    parser.add_argument(
        "--requested-during-source",
        default=None,
        help="the in-flight source id this removal was requested during, if any (provenance only -- "
        "never affects source-segment ledger bookkeeping)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    page = _normalize_page_name(args.page)
    if args.target:
        target = _normalize_page_name(args.target)
        if target == page:
            print(f"cannot absorb '{page}' into itself", file=sys.stderr)
            return 1
        directive = RetractDirective(page=page, mode="absorb", target=target)
    else:
        directive = RetractDirective(page=page, mode="delete", target=None)

    try:
        record = retract_page(wr, directive, requested_during_source_id=args.requested_during_source)
    except (FileNotFoundError, ValueError, RetractDirectiveError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
