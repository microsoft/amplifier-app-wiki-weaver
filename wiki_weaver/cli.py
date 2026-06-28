# pyright: reportMissingImports=false
#!/usr/bin/env python3
"""wiki-weaver CLI.

Thin argparse wrapper around the importable lib API (wiki_weaver.lib).

Subcommands:
    init <wiki_dir>            scaffold a fresh wiki
    ingest [--wiki] [--source] integrate inbox sources via the engine
    lint   [--wiki]            run the structural validator
    doctor                     environment diagnostics
    update [--check]           refresh @main sources to latest
    query  [--wiki] <q>        (stub) list pages matching a term
    ask    <question> [--wiki] answer a question by reading the compiled wiki
    build-dashboard <corpus>   build a self-contained HTML dashboard
    migrate <corpus>           relocate an old-layout corpus to the .wiki/ layout
"""

from __future__ import annotations

import argparse

from wiki_weaver._version import __version__

# ---------------------------------------------------------------------------
# Re-exports: symbols imported by tests from wiki_weaver.cli
# ---------------------------------------------------------------------------
from wiki_weaver.lib import (
    SOURCES,
    FAILED,
    INBOX,
    REGISTRY_NAME,
    _assign_source_id,
    _fail,
    _parse_transcript_header,
    _read_source_frontmatter,
    ask,
    doctor,
    ingest,
    init,
    lint,
    migrate,
    preflight,
    query,
    update,
)

__all__ = [
    # constants (test imports)
    "SOURCES",
    "FAILED",
    "INBOX",
    "REGISTRY_NAME",
    # helpers (test imports)
    "_assign_source_id",
    "_parse_transcript_header",
    "_read_source_frontmatter",
    # clean lib API (re-exported for convenience)
    "init",
    "ingest",
    "lint",
    "doctor",
    "update",
    "query",
    "ask",
    "migrate",
]


# ---------------------------------------------------------------------------
# cmd_* wrappers: unpack argparse.Namespace → call lib function
# ---------------------------------------------------------------------------
# These stay here (not in lib) so they remain importable from wiki_weaver.cli,
# which is what tests and the main() dispatch expect.


def _gate(*, require_api_key: bool) -> int:
    """Run the HARD-prereq preflight; return 0 if OK, nonzero if not.

    On failure, print the clean one/two-line message(s) + a doctor hint so a
    broken environment fails UPFRONT instead of crashing minutes into an
    engine/LLM run. preflight() catches ImportError etc. internally, so nothing
    bubbles up as a traceback.
    """
    failures = preflight(require_api_key=require_api_key)
    if not failures:
        return 0
    for msg in failures:
        _fail(msg)
    print("Run `wiki-weaver doctor` for full diagnostics.")
    return 1


def cmd_init(args: argparse.Namespace) -> int:
    # init drives the engine + LLM (schema design) -> full preflight w/ key.
    if rc := _gate(require_api_key=True):
        return rc

    from wiki_weaver.engine_runner import run_init

    return run_init(
        args.wiki_dir,
        purpose=args.purpose,
        sample_inbox=not args.no_sample_inbox,
        plain=args.plain,
    )


def cmd_ingest(args: argparse.Namespace) -> int:
    # ingest drives the engine + LLM convergence loop -> full preflight w/ key.
    if rc := _gate(require_api_key=True):
        return rc

    return ingest(
        args.wiki,
        source=args.source,
        max_cycles=args.max_cycles,
        keep_going=args.keep_going,
    )


def cmd_lint(args: argparse.Namespace) -> int:
    # lint runs validate_wiki.py deterministically through the engine -- no LLM
    # call, so it needs foundation + the validator but NOT an API key.
    if rc := _gate(require_api_key=False):
        return rc

    from wiki_weaver.engine_runner import run_lint

    return run_lint(args.wiki)


def cmd_doctor(args: argparse.Namespace) -> int:
    return doctor(wiki=args.wiki)


