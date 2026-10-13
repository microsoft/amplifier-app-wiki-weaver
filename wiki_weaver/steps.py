"""Tool-node glue for the three graphs: deterministic shell steps, no inference.

Each graph's parallelogram nodes run ``"$py" -m wiki_weaver.steps <step> [args]`` with the
corpus as the working directory. A step prints one routing token on its last stdout line.
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from . import checks as ck
from . import ledger as lg
from .lens import parse_page_types
from .lib import (
    MAX_SOURCE_CHARS,
    NON_PAGE_FILES,
    atomic_append_line,
    atomic_write_text,
    contained_file,
    corrections_dir,
    is_valid_slug,
    lens_path,
    page_files,
    schema_fragments,
    wiki_failed,
    wiki_inbox,
    wiki_sources,
    wiki_work,
)
from .sources import (
    page_frontmatter,
    read_text,
    sha256_file,
    source_meta,
    source_summary_slug,
)

WIKI = Path(".")
MAX_SLUGS = 8
MAX_ASK_PAGES = 6
LENS_HEADINGS = ("Purpose", "What matters", "People", "Question shapes", "Page types", "Owner")
GIT_ID = ["-c", "user.name=wiki-weaver", "-c", "user.email=wiki-weaver@localhost"]


# ---------------------------------------------------------------- helpers


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def jread(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default


def jwrite(p: Path, data) -> None:
    atomic_write_text(p, json.dumps(data, indent=2) + "\n")


def jappend(p: Path, row: dict) -> None:
    atomic_append_line(p, json.dumps(row, ensure_ascii=False))


def jlines(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(
        ["git", *GIT_ID, *args], cwd=WIKI, capture_output=True, text=True, check=False
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def changed_paths() -> list[str]:
    """Paths changed since the last commit (tracked or untracked, not ignored)."""
    out = git("status", "--porcelain", "-z", "--untracked-files=all", "--no-renames")
    paths, parts, i = [], out.split("\0"), 0
    while i < len(parts):
        entry = parts[i]
        if not entry:
            i += 1
            continue
        code, path = entry[:2], entry[3:]
        paths.append(path)
        if code[0] in "RC":
            i += 1  # skip the rename source
        i += 1
    return paths


def root_md_changes() -> list[str]:
    return sorted(p for p in changed_paths() if "/" not in p and p.endswith(".md"))


def current() -> dict:
    return jread(wiki_work(WIKI) / "current.json", {}) or {}


def save_current(cur: dict) -> None:
    jwrite(wiki_work(WIKI) / "current.json", cur)


def record(run_dir: Path, row: dict) -> None:
    """One V1-shaped ledger row, mirrored into this batch's changes.jsonl."""
    row.setdefault("run_id", run_dir.parent.name)
    lg.append_row(WIKI, row)
    jappend(run_dir / "changes.jsonl", row)


LEDGER_REL = ".wiki/.processed.jsonl"


def commit_paths(paths, message: str) -> None:
    """Stage and commit exactly these paths (additions, edits and deletions). Nothing
    else in the corpus is staged or committed - an owner's unrelated work is left alone."""
    keep = sorted({p for p in paths if p and ((WIKI / p).exists() or _in_head(p))})
    if not keep:
        return
    git("add", "-A", "--", *keep)
    staged = git("diff", "--cached", "--name-only", "--", *keep).strip()
    if staged:
        git("commit", "-q", "--only", "-m", message, "--", *keep)


def check_sources(cur: dict) -> dict[int, tuple[str, str]]:
    """source_id -> (filename, text) for every resolvable version, plus the one in flight."""
    texts = lg.source_versions(WIKI)
    texts[cur["source_id"]] = (cur["filename"], read_text(wiki_inbox(WIKI) / cur["filename"]))
    return texts


def pages_citing(name: str) -> list[str]:
    """Pages that cite any version of source ``name`` or list it in `sources:`."""
    ids = lg.ids_for_source(WIKI, name)
    out = []
    for p in page_files(WIKI):
        text = read_text(p)
        srcs = (page_frontmatter(p) or {}).get("sources") or []
        if name in srcs or any(int(m.group(1)) in ids for m in ck.CITE_RE.finditer(text)):
            out.append(p.name)
    return out


# ---------------------------------------------------------------- ingest


JOURNAL = "journal.json"


class JournalError(Exception):
    pass


def _inside(rel) -> bool:
    """A journal path: a relative string that resolves under the corpus root."""
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
        return False
    root = WIKI.resolve()
    try:
        return (root / rel).resolve().is_relative_to(root)
    except (OSError, ValueError, RuntimeError):
        return False


