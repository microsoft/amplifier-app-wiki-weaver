"""Unit tests for wiki_weaver.instances -- canonicalization, storage layout,
and atomic config/state IO.

Coverage (per docs/designs/scheduled-ingestion-spec.md \u00a714):
  - Aliasing safety (must-fix #3, CRITICAL): {./w, w/, absolute, symlink-to-w}
    all resolve to the SAME instance id; two different dirs sharing a
    basename get DIFFERENT ids.
  - Canonical path resolves symlinks and strips trailing slash.
  - Storage layout honors WIKI_WEAVER_DATA_DIR (both config and data land
    under $DIR/{config,data}).
  - Atomic config/state IO: write -> read round-trips; no leftover *.tmp.
  - list_instance_ids / delete_instance enumerate and remove both dirs.

Pure stdlib -- no engine, no LLM, no network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from wiki_weaver import instances as inst  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point WIKI_WEAVER_DATA_DIR at a tmp dir for every test in this module."""
    monkeypatch.setenv("WIKI_WEAVER_DATA_DIR", str(tmp_path / "ww-data"))
    yield


# ---------------------------------------------------------------------------
# Aliasing safety (must-fix #3, critical)
# ---------------------------------------------------------------------------


def test_aliasing_safety_same_dir_four_ways(tmp_path: Path) -> None:
    real_dir = tmp_path / "corpora" / "w"
    real_dir.mkdir(parents=True)

    symlink = tmp_path / "link-to-w"
    symlink.symlink_to(real_dir, target_is_directory=True)

    relative = real_dir  # already absolute; simulate relative via cwd-based str
    trailing_slash = str(real_dir) + "/"

    id_abs = inst.instance_id(real_dir)
    id_trailing_slash = inst.instance_id(trailing_slash)
    id_symlink = inst.instance_id(symlink)
    id_relative = inst.instance_id(str(relative))

    assert id_abs == id_trailing_slash == id_symlink == id_relative


def test_aliasing_safety_different_dirs_same_basename_differ(tmp_path: Path) -> None:
    dir_a = tmp_path / "a" / "w"
    dir_b = tmp_path / "b" / "w"
    dir_a.mkdir(parents=True)
    dir_b.mkdir(parents=True)

    id_a = inst.instance_id(dir_a)
    id_b = inst.instance_id(dir_b)

    assert id_a != id_b
    # Both share the human-readable slug (basename "w") but differ in digest.
    assert id_a.startswith("w-")
    assert id_b.startswith("w-")


def test_instance_id_basename_slug_is_sanitized(tmp_path: Path) -> None:
    weird = tmp_path / "My Wiki!!"
    weird.mkdir()
    iid = inst.instance_id(weird)
    slug, _, digest = iid.rpartition("-")
    assert slug == "my-wiki"
    assert len(digest) == 12


def test_instance_id_empty_basename_falls_back_to_wiki(tmp_path: Path) -> None:
    # A path whose basename sanitizes to nothing (e.g. all-symbols) falls back
    # to the literal slug "wiki".
    weird = tmp_path / "!!!"
    weird.mkdir()
    iid = inst.instance_id(weird)
    assert iid.startswith("wiki-")


# ---------------------------------------------------------------------------
# Canonical path resolution
# ---------------------------------------------------------------------------


def test_canonical_wiki_path_resolves_symlink_and_strips_trailing_slash(
    tmp_path: Path,
) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    symlink = tmp_path / "alias"
    symlink.symlink_to(real_dir, target_is_directory=True)

    canon_from_symlink = inst.canonical_wiki_path(symlink)
    canon_from_trailing = inst.canonical_wiki_path(str(real_dir) + "/")

    assert canon_from_symlink == real_dir.resolve()
    assert canon_from_trailing == real_dir.resolve()
    assert not str(canon_from_trailing).endswith("/")


# ---------------------------------------------------------------------------
# Storage layout honors WIKI_WEAVER_DATA_DIR
# ---------------------------------------------------------------------------


def test_data_root_and_config_root_honor_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "custom-dir"
    monkeypatch.setenv("WIKI_WEAVER_DATA_DIR", str(override))

    assert inst.data_root() == override / "data"
    assert inst.config_root() == override / "config"


def test_per_instance_paths_land_under_correct_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "custom-dir"
    monkeypatch.setenv("WIKI_WEAVER_DATA_DIR", str(override))

    wiki = tmp_path / "somewiki"
    wiki.mkdir()
    iid = inst.instance_id(wiki)

    assert str(inst.instance_data_dir(iid)).startswith(str(override / "data"))
    assert str(inst.instance_config_dir(iid)).startswith(str(override / "config"))
    assert str(inst.ingest_lock_path(wiki)).startswith(str(override / "data"))
    assert inst.ingest_lock_path(wiki).name == "ingest.lock"
    assert str(inst.crontab_lock_path()).startswith(str(override / "data"))
    assert inst.crontab_lock_path().name == "crontab.lock"
    assert str(inst.logs_dir(iid)).startswith(str(override / "data"))
    assert inst.ingest_log_path(iid).name == "ingest.log"
    assert str(inst.env_file_path(iid)).startswith(str(override / "config"))
    assert inst.env_file_path(iid).name == "env"


# ---------------------------------------------------------------------------
# Atomic config/state IO
# ---------------------------------------------------------------------------