def cmd_update(args: argparse.Namespace) -> int:
    return update(check_only=args.check)


def cmd_query(args: argparse.Namespace) -> int:
    # query is a pure substring grep over the compiled wiki -- no engine, no
    # foundation, no key. Deliberately NOT gated so it works fully offline.
    return query(args.wiki, args.term)


def cmd_ask(args: argparse.Namespace) -> int:
    # ask spawns an engine sub-session that reads the wiki + calls the LLM ->
    # full preflight w/ key.
    if rc := _gate(require_api_key=True):
        return rc

    return ask(args.wiki, args.question, json_out=args.json_out)


def cmd_migrate(args: argparse.Namespace) -> int:
    """Migrate a corpus from the OLD (pre-0.5.0) layout to the NEW layout.

    Deterministic — no LLM, no Amplifier runtime required.
    """
    from pathlib import Path

    corpus = Path(args.corpus).expanduser().resolve()
    return migrate(corpus, dry_run=args.dry_run, force=args.force)


def cmd_build_dashboard(args: argparse.Namespace) -> int:
    """Build a self-contained HTML dashboard from a wiki corpus.

    Deterministic — no LLM, no Amplifier runtime required.  Builds corpus
    indexes first (unless --skip-index), then renders the Almanac-themed
    dashboard HTML.
    """
    import json
    from pathlib import Path

    from wiki_weaver.dashboard import build_dashboard
    from wiki_weaver.index import build_indexes

    corpus = Path(args.corpus).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()

    if not corpus.is_dir():
        _fail(f"corpus directory not found: {corpus}")
        return 1

    theme: dict | None = None
    if args.theme:
        theme_path = Path(args.theme).expanduser().resolve()
        try:
            theme = json.loads(theme_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            _fail(f"theme file not found: {theme_path}")
            return 1
        except json.JSONDecodeError as exc:
            _fail(f"invalid JSON in theme file {theme_path}: {exc}")
            return 1

    if not args.skip_index:
        build_indexes(corpus)

    build_dashboard(
        corpus,
        out,
        theme=theme,
        group_by=args.group_by,
        group_link_template=args.group_link_template,
    )
    print(f"Dashboard written \u2192 {out}")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="wiki-weaver",
        description="LLM-wiki ingest pipeline driven by the attractor engine.",
    )
    parser.add_argument(
        "--version", action="version", version=f"wiki-weaver {__version__}"
    )
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser(
        "init", help="scaffold a wiki directory and design its schema"
    )
    p_init.add_argument("wiki_dir")
    p_init.add_argument(
        "--purpose",
        default=None,
        metavar="TEXT",
        help=(
            "Rich free-text description of the wiki's intended use and desired outcomes. "
            "When provided (and ANTHROPIC_API_KEY is set), the LLM designs a domain-fit "
            "schema and writes it to <wiki>/.wiki/policy/schema.md. "
            "Example: --purpose 'AI coding tools second brain for answering which tool "
            "to use for X and comparing alternatives'"
        ),
    )
    p_init.add_argument(
        "--plain",
        action="store_true",
        help="scaffold only — no LLM schema design, use the generic built-in schema",
    )
    p_init.add_argument(
        "--no-sample-inbox",
        action="store_true",
        dest="no_sample_inbox",
        help="do not sample existing _inbox/ sources to inform schema design",
    )

    p_ingest = sub.add_parser("ingest", help="integrate inbox sources via the engine")
    p_ingest.add_argument("--wiki", default=".", help="wiki directory (default: .)")
    p_ingest.add_argument("--source", default=None, help="ingest a single source file")
    p_ingest.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="convergence budget (default: from wiki.config.yaml or 3)",
    )
    p_ingest.add_argument(
        "--keep-going",
        action="store_true",
        help="continue to next source after a failure",
    )

    p_lint = sub.add_parser("lint", help="run the structural validator")
    p_lint.add_argument("--wiki", default=".", help="wiki directory (default: .)")

    p_doctor = sub.add_parser("doctor", help="environment diagnostics")
    p_doctor.add_argument(
        "--wiki", default=None, help="also check this wiki's structure"
    )

    p_update = sub.add_parser(
        "update",
        help=(
            "refresh wiki-weaver and its @main engine sources to latest "
            "(Layer 1: uv reinstall; Layer 2: engine bundle re-clone)"
        ),
    )
    p_update.add_argument(
        "--check",
        action="store_true",
        help=(
            "detect and report drift only — ls-remote each @main source and compare "
            "to local commit; no reinstall, no rmtree (safe, read-only)"
        ),
    )
    # --dry-run is an alias for --check (spec names both)
    p_update.add_argument(
        "--dry-run",
        dest="check",
        action="store_true",
        help="alias for --check",
    )

    p_query = sub.add_parser(
        "query",
        help="naive substring page search; for real cited answers use 'ask'",
    )
    p_query.add_argument("term")
    p_query.add_argument("--wiki", default=".", help="wiki directory (default: .)")

    p_ask = sub.add_parser(
        "ask", help="answer a question by reading the compiled wiki (no embeddings)"
    )
    p_ask.add_argument("question", help="question to answer")
    p_ask.add_argument("--wiki", default=".", help="wiki directory (default: .)")
    p_ask.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="output JSON: {answer, pages_used, refused}",
    )

    p_build_dashboard = sub.add_parser(
        "build-dashboard",
        help="build a self-contained HTML dashboard from a wiki corpus",
    )
    p_build_dashboard.add_argument("corpus", help="wiki corpus directory")
    p_build_dashboard.add_argument(
        "--out",
        required=True,
        metavar="PATH",
        help="destination .html file",
    )
    p_build_dashboard.add_argument(
        "--theme",
        default=None,
        metavar="PATH",
        help="path to theme.json (optional; overrides .wiki/dashboard/theme.json in corpus)",
    )
    p_build_dashboard.add_argument(
        "--group-by",
        default="type",
        metavar="FIELD",
        dest="group_by",
        help="frontmatter field to group sidebar nav by (default: type)",
    )
    p_build_dashboard.add_argument(
        "--group-link-template",
        default=None,
        metavar="TEMPLATE",
        dest="group_link_template",
        help=(
            "URL template for group header links.  {group} is replaced by the "
            "URL-encoded group value.  Only http:// and https:// schemes are "
            "accepted; non-http templates are ignored with a warning.  "
            "Example: --group-link-template 'https://github.com/{group}'"
        ),
    )
    p_build_dashboard.add_argument(
        "--skip-index",
        action="store_true",
        dest="skip_index",
        help="skip index rebuild (use existing .wiki/index/ files)",
    )

    p_migrate = sub.add_parser(
        "migrate",
        help=(
            "migrate a corpus from the OLD layout (pre-0.5.0) to the NEW layout "
            "(machine files under .wiki/, _archive/ renamed to _sources/)"
        ),
    )
    p_migrate.add_argument("corpus", help="wiki corpus directory to migrate")
    p_migrate.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="print the migration plan without making any changes",
    )
    p_migrate.add_argument(
        "--force",
        action="store_true",
        help="re-run even if the migration sentinel already exists",
    )

    args = parser.parse_args()

    dispatch = {
        "init": cmd_init,
        "ingest": cmd_ingest,
        "lint": cmd_lint,
        "doctor": cmd_doctor,
        "update": cmd_update,
        "query": cmd_query,
        "ask": cmd_ask,
        "build-dashboard": cmd_build_dashboard,
        "migrate": cmd_migrate,
    }
    if args.command is None:
        parser.print_help()
        raise SystemExit(0)
    raise SystemExit(dispatch[args.command](args))


if __name__ == "__main__":
    main()
