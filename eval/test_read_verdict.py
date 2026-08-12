"""init.read_verdict: fail-closed verdict gate. Only the literal 'enough'
counts; the verdict file is always rm -f'd after reading."""

from __future__ import annotations

from wiki_weaver.init import read_verdict


def test_missing_file_is_not_enough(tmp_path, capsys):
    path = tmp_path / "init-verdict.txt"

    code = read_verdict.main(["--verdict-file", str(path)])

    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "not_enough"


def test_empty_file_is_not_enough(tmp_path, capsys):
    path = tmp_path / "init-verdict.txt"
    path.write_text("", encoding="utf-8")

    code = read_verdict.main(["--verdict-file", str(path)])

    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "not_enough"
    assert not path.exists()  # rm -f even though it existed


def test_garbage_content_is_not_enough(tmp_path, capsys):
    path = tmp_path / "init-verdict.txt"
    path.write_text("ENOUGH!!\n", encoding="utf-8")

    code = read_verdict.main(["--verdict-file", str(path)])

    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "not_enough"
    assert not path.exists()


def test_exact_enough_is_enough(tmp_path, capsys):
    path = tmp_path / "init-verdict.txt"
    path.write_text("enough", encoding="utf-8")

    code = read_verdict.main(["--verdict-file", str(path)])

    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "enough"


def test_verdict_file_is_always_deleted_after_reading(tmp_path):
    path = tmp_path / "init-verdict.txt"
    path.write_text("enough\n", encoding="utf-8")  # trailing newline still strips to "enough"

    read_verdict.main(["--verdict-file", str(path)])

    assert not path.exists()


def test_stale_verdict_never_leaks_into_next_round(tmp_path, capsys):
    """Simulates the loop: round 1 writes 'enough', is read and consumed;
    round 2 runs read_verdict again with no new verdict file written --
    must fail closed, not silently reuse the deleted verdict."""
    path = tmp_path / "init-verdict.txt"
    path.write_text("enough", encoding="utf-8")
    read_verdict.main(["--verdict-file", str(path)])
    capsys.readouterr()

    code = read_verdict.main(["--verdict-file", str(path)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "not_enough"