def _validate(j) -> dict:
    if not isinstance(j, dict) or not isinstance(j.get("op"), str):
        raise JournalError("not an object with an op")
    src = j.get("source")
    if src is not None and (not isinstance(src, str) or "/" in src or src in ("", ".", "..")):
        raise JournalError(f"bad source {src!r}")
    if j.get("dest") is not None and not _inside(j["dest"]):
        raise JournalError(f"dest outside the corpus: {j['dest']!r}")
    paths = j.get("paths")
    if not isinstance(paths, list):
        raise JournalError("paths is not a list")
    bad = [p for p in paths if not _inside(p)]
    if bad:
        raise JournalError(f"paths outside the corpus: {bad!r}")
    return j


def journal() -> dict | None:
    """The in-flight operation: what it is, its source, where that source is being
    placed, every corpus path it owns, and whether its commit has landed. Recovery
    touches only these paths, and none of them once `committed` is set."""
    j = wiki_work(WIKI) / JOURNAL
    if not j.exists():
        return None
    try:
        data = json.loads(j.read_text())
    except ValueError as e:
        raise JournalError(f"unreadable: {e}") from None
    return _validate(data)


def journal_open(op: str, source: str | None, dest: str | None, paths) -> None:
    jwrite(
        wiki_work(WIKI) / JOURNAL,
        {
            "op": op,
            "source": source,
            "dest": dest,
            "paths": sorted({p for p in paths if p}),
            "committed": False,
        },
    )


def journal_update(op: str | None = None, dest: str | None = None, paths=()) -> None:
    j = journal() or {"op": op, "source": None, "dest": None, "paths": []}
    if op:
        j["op"] = op
    if dest:
        j["dest"] = dest
    j["paths"] = sorted(set(j["paths"]) | {p for p in paths if p})
    jwrite(wiki_work(WIKI) / JOURNAL, j)


def journal_committed() -> None:
    """Mark, atomically, that the operation's commit has landed: from here recovery has
    nothing to revert, only the inbox copy to drop."""
    j = journal()
    if j is not None:
        j["committed"] = True
        jwrite(wiki_work(WIKI) / JOURNAL, j)


def journal_close() -> None:
    (wiki_work(WIKI) / JOURNAL).unlink(missing_ok=True)


def clear_work() -> None:
    """A finished operation leaves no scratch: anything in .wiki/work/ at the start of a
    run means a run died, and recovery decides what to do with it."""
    shutil.rmtree(wiki_work(WIKI), ignore_errors=True)


def owned_paths() -> set[str]:
    j = journal()
    return set(j["paths"]) if j else set()


def _place(src: Path, dest_dir: Path) -> Path:
    """Copy a source to its retained place atomically. The inbox copy is removed only
    after the commit that records it (see _finish), so a crash leaves it in _inbox/."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    tmp = dest_dir / f".{src.name}.tmp"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)
    return dest


def _finish(src: Path) -> None:
    """Last step of a source's bookkeeping: drop the inbox copy."""
    src.unlink(missing_ok=True)


def _summary_page(filename: str) -> str:
    base = source_summary_slug(filename)
    page = WIKI / f"{base}.md"
    if page.exists():
        srcs = (page_frontmatter(page) or {}).get("sources") or []
        if srcs and filename not in srcs:
            import hashlib

            base = f"{base}-{hashlib.sha256(filename.encode()).hexdigest()[:6]}"
    return f"{base}.md"


def _attempted(rows: list[dict]) -> int:
    return sum(1 for r in rows if r.get("status") != lg.STATUS_SKIPPED)


def _drained() -> str:
    clear_work()
    return "drained"


