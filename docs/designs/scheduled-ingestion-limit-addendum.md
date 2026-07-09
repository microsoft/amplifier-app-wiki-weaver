# Addendum spec: `--limit` scope-capping for scheduled ingestion

**Status:** Ready for implementation (hand to modular-builder).
**Scope:** An *addendum* to `docs/designs/scheduled-ingestion-spec.md` — not a rewrite. Adds a
single `--limit` knob that caps how many sources actually invoke LLM synthesis per invocation,
so a fresh wiki pointed at a large backlog (the maintainer cited 800+ articles) cannot turn one
unattended tick into many hours of unattended, LLM-driven work.

**Hard constraint (maintainer, non-negotiable):** `ingest()`'s **core sort/selection order** and
its **single-file `--source` path** stay unchanged. This addendum adds a parameter and one gate
inside the *drain* loop only. It does not reorder, re-rank, or re-select.

**Council-verified:** the design below resolves every concrete correctness issue the 6-lens
council raised. Each requirement is cross-referenced (`[C#]`) at its implementation point.

---

## 1. Design in one paragraph

Add an optional `limit: int | None` to `ingest()`. In the **drain path only**, count sources that
reach real LLM synthesis (`run_inner`). Cheap dispositions the loop already skips for free
(binary rejects, already-ingested duplicates) never touch the counter. A gate placed *exactly at
the point of committing a source to `run_inner`* stops the drain when the budget is spent — and,
because the gate sits **after** eligibility has been determined, "we stopped with real work still
pending" is decided precisely and cheaply, without any end-of-loop inbox rescan. Manual
`wiki-weaver ingest` defaults to unlimited (a human typed it — attended). `schedule install` /
`run_now` default to a conservative built-in cap (unattended by definition). `run-now --limit N`
overrides the persisted per-instance cap for one tick without rewriting config. On cap-hit the
drain emits a loud `WARN` (the codebase's established skip/escalation convention) and the tick
records a queryable `hit_limit` flag in run-state.

---

## 2. The counting model (the crux — read this first)

The drain loop (`lib.py` ~840–1001) disposes each picked file in one of these ways per pass:

| Disposition | Where | Cost | Counts against `--limit`? |
|---|---|---|---|
| binary sniff fail → `_failed/` | ~909–916, **before** `run_inner` | cheap | **No** `[C3]` |
| already-ingested duplicate → `_sources/` | ~924–932, **before** `run_inner` | cheap | **No** `[C3]` |
| `run_inner(...)` invoked (converged / not-converged / error / tamper) | ~945–999 | **real LLM work** | **Yes** `[C3]` |

**The budget counts commitments to `run_inner`, nothing else.** A source that raises inside
`run_inner`, tampers, or fails to converge still consumed real LLM work, so it still counts. Only
the free pre-`run_inner` dispositions are exempt. This is exactly the maintainer's case: a tick
full of duplicates disposes them all cheaply, consumes zero budget, and reports a **complete**
drain — never a false "capped" `[C3][C4]`.

### The gate (off-by-one-correct by construction) `[C4]`

Place the gate **after** the eligibility determination (post binary-sniff, post dedup) and
**immediately before** `run_inner`. Check *then* increment:

```
# eligibility already determined here: text file, not a duplicate, real source
if limit is not None and real_count >= limit:
    # We are holding the (limit+1)-th ELIGIBLE source and have no budget left.
    # => real work remains pending  => this is a genuine cap.
    if report is not None:
        report.hit_limit = True
    _warn(<loud cap message, see §7>)
    break                       # stop the drain; leave this file in _inbox for the next tick
real_count += 1
# ... existing snapshot + run_inner(...) block, UNCHANGED ...
```

Why this placement is exactly right:

- **Exactly-N eligible files → complete, not capped** `[C4]`. With `limit == N`, files 1..N each
  pass the gate (`real_count` is `0..N-1` at check time) and run. The next loop pass re-globs,
  finds no ready eligible file, and breaks *normally* with `hit_limit` still `False`.
- **N+1 eligible files → capped** `[C4]`. Files 1..N run (`real_count == N`); the gate then holds
  file N+1, sees `N >= N`, sets `hit_limit`, and breaks — leaving file N+1 in `_inbox/`.
- **"Still pending?" excludes cheap dispositions for free** `[C4]`. Duplicates/binaries after the
  last real source are disposed cheaply on subsequent passes; they never trip the gate. So the
  cap fires **iff a genuine real-work source remains undone** — no separate "is the inbox
  non-empty?" scan, which would wrongly count leftover duplicates as pending work.
- **`--limit 0` → zero real ingests** `[C5]`. The first eligible source hits `0 >= 0` and breaks
  before any `run_inner`. Cheap dispositions still run (they cost milliseconds and keep the inbox
  tidy); if any eligible source exists, `hit_limit` is `True` + loud WARN. `0` is a real value,
  never a truthy/falsy "unlimited".

**Load-bearing-invariant note:** the drain's documented invariant is "every file *picked* from
`_inbox/` must *leave* `_inbox/` this pass" — this guarantees termination (no infinite spin). The
gate only ever `break`s (stops the whole drain); it never `continue`s while leaving a picked file
behind. Leaving file N+1 in `_inbox/` on a `break` is the *intended* deferral, not a spin. The
invariant is preserved. `_assign_source_id` may have pre-registered the deferred source's stable
id — that is idempotent and harmless; the next tick re-looks-it-up. Document this with an inline
comment at the gate.

---

## 3. File-by-file changes

### 3.1 `wiki_weaver/instances.py` — backward-compat + new fields `[C1]` (**highest priority**)

**The bug being fixed:** `InstanceConfig(**raw)` / `RunState(**raw)` are bare kwarg-unpacks. Any
on-disk `instance.json` / `run-state.json` written by the version about to merge lacks the new
keys; a bare unpack of a dict *missing* a required field, or *carrying* an unknown field, raises
`TypeError`. That would crash `schedule list` / `status` / `run-now` on **every already-scheduled
instance**. Fix both directions: defaults for missing keys **and** tolerant reconstruction that
drops unknown keys.

1. Add `from dataclasses import asdict, dataclass, fields` (add `fields`).

2. `InstanceConfig`: append one **defaulted** field (last position keeps the existing
   non-defaulted fields valid):
   ```
   limit: int | None = None   # per-instance cap; None = not configured (run_now falls back to
                              # the unattended default). Set concretely by `schedule install`.
   ```

3. `RunState`: append one **defaulted** field:
   ```
   hit_limit: bool = False    # did the most recent RUN tick stop early on --limit?
   ```

4. Add a tolerant reconstructor and use it in **both** readers:
   ```
   def _reconstruct(cls, raw: dict):
       known = {f.name for f in fields(cls)}
       return cls(**{k: v for k, v in raw.items() if k in known})
   ```
   - `read_instance_config` (191–197): `return _reconstruct(InstanceConfig, raw)`
   - `read_run_state` (200–206): `return _reconstruct(RunState, raw)`

   Missing keys → dataclass defaults fill them. Unknown keys → dropped. Neither can raise
   `TypeError`. Existing `_atomic_write_json` / `write_*` are unchanged (`asdict` serializes the
   new fields automatically).

### 3.2 `wiki_weaver/lib.py` — `ingest()` gains `limit` (drain path only)

1. Add a tiny result holder near the other module-level defs (add `from dataclasses import
   dataclass` if lib.py doesn't already import it — it currently does not):
   ```
   @dataclass
   class DrainReport:
       hit_limit: bool = False
   ```
   Export it in lib's public surface if lib has an `__all__`; otherwise it is importable by name.

2. Widen the signature — **return type stays `-> int`** (see rationale below):
   ```
   def ingest(
       wiki: str | Path = ".",
       *,
       source: str | Path | None = None,
       max_cycles: int | None = None,
       keep_going: bool = False,
       limit: int | None = None,          # NEW: drain-path cap on real-ingest sources; None = unlimited
       report: DrainReport | None = None, # NEW: opt-in out-channel for hit_limit (run_now passes one)
   ) -> int:
   ```
   Update the docstring: `limit` caps the number of sources that reach `run_inner` in **drain
   mode**; `None` means unlimited; `0` means process zero real-ingestion sources this call.
   `report`, when provided, has `.hit_limit` set to `True` if the drain stopped early on the cap.

   **Why an opt-in `report` holder instead of widening the return type:** `ingest() -> int` is a
   contract three call sites and the existing tests depend on (`cmd_ingest` does `return
   ingest(...)`; tests assert `== 0`). Widening the return would ripple to all of them and risks
   the exact backward-compat class of breakage `[C1]` warns about. The optional holder is
   invisible to every existing caller (default `None`) and gives `run_now` the one bool it needs.
   The **loud** signal does not depend on it (see §7) — the holder only feeds the queryable
   run-state field.

3. **Single-file `--source` path (unchanged) `[C9c]`.** Add one comment at the top of the
   `if source:` branch: `# NOTE: --limit is a no-op in single-file mode (exactly one source).`
   Do not reference `limit` anywhere in this branch.

4. **Drain path.** Initialize `real_count = 0` just before `with shared_engine_loop():` (~869).
   Insert the gate from §2 after the dedup `already_done` block and the `is_new` prints (~936),
   immediately before the `print(f"\n=== ingest: ...")` / snapshot / `run_inner` block (~938–946).
   Add an inline comment at `pending = sorted(...)` (~889) recording the accepted alphabetical
   selection-order limitation (see §8, `[C9a]`).

   Do **not** change the sort, the debounce, the fresh-retry logic, the dispositions, the
   post-drain fail-loud summary, or the exit-code computation.

### 3.3 `wiki_weaver/schedule.py` — validation, default, install, run_now, status/list

1. Constant with the reviewer-aligned reasoning **inline** `[C6]`:
   ```
   # Unattended default cap. A scheduled tick runs with NOBODY watching, so one tick must not
   # balloon into hours of LLM-driven synthesis (the maintainer's 800+-article backlog case).
   # Sizing: real per-source convergence is multi-cycle and empirically runs a few minutes each.
   # At ~3-5 min/source, 10 real ingests is roughly 30-50 min of work — comfortably inside a
   # common cron cadence (e.g. hourly) with margin, so a large backlog drains steadily over many
   # bounded ticks instead of one unbounded marathon. Operators who want more per tick set an
   # explicit `schedule install --limit N` (persisted) or `run-now --limit N` (one-off).
   _DEFAULT_SCHEDULED_LIMIT = 10
   ```

2. Single validator, mirroring `interval_to_cron`'s style (raise `ValueError`; callers `_fail` +
   return exit 2). Export in `__all__` `[C5]`:
   ```
   def validate_limit(n: int | None) -> int | None:
       """Reject a negative --limit. None = unlimited; 0 = pause real ingestion this run."""
       if n is not None and n < 0:
           raise ValueError(
               f"--limit must be >= 0 (got {n}); use 0 to process zero real-ingestion "
               f"sources this run, or omit --limit for unlimited"
           )
       return n
   ```

3. `install(...)` gains `limit: int | None = None` `[C6][C5]`:
   - Validate via `validate_limit` inside the existing `try/except ValueError` block that already
     wraps `interval_to_cron`/`_validate_cron_expr` (`_fail(str(exc)); return 2`).
   - Resolve the **persisted** cap: `persisted = limit if limit is not None else
     _DEFAULT_SCHEDULED_LIMIT`. So a plain `schedule install` writes `cfg.limit == 10` (visible in
     `status`), and `--limit N` persists `N`. Set `InstanceConfig(..., limit=persisted)`.

4. `run_now(wiki, *, limit: int | None = None)` gains the ad-hoc override `[C7][C5]`:
   - Validate the incoming `limit` via `validate_limit` early (on `ValueError`: `_fail`, return 2).
   - Resolve the **effective** cap (explicit override > persisted > unattended default):
     ```
     if limit is not None:                              # ad-hoc override, incl. 0
         effective = limit
     elif cfg is not None and cfg.limit is not None:
         effective = cfg.limit                          # persisted per-instance cap
     else:
         effective = _DEFAULT_SCHEDULED_LIMIT           # bare run-now OR legacy config w/o limit
     ```
     Note `cfg` is read **once** at the top of `run_now` — a persisted-limit change via `schedule
     install` therefore takes effect on the **next** tick, not a currently-running one. This is
     expected `[C9b]`; add an inline comment at the `cfg = read_instance_config(...)` read.
     Bare `run-now` and legacy pre-feature instances fall to the conservative default, so
     unattended paths are **always bounded** — the only route to unbounded unattended work is an
     explicit large `--limit`, by design (safety follows the reasoning to its conclusion).
   - In the RUN path (after `st.consecutive_skips = 0`), also reset `st.hit_limit = False`.
   - Build `report = _DrainReport()` (import `DrainReport` from `wiki_weaver.lib`), pass both
     `limit=effective` and `report=report` into `_ingest(canon, limit=effective, report=report)`.
   - After the call, set `st.hit_limit = report.hit_limit` before the existing
     `write_run_state`. The loud WARN itself comes from inside `ingest()` and is already captured
     into the tick log by the existing `_tee_stdout_stderr` wrapper `[C8]`.
   - **Do not** treat a cap-hit as failure: the tick still returns `ingest()`'s exit code (0 on a
     clean capped drain), so cron sends no `MAILTO` spam.

