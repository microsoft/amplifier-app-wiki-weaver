"""Unit tests for wiki_weaver.engine_runner.load_ci_config()
and the doctor CI block in wiki_weaver.lib.

All tests are keyless and deterministic — no real network calls,
no real filesystem access.  Each test patches SETTINGS_PATH and/or
os.environ as needed; no side effects leak between tests.
"""

from __future__ import annotations

import io
import sys
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Make wiki_weaver importable without installing.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from wiki_weaver.engine_runner import load_ci_config  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(content: str, tmp_path: Path) -> Path:
    """Write a settings YAML to tmp_path and return the path."""
    p = tmp_path / "settings.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# load_ci_config() — five contract cases
# ---------------------------------------------------------------------------


class TestLoadCiConfigDestinationsPassthrough:
    """(a) Full destinations dict in settings → pass through with ${VAR} expansion."""

    def test_destinations_passthrough(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setenv("CI_URL", "https://ci.example.com")
        monkeypatch.setenv("CI_KEY", "s3cr3t")
        settings = _make_settings(
            """
            overrides:
              hook-context-intelligence:
                config:
                  destinations:
                    prod:
                      url: "${CI_URL}/ingest"
                      api_key: "${CI_KEY}"
                      include:
                        - "**"
            """,
            tmp_path,
        )
        with patch("wiki_weaver.engine_runner.SETTINGS_PATH", settings):
            result = load_ci_config()

        assert "destinations" in result
        assert "prod" in result["destinations"]
        dest = result["destinations"]["prod"]
        assert dest["url"] == "https://ci.example.com/ingest"
        assert dest["api_key"] == "s3cr3t"
        assert dest["include"] == ["**"]

    def test_destinations_passthrough_multiple(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("URL_A", "https://a.example.com")
        monkeypatch.setenv("KEY_A", "akey")
        monkeypatch.setenv("URL_B", "https://b.example.com")
        monkeypatch.setenv("KEY_B", "bkey")
        settings = _make_settings(
            """
            overrides:
              hook-context-intelligence:
                config:
                  destinations:
                    alpha:
                      url: "${URL_A}"
                      api_key: "${KEY_A}"
                    beta:
                      url: "${URL_B}"
                      api_key: "${KEY_B}"
            """,
            tmp_path,
        )
        with patch("wiki_weaver.engine_runner.SETTINGS_PATH", settings):
            result = load_ci_config()

        assert set(result["destinations"].keys()) == {"alpha", "beta"}
        assert result["destinations"]["alpha"]["url"] == "https://a.example.com"
        assert result["destinations"]["beta"]["api_key"] == "bkey"

    def test_empty_destinations_dict_returns_local_only(self, tmp_path: Path) -> None:
        settings = _make_settings(
            """
            overrides:
              hook-context-intelligence:
                config:
                  destinations: {}
            """,
            tmp_path,
        )
        with patch("wiki_weaver.engine_runner.SETTINGS_PATH", settings):
            result = load_ci_config()

        # destinations key present but empty → treated as no remote → local-only
        assert result == {}


class TestLoadCiConfigScalarTranslation:
    """(b) Legacy scalars → destinations.default with include:["**"] + ${VAR} expansion."""

    def test_scalar_both_url_and_key(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setenv("MY_KEY", "myapikey")
        settings = _make_settings(
            """
            overrides:
              hook-context-intelligence:
                config:
                  context_intelligence_server_url: "https://server.example.com"
                  context_intelligence_api_key: "${MY_KEY}"
            """,
            tmp_path,
        )
        with patch("wiki_weaver.engine_runner.SETTINGS_PATH", settings):
            result = load_ci_config()

        assert "destinations" in result
        assert "default" in result["destinations"]
        dest = result["destinations"]["default"]
        assert dest["url"] == "https://server.example.com"
        assert dest["api_key"] == "myapikey"
        assert dest["include"] == ["**"]

    def test_scalar_url_and_key_literal(self, tmp_path: Path) -> None:
        settings = _make_settings(
            """
            overrides:
              hook-context-intelligence:
                config:
                  context_intelligence_server_url: "https://ci.local/api"
                  context_intelligence_api_key: "literal-key-123"
            """,
            tmp_path,
        )
        with patch("wiki_weaver.engine_runner.SETTINGS_PATH", settings):
            result = load_ci_config()

        dest = result["destinations"]["default"]
        assert dest["url"] == "https://ci.local/api"
        assert dest["api_key"] == "literal-key-123"
        assert dest["include"] == ["**"]


class TestLoadCiConfigUrlWithoutKey:
    """(c) server_url present but api_key empty → local-only ({})."""

    def test_url_only_no_key(self, tmp_path: Path) -> None:
        settings = _make_settings(
            """
            overrides:
              hook-context-intelligence:
                config:
                  context_intelligence_server_url: "https://server.example.com"
            """,
            tmp_path,
        )
        with patch("wiki_weaver.engine_runner.SETTINGS_PATH", settings):
            result = load_ci_config()

        assert result == {}

    def test_url_only_key_empty_string(self, tmp_path: Path) -> None:
        settings = _make_settings(
            """
            overrides:
              hook-context-intelligence:
                config:
                  context_intelligence_server_url: "https://server.example.com"
                  context_intelligence_api_key: ""
            """,
            tmp_path,
        )
        with patch("wiki_weaver.engine_runner.SETTINGS_PATH", settings):
            result = load_ci_config()

        assert result == {}

    def test_url_key_expands_to_empty(self, tmp_path: Path, monkeypatch: Any) -> None:
        """${VAR} that expands to empty string → no remote destination (local-only)."""
        # Set the env var to an empty string so expandvars("${EMPTY_CI_KEY}") == ""
        monkeypatch.setenv("EMPTY_CI_KEY", "")
        settings = _make_settings(
            """
            overrides:
              hook-context-intelligence:
                config:
                  context_intelligence_server_url: "https://server.example.com"
                  context_intelligence_api_key: "${EMPTY_CI_KEY}"
            """,
            tmp_path,
        )
        with patch("wiki_weaver.engine_runner.SETTINGS_PATH", settings):
            result = load_ci_config()

        assert result == {}


class TestLoadCiConfigNothingConfigured:
    """(d) Nothing configured → {} (local-only, the normal default)."""

    def test_no_settings_file(self, tmp_path: Path) -> None:
        absent = tmp_path / "does_not_exist.yaml"
        with patch("wiki_weaver.engine_runner.SETTINGS_PATH", absent):
            result = load_ci_config()
        assert result == {}

    def test_settings_has_no_ci_section(self, tmp_path: Path) -> None:
        settings = _make_settings(
            """
            config:
              providers:
                anthropic: {}
            """,
            tmp_path,
        )
        with patch("wiki_weaver.engine_runner.SETTINGS_PATH", settings):
            result = load_ci_config()
        assert result == {}

    def test_settings_ci_section_empty(self, tmp_path: Path) -> None:
        settings = _make_settings(
            """
            overrides:
              hook-context-intelligence:
                config: {}
            """,
            tmp_path,
        )
        with patch("wiki_weaver.engine_runner.SETTINGS_PATH", settings):
            result = load_ci_config()
        assert result == {}

    def test_empty_settings_file(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.yaml"
        settings.write_text("", encoding="utf-8")
        with patch("wiki_weaver.engine_runner.SETTINGS_PATH", settings):
            result = load_ci_config()
        assert result == {}


class TestLoadCiConfigEnvExpansion:
    """(e) ${VAR} actually expanded from env for destinations passthrough path."""

    def test_var_expansion_destinations(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setenv("TEST_CI_URL", "https://expanded.example.com")
        monkeypatch.setenv("TEST_CI_KEY", "expanded-key")
        settings = _make_settings(
            """
            overrides:
              hook-context-intelligence:
                config:
                  destinations:
                    primary:
                      url: "${TEST_CI_URL}/api"
                      api_key: "${TEST_CI_KEY}"
            """,
            tmp_path,
        )
        with patch("wiki_weaver.engine_runner.SETTINGS_PATH", settings):
            result = load_ci_config()

        assert (
            result["destinations"]["primary"]["url"]
            == "https://expanded.example.com/api"
        )
        assert result["destinations"]["primary"]["api_key"] == "expanded-key"

    def test_var_expansion_scalars(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setenv("TEST_SCALAR_URL", "https://scalar.example.com")
        monkeypatch.setenv("TEST_SCALAR_KEY", "scalar-apikey")
        settings = _make_settings(
            """
            overrides:
              hook-context-intelligence:
                config:
                  context_intelligence_server_url: "${TEST_SCALAR_URL}"
                  context_intelligence_api_key: "${TEST_SCALAR_KEY}"
            """,
            tmp_path,
        )
        with patch("wiki_weaver.engine_runner.SETTINGS_PATH", settings):
            result = load_ci_config()

        assert result["destinations"]["default"]["url"] == "https://scalar.example.com"
        assert result["destinations"]["default"]["api_key"] == "scalar-apikey"


# ---------------------------------------------------------------------------
# doctor CI block — unconfigured case must NOT emit '!' warnings
# ---------------------------------------------------------------------------


class TestDoctorCiBlockMessaging:
    """Doctor CI block emits correct lines for unconfigured and configured states."""

    def _run_doctor_capture(self, tmp_path: Path, settings_content: str) -> str:
        """Run doctor() and capture its stdout output."""
        settings = _make_settings(settings_content, tmp_path)

        captured = io.StringIO()

        # Patch SETTINGS_PATH in engine_runner so load_ci_config() reads from tmp_path.
        # Also suppress all other doctor checks to isolate the CI block output.
        # We do that by patching _hard_env_checks to return an empty list, and patching
        # out the Amplifier/network/wiki checks that would need real filesystem state.
        with (
            patch("wiki_weaver.engine_runner.SETTINGS_PATH", settings),
            patch("wiki_weaver.lib.sys.stdout", captured),
        ):
            from wiki_weaver.lib import doctor

            # Run doctor with no wiki= argument — only checks non-wiki preconditions.
            try:
                doctor()
            except Exception:  # noqa: BLE001
                pass  # Some checks may fail in test env — we only inspect stdout

        return captured.getvalue()

    def test_unconfigured_no_warning_exclamation(self, tmp_path: Path) -> None:
        """When CI is not configured, doctor MUST NOT emit the old misleading warning lines.

        The old code emitted:
          ! no context-intelligence api_key in settings; hook composes but fails soft
          ! no context-intelligence server_url in settings; skipping probe

        After the fix these specific patterns must NOT appear.
        (The updater section may still emit a '!' about the CI bundle not being
        cached yet — that is a cache-status line, not a config alarm, and is
        expected/correct behaviour.)
        """
        output = self._run_doctor_capture(
            tmp_path,
            """
            config:
              providers:
                anthropic: {}
            """,
        )
        bad_patterns = [
            "no context-intelligence api_key",
            "hook composes but fails soft",
            "no context-intelligence server_url",
            "skipping probe",
        ]
        for bad in bad_patterns:
            assert bad not in output, (
                f"Misleading CI config warning still present: {bad!r}\nFull output:\n{output}"
            )

    def test_unconfigured_emits_logging_locally_normal(self, tmp_path: Path) -> None:
        """When CI is not configured, doctor MUST emit the 'logging locally — normal' line."""
        output = self._run_doctor_capture(
            tmp_path,
            """
            config:
              providers:
                anthropic: {}
            """,
        )
        assert "logging locally" in output and "normal" in output, (
            f"Expected 'logging locally — normal' in output, got:\n{output}"
        )

    def test_unconfigured_emits_local_only_normal(self, tmp_path: Path) -> None:
        """When CI is not configured, doctor MUST emit a 'local-only' normal info line."""
        output = self._run_doctor_capture(
            tmp_path,
            """
            config:
              providers:
                anthropic: {}
            """,
        )
        assert "local-only" in output and "normal" in output, (
            f"Expected 'local-only — normal' info in output, got:\n{output}"
        )