def step_select(run_dir: str, cap_s: str, only: str) -> str:
    """Pick the next source. ``cap_s`` bounds attempts in this batch (0 = no cap);
    0-byte sources are skipped (ledgered, left in place) and oversized ones held."""
    run = Path(run_dir)
    work = wiki_work(WIKI)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    cap = int(cap_s) if cap_s.strip().isdigit() else 0
    inbox = wiki_inbox(WIKI)
    inbox.mkdir(exist_ok=True)
    while True:
        batch = jlines(run / "changes.jsonl")
        if cap > 0 and _attempted(batch) >= cap:
            return _drained()
        if only and only != "-":
            name = Path(only).name
            if any(r.get("source") == name for r in batch):
                return _drained()
            held = wiki_failed(WIKI) / name
            if not (inbox / name).exists() and held.exists():
                # retry: the held copy comes back to _inbox/; record that in git now so
                # the write's scope check sees only the writer's changes
                rel = f".wiki/failed/{name}"
                journal_open("retry", name, rel, [rel])
                _place(held, inbox)
                held.unlink()
                commit_paths([rel], f"retry: {name}")
                journal_committed()
                journal_close()
            p = inbox / name
            if not p.exists() or sha256_file(p) in lg.converged_hashes(lg.read_rows(WIKI)):
                return _drained()
        else:
            cands = lg.eligible(WIKI)
            if not cands:
                return _drained()
            p = cands[0]
        sha = sha256_file(p)
        if p.stat().st_size == 0:
            rel = f".wiki/skipped/{p.name}"
            journal_open("skip", p.name, rel, [rel, LEDGER_REL])
            _place(p, WIKI / ".wiki" / "skipped")
            record(
                run,
                lg.make_row(
                    WIKI,
                    source=p.name,
                    file_hash=sha,
                    status=lg.STATUS_SKIPPED,
                    reason="empty source (0 bytes)",
                    failure_kind=lg.KIND_EMPTY,
                    pages_touched=[],
                    model_calls=0,
                ),
            )
            commit_paths([rel, LEDGER_REL], f"skip: {p.name} (empty)")
            journal_committed()
            _finish(p)
            journal_close()
            if only and only != "-":
                return _drained()
            continue
        if lg.is_oversized(p):
            rel = f".wiki/failed/{p.name}"
            journal_open("oversized", p.name, rel, [rel, LEDGER_REL])
            dest = _place(p, wiki_failed(WIKI))
            record(
                run,
                lg.make_row(
                    WIKI,
                    source=p.name,
                    file_hash=sha,
                    status=lg.STATUS_FAILED,
                    reason=f"oversized: more than {MAX_SOURCE_CHARS} characters",
                    failure_kind=lg.KIND_OVERSIZED,
                    failed_to=str(dest.resolve()),
                    pages_touched=[],
                    model_calls=0,
                ),
            )
            commit_paths([rel, LEDGER_REL], f"hold: {p.name} (oversized)")
            journal_committed()
            _finish(p)
            journal_close()
            continue
        rows = lg.read_rows(WIKI)
        prev = wiki_sources(WIKI) / p.name
        prev_sha = sha256_file(prev) if prev.is_file() else None
        changed = prev_sha is not None and prev_sha != sha
        prev_id = lg.source_id_for(rows, prev_sha) if changed else None
        journal_open(
            "source",
            p.name,
            f"_sources/{p.name}",
            [
                LEDGER_REL,
                f"_sources/{p.name}",
                f".wiki/failed/{p.name}",
                f".wiki/source-versions/s{prev_id}.md" if changed else None,
            ],
        )
        save_current(
            {
                "filename": p.name,
                "sha256": sha,
                "source_id": lg.source_id_for(rows, sha),
                "changed": changed,
                "prev_source_id": prev_id,
                "summary_page": _summary_page(p.name),
                "started": now(),
                "t0": time.time(),
                "attempts": 0,
                "model_calls": 0,
                "stage": "select",
            }
        )
        return "source"


def step_assemble() -> str:
    cur = current()
    src = wiki_inbox(WIKI) / cur["filename"]
    meta = source_meta(src)
    lens = contained_file(WIKI, "lens.md")
    if lens is None:
        print(
            "lens.md is missing or resolves outside the corpus; refusing to read it",
            file=sys.stderr,
        )
        sys.exit(1)
    parts = ["# Lens\n", read_text(lens).strip(), "\n\n# Standing corrections\n"]
    cdir = corrections_dir(WIKI)
    corr = []
    for c in sorted(cdir.glob("*.md")) if cdir.is_dir() else []:
        safe = contained_file(WIKI, c.relative_to(WIKI))
        if safe is None:
            print(f"refused correction outside the corpus: {c.name}", file=sys.stderr)
            continue
        corr.append((c.name, read_text(safe).strip()))
    parts += [f"\n## {n}\n{t}\n" for n, t in corr] or ["(none yet)\n"]
    for frag in schema_fragments(WIKI):
        safe = contained_file(WIKI, frag.relative_to(WIKI))
        if safe is None:
            print(f"refused schema fragment outside the corpus: {frag}", file=sys.stderr)
            continue
        parts += [f"\n# Wrapper policy fragment ({frag.as_posix()})\n", read_text(safe).strip()]
    sid = cur["source_id"]
    parts += [
        "\n\n# Source\n",
        f"filename: {cur['filename']}\n",
        f'source id: s{sid} - cite this source as [s{sid}: "five or more words quoted exactly"]\n',
        *(f"{k}: {v}\n" for k, v in meta.items() if k != "filename" and v),
        f"summary page to write: {cur['summary_page']}\n",
    ]
    if cur.get("changed"):
        parts.append(f"ingest date: {now()[:10]}\n")
        old = read_text(wiki_sources(WIKI) / cur["filename"])
        diff = difflib.unified_diff(
            old.splitlines(keepends=True),
            read_text(src).splitlines(keepends=True),
            fromfile=f"s{cur['prev_source_id']} (previous version)",
            tofile=f"s{sid} (this version)",
            n=2,
        )
        parts += [
            (
                "\nCHANGED SOURCE: this source was ingested before and has changed. The "
                f"previous version is s{cur['prev_source_id']}; existing citations of it stay "
                "as they are. A quoted passage that no longer appears in this version is "
                "wrapped in a superseded block, not deleted. The unified diff, previous -> this version:\n\n```diff\n"
            ),
            "".join(diff),
            "```\n",
        ]
    parts += [
        "\n----- SOURCE TEXT BEGINS -----\n",
        read_text(src),
        "\n----- SOURCE TEXT ENDS -----\n",
    ]
    (wiki_work(WIKI) / "context.md").write_text("".join(parts))
    cur.update(stage="brief", model_calls=cur["model_calls"] + 1)
    save_current(cur)
    return "ok"