5. `status(...)` prints two new lines `[C1][C8]`:
   `print(f"limit: {cfg.limit}")` (inside the `cfg is not None` block) and
   `print(f"hit_limit: {st.hit_limit}")` (with the other run-state lines).

6. `list_all(...)`: append a ` CAPPED` marker to the row when `st.hit_limit` (mirroring the
   existing ` ALERT` marker), so a capped tick is visible at a glance.

### 3.4 `wiki_weaver/cli.py` — argparse + dispatch

1. `cmd_ingest` `[C5]`: validate then pass through:
   ```
   from wiki_weaver.schedule import EXIT_SKIP, validate_limit
   try:
       limit = validate_limit(args.limit)
   except ValueError as exc:
       _fail(str(exc)); return 2
   ...
   return ingest(args.wiki, source=args.source, max_cycles=args.max_cycles,
                 keep_going=args.keep_going, limit=limit)
   ```

2. `cmd_schedule`: forward `limit=args.limit` to `sched.install(...)` and
   `sched.run_now(args.wiki, limit=args.limit)`. (`run-now`/`install` do their own
   `validate_limit`; `cmd_ingest` validates locally because it calls `ingest()` directly.)

3. Argparse — one flag name, `--limit`, **no `--max-sources` alias** `[C2]`. `type=int,
   default=None` on all three parsers (the argparse `default=None` is what distinguishes
   "omitted → unlimited/inherit" from an explicit `0`):
   - `p_ingest`: `--limit` help: `"cap real-ingest sources this run (default: unlimited)"`.
   - `s_install`: `--limit` help: `"per-tick cap on real-ingest sources (default: 10)"`.
   - `s_run` (run-now): `--limit` help: `"override the persisted per-tick cap for THIS tick only"`.

