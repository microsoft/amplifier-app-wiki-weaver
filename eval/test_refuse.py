"""ask.refuse: reads --refusal-file directly and prints it loudly -- refusal
is a first-class outcome, not a swallowed error."""

from __future__ import annotations

from wiki_weaver.ask import refuse


def test_prints_refusal_content_loudly(tmp_path, capsys):
    refusal_path = tmp_path / "refusal.md"
    refusal_path.write_text(
        "Searched candidate pages for 'quarterly headcount' -- no page discusses staffing numbers.\n",
        encoding="utf-8",
    )

    code = refuse.main(["--loud", "--refusal-file", str(refusal_path)])

    assert code == 0
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err
    assert "quarterly headcount" in captured.out


def test_missing_refusal_file_fails_loud(tmp_path, capsys):
    missing_path = tmp_path / "refusal.md"

    code = refuse.main(["--loud", "--refusal-file", str(missing_path)])

    assert code == 1
    assert "no refusal file" in capsys.readouterr().err
