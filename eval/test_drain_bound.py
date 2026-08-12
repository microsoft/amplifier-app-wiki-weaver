"""drain_bound: per-invocation source cap, tracked purely as an in-context
counter round-tripped through parse_json."""

from __future__ import annotations

import json

from wiki_weaver.ingest import drain_bound


def test_continue_below_cap(capsys):
    code = drain_bound.main(["--count", "0", "--max-sources", "200"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert payload == {"drain_count": 1, "drain_status": "continue"}


def test_budget_exhausted_when_exceeding_cap(capsys):
    code = drain_bound.main(["--count", "200", "--max-sources", "200"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert payload == {"drain_count": 201, "drain_status": "budget_exhausted"}


def test_zero_max_sources_exhausts_immediately(capsys):
    code = drain_bound.main(["--count", "0", "--max-sources", "0"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert payload["drain_status"] == "budget_exhausted"


def test_output_is_exactly_one_json_line(capsys):
    drain_bound.main(["--count", "5", "--max-sources", "10"])
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == 1


def test_empty_string_count_and_max_sources_fall_back_to_defaults(capsys):
    """Regression (bare-$var substitution defect): pipeline/ingest.dot passes
    --count/--max-sources via bare $drain_count/$max_sources_per_run
    substitution. When those context keys are absent (e.g. drain_bound's
    first-ever invocation in a run, before any parse_json round-trip has
    populated drain_count), the engine leaves the literal token in place and
    bash's unset-variable expansion resolves it to an empty string -- so
    this CLI receives --count "" --max-sources "" (never a missing flag).
    It must apply the defaults (0, 200), not crash with an int() ValueError."""
    code = drain_bound.main(["--count", "", "--max-sources", ""])
    payload = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert payload == {"drain_count": 1, "drain_status": "continue"}