---

## 4. Default matrix (summary)

| Invocation | `--limit` omitted | `--limit N` | `--limit 0` |
|---|---|---|---|
| `wiki-weaver ingest` (attended) | **unlimited** (`None`) `[C6]` | cap at N | zero real ingests `[C5]` |
| `schedule install` (persists cap) | persist **10** `[C6]` | persist N | persist 0 (pause-without-uninstall) |
| `schedule run-now` (one tick) | inherit persisted / **10** if none `[C7]` | override → N this tick, config unchanged `[C7]` | override → 0 this tick |

---

## 5. Backward compatibility (explicit) `[C1]`

- New dataclass fields are **defaulted** → dicts missing them reconstruct fine.
- Both readers use `_reconstruct` → dicts with **unknown** keys reconstruct fine.
- `ingest()` return type is **unchanged** (`-> int`) → `cmd_ingest` and existing tests unaffected.
- A pre-feature scheduled instance (config has no `limit`, state has no `hit_limit`) loads,
  `status`/`list`/`run-now` all succeed, and its ticks become bounded at `_DEFAULT_SCHEDULED_LIMIT`.

---

## 6. Accepted limitations — document, do NOT "fix" `[C9]`

State all three in this spec **and** as inline doc comments at the cited locations. Do not attempt
to fix any of them in this change.

