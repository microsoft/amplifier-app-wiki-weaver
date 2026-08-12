"""init.check_bound: same in-context-counter shape as ingest.drain_bound,
plus a bound-hit.flag file touched on hit (relative to cwd -- there is no
--wiki-root argument on this subcommand)."""

from __future__ import annotations

import json

from wiki_weaver.init import check_bound


def test_ok_status_below_max_rounds(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code = check_bound.main(["--count", "0", "--max-rounds", "5"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload == {"interview_rounds": 1, "bound_status": "ok"}
    assert not (tmp_path / ".ai" / "bound-hit.flag").exists()


def test_hit_status_at_max_rounds_writes_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code = check_bound.main(["--count", "5", "--max-rounds", "5"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload == {"interview_rounds": 6, "bound_status": "hit"}
    assert (tmp_path / ".ai" / "bound-hit.flag").is_file()


def test_empty_string_count_and_max_rounds_fall_back_to_defaults(tmp_path, monkeypatch, capsys):
    """Regression (bare-$var substitution defect): pipeline/init.dot passes
    --count/--max-rounds via bare $interview_rounds/$max_interview_rounds
    substitution. When those context keys are absent, the engine leaves the
    literal token in place and bash's unset-variable expansion resolves it
    to an empty string -- so this CLI receives --count "" --max-rounds ""
    (never a missing flag). It must apply the defaults (0, 5), not crash
    with an int() ValueError."""
    monkeypatch.chdir(tmp_path)
    code = check_bound.main(["--count", "", "--max-rounds", ""])

    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload == {"interview_rounds": 1, "bound_status": "ok"}


def test_stdout_is_exactly_one_json_line(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    check_bound.main(["--count", "0", "--max-rounds", "1"])
    out = capsys.readouterr().out.strip()
    assert len(out.splitlines()) == 1
    payload = json.loads(out)
    assert set(payload) == {"interview_rounds", "bound_status"}
