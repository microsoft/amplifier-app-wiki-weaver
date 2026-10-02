"""wiki-weaver CLI: a dispatcher.

Verbs that need a model (init --purpose / interview, ingest, ask) launch
``dot-runner run pipeline/<graph>.dot``. Everything else is plain Python.
This module never calls a model.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, pidlock
from . import result as rs
from .dashboard import build_dashboard
from .ledger import eligible
from .lib import (
    feedback_log,
    lens_path,
    page_files,
    wiki_failed,
    wiki_inbox,
    wiki_ledger,
    wiki_runs,
    wiki_sources,
)
from .steps import GIT_ID, jlines
from .version import resolve_version

PKG = Path(__file__).resolve().parent
CORPUS_IGNORES = (
    "_inbox/",
    ".wiki/work/",
    ".wiki/runs/",
    ".wiki/ask/",
    ".wiki/init/",
    ".wiki/ingest.lock",
    ".DS_Store",
)


def pipeline_dir() -> Path:
    for cand in (PKG / "pipeline", PKG.parent / "pipeline"):
        if (cand / "ingest.dot").is_file():
            return cand
    raise SystemExit("wiki-weaver: pipeline/*.dot not found (broken install)")


def stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def die(msg: str, code: int = 1) -> None:
    print(f"wiki-weaver: {msg}", file=sys.stderr)
    raise SystemExit(code)


def run_graph(
    graph: str,
    corpus: Path,
    params: dict[str, str],
    logs_root: Path,
    human_gate: str = "fail",
    log_file: Path | None = None,
) -> int:
    """Launch one graph on the dot-runner engine with the corpus as cwd."""
    exe = shutil.which("dot-runner")
    if not exe:
        die("dot-runner not found on PATH (run `wiki-weaver doctor`)")
    cmd = [exe, "run", str(pipeline_dir() / f"{graph}.dot"), "--cwd", "."]
    cmd += ["--logs-root", str(logs_root), "--on-human-gate", human_gate]
    for k, v in {"py": sys.executable, **params}.items():
        cmd += ["--param", f"{k}={v}"]
    logs_root.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_file or logs_root.parent / f"{graph}.engine.log"
    if human_gate == "console":
        return subprocess.run(cmd, cwd=corpus, check=False).returncode
    with log_file.open("a") as fh:
        return subprocess.run(
            cmd,
            cwd=corpus,
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        ).returncode


def git(corpus: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *GIT_ID, *args], cwd=corpus, capture_output=True, text=True, check=False
    )


def scaffold(corpus: Path) -> None:
    """Create the layout. Idempotent: never overwrites anything that exists."""
    for d in (
        wiki_inbox(corpus),
        wiki_sources(corpus),
        wiki_failed(corpus),
        wiki_runs(corpus),
        corpus / "lens" / "corrections",
        corpus / "feedback",
    ):
        d.mkdir(parents=True, exist_ok=True)
    for keep in (wiki_sources(corpus), wiki_failed(corpus), corpus / "lens" / "corrections"):
        (keep / ".gitkeep").touch()
    feedback_log(corpus).touch()
    reading = corpus / "READING.md"
    if not reading.exists():
        shutil.copy2(PKG / "data" / "READING.md", reading)
    gi = corpus / ".gitignore"
    have = gi.read_text().splitlines() if gi.exists() else []
    missing = [line for line in CORPUS_IGNORES if line not in have]
    if missing:
        with gi.open("a") as f:
            if have and have[-1].strip():
                f.write("\n")
            f.write("\n".join(missing) + "\n")
    if not (corpus / ".git").exists():
        git(corpus, "init", "-q")
    commit_pending(corpus, "chore: scaffold wiki")


def commit_pending(corpus: Path, message: str) -> None:
    if git(corpus, "status", "--porcelain").stdout.strip():
        git(corpus, "add", "-A")
        git(corpus, "commit", "-q", "-m", message)


# ---------------------------------------------------------------- verbs


def cmd_init(a: argparse.Namespace) -> int:
    corpus = Path(a.wiki_dir).expanduser().resolve()
    corpus.mkdir(parents=True, exist_ok=True)
    scaffold(corpus)
    if lens_path(corpus).exists():
        print(f"wiki-weaver: {corpus} already initialized; lens.md kept as is")
        return 0
    if a.plain:
        mode = "plain"
    elif a.purpose:
        mode = "purpose"
        p = corpus / ".wiki" / "init" / "purpose.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(a.purpose.strip() + "\n")
    else:
        mode = "interview" if sys.stdin.isatty() else "plain"
    run_dir = wiki_runs(corpus) / f"init-{stamp()}"
    gate = "console" if mode == "interview" else "fail"
    rc = run_graph("init", corpus, {"mode": mode}, run_dir / "engine", human_gate=gate)
    if rc != 0 or not lens_path(corpus).exists():
        die(f"init failed (engine exit {rc}); logs in {run_dir}")
    commit_pending(corpus, "init: lens.md")
    print(f"wiki-weaver: initialized {corpus} (lens: {mode})")
    return 0


def _trace(logs_root: Path) -> list[dict]:
    rows: list[dict] = []
    for tr in sorted(logs_root.rglob("trace.jsonl")):
        rows += jlines(tr)
    return rows


def _preflight(corpus: Path) -> list[str]:
    problems = []
    if not lens_path(corpus).exists():
        problems.append(f"{corpus} has no lens.md; run `wiki-weaver init {corpus}` first")
    if not shutil.which("dot-runner"):
        problems.append("dot-runner not found on PATH")
    if not shutil.which("git"):
        problems.append("git not found on PATH")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        problems.append("ANTHROPIC_API_KEY is not set")
    return problems


BATCH = 40  # sources per engine run; the engine caps steps at 50 x node count per run


def cmd_ingest(a: argparse.Namespace) -> int:
    corpus = Path(a.wiki).expanduser().resolve()
    if not corpus.is_dir():
        print(f"wiki-weaver: wiki dir not found: {corpus}", file=sys.stderr)
        return rs.EXIT_ERRORED
    lock = pidlock.lock_path(corpus)
    if not pidlock.acquire(lock):
        print(
            f"wiki-weaver: another ingest holds {corpus} (PID {pidlock.holder(lock)}); skipping",
            file=sys.stderr,
        )
        return rs.EXIT_LOCKED
    prev = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(1)))
    try:
        return _ingest_locked(corpus, a)
    finally:
        signal.signal(signal.SIGTERM, prev)
        pidlock.release(lock)


def _ingest_locked(corpus: Path, a: argparse.Namespace) -> int:
    started, t0 = datetime.now(UTC), time.time()
    run_id = f"ingest-{stamp()}"
    run_dir = wiki_runs(corpus) / run_id
    run_dir.mkdir(parents=True)
    rows: list[dict] = []
    errored: list[dict] = []
    trace: list[dict] = []
    before_pages = {p.name for p in page_files(corpus)}
    batches = 0

    def snapshot(status: str) -> dict:
        per_node = {
            n: sum(1 for t in trace if t.get("node_id") == n) for n in ("brief", "write", "index")
        }
        conv = [r for r in rows if r.get("converged")]
        touched = {p for r in conv for p in r.get("pages_touched", [])}
        data = rs.build(
            run_id=run_id,
            status=status,
            rows=rows,
            errored=errored,
            extra={
                "started": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "finished": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                if status == "final"
                else None,
                "wall_seconds": round(time.time() - t0, 1),
                "pages_written": len(touched),
                "pages_created": len({p for p in touched if p not in before_pages}),
                "model_calls": sum(per_node.values()),
                "model_calls_by_node": per_node,
                "batches": batches,
                "max_cycles": a.max_cycles,
                "limit": a.limit,
                "source": a.source,
            },
        )
        rs.write(run_dir / "result.json", data)
        return data

    try:
        problems = _preflight(corpus)
        if problems:
            errored += [{"reason": f"preflight: {p}"} for p in problems]
            for p in problems:
                print(f"wiki-weaver: {p}", file=sys.stderr)
            return rs.EXIT_FOR_VERDICT[snapshot("final")["verdict"]]
        scaffold(corpus)
        commit_pending(corpus, "chore: snapshot before ingest")
        only = Path(a.source).name if a.source else None
        if only:
            if not ((wiki_inbox(corpus) / only).exists() or (wiki_failed(corpus) / only).exists()):
                errored.append({"reason": f"--source {only}: not in _inbox/ or .wiki/failed/"})
                print(f"wiki-weaver: --source {only} not found", file=sys.stderr)
                return rs.EXIT_FOR_VERDICT[snapshot("final")["verdict"]]
        elif not eligible(corpus):
            # decided once, before any batching
            print("wiki-weaver: nothing to do (no new sources in _inbox/)")
            snapshot("final")
            return rs.EXIT_EMPTY
        snapshot("in_progress")
        remaining = a.limit if a.limit and a.limit > 0 else None
        while True:
            batches += 1
            cap = BATCH if remaining is None else min(BATCH, remaining)
            bdir = run_dir / f"batch-{batches:02d}"
            bdir.mkdir()
            params = {"run_dir": str(bdir), "cap": str(cap), "only": only or "-"}
            rc = run_graph("ingest", corpus, params, bdir / "engine", log_file=bdir / "engine.log")
            brows = jlines(bdir / "changes.jsonl")
            rows += brows
            btrace = _trace(bdir / "engine")
            trace += btrace
            if rc != 0:
                errored.append(
                    {
                        "reason": f"engine run failed in batch {batches} (exit {rc}); see {bdir}/engine.log"
                    }
                )
            if any(t.get("node_id") == "index" and t.get("status") != "success" for t in btrace):
                errored.append(
                    {
                        "reason": f"index step failed in batch {batches}; pages were committed, index.md/log.md may be stale"
                    }
                )
            attempted = sum(1 for r in brows if r.get("status") != "skipped")
            if remaining is not None:
                remaining -= attempted
            snapshot("in_progress")
            if rc != 0 or only or attempted < cap or (remaining is not None and remaining <= 0):
                break
        data = snapshot("final")
        print(
            json.dumps(
                {k: data[k] for k in ("run_id", "verdict", "counts", "model_calls", "wall_seconds")}
            )
        )
        return rs.EXIT_FOR_VERDICT[data["verdict"]]
    except BaseException as exc:
        errored.append({"reason": f"ingest interrupted: {type(exc).__name__}: {exc}"})
        snapshot("final")
        if isinstance(exc, Exception):
            print(f"wiki-weaver: {exc}", file=sys.stderr)
            return rs.EXIT_ERRORED
        raise


def cmd_ask(a: argparse.Namespace) -> int:
    corpus = Path(a.wiki).expanduser().resolve()
    if not corpus.is_dir():
        die(f"{corpus} is not a directory")
    ask_dir = corpus / ".wiki" / "ask" / stamp()
    ask_dir.mkdir(parents=True)
    (ask_dir / "question.txt").write_text(a.question.strip() + "\n")
    fmt = "json" if a.json_out else "text"
    rc = run_graph("ask", corpus, {"ask_dir": str(ask_dir), "fmt": fmt}, ask_dir / "engine")
    out = ask_dir / "out.txt"
    if rc != 0 or not out.exists():
        die(f"ask failed (engine exit {rc}); logs in {ask_dir}")
    sys.stdout.write(out.read_text())
    return 0


def cmd_feedback(a: argparse.Namespace) -> int:
    corpus = Path(a.wiki).expanduser().resolve()
    if not corpus.is_dir():
        die(f"{corpus} is not a directory")
    row = {"ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "text": a.text, "why": a.why}
    if a.page:
        row["page"] = a.page
    log = feedback_log(corpus)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wiki-weaver: feedback recorded in {log}")
    return 0


def cmd_dashboard(a: argparse.Namespace) -> int:
    corpus = Path(a.corpus).expanduser().resolve()
    if not corpus.is_dir():
        die(f"{corpus} is not a directory")
    out = build_dashboard(corpus, Path(a.out), a.group_by, a.group_link_template, a.theme)
    print(f"wiki-weaver: dashboard written to {out}")
    return 0


def cmd_doctor(a: argparse.Namespace) -> int:
    ok = True

    def report(good: bool, msg: str) -> None:
        nonlocal ok
        ok = ok and good
        print(f"[{'ok' if good else 'FAIL'}] {msg}")

    report(True, f"wiki-weaver {resolve_version(__version__)}")
    exe = shutil.which("dot-runner")
    report(bool(exe), f"dot-runner on PATH: {exe or 'not found'}")
    if exe:
        r = subprocess.run([exe, "doctor"], capture_output=True, text=True, check=False)
        report(r.returncode == 0, "dot-runner doctor")
    report(bool(shutil.which("git")), "git on PATH")
    report(bool(os.environ.get("ANTHROPIC_API_KEY")), "ANTHROPIC_API_KEY set")
    try:
        report(True, f"graphs: {pipeline_dir()}")
    except SystemExit:
        report(False, "graphs: pipeline/*.dot not found")
    if a.wiki:
        w = Path(a.wiki).expanduser()
        report(lens_path(w).exists(), f"{w}/lens.md present")
        report(wiki_inbox(w).is_dir(), f"{w}/_inbox present")
        report(wiki_ledger(w).parent.is_dir(), f"{w}/.wiki present")
        print(f"     pages: {len(page_files(w))}")
    return 0 if ok else 1


def cmd_update(a: argparse.Namespace) -> int:
    print(f"wiki-weaver {resolve_version(__version__)}")
    if not a.check:
        print("wiki-weaver: update is not wired in V4 Stage 1; nothing fetched")
    return 0


# ---------------------------------------------------------------- parser


class _VersionAction(argparse.Action):
    def __init__(self, option_strings, dest=argparse.SUPPRESS, **kwargs) -> None:
        kwargs.setdefault("nargs", 0)
        kwargs.setdefault("help", "show program's version number and exit")
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        print(f"wiki-weaver {resolve_version(__version__)}")
        parser.exit()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wiki-weaver", description="WikiWeaver V4")
    p.add_argument("--version", action=_VersionAction)
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("doctor", help="environment diagnostics")
    s.add_argument("--wiki", default=None, help="also check this wiki's layout")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("init", help="scaffold a wiki and seed its lens")
    s.add_argument("wiki_dir")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--purpose", default=None, metavar="TEXT", help="draft lens.md from this")
    g.add_argument("--plain", action="store_true", help="default lens, no model")
    s.add_argument("--no-sample-inbox", action="store_true", help=argparse.SUPPRESS)
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("ingest", help="integrate _inbox/ sources")
    s.add_argument("--wiki", default=".", help="wiki directory (default: .)")
    s.add_argument("--source", default=None, help="ingest one source (also retries a held one)")
    s.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="accepted for V1 compatibility; V4 rewrites at most once",
    )
    s.add_argument("--keep-going", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--limit", type=int, default=None, help=argparse.SUPPRESS)
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("ask", help="answer a question from the wiki")
    s.add_argument("question")
    s.add_argument("--wiki", default=".", help="wiki directory (default: .)")
    s.add_argument(
        "--json", dest="json_out", action="store_true", help="{answer, pages_used, refused}"
    )
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("build-dashboard", help="write a minimal HTML page list")
    s.add_argument("corpus")
    s.add_argument("--out", required=True, metavar="PATH")
    s.add_argument("--theme", default=None, metavar="PATH")
    s.add_argument("--group-by", dest="group_by", default="type", metavar="FIELD")
    s.add_argument("--group-link-template", dest="group_link_template", default=None, metavar="T")
    s.add_argument("--skip-index", action="store_true", help=argparse.SUPPRESS)
    s.set_defaults(func=cmd_dashboard)

    s = sub.add_parser("feedback", help="record feedback with a why")
    s.add_argument("--wiki", default=".", help="wiki directory (default: .)")
    s.add_argument("text")
    s.add_argument("--why", required=True)
    s.add_argument("--page", default=None)
    s.set_defaults(func=cmd_feedback)

    s = sub.add_parser("update", help="print the installed version (Stage 1: no fetch)")
    s.add_argument("--check", action="store_true")
    s.add_argument("--dry-run", dest="check", action="store_true", help=argparse.SUPPRESS)
    s.set_defaults(func=cmd_update)
    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    a = parser.parse_args(argv)
    if not getattr(a, "func", None):
        parser.print_help()
        raise SystemExit(2)
    raise SystemExit(a.func(a))


if __name__ == "__main__":
    main()