1. **Selection order is existing alphabetical-by-filename** (`pending = sorted(...)`, lib.py ~889).
   It predates this feature. Under sustained new arrivals with early-sorting names, an older
   backlog item with a late-sorting name can be deferred across many ticks. Accepted; not a
   fairness bug introduced here. Comment at the `sorted(...)` line. `[C9a]`
2. **A persisted `--limit` change takes effect on the NEXT tick**, because `run_now` reads config
   once at the top. Expected behavior, not a bug. Comment at the `read_instance_config` read. `[C9b]`
3. **`--limit` has no effect with `--source`** (single-file mode processes exactly one file).
   Comment in the `if source:` branch; asserted by a test. `[C9c]`

---

## 7. The loud cap-hit signal `[C8]`

On cap the drain calls `_warn(...)` — the same loud convention lib already uses for skip /
"already ingested" / escalation lines (and which `run_now`'s `_tee_stdout_stderr` copies into the
tick log). It is **not** merely a passive run-state field. Suggested message (state the cap was
hit and that real work remains, without an exact remaining count — counting all remaining eligible
sources would need an extra dedup scan we deliberately avoid):

```
_warn(
    f"LIMIT REACHED: processed {real_count} real-ingest source(s) this pass "
    f"(--limit {limit}); at least one more eligible source remains in _inbox/ and will be "
    f"handled on the next tick. Raise the cap with `schedule install --limit N`, or process "
    f"more now with `schedule run-now --wiki <dir> --limit N`."
)
```

