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
    ask    <question> [--wiki] answer a question by reading the compiled wiki
    build-dashboard <corpus>   build a self-contained HTML dashboard
    migrate <corpus>           relocate an old-layout corpus to the .wiki/ layout
"""

from __future__ import annotations

import argparse

from wiki_weaver._version import __version__
from wiki_weaver._version_resolve import resolve_version

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

    # Acquire the per-wiki ingest lock (D3) so a manual `wiki-weaver ingest` and a
    # scheduled `schedule run-now` tick on the same wiki cannot run concurrently.
    # This is a triggering-layer concern only -- `ingest()` itself stays lock-free.
    from wiki_weaver import instances as _inst
    from wiki_weaver import pidlock as _lock
    from wiki_weaver.schedule import EXIT_SKIP, validate_limit

    try:
        limit = validate_limit(args.limit)
    except ValueError as exc:
        _fail(str(exc))
        return 2

    lock = _inst.ingest_lock_path(args.wiki)
    res = _lock.try_acquire(lock)
    if not res.acquired:
        _fail(
            f"another ingest is already running for this wiki (PID {res.holder_pid}); "
            f"skipping to avoid a concurrent-write race."
        )
        return EXIT_SKIP
    try:
        return ingest(
            args.wiki,
            source=args.source,
            max_cycles=args.max_cycles,
            keep_going=args.keep_going,
            limit=limit,
        )
    finally:
        _lock.release(lock)


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


def cmd_schedule(args: argparse.Namespace) -> int:
    """Dispatch a `schedule` subcommand (install/remove/status/list/run-now).

    Deterministic dispatch only -- the schedule module owns all logic.
    """
    from wiki_weaver import schedule as sched

    cmd = args.schedule_command
    if cmd == "install":
        return sched.install(
            args.wiki,
            every=args.every,
            cron=args.cron,
            alert_after=args.alert_after,
            limit=args.limit,
        )
    if cmd == "remove":
        if not args.wiki and not args.instance_id:
            _fail("schedule remove: pass --wiki or --id")
            return 2
        return sched.remove(args.wiki, instance_id=args.instance_id, purge=args.purge)
    if cmd == "status":
        if not args.wiki and not args.instance_id:
            _fail("schedule status: pass --wiki or --id")
            return 2
        return sched.status(args.wiki, instance_id=args.instance_id)
    if cmd == "list":
        return sched.list_all()
    if cmd == "run-now":
        return sched.run_now(args.wiki, limit=args.limit)
    # no subcommand -> print schedule help
    _fail("schedule: specify install|remove|status|list|run-now")
    return 2


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


class _VersionAction(argparse.Action):
    """Resolves + prints the version lazily, only when --version is passed.

    Unlike argparse's built-in "version" action (which formats its string
    eagerly at add_argument() time), this defers resolve_version() until the
    flag is actually invoked -- so a possible dev-mode git subprocess call
    (see wiki_weaver._version_resolve) never runs on ordinary command
    invocations, only on `wiki-weaver --version` itself.
    """

    def __init__(self, option_strings, dest=argparse.SUPPRESS, **kwargs) -> None:
        kwargs.setdefault("nargs", 0)
        kwargs.setdefault("help", "show program's version number and exit")
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        print(f"wiki-weaver {resolve_version(__version__)}")
        parser.exit()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="wiki-weaver",
        description="LLM-wiki ingest pipeline driven by the attractor engine.",
    )
    parser.add_argument("--version", action=_VersionAction)
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
    p_ingest.add_argument(
        "--limit",
        type=int,
        default=None,
        help="cap real-ingest sources this run (default: unlimited)",
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

    p_sched = sub.add_parser(
        "schedule", help="manage unattended, cron-scheduled ingestion"
    )
    sched_sub = p_sched.add_subparsers(dest="schedule_command")

    s_install = sched_sub.add_parser("install", help="install a cron entry for a wiki")
    s_install.add_argument("--wiki", required=True, help="wiki directory to schedule")
    g = s_install.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--every",
        metavar="INTERVAL",
        help="friendly interval sugar, e.g. 5m, 30m, 1h, 1d",
    )
    g.add_argument(
        "--cron",
        metavar="EXPR",
        help="raw 5-field cron expression (power-user escape hatch)",
    )
    s_install.add_argument(
        "--alert-after",
        type=int,
        default=3,
        dest="alert_after",
        help="escalate after N consecutive skipped cycles (default: 3)",
    )
    s_install.add_argument(
        "--limit",
        type=int,
        default=None,
        help="per-tick cap on real-ingest sources (default: 10)",
    )

    s_remove = sched_sub.add_parser("remove", help="remove a wiki's cron entry")
    s_remove.add_argument(
        "--wiki", help="wiki directory (canonicalized to find the instance)"
    )
    s_remove.add_argument(
        "--id", dest="instance_id", help="instance id (use after moving a wiki)"
    )
    s_remove.add_argument(
        "--purge",
        action="store_true",
        help="also delete the instance's stored config/state/logs",
    )

    s_status = sched_sub.add_parser(
        "status", help="show a wiki's schedule + last-run state"
    )
    s_status.add_argument("--wiki", help="wiki directory")
    s_status.add_argument("--id", dest="instance_id", help="instance id")

    sched_sub.add_parser("list", help="list all scheduled wikis and their state")

    s_run = sched_sub.add_parser(
        "run-now", help="run one ingest tick now (what cron invokes)"
    )
    s_run.add_argument("--wiki", required=True, help="wiki directory")
    s_run.add_argument(
        "--limit",
        type=int,
        default=None,
        help="override the persisted per-tick cap for THIS tick only",
    )

    args = parser.parse_args()

    dispatch = {
        "init": cmd_init,
        "ingest": cmd_ingest,
        "lint": cmd_lint,
        "doctor": cmd_doctor,
        "update": cmd_update,
        "ask": cmd_ask,
        "build-dashboard": cmd_build_dashboard,
        "migrate": cmd_migrate,
        "schedule": cmd_schedule,
    }
    if args.command is None:
        parser.print_help()
        raise SystemExit(0)
    raise SystemExit(dispatch[args.command](args))


if __name__ == "__main__":
    main()
