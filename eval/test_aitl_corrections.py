"""wiki_weaver.aitl.corrections: fail-soft loader for lens/corrections/ --
the AITL proxy's read path onto the same durable artifact wiki_weaver
.correct.persist_lens writes and weave already reads."""

from __future__ import annotations

from wiki_weaver.aitl.corrections import MAX_CORRECTIONS_BYTES, load_corrections


def test_missing_directory_yields_empty_result_not_a_failure(tmp_path):
    result = load_corrections(tmp_path)

    assert result.text == ""
    assert result.count == 0
    assert result.truncated is False


def test_loads_a_single_correction(tmp_path):
    corrections_dir = tmp_path / "lens" / "corrections"
    corrections_dir.mkdir(parents=True)
    (corrections_dir / "abc123.md").write_text(
        "---\nid: abc123\nscope: page\npages: [migration-history.md]\n---\n\nAlice, not Bob, led the migration.",
        encoding="utf-8",
    )

    result = load_corrections(tmp_path)

    assert result.count == 1
    assert "Alice, not Bob, led the migration." in result.text
    assert result.truncated is False


def test_loads_multiple_corrections_in_stable_order(tmp_path):
    corrections_dir = tmp_path / "lens" / "corrections"
    corrections_dir.mkdir(parents=True)
    (corrections_dir / "bbb.md").write_text("second correction text", encoding="utf-8")
    (corrections_dir / "aaa.md").write_text("first correction text", encoding="utf-8")

    result = load_corrections(tmp_path)

    assert result.count == 2
    assert result.text.index("first correction text") < result.text.index("second correction text")


def test_unreadable_file_is_skipped_not_fatal(tmp_path, monkeypatch):
    corrections_dir = tmp_path / "lens" / "corrections"
    corrections_dir.mkdir(parents=True)
    good = corrections_dir / "good.md"
    good.write_text("a real correction", encoding="utf-8")
    bad = corrections_dir / "bad.md"
    bad.write_text("unreadable", encoding="utf-8")

    real_read_text = type(bad).read_text

    def _flaky_read_text(self, *args, **kwargs):
        if self.name == "bad.md":
            raise OSError("simulated unreadable file")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(type(bad), "read_text", _flaky_read_text)

    result = load_corrections(tmp_path)

    assert result.count == 1
    assert "a real correction" in result.text


def test_empty_directory_yields_empty_result(tmp_path):
    (tmp_path / "lens" / "corrections").mkdir(parents=True)

    result = load_corrections(tmp_path)

    assert result.text == ""
    assert result.count == 0


def test_truncates_loudly_past_byte_budget(tmp_path):
    corrections_dir = tmp_path / "lens" / "corrections"
    corrections_dir.mkdir(parents=True)
    big_text = "x" * (MAX_CORRECTIONS_BYTES + 1000)
    (corrections_dir / "huge.md").write_text(big_text, encoding="utf-8")
    (corrections_dir / "zzz-second.md").write_text("a smaller correction", encoding="utf-8")

    result = load_corrections(tmp_path, max_bytes=MAX_CORRECTIONS_BYTES)

    assert result.truncated is True
    assert "TRUNCATED" in result.text