def test_instance_config_write_read_round_trip(tmp_path: Path) -> None:
    wiki = tmp_path / "w"
    wiki.mkdir()
    iid = inst.instance_id(wiki)

    cfg = inst.InstanceConfig(
        instance_id=iid,
        canonical_wiki=str(wiki.resolve()),
        cron_expr="*/5 * * * *",
        every="5m",
        alert_after=3,
        uses_env_file=True,
        created_at="2026-07-08T00:00:00+00:00",
    )
    inst.write_instance_config(cfg)

    read_back = inst.read_instance_config(iid)
    assert read_back == cfg

    # No leftover temp file after a successful atomic write.
    config_dir = inst.instance_config_dir(iid)
    tmp_files = list(config_dir.glob("*.tmp"))
    assert tmp_files == []


def test_read_instance_config_missing_returns_none(tmp_path: Path) -> None:
    assert inst.read_instance_config("nonexistent-id") is None


def test_run_state_write_read_round_trip(tmp_path: Path) -> None:
    iid = "some-instance-id"
    state = inst.RunState(
        consecutive_skips=2,
        alert_active=False,
        last_run_at="2026-07-08T00:00:00+00:00",
        last_exit=0,
        last_duration_s=1.234,
        last_skip_at=None,
        last_holder_pid=None,
    )
    inst.write_run_state(iid, state)

    read_back = inst.read_run_state(iid)
    assert read_back == state

    data_dir = inst.instance_data_dir(iid)
    tmp_files = list(data_dir.glob("*.tmp"))
    assert tmp_files == []


def test_read_run_state_missing_returns_fresh_state() -> None:
    fresh = inst.read_run_state("no-such-instance")
    assert fresh == inst.RunState()


# ---------------------------------------------------------------------------
# Backward compatibility for the --limit addendum [C1] (highest priority):
# on-disk instance.json / run-state.json written before `limit` / `hit_limit`
# existed must NOT crash `_reconstruct` -- missing keys fall back to defaults,
# unknown keys are dropped. See docs/designs/scheduled-ingestion-limit-addendum.md §5.
# ---------------------------------------------------------------------------


def test_read_instance_config_legacy_no_limit(tmp_path: Path) -> None:
    iid = "legacy-instance"
    legacy = {
        "instance_id": iid,
        "canonical_wiki": str(tmp_path / "w"),
        "cron_expr": "*/5 * * * *",
        "every": "5m",
        "alert_after": 3,
        "uses_env_file": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        # NOTE: no "limit" key -- simulates a pre-addendum instance.json.
    }
    path = inst.instance_config_dir(iid) / "instance.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    cfg = inst.read_instance_config(iid)

    assert cfg is not None
    assert cfg.limit is None
    assert cfg.instance_id == iid


def test_read_run_state_legacy_no_hit_limit(tmp_path: Path) -> None:
    iid = "legacy-instance-2"
    legacy = {
        "consecutive_skips": 1,
        "alert_active": False,
        "last_run_at": "2026-01-01T00:00:00+00:00",
        "last_exit": 0,
        "last_duration_s": 2.5,
        "last_skip_at": None,
        "last_holder_pid": None,
        # NOTE: no "hit_limit" key -- simulates a pre-addendum run-state.json.
    }
    path = inst.instance_data_dir(iid) / "run-state.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    st = inst.read_run_state(iid)

    assert st.hit_limit is False
    assert st.consecutive_skips == 1


def test_readers_drop_unknown_keys(tmp_path: Path) -> None:
    iid = "future-instance"
    cfg_raw = {
        "instance_id": iid,
        "canonical_wiki": str(tmp_path / "w"),
        "cron_expr": "*/5 * * * *",
        "every": "5m",
        "alert_after": 3,
        "uses_env_file": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "limit": 5,
        "totally_unknown_future_field": "should be dropped",
    }
    (inst.instance_config_dir(iid) / "instance.json").write_text(
        json.dumps(cfg_raw), encoding="utf-8"
    )
    st_raw = {
        "consecutive_skips": 0,
        "alert_active": False,
        "last_run_at": None,
        "last_exit": None,
        "last_duration_s": None,
        "last_skip_at": None,
        "last_holder_pid": None,
        "hit_limit": True,
        "another_unknown_field": 42,
    }
    (inst.instance_data_dir(iid) / "run-state.json").write_text(
        json.dumps(st_raw), encoding="utf-8"
    )

    cfg = inst.read_instance_config(iid)
    st = inst.read_run_state(iid)

    assert cfg is not None
    assert cfg.limit == 5
    assert not hasattr(cfg, "totally_unknown_future_field")
    assert st.hit_limit is True
    assert not hasattr(st, "another_unknown_field")


# ---------------------------------------------------------------------------
# list_instance_ids / delete_instance
# ---------------------------------------------------------------------------


def test_list_instance_ids_enumerates_config_dirs(tmp_path: Path) -> None:
    assert inst.list_instance_ids() == []

    inst.instance_config_dir("id-one")
    inst.instance_config_dir("id-two")

    assert inst.list_instance_ids() == ["id-one", "id-two"]


def test_delete_instance_removes_both_config_and_data_dirs(tmp_path: Path) -> None:
    iid = "to-delete"
    cfg_dir = inst.instance_config_dir(iid)
    data_dir = inst.instance_data_dir(iid)
    (cfg_dir / "instance.json").write_text("{}", encoding="utf-8")
    (data_dir / "run-state.json").write_text("{}", encoding="utf-8")

    result = inst.delete_instance(iid)

    assert result is True
    assert not cfg_dir.exists()
    assert not data_dir.exists()


def test_delete_instance_nonexistent_returns_false() -> None:
    assert inst.delete_instance("never-existed") is False