DEFAULT_MAX_WRITER_CHARS = 3_500_000


def _latest_source_date(sources: list[str], this_date: str) -> str:
    """The `(as of)` date for a page: the latest date among its sources and this one."""
    dates = [this_date] if this_date else []
    for name in sources:
        f = contained_file(WIKI, Path("_sources") / name)
        d = str(source_meta(f).get("date") or "") if f else ""
        if d:
            dates.append(d)
    return max(dates) if dates else ""


def _escapes(rel: str) -> bool:
    """True if ``rel`` exists (or dangles) as a link that resolves outside the corpus."""
    p = WIKI / rel
    if not (p.exists() or p.is_symlink()):
        return False
    return contained_file(WIKI, rel) is None or contained_file(WIKI, rel).parent != WIKI.resolve()


def _parse_slugs(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        s = line.strip().lstrip("-*").strip().strip("`").removesuffix(".md").strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def step_page_select() -> str:
    cur = current()
    work = wiki_work(WIKI)
    sl = work / "slugs.txt"
    if not sl.exists():
        print("brief did not write .wiki/work/slugs.txt", file=sys.stderr)
        sys.exit(1)
    slugs, notes = [], []
    root = WIKI.resolve()
    for s in _parse_slugs(read_text(sl)):
        if not is_valid_slug(s) or s.startswith("source-"):
            notes.append(f"dropped invalid or reserved slug: {s}")
        elif _escapes(f"{s}.md"):
            notes.append(f"refused slug resolving outside the corpus: {s}")
        elif s not in slugs:
            slugs.append(s)
    if len(slugs) > MAX_SLUGS:
        notes.append(f"dropped slugs beyond {MAX_SLUGS}: {', '.join(slugs[MAX_SLUGS:])}")
        slugs = slugs[:MAX_SLUGS]
    selected = [cur["summary_page"]] + [f"{s}.md" for s in slugs]
    if cur.get("changed"):
        citing = [n for n in pages_citing(cur["filename"]) if n not in selected]
        selected += citing
        if citing:
            notes.append(f"added because they cite the changed source: {', '.join(citing)}")
    before = work / "before"
    before.mkdir(exist_ok=True)
    out = ["# Selected pages\n", "You may write ONLY these files:\n"]
    out += [f"- {n}\n" for n in selected]
    out += [f"\n({n})\n" for n in notes]
    this_date = str(source_meta(wiki_inbox(WIKI) / cur["filename"]).get("date") or "")
    for name in selected:
        p = contained_file(WIKI, name)
        if p is not None and p.parent == root:
            shutil.copy2(p, before / name)
            srcs = [
                x for x in (page_frontmatter(p) or {}).get("sources") or [] if isinstance(x, str)
            ]
            as_of = _latest_source_date(srcs, this_date)
            head = f"existing, {len(srcs)} sources"
            body = read_text(p)
        else:
            as_of, head, body = this_date, "new page - does not exist yet", ""
        if not name.startswith("source-") and as_of:
            head += f"; Current state as of {as_of} (the latest source date on the page)"
        out += [f"\n\n## {name} ({head})\n\n", body]
    idx = contained_file(WIKI, "index.md")
    out += ["\n\n# index.md\n\n", read_text(idx) if idx else "(empty)\n"]
    (work / "pages.md").write_text("".join(out))
    (work / "selected.txt").write_text("\n".join(selected) + "\n")
    if _measure_writer_call(cur) == "oversized":
        # held before the write model call: no page entries, nothing for hold to revert
        return "oversized"
    journal_update(paths=selected)
    cur.update(stage="write", model_calls=cur["model_calls"] + 1, selected=selected)
    save_current(cur)
    return "ok"


def _measure_writer_call(cur: dict) -> str:
    """Count what the writer is about to read - brief, context, pages and, on a rewrite,
    the findings - record it as this call's size, and refuse the call over the limit."""
    work = wiki_work(WIKI)
    files = ("context.md", "pages.md", "brief.md", "findings.md")
    chars = sum(len(read_text(work / f)) for f in files if (work / f).exists())
    calls = cur.setdefault("writer_chars_per_call", [])
    calls.append(chars)
    cur["writer_input_chars"] = max(calls)
    limit = int(os.environ.get("WIKI_WEAVER_MAX_WRITER_CHARS") or DEFAULT_MAX_WRITER_CHARS)
    if chars > limit:
        cur.update(stage="oversized-context", hold_reason=f"oversized-context: {chars} chars")
        save_current(cur)
        return "oversized"
    save_current(cur)
    return "ok"


def step_write_guard() -> str:
    """Before the rewrite: the same measurement and limit as the first write call."""
    cur = current()
    if _measure_writer_call(cur) == "oversized":
        return "oversized"
    cur = current()
    cur.update(stage="write", model_calls=cur["model_calls"] + 1)
    save_current(cur)
    return "ok"


def step_checks() -> str:
    cur = current()
    work = wiki_work(WIKI)
    cur["attempts"] += 1
    cur["stage"] = "checks"
    selected = cur["selected"]
    changed = changed_paths()
    outside = [p for p in changed if p not in selected]
    cur["scope_named"] = sorted(set(cur.get("scope_named", [])) | set(outside))
    errs = [f"wrote outside the selected pages: {p}" for p in outside]
    if cur["summary_page"] not in changed:
        errs.append(f"source summary {cur['summary_page']} was not written")
    pages = [p for p in changed if p in selected]
    pages += [p for p in selected if p not in pages and (work / "before" / p).exists()]
    lens = contained_file(WIKI, "lens.md")
    types = parse_page_types(read_text(lens)) if lens else {}
    errs += ck.run_page_checks(
        WIKI, pages, work / "before", check_sources(cur), cur.get("source_id"), types
    )
    if not errs:
        save_current(cur)
        return "pass"
    block = f"\n## Findings after write attempt {cur['attempts']}\n" + "".join(
        f"- {e}\n" for e in errs
    )
    with (work / "findings.md").open("a") as f:
        f.write(block)
    cur["findings"] = errs
    kinds: dict[str, int] = {}
    for e in errs:
        kinds[ck.check_kind(e)] = kinds.get(ck.check_kind(e), 0) + 1
    cur.setdefault("failed_checks", {})[f"write_{cur['attempts']}"] = kinds
    if cur["attempts"] < 2:
        save_current(cur)  # write_guard measures and counts the rewrite call
        return "rewrite"
    save_current(cur)
    return "hold"


def _in_head(path: str) -> bool:
    r = subprocess.run(
        ["git", "cat-file", "-e", f"HEAD:{path}"], cwd=WIKI, capture_output=True, check=False
    )
    return r.returncode == 0


def _revert_writes(only: set[str]) -> tuple[list[str], list[str]]:
    """Return the uncommitted paths in ``only`` (the in-flight operation's own paths) to
    their state at HEAD - from HEAD, not the index, so a staged half-page does not
    survive. Every other uncommitted change is reported and left alone.
    Returns (reverted, untouched)."""
    reverted, untouched = [], []
    for p in changed_paths():
        if p not in only:
            untouched.append(p)
            continue
        if _in_head(p):
            git("checkout", "HEAD", "--", p)
        else:
            git("rm", "-q", "--cached", "--ignore-unmatch", "--", p, check=False)
            (WIKI / p).unlink(missing_ok=True)
        reverted.append(p)
    return reverted, untouched


def _head_bytes_hash(path: str) -> str | None:
    r = subprocess.run(["git", "show", f"HEAD:{path}"], cwd=WIKI, capture_output=True, check=False)
    if r.returncode != 0:
        return None
    import hashlib

    return hashlib.sha256(r.stdout).hexdigest()


def step_recover() -> str:
    """Undo an operation a dead run left half-done, touching only what it owned.

    The journal (.wiki/work/journal.json) names the operation's own paths: its source's
    retained copy, the pages it was given, the ledger, index.md/log.md for the index
    phase. Those return to HEAD; every other uncommitted change - an owner's edit, an
    untracked draft - is reported and left exactly as it is. Then the source is in one
    place: if the operation committed before dying, its leftover inbox copy (identical to
    the committed copy at the journal's destination) is dropped; otherwise it stays in
    _inbox/ and is picked up again."""
    work = wiki_work(WIKI)
    try:
        j = journal()
    except JournalError as e:
        print(
            f"recover: invalid journal in .wiki/work/journal.json ({e}); touching nothing",
            file=sys.stderr,
        )
        sys.exit(1)
    if j is None:
        if work.exists() and any(work.iterdir()):
            print(
                "recover: .wiki/work/ holds scratch from a run that died but no journal names "
                "what it owned; touching nothing. Inspect .wiki/work/ and the uncommitted "
                "changes, then remove .wiki/work/ to continue.",
                file=sys.stderr,
            )
            sys.exit(1)
        clear_work()
        return "clean"
    if j.get("committed"):
        print(
            f"recover: {j.get('op')} ({j.get('source') or 'no source'}) had committed; nothing to revert",
            file=sys.stderr,
        )
    else:
        reverted, untouched = _revert_writes(set(j["paths"]))
        msg = f"recovered interrupted {j.get('op')} ({j.get('source') or 'no source'}): reverted {reverted}"
        if untouched:
            msg += f"; left untouched (not this operation's): {untouched}"
        print(msg, file=sys.stderr)
    src, dest = j.get("source"), j.get("dest")
    if src and dest and dest.split("/")[-1] == src:
        p = wiki_inbox(WIKI) / src
        if p.exists() and _head_bytes_hash(dest) == sha256_file(p):
            p.unlink()
            print(f"recover: {src} is committed at {dest}; dropped its inbox copy", file=sys.stderr)
    clear_work()
    return "recovered"


def step_hold(run_dir: str) -> str:
    cur = current()
    stage = cur.get("stage", "?")
    if stage == "checks" and cur.get("findings"):
        f = cur["findings"]
        reason = f"checks failed twice: {'; '.join(f[:3])}" + (
            f" (+{len(f) - 3} more)" if len(f) > 3 else ""
        )
    elif stage == "write":
        # a missing slugs file fails page_select before stage flips to write
        reason = "write: model step failed"
    elif stage == "brief":
        reason = "brief: model step failed or wrote no slugs"
    elif stage == "oversized-context":
        reason = cur.get("hold_reason", "oversized-context")
    else:
        reason = f"failed at {stage}"
    kind = {
        "checks": lg.KIND_CHECKS,
        "brief": lg.KIND_MODEL_STEP,
        "write": lg.KIND_MODEL_STEP,
        "oversized-context": lg.KIND_OVERSIZED_CONTEXT,
    }.get(stage, lg.KIND_UNKNOWN)
    # Revert exactly two known lists: the journal's owned paths (the pages this source's
    # writer was given) and the paths the scope check named. If no writer ran, there are
    # no page entries and nothing is reverted. Everything else is left as it is.
    named = set(cur.get("scope_named", []))
    _, untouched = _revert_writes((owned_paths() | named) - {LEDGER_REL})
    if untouched:
        print(f"hold: left untouched (not this source's): {untouched}", file=sys.stderr)
    src = wiki_inbox(WIKI) / cur["filename"]
    rel = f".wiki/failed/{cur['filename']}"
    journal_update(op="hold", dest=rel, paths=[rel])
    dest = _place(src, wiki_failed(WIKI)) if src.exists() else wiki_failed(WIKI) / cur["filename"]
    record(
        Path(run_dir),
        lg.make_row(
            WIKI,
            source=cur["filename"],
            file_hash=cur["sha256"],
            status=lg.STATUS_FAILED,
            reason=reason,
            failure_kind=kind,
            failed_to=str(dest.resolve()),
            pages_touched=[],
            model_calls=cur.get("model_calls", 0),
            failed_checks=cur.get("failed_checks", {}),
            writer_input_chars=cur.get("writer_input_chars", 0),
            writer_chars_per_call=cur.get("writer_chars_per_call", []),
            wall_seconds=round(time.time() - cur.get("t0", time.time()), 1),
        ),
    )
    commit_paths(owned_paths(), f"hold: {cur['filename']} ({kind})")
    journal_committed()
    _finish(src)
    clear_work()
    return "next"


def step_commit(run_dir: str) -> str:
    cur = current()
    selected = set(cur.get("selected", []))
    pages = [p for p in root_md_changes() if p in selected]
    srcs = wiki_sources(WIKI)
    srcs.mkdir(exist_ok=True)
    dest = srcs / cur["filename"]
    src = wiki_inbox(WIKI) / cur["filename"]
    if cur.get("changed") and dest.exists():
        keep = lg.versions_dir(WIKI) / f"s{cur['prev_source_id']}.md"
        keep.parent.mkdir(parents=True, exist_ok=True)
        if not keep.exists():
            shutil.copyfile(dest, keep)
    _place(src, srcs)
    record(
        Path(run_dir),
        lg.make_row(
            WIKI,
            source=cur["filename"],
            file_hash=cur["sha256"],
            status=lg.STATUS_CONVERGED,
            archived_to=str(dest.resolve()),
            pages_touched=pages,
            model_calls=cur.get("model_calls", 0),
            failed_checks=cur.get("failed_checks", {}),
            writer_input_chars=cur.get("writer_input_chars", 0),
            writer_chars_per_call=cur.get("writer_chars_per_call", []),
            wall_seconds=round(time.time() - cur.get("t0", time.time()), 1),
        ),
    )
    journal_update(op="commit")
    commit_paths(owned_paths(), f"ingest: {cur['filename']}")
    journal_committed()
    _finish(src)  # last: until here a crash leaves the source in _inbox/
    clear_work()
    return "next"


def step_index_prep(run_dir: str) -> str:
    changes = jlines(Path(run_dir) / "changes.jsonl")
    if not any(r.get("converged") for r in changes):
        return "skip"
    work = wiki_work(WIKI)
    work.mkdir(parents=True, exist_ok=True)
    journal_open("index", None, None, ["index.md", "log.md"])  # before anything is written
    dates: dict[str, str] = {}
    for p in wiki_sources(WIKI).glob("*.md"):
        m = source_meta(p)
        dates[p.name] = str(m.get("date") or "")
    out = ["# What changed in this run\n\n"]
    for r in changes:
        line = f"- {r['status']}: {r['source']}"
        line += f" -> pages: {', '.join(r['pages_touched'])}" if r.get("pages_touched") else ""
        line += f" (reason: {r['reason']})" if r.get("reason") else ""
        out.append(line + "\n")
    out.append("\n# Page manifest (every page in the wiki)\n\n")
    ids: dict[str, int] = {}
    for r in lg.read_rows(WIKI):
        if r.get("converged") and isinstance(r.get("source_id"), int):
            ids[r["source"]] = max(ids.get(r["source"], 0), r["source_id"])
    for p in page_files(WIKI):
        fm = page_frontmatter(p) or {}
        srcs = [s for s in (fm.get("sources") or []) if isinstance(s, str)]
        ds = sorted(d for d in (dates.get(s, "") for s in srcs) if d)
        span = f"{ds[0]}..{ds[-1]}" if ds else "undated"
        sid = ""
        if p.name.startswith("source-") and len(srcs) == 1 and srcs[0] in ids:
            sid = f" | source id: s{ids[srcs[0]]}"
        out.append(
            f"- {p.name} | title: {fm.get('title', '?')} | type: {fm.get('type', '?')} | "
            f"source dates: {span} | last_updated: {fm.get('last_updated', '?')}{sid}\n"
        )
    idx, log = WIKI / "index.md", WIKI / "log.md"
    out += ["\n# Current index.md\n\n", read_text(idx) if idx.exists() else "(none)\n"]
    (work / "index_input.md").write_text("".join(out))
    if log.exists():
        shutil.copy2(log, work / "log_before.md")
    return "index"


def step_finalize() -> str:
    work = wiki_work(WIKI)
    log, before = WIKI / "log.md", work / "log_before.md"
    if before.exists() and log.exists() and not read_text(log).startswith(read_text(before)):
        # log.md is append-only: restore the prior history and keep the new tail.
        new = read_text(log)
        log.write_text(read_text(before).rstrip("\n") + "\n\n" + new)
        print("log.md was rewritten, not appended; restored prior history", file=sys.stderr)
    extras = [p for p in changed_paths() if p not in ("index.md", "log.md")]
    if extras:
        print(f"finalize: left untouched (not index.md/log.md): {extras}", file=sys.stderr)
    commit_paths(["index.md", "log.md"], "index: update index.md and log.md")
    journal_committed()
    clear_work()
    return "done"


def step_index_restore() -> str:
    """The index step failed: keep the per-source page commits, return index.md and log.md
    to HEAD, and fail the run. Nothing else is touched."""
    reverted, _ = _revert_writes({"index.md", "log.md"})
    clear_work()
    print(
        f"index step failed; restored from HEAD: {reverted or 'nothing to restore'}",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------- init


def step_init_mode(mode: str) -> str:
    if lens_path(WIKI).exists():
        return "exists"
    return mode if mode in ("plain", "purpose", "interview") else "plain"


def step_default_lens() -> str:
    if not lens_path(WIKI).exists():
        data = Path(__file__).parent / "data" / "default_lens.md"
        shutil.copy2(data, lens_path(WIKI))
    return "ok"


def step_lens_check() -> str:
    p = lens_path(WIKI)
    if not p.exists():
        print("lens.md was not written", file=sys.stderr)
        return "bad"
    heads = {h.strip().lower() for h in ck._headings(read_text(p), level=2)}
    missing = [h for h in LENS_HEADINGS if h.lower() not in heads]
    if missing:
        print(f"lens.md missing headings: {', '.join(missing)}", file=sys.stderr)
        rej = WIKI / ".wiki" / "lens.rejected.md"
        rej.parent.mkdir(exist_ok=True)
        shutil.move(str(p), rej)
        return "bad"
    return "ok"


# ---------------------------------------------------------------- ask


def step_ask_assemble(ask_dir: str) -> str:
    d = Path(ask_dir)
    reading = contained_file(WIKI, "READING.md")
    idx = contained_file(WIKI, "index.md")
    parts = [
        "# READING.md\n\n",
        read_text(reading) if reading else "(no READING.md)\n",
        "\n# index.md\n\n",
        read_text(idx) if idx else "(the wiki has no index yet)\n",
        "\n# Question\n\n",
        read_text(d / "question.txt"),
        "\n",
    ]
    (d / "context.md").write_text("".join(parts))
    return "ok"


def step_ask_read(ask_dir: str) -> str:
    d = Path(ask_dir)
    picks = _parse_slugs(read_text(d / "picks.txt")) if (d / "picks.txt").exists() else []
    root = WIKI.resolve()
    chosen = []
    for s in picks:
        name = s if s.endswith(".md") else f"{s}.md"
        target = contained_file(WIKI, name)
        # a page is a file directly under the corpus root; anything else is refused
        if target is None or target.parent != root:
            continue
        name = target.name
        if name not in NON_PAGE_FILES and name not in chosen:
            chosen.append(name)
    chosen = chosen[:MAX_ASK_PAGES]
    out = [f"# Pages picked ({len(chosen)})\n"]
    for n in chosen:
        out += [f"\n\n## {n}\n\n", read_text(WIKI / n)]
    if not chosen:
        out.append("\n(no page in the wiki was picked for this question)\n")
    (d / "pages.md").write_text("".join(out))
    jwrite(d / "pages.json", chosen)
    return "ok"


def step_ask_emit(ask_dir: str, fmt: str) -> str:
    d = Path(ask_dir)
    ans = jread(d / "answer.json", None)
    if not isinstance(ans, dict) or "answer" not in ans:
        print("answer step did not write a valid answer.json", file=sys.stderr)
        sys.exit(1)
    read = jread(d / "pages.json", [])
    used = [p for p in (ans.get("pages_used") or []) if isinstance(p, str)]
    used = [p if p.endswith(".md") else f"{p}.md" for p in used]
    used = [p for p in used if (WIKI / p).is_file()]
    result = {
        "answer": str(ans.get("answer", "")),
        "pages_used": used or ([] if ans.get("refused") else read),
        "refused": bool(ans.get("refused", False)),
    }
    if fmt == "json":
        (d / "out.txt").write_text(json.dumps(result, ensure_ascii=False) + "\n")
    else:
        text = result["answer"]
        if result["pages_used"]:
            text += "\n\nPages used: " + ", ".join(result["pages_used"])
        (d / "out.txt").write_text(text + "\n")
    return "ok"


STEPS = {
    "select": step_select,
    "assemble": step_assemble,
    "page_select": step_page_select,
    "checks": step_checks,
    "write_guard": step_write_guard,
    "hold": step_hold,
    "recover": step_recover,
    "commit": step_commit,
    "index_prep": step_index_prep,
    "finalize": step_finalize,
    "index_restore": step_index_restore,
    "init_mode": step_init_mode,
    "default_lens": step_default_lens,
    "lens_check": step_lens_check,
    "ask_assemble": step_ask_assemble,
    "ask_read": step_ask_read,
    "ask_emit": step_ask_emit,
}


# Parameters reach a step through the environment (the graph's tool_env), never through
# shell text: a source name like "Bob's chat $(...)" is data, not a command.
STEP_ENV = {
    "select": ("RUN_DIR", "CAP", "ONLY"),
    "commit": ("RUN_DIR",),
    "hold": ("RUN_DIR",),
    "index_prep": ("RUN_DIR",),
    "init_mode": ("MODE",),
    "ask_assemble": ("ASK_DIR",),
    "ask_read": ("ASK_DIR",),
    "ask_emit": ("ASK_DIR", "FMT"),
}


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in STEPS:
        print(f"usage: python -m wiki_weaver.steps {{{','.join(STEPS)}}} [args]", file=sys.stderr)
        sys.exit(2)
    args = argv[1:] or [os.environ.get(k, "") for k in STEP_ENV.get(argv[0], ())]
    token = STEPS[argv[0]](*args)
    print(token)


if __name__ == "__main__":
    main()
