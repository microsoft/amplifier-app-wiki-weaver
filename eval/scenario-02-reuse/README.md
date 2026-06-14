# Scenario 02 — Schema Externalization Reuse Proof

**Purpose:** Prove that a second, differently-shaped wiki (different domain, different page
taxonomy) can run on the *same engine code* with only project-supplied policy files.

This is the Phase D headline validation scenario.

## What's here

```
eval/scenario-02-reuse/
  sources/                        # 3 tiny source articles (copy to <wiki>/_inbox/ before ingest)
    catan.md                      # Catan — foundational euro game
    wingspan.md                   # Wingspan — 40-70 min playtime (PLANTED CONTRADICTION SOURCE 1)
    engine-building-mechanics.md  # Engine building overview — Wingspan 60-90 min (SOURCE 2)
  policy/                         # Project-supplied policy files
    schema.md                     # Board-games taxonomy (game|mechanic|designer|comparison|catalog|landing)
    validator.yaml                # Custom nav_pages=[catalog,landing], meta_types=[catalog,landing]
  wiki.config.yaml                # max_cycles=2, models.feedback=claude-haiku-4-5
  README.md                       # this file
```

## Planted contradiction

`wingspan.md` (Source 1) cites **40–70 minutes** playtime.
`engine-building-mechanics.md` (Source 2) cites **60–90 minutes** playtime.

The assembled wiki's convergence loop should surface this in an `## Open tensions`
section citing both sides — proving the same contradiction-detection machinery works
on a new domain without any engine changes.

## Validator-seam calibration (deterministic proof, no ingest required)

The Phase D proof requires showing:

1. **Default validator FAILS on catalog/landing** — they're not in built-in `NAV_PAGES`
   so they're flagged as orphans, and not in built-in `META_TYPES` so they must cite a
   source. Running `python pipeline/validate_wiki.py <wiki_with_catalog_landing>` (with
   no `--config`) should FAIL.

2. **Project validator PASSES** — running with
   `--config eval/scenario-02-reuse/policy/validator.yaml` should PASS.

See `eval/test_policy_fallback.py::TestValidatorDefaults::test_config_nav_pages_overrides_default`
for the deterministic test that proves both sides.

## Running the full ingest (slow, needs API key)

```bash
# 1. Init a fresh wiki
wiki-weaver init runs/scenario-02-reuse/wiki

# 2. Stage the source articles
cp eval/scenario-02-reuse/sources/*.md runs/scenario-02-reuse/wiki/_inbox/

# 3. Copy project policy files into the wiki
mkdir -p runs/scenario-02-reuse/wiki/policy
cp eval/scenario-02-reuse/policy/* runs/scenario-02-reuse/wiki/policy/
cp eval/scenario-02-reuse/wiki.config.yaml runs/scenario-02-reuse/wiki/

# 4. Run ingest (max_cycles=2 from wiki.config.yaml, feedback=claude-haiku-4-5)
wiki-weaver ingest --wiki runs/scenario-02-reuse/wiki

# 5. Lint with the project validator (should PASS)
wiki-weaver lint --wiki runs/scenario-02-reuse/wiki

# 6. Verify the taxonomy and contradiction
grep -r "type: game" runs/scenario-02-reuse/wiki/*.md
grep -r "Open tensions" runs/scenario-02-reuse/wiki/*.md
```

## Pass criteria (after ingest)

1. `3/3 sources converge`, archived, ledger written.
2. `wiki-weaver lint` → **PASS** (catalog/landing not flagged as orphans or uncited).
3. At least one page has `type: game` and one `type: mechanic`.
4. `catalog.md` (`type: catalog`) and `landing.md` exist.
5. The Wingspan playtime contradiction surfaces in an `## Open tensions` section.
6. `load_policy(runs/corpus/wiki)` → built-in defaults AND
   `load_policy(runs/scenario-02-reuse/wiki)` → project files (same process, two policies).
