"""Tool-node glue for the three graphs: deterministic shell steps, no inference.

Each graph's parallelogram nodes run ``"$py" -m wiki_weaver.steps <step> [args]`` with the
corpus as the working directory. A step prints one routing token on its last stdout line.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from . import checks as ck
from . import ledger as lg
from .lib import (
    MAX_PAGE_SOURCES,
    MAX_SOURCE_CHARS,
    NON_PAGE_FILES,
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
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")


def jappend(p: Path, row: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


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
    out = git("status", "--porcelain", "-z", "--untracked-files=all")
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
    lg.append_row(WIKI, row)
    jappend(run_dir / "changes.jsonl", row)


def commit_state(message: str) -> None:
    """Commit bookkeeping (ledger, held files) so the next write's scope check sees
    only what that write changed."""
    git("add", "-A")
    if git("diff", "--cached", "--name-only").strip():
        git("commit", "-q", "-m", message)


def source_texts(cur: dict) -> dict[str, str]:
    texts = {p.name: read_text(p) for p in wiki_sources(WIKI).glob("*.md")}
    texts[cur["filename"]] = read_text(wiki_inbox(WIKI) / cur["filename"])
    return texts


# ---------------------------------------------------------------- ingest


def _move_to_failed(path: Path) -> Path:
    failed = wiki_failed(WIKI)
    failed.mkdir(parents=True, exist_ok=True)
    dest = failed / path.name
    shutil.move(str(path), dest)
    return dest


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
            return "drained"
        if only and only != "-":
            name = Path(only).name
            if any(r.get("source") == name for r in batch):
                return "drained"
            held = wiki_failed(WIKI) / name
            if not (inbox / name).exists() and held.exists():
                shutil.move(str(held), inbox / name)
            p = inbox / name
            if not p.exists() or sha256_file(p) in lg.converged_hashes(lg.read_rows(WIKI)):
                return "drained"
        else:
            cands = lg.eligible(WIKI)
            if not cands:
                return "drained"
            p = cands[0]
        sha = sha256_file(p)
        if p.stat().st_size == 0:
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
            commit_state(f"skip: {p.name} (empty)")
            if only and only != "-":
                return "drained"
            continue
        if lg.is_oversized(p):
            dest = _move_to_failed(p)
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
            commit_state(f"hold: {p.name} (oversized)")
            continue
        save_current(
            {
                "filename": p.name,
                "sha256": sha,
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
    parts = ["# Lens\n", read_text(lens_path(WIKI)).strip(), "\n\n# Standing corrections\n"]
    corr = sorted(corrections_dir(WIKI).glob("*.md")) if corrections_dir(WIKI).is_dir() else []
    parts += [f"\n## {c.name}\n{read_text(c).strip()}\n" for c in corr] or ["(none yet)\n"]
    for frag in schema_fragments(WIKI):
        parts += [f"\n# Wrapper policy fragment ({frag.as_posix()})\n", read_text(frag).strip()]
    parts += [
        "\n\n# Source\n",
        f"filename: {cur['filename']}\n",
        *(f"{k}: {v}\n" for k, v in meta.items() if k != "filename" and v),
        f"summary page to write: {cur['summary_page']}\n",
        "\n----- SOURCE TEXT BEGINS -----\n",
        read_text(src),
        "\n----- SOURCE TEXT ENDS -----\n",
    ]
    (wiki_work(WIKI) / "context.md").write_text("".join(parts))
    cur.update(stage="brief", model_calls=cur["model_calls"] + 1)
    save_current(cur)
    return "ok"


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
    for s in _parse_slugs(read_text(sl)):
        if not is_valid_slug(s) or s.startswith("source-"):
            notes.append(f"dropped invalid or reserved slug: {s}")
        elif s not in slugs:
            slugs.append(s)
    if len(slugs) > MAX_SLUGS:
        notes.append(f"dropped slugs beyond {MAX_SLUGS}: {', '.join(slugs[MAX_SLUGS:])}")
        slugs = slugs[:MAX_SLUGS]
    selected = [cur["summary_page"]] + [f"{s}.md" for s in slugs]
    before = work / "before"
    before.mkdir(exist_ok=True)
    out = ["# Selected pages\n", "You may write ONLY these files:\n"]
    out += [f"- {n}\n" for n in selected]
    out += [f"\n({n})\n" for n in notes]
    for name in selected:
        p = WIKI / name
        if p.exists():
            shutil.copy2(p, before / name)
            n_src = len((page_frontmatter(p) or {}).get("sources") or [])
            cap = " - AT SOURCE CAP: do not add another source" if n_src >= MAX_PAGE_SOURCES else ""
            out += [f"\n\n## {name} (existing, {n_src} sources{cap})\n\n", read_text(p)]
        else:
            out += [f"\n\n## {name} (new page - does not exist yet)\n"]
    idx = WIKI / "index.md"
    out += ["\n\n# index.md\n\n", read_text(idx) if idx.exists() else "(empty)\n"]
    (work / "pages.md").write_text("".join(out))
    (work / "selected.txt").write_text("\n".join(selected) + "\n")
    cur.update(stage="write", model_calls=cur["model_calls"] + 1, selected=selected)
    save_current(cur)
    return "ok"


def step_checks() -> str:
    cur = current()
    work = wiki_work(WIKI)
    cur["attempts"] += 1
    cur["stage"] = "checks"
    selected = cur["selected"]
    changed = changed_paths()
    errs = [f"wrote outside the selected pages: {p}" for p in changed if p not in selected]
    if cur["summary_page"] not in changed:
        errs.append(f"source summary {cur['summary_page']} was not written")
    pages = [p for p in changed if p in selected]
    errs += ck.run_page_checks(WIKI, pages, work / "before", source_texts(cur))
    if not errs:
        save_current(cur)
        return "pass"
    block = f"\n## Findings after write attempt {cur['attempts']}\n" + "".join(
        f"- {e}\n" for e in errs
    )
    with (work / "findings.md").open("a") as f:
        f.write(block)
    cur["findings"] = errs
    if cur["attempts"] < 2:
        cur["model_calls"] += 1
        cur["stage"] = "write"
        save_current(cur)
        return "rewrite"
    save_current(cur)
    return "hold"


def _revert_writes(keep: tuple[str, ...] = ()) -> None:
    """Undo every uncommitted change in the corpus (ignored paths are untouched)."""
    for p in changed_paths():
        if p in keep:
            continue
        tracked = git("ls-files", "--error-unmatch", p, check=False).strip()
        if tracked:
            git("checkout", "--", p)
        else:
            (WIKI / p).unlink(missing_ok=True)


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
    else:
        reason = f"failed at {stage}"
    kind = lg.KIND_CHECKS if stage == "checks" else lg.KIND_MODEL_STEP
    if stage not in ("checks", "brief", "write"):
        kind = lg.KIND_UNKNOWN
    _revert_writes()
    src = wiki_inbox(WIKI) / cur["filename"]
    dest = _move_to_failed(src) if src.exists() else wiki_failed(WIKI) / cur["filename"]
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
        ),
    )
    commit_state(f"hold: {cur['filename']} ({kind})")
    return "next"


def step_commit(run_dir: str) -> str:
    cur = current()
    pages = root_md_changes()
    srcs = wiki_sources(WIKI)
    srcs.mkdir(exist_ok=True)
    dest = srcs / cur["filename"]
    shutil.move(str(wiki_inbox(WIKI) / cur["filename"]), dest)
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
        ),
    )
    git("add", "-A")
    git("commit", "-q", "-m", f"ingest: {cur['filename']}")
    return "next"


def step_index_prep(run_dir: str) -> str:
    changes = jlines(Path(run_dir) / "changes.jsonl")
    if not any(r.get("converged") for r in changes):
        return "skip"
    work = wiki_work(WIKI)
    work.mkdir(parents=True, exist_ok=True)
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
    for p in page_files(WIKI):
        fm = page_frontmatter(p) or {}
        srcs = [s for s in (fm.get("sources") or []) if isinstance(s, str)]
        ds = sorted(d for d in (dates.get(s, "") for s in srcs) if d)
        span = f"{ds[0]}..{ds[-1]}" if ds else "undated"
        out.append(
            f"- {p.name} | title: {fm.get('title', '?')} | type: {fm.get('type', '?')} | "
            f"source dates: {span} | last_updated: {fm.get('last_updated', '?')}\n"
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
        print(f"index step touched {', '.join(extras)}; reverting those", file=sys.stderr)
        _revert_writes(keep=("index.md", "log.md"))
    git("add", "-A")
    if changed_paths() or git("diff", "--cached", "--name-only").strip():
        git("commit", "-q", "-m", "index: update index.md and log.md")
    return "done"


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
    reading = WIKI / "READING.md"
    idx = WIKI / "index.md"
    parts = [
        "# How to read this wiki\n\n",
        read_text(reading) if reading.exists() else "(no READING.md)\n",
        "\n# index.md\n\n",
        read_text(idx) if idx.exists() else "(the wiki has no index yet)\n",
        "\n# Question\n\n",
        read_text(d / "question.txt"),
        "\n",
    ]
    (d / "context.md").write_text("".join(parts))
    return "ok"


def step_ask_read(ask_dir: str) -> str:
    d = Path(ask_dir)
    picks = _parse_slugs(read_text(d / "picks.txt")) if (d / "picks.txt").exists() else []
    chosen = []
    for s in picks:
        name = s if s.endswith(".md") else f"{s}.md"
        if name not in NON_PAGE_FILES and (WIKI / name).is_file() and name not in chosen:
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
    "hold": step_hold,
    "commit": step_commit,
    "index_prep": step_index_prep,
    "finalize": step_finalize,
    "init_mode": step_init_mode,
    "default_lens": step_default_lens,
    "lens_check": step_lens_check,
    "ask_assemble": step_ask_assemble,
    "ask_read": step_ask_read,
    "ask_emit": step_ask_emit,
}


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in STEPS:
        print(f"usage: python -m wiki_weaver.steps {{{','.join(STEPS)}}} [args]", file=sys.stderr)
        sys.exit(2)
    token = STEPS[argv[0]](*argv[1:])
    print(token)


if __name__ == "__main__":
    main()
