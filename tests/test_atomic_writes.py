"""Every page the glue places lands whole: the complete old version or the complete new one.

The resolver returns the corpus whatever the outcome, so a job that dies mid-write would
ship a half-written page, and with .wiki/work/ out of the round trip the next job cannot
know. Glue writes go to a temp file in the same directory and are moved into place with
os.replace. (The writer and index model steps write pages through their own file tools;
see LEARNINGS 2026-10-16 for what moving those behind the same rule takes.)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wiki_weaver import lib


def _no_temps(d: Path) -> None:
    assert not [p.name for p in d.iterdir() if p.name.endswith(".tmp")]


def _failing_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(src, dst):
        raise OSError("killed between staging and replace")

    monkeypatch.setattr(os, "replace", boom)


@pytest.mark.parametrize("op", ["write", "copy"])
def test_failure_between_staging_and_replace_keeps_the_old_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, op: str
) -> None:
    page = tmp_path / "brian.md"
    page.write_bytes(b"old complete page\n")
    src = tmp_path / "src.txt"
    src.write_text("new complete page\n")
    _failing_replace(monkeypatch)
    with pytest.raises(OSError, match="killed"):
        if op == "write":
            lib.atomic_write_text(page, "new complete page\n")
        else:
            lib.atomic_copy(src, page)
    assert page.read_bytes() == b"old complete page\n"
    _no_temps(tmp_path)


@pytest.mark.parametrize("op", ["write", "copy"])
def test_successful_write_places_the_new_page(tmp_path: Path, op: str) -> None:
    page = tmp_path / "brian.md"
    page.write_text("old\n")
    src = tmp_path / "src.txt"
    src.write_text("new complete page\n")
    if op == "write":
        lib.atomic_write_text(page, "new complete page\n")
    else:
        lib.atomic_copy(src, page)
    assert page.read_text() == "new complete page\n"
    _no_temps(tmp_path)


def test_scaffold_reading_md_is_never_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wiki_weaver.cli import scaffold

    _failing_replace(monkeypatch)
    with pytest.raises(OSError):
        scaffold(tmp_path)
    assert not (tmp_path / "READING.md").exists()
    _no_temps(tmp_path)


GLUE = ("cli.py", "steps.py", "migrate.py")
ROUND_TRIP_TARGETS = ("READING.md", "lens_path", "log", "keep", "p.write_text(text")


def test_glue_has_no_direct_writes_to_round_trip_files() -> None:
    """The four glue writes that landed in the round-trip set (READING.md, lens.md, log.md,
    source-versions) and migrate's page rewrites go through the atomic helpers."""
    pkg = Path(__file__).parent.parent / "wiki_weaver"
    offenders = []
    for name in GLUE:
        for n, line in enumerate((pkg / name).read_text().splitlines(), 1):
            s = line.strip()
            direct = (".write_text(" in s or "shutil.copy" in s) and "atomic" not in s
            if direct and any(t in s for t in ROUND_TRIP_TARGETS):
                offenders.append(f"{name}:{n}: {s}")
    assert not offenders, offenders
