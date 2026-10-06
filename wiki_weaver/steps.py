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
from .lib import (
    MAX_SOURCE_CHARS,
    NON_PAGE_FILES,
    atomic_append_line,
    atomic_write_text,
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


def commit_state(message: str) -> None:
    """Commit bookkeeping (ledger, held files) so the next write's scope check sees
    only what that write changed."""
    git("add", "-A")
    if git("diff", "--cached", "--name-only").strip():
        git("commit", "-q", "-m", message)


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


INFLIGHT = "inflight"  # .wiki/work/inflight: tracked state is being changed right now


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


def _mark_inflight() -> None:
    work = wiki_work(WIKI)
    work.mkdir(parents=True, exist_ok=True)
    (work / INFLIGHT).write_text(now())


def _clear_inflight() -> None:
    (wiki_work(WIKI) / INFLIGHT).unlink(missing_ok=True)


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
                # retry: the held copy comes back to _inbox/; record that in git now so
                # the write's scope check sees only the writer's changes
                _mark_inflight()
                _place(held, inbox)
                held.unlink()
                commit_state(f"retry: {name}")
                _clear_inflight()
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
            _mark_inflight()
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
            commit_state(f"skip: {p.name} (empty)")
            _finish(p)
            _clear_inflight()
            if only and only != "-":
                return "drained"
            continue
        if lg.is_oversized(p):
            _mark_inflight()
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
            commit_state(f"hold: {p.name} (oversized)")
            _finish(p)
            _clear_inflight()
            continue
        rows = lg.read_rows(WIKI)
        prev = wiki_sources(WIKI) / p.name
        prev_sha = sha256_file(prev) if prev.is_file() else None
        changed = prev_sha is not None and prev_sha != sha
        save_current(
            {
                "filename": p.name,
                "sha256": sha,
                "source_id": lg.source_id_for(rows, sha),
                "changed": changed,
                "prev_source_id": lg.source_id_for(rows, prev_sha) if changed else None,
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
    sid = cur["source_id"]
    parts += [
        f"\n\ntoday: {now()[:10]}\n",
        "\n# Source\n",
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
    for name in selected:
        p = WIKI / name
        if p.exists():
            shutil.copy2(p, before / name)
            n_src = len((page_frontmatter(p) or {}).get("sources") or [])
            out += [f"\n\n## {name} (existing, {n_src} sources)\n\n", read_text(p)]
        else:
            out += [f"\n\n## {name} (new page - does not exist yet)\n"]
    idx = WIKI / "index.md"
    out += ["\n\n# index.md\n\n", read_text(idx) if idx.exists() else "(empty)\n"]
    (work / "pages.md").write_text("".join(out))
    (work / "selected.txt").write_text("\n".join(selected) + "\n")
    chars = sum(len(read_text(work / f)) for f in ("context.md", "pages.md", "brief.md"))
    cur.update(stage="write", model_calls=cur["model_calls"] + 1, selected=selected)
    cur["writer_input_chars"] = chars
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
    pages += [p for p in selected if p not in pages and (work / "before" / p).exists()]
    errs += ck.run_page_checks(
        WIKI, pages, work / "before", check_sources(cur), cur.get("source_id")
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
        cur["model_calls"] += 1
        cur["stage"] = "write"
        save_current(cur)
        return "rewrite"
    save_current(cur)
    return "hold"


def _in_head(path: str) -> bool:
    r = subprocess.run(
        ["git", "cat-file", "-e", f"HEAD:{path}"], cwd=WIKI, capture_output=True, check=False
    )
    return r.returncode == 0


def _revert_writes(keep: tuple[str, ...] = ()) -> list[str]:
    """Return every uncommitted path (tracked, staged or untracked; ignored paths are
    untouched) to its state at HEAD. Restores from HEAD, not the index, so a staged
    half-page does not survive."""
    reverted = []
    for p in changed_paths():
        if p in keep:
            continue
        if _in_head(p):
            git("checkout", "HEAD", "--", p)
        else:
            git("rm", "-q", "--cached", "--ignore-unmatch", "--", p, check=False)
            (WIKI / p).unlink(missing_ok=True)
        reverted.append(p)
    return reverted


def step_recover() -> str:
    """Undo a source left half-processed by a run that died: if a source or its
    bookkeeping was in flight, return every uncommitted change to HEAD and clear the
    scratch space. The source is then in exactly one place: still in _inbox/ (picked up
    again), or committed (its leftover inbox copy, identical to _sources/, is dropped)."""
    work = wiki_work(WIKI)
    did = False
    if (work / "current.json").exists() or (work / INFLIGHT).exists():
        cur = jread(work / "current.json", {}) if (work / "current.json").exists() else {}
        reverted = _revert_writes()
        shutil.rmtree(work, ignore_errors=True)
        print(
            f"recovered interrupted source {cur.get('filename', '(bookkeeping)')}: "
            f"reverted {reverted}",
            file=sys.stderr,
        )
        did = True
    done = lg.converged_hashes(lg.read_rows(WIKI))
    for p in sorted(wiki_inbox(WIKI).glob("*.md")) if wiki_inbox(WIKI).is_dir() else []:
        kept = wiki_sources(WIKI) / p.name
        h = sha256_file(p)
        if (
            h in done
            and kept.is_file()
            and sha256_file(kept) == h
            and _in_head(f"_sources/{p.name}")
        ):
            p.unlink()
            print(
                f"recovered: {p.name} was already committed; dropped its inbox copy",
                file=sys.stderr,
            )
            did = True
    return "recovered" if did else "clean"


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
            wall_seconds=round(time.time() - cur.get("t0", time.time()), 1),
        ),
    )
    commit_state(f"hold: {cur['filename']} ({kind})")
    _finish(src)
    return "next"


def step_commit(run_dir: str) -> str:
    cur = current()
    pages = root_md_changes()
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
            wall_seconds=round(time.time() - cur.get("t0", time.time()), 1),
        ),
    )
    git("add", "-A")
    git("commit", "-q", "-m", f"ingest: {cur['filename']}")
    _finish(src)  # last: until here a crash leaves the source in _inbox/
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


def step_index_restore() -> str:
    """The index step failed: keep the per-source page commits, return index.md, log.md
    and anything else the index step touched to HEAD, and fail the run."""
    reverted = _revert_writes()
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
    reading = WIKI / "READING.md"
    idx = WIKI / "index.md"
    parts = [
        "# READING.md\n\n",
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
    root = WIKI.resolve()
    chosen = []
    for s in picks:
        name = s if s.endswith(".md") else f"{s}.md"
        try:
            target = (root / name).resolve()
        except (OSError, ValueError):
            continue
        # a page is a file directly under the corpus root; anything else is refused
        if target.parent != root or not target.is_file():
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