`run_now` additionally persists `hit_limit=True` so `status`/`list` surface it after the fact. Two
signals, one loud (log/stderr) and one queryable (run-state) — the council required the loud one.

---

## 8. Test plan (must prove every requirement)

Tests live in `eval/` (`test_*.py`); run with `uv run pytest eval/ -q`. The drain tests must
**mock `run_inner`** (a counter that returns a converged-result stub) so no real LLM/runtime is
needed — these are deterministic and must not self-skip on absent `amplifier_foundation`. Use a
`tmp_path` wiki with a real `_inbox/`, and `monkeypatch WIKI_WEAVER_DATA_DIR` to a tmp dir for the
instance-storage tests.

**Backward compatibility `[C1]` (highest priority):**
- `test_read_instance_config_legacy_no_limit` — write `instance.json` with only the original
  fields (no `limit`); `read_instance_config` returns `limit is None`, no crash.
- `test_read_run_state_legacy_no_hit_limit` — write `run-state.json` without `hit_limit`;
  `read_run_state` returns `hit_limit is False`, no crash.
- `test_readers_drop_unknown_keys` — both JSONs carry an extra unknown key; reconstruction drops
  it, no `TypeError`.
- `test_status_on_legacy_instance` — `schedule status` on a legacy config/state exits 0 and prints
  (command-level proof of the crash fix).

**Counting semantics `[C3]`:**
- `test_limit_counts_only_real_ingests` — inbox = 2 duplicates + 2 binaries + 3 new, `limit=2` →
  `run_inner` called exactly 2×; dups+binaries disposed; 1 new left in `_inbox/`; `hit_limit True`.
- `test_all_duplicates_report_complete_not_capped` — inbox = only already-ingested duplicates,
  `limit=1` → `run_inner` called 0×; all disposed; `hit_limit False` (this is the maintainer's
  "tick full of duplicates must not report capped" case).

