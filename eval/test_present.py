"""ask.present: reads --answer-file directly and prints it -- the answer
text never transits a $substitution token."""

from __future__ import annotations

from wiki_weaver.ask import present


def test_prints_answer_file_content_verbatim(tmp_path, capsys):
    answer_path = tmp_path / "answer.md"
    answer_path.write_text("The runbook owner is [[jane-doe|Jane Doe]] (see [[oncall-runbook]]).\n", encoding="utf-8")

    code = present.main(["--answer-file", str(answer_path)])

    assert code == 0
    out = capsys.readouterr().out
    assert "The runbook owner is [[jane-doe|Jane Doe]]" in out


def test_missing_answer_file_fails_loud(tmp_path, capsys):
    missing_path = tmp_path / "answer.md"

    code = present.main(["--answer-file", str(missing_path)])

    assert code == 1
    assert "not found" in capsys.readouterr().err