**Off-by-one `[C4]`:**
- `test_exactly_N_eligible_reports_complete` — inbox = exactly 3 new, `limit=3` → 3 calls, inbox
  drained, `hit_limit False`.
- `test_N_plus_one_eligible_reports_capped` — inbox = 4 new, `limit=3` → 3 calls, 1 left,
  `hit_limit True`.

**Validation & zero `[C5]`:**
- `test_negative_limit_rejected` — `validate_limit(-1)` raises `ValueError`; and `cmd_ingest` /
  `schedule install` / `schedule run-now` with `--limit -1` return exit 2 with a clear message and
  **no** `run_inner` call.
- `test_limit_zero_processes_zero_real_sources` — inbox = 3 new, `limit=0` → `run_inner` 0×,
  `hit_limit True`, loud WARN emitted; contrast `limit=None` on the same inbox processes all 3.

**Defaults `[C6]`:**
- `test_manual_ingest_default_unlimited` — `ingest(wiki)` (no `limit`) over N>10 new sources →
  all N processed.
- `test_install_persists_default_limit` — `schedule install` with no `--limit` → reloaded
  `cfg.limit == _DEFAULT_SCHEDULED_LIMIT`.
- `test_bare_run_now_uses_unattended_default` — `run_now` with no installed config, inbox >
  default new sources → capped at `_DEFAULT_SCHEDULED_LIMIT`.

**Ad-hoc override `[C7]`:**
- `test_run_now_override_does_not_persist` — install persists `limit=5`; `run_now(wiki, limit=2)`
  → exactly 2 processed this tick; reload `cfg.limit` still `5`.
- `test_run_now_inherits_persisted_when_no_override` — persisted `limit=5`, `run_now(wiki)` (no
  override) → caps at 5.

**Loud signal `[C8]`:**
- `test_cap_hit_emits_loud_warn_and_records_state` — capture stdout/stderr (or the tick log); a
  WARN-level line containing `LIMIT REACHED` is present (not just the field); and `run-state.json`
  `hit_limit` is `True`.

**Selection order preserved `[C9a]`:**
- `test_cap_processes_alphabetically_first_N` — inbox files `z1, z2, a1, a2, a3`, `limit=2` →
  `run_inner` receives `a1` then `a2`; `a3, z1, z2` remain. Confirms sort/selection unchanged.

**Single-file mode `[C9c]`:**
- `test_source_mode_ignores_limit` — `ingest(wiki, source=<file>, limit=0)` still processes the one
  file (`run_inner` called once); `limit` has no effect in single-file mode.

---

## 9. Docs to update (keep docs coherent — repo's "no context-poison" discipline)

- `AGENTS.md`: the `schedule` paragraph currently says ticks "drain `_inbox/` via the existing
  `ingest()` core **unchanged**." Amend to note the drain now accepts a per-tick `--limit` cap
  (unattended default 10; manual `ingest` unlimited; `run-now --limit N` one-off override) and add
  a one-line pointer to this addendum. Keep it to the schedule paragraph.
- `docs/designs/scheduled-ingestion-spec.md`: add a short cross-reference line pointing here.

---

## 10. Definition of done

- [ ] `instances.py`: `limit` / `hit_limit` fields defaulted; both readers use `_reconstruct`.
- [ ] `lib.py`: `ingest()` gains `limit` + `report`; drain gate + counter added; single-file path
      and sort/selection untouched; three limitation comments in place.
- [ ] `schedule.py`: `_DEFAULT_SCHEDULED_LIMIT` (with reasoning comment), `validate_limit`,
      `install`/`run_now` limit plumbing, `hit_limit` reset+record, `status`/`list` display.
- [ ] `cli.py`: `--limit` on `ingest` / `schedule install` / `schedule run-now` (one name, no
      alias); validation wired; dispatch forwards `limit`.
- [ ] All §8 tests added and passing under `uv run pytest eval/ -q`.
- [ ] Quality checks (format, lint, types) pass.
- [ ] `AGENTS.md` + `scheduled-ingestion-spec.md` cross-references updated.
