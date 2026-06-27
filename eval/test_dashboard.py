"""Tests for wiki_weaver/dashboard.py — Increment 2.

Coverage (spec §4, §5, §7, §10):

CSS injection (spec §4):
  Five mandatory injection inputs must produce zero executable
  ``</style>``-style breakouts inside the ``data-wiki-enrichment`` slot,
  and zero ``@import`` statements in the output HTML.

Fixture render (spec §7):
  build_indexes() + build_dashboard() on wiki-min/ must emit:
  - A self-contained HTML file (no ``src="http`` external resources).
  - All page titles present (Alpha, Beta, Gamma, Delta).
  - Zero raw ``[[`` wikilink markers in the output.
  - Type-grouped sidebar nav present (``nav-group`` CSS class used in
    the template — the data driving it is in the embedded PAGES JSON).
  - Backlinks data wired (BACKLINKS JSON non-empty).
  - Correct stat numbers (6 pages, 2 tags, correct link count).

Theming (spec §5, §10):
  - Malformed theme.json → falls back per-token, build succeeds, warning emitted.
  - Low-contrast token pair → loud warning emitted, build still succeeds.

No LLM, no engine, no Amplifier runtime required.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import warnings
from pathlib import Path

import pytest

# ── Repo-root path plumbing (mirrors existing eval/*.py convention) ───────────

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from wiki_weaver.dashboard import (  # noqa: E402
    _contrast_ratio,
    _hex_to_luminance,
    _sanitize_enrichment_css,
    build_dashboard,
)
from wiki_weaver.index import build_indexes  # noqa: E402

# ── Fixture helpers ───────────────────────────────────────────────────────────

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "wiki-min"
_EXPECTED = json.loads((_FIXTURE_DIR / "expected.json").read_text(encoding="utf-8"))


@pytest.fixture()
def wiki(tmp_path: Path) -> Path:
    """Copy wiki-min fixture to a temp dir, build indexes, and return the path."""
    dest = tmp_path / "wiki-min"
    shutil.copytree(_FIXTURE_DIR, dest)
    build_indexes(dest)
    return dest


@pytest.fixture()
def dashboard_html(wiki: Path, tmp_path: Path) -> str:
    """Build a dashboard over the wiki-min fixture and return the HTML string."""
    out = tmp_path / "dashboard.html"
    build_dashboard(wiki, out)
    return out.read_text(encoding="utf-8")


# ── §4 CSS injection tests ────────────────────────────────────────────────────
#
# These tests must be NON-VACUOUS: each must prove the tinycss2 sanitizer
# actually PROCESSED the fragment, not that it was silently dropped.  A dropped
# (None) enrichment slot must FAIL these tests, never pass them.
#
# How non-vacuousness is guaranteed:
#   1. dashboard.py imports tinycss2 at top level (fail-loud) — there is no
#      "tinycss2 not installed → drop slot" branch any more.  test_sanitizer_*
#      below asserts the live module reference is the real tinycss2.
#   2. Each tag-break attack is embedded INSIDE a valid CSS string value next
#      to a BENIGN sibling rule.  The sanitizer's contract for such valid CSS
#      is RE-SERIALIZE (not reject), so the benign rule MUST survive in the
#      output.  If the sanitizer were a no-op that dropped everything, the
#      benign marker would be absent and the test would fail.

# A unique benign rule whose survival proves the sanitizer ran (not dropped).
_BENIGN_MARKER = "benign-survivor-7f3a"
_BENIGN_RULE = f".{_BENIGN_MARKER}{{color:#abc}}"

# Pattern that detects a raw `</style` (any case) NOT already CSS-escaped.
# After sanitisation every `<` becomes the escape `\3c `, so a surviving raw
# `</style` means the injection broke out.
_RAW_CLOSE_STYLE_RE = re.compile(r"(?<!\\)<\s*/\s*style", re.IGNORECASE)

# Tag-break attack payloads (spec §4) — each is placed inside a CSS string so
# the fragment as a whole is VALID CSS that the sanitizer re-serializes; the
# dangerous `<` chars must come out CSS-escaped.
_TAG_BREAK_ATTACKS = [
    "</style><script>alert(1)</script>",  # standard tag-close
    "</STYLE>alert(1)</STYLE>",  # upper-case
    "</style ><script>alert(2)</script>",  # space before >
    r"\3c/style\3e<script>alert(3)</script>",  # CSS-escaped form
]


def _fragment_with_attack(attack: str) -> str:
    """Embed *attack* in a valid CSS string value next to a benign sibling rule."""
    # Strip any double-quote so the attack stays inside the quoted value.
    safe_attack = attack.replace('"', "")
    return f'{_BENIGN_RULE} a::after{{content:"{safe_attack}"}}'


def test_sanitizer_uses_real_tinycss2() -> None:
    """The sanitizer is wired to the real tinycss2 module (no silent-degrade).

    This is the root guard against the original vacuous-pass bug: if tinycss2
    were absent (or a silent None-guard reintroduced), the sanitizer could not
    run and every injection test would pass trivially.  Fail loud here instead.
    """
    import tinycss2  # noqa: PLC0415

    from wiki_weaver import dashboard as _dash  # noqa: PLC0415

    assert _dash._tinycss2 is tinycss2, (
        "dashboard._tinycss2 must be the real tinycss2 module — "
        "a silent-degrade guard would defeat the CSS sanitizer"
    )


@pytest.mark.parametrize("attack", _TAG_BREAK_ATTACKS)
def test_css_injection_neutralised_in_dashboard(
    attack: str, wiki: Path, tmp_path: Path
) -> None:
    """Full build path: attack neutralised AND benign sibling rule survives.

    Non-vacuous: the benign rule only appears if the sanitizer actually
    processed (re-serialized) the fragment.  A dropped/None slot fails this.
    """
    fragment = _fragment_with_attack(attack)
    out = tmp_path / f"dash_{abs(hash(attack))}.html"
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        build_dashboard(wiki, out, enrichment_css=fragment)

    html = out.read_text(encoding="utf-8")

    # The enrichment slot MUST be present (proves the fragment was accepted,
    # not dropped).
    slot_match = re.search(
        r"<style\s+data-wiki-enrichment>(.*?)</style>",
        html,
        re.DOTALL | re.IGNORECASE,
    )
    assert slot_match is not None, (
        f"No data-wiki-enrichment slot — fragment was dropped (vacuous!).\n"
        f"Attack: {attack!r}"
    )
    slot = slot_match.group(1)

    # PROOF the sanitizer ran: the benign sibling rule survives in the slot.
    assert _BENIGN_MARKER in slot, (
        f"Benign marker {_BENIGN_MARKER!r} not in enrichment slot — the "
        f"sanitizer did not process the fragment (vacuous neutralisation).\n"
        f"Slot: {slot[:200]!r}"
    )

    # NEUTRALISATION: no raw </style> breakout inside the slot.
    assert not _RAW_CLOSE_STYLE_RE.search(slot), (
        f"Injection not neutralised — raw </style> in enrichment slot.\n"
        f"Attack: {attack!r}\nSlot: {slot[:200]!r}"
    )

    # No @import smuggled in.
    assert "@import" not in slot, f"@import survived for attack: {attack!r}"

    # Whole-document <style>/</style> balance preserved (no extra closer).
    open_count = len(re.findall(r"<style[\s>]", html, re.IGNORECASE))
    close_count = len(re.findall(r"</style>", html, re.IGNORECASE))
    assert open_count == close_count, (
        f"Mismatched <style>/</style> count after injection attempt.\n"
        f"Attack: {attack!r}\nopen={open_count} close={close_count}"
    )


@pytest.mark.parametrize("attack", _TAG_BREAK_ATTACKS)
def test_css_injection_neutralised_unit(attack: str) -> None:
    """Unit-level: the sanitizer re-serializes the fragment and escapes `<`.

    Asserts the benign rule survives (processed, not dropped) AND the output
    contains no literal `<` at all (every `<` CSS-escaped to `\\3c`).
    """
    fragment = _fragment_with_attack(attack)
    result = _sanitize_enrichment_css(fragment)
    assert result is not None, (
        f"Sanitizer dropped a VALID fragment (vacuous): {attack!r}"
    )
    assert _BENIGN_MARKER in result, (
        f"Benign marker missing — fragment not processed: {attack!r}"
    )
    assert "<" not in result, (
        f"Literal `<` survived sanitisation (should be CSS-escaped): {attack!r}\n"
        f"Result: {result[:200]!r}"
    )


def test_css_injection_raw_tag_break_rejected_wholesale() -> None:
    """A RAW (not string-embedded) tag-break is a parse error → reject + warn.

    Complements the re-serialization tests above: when the attack is NOT valid
    CSS, the contract is to reject the ENTIRE fragment (fail-safe), warn, and
    omit the slot — never partial output.
    """
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = _sanitize_enrichment_css(
            f"{_BENIGN_RULE} </style><script>bad()</script>"
        )
    assert result is None, "Raw tag-break must reject the whole fragment"
    assert any(w), "Expected a warning on wholesale rejection"


def test_css_injection_import_rejected_warns() -> None:
    """@import causes _sanitize_enrichment_css to return None + emit a warning.

    Non-vacuous: paired with a valid benign rule — the whole fragment must
    STILL be rejected (the contract), proving @import triggers wholesale
    rejection rather than the rule being silently kept.
    """
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = _sanitize_enrichment_css(
            f"{_BENIGN_RULE} @import url(https://evil/x.css);"
        )

    assert result is None, "Expected None for @import input"
    assert any("import" in str(ww.message).lower() for ww in w), (
        "Expected a warning mentioning @import"
    )


def test_css_injection_valid_css_passes() -> None:
    """Valid CSS passes through sanitisation and is re-serialized.

    This is the positive control that makes the reject-path tests non-vacuous:
    it proves the sanitizer CAN accept CSS, so a None result elsewhere is a
    real rejection, not a sanitizer that always returns None.
    """
    result = _sanitize_enrichment_css(
        ".wiki-custom { color: var(--wiki-accent); background: #fff; }"
    )
    assert result is not None, "Expected valid CSS to be accepted"
    assert "<" not in result, (
        "Serialised enrichment CSS must not contain literal < characters "
        "(they should be CSS-escaped as \\3c)"
    )
    assert "wiki-custom" in result, "Expected the selector to survive sanitisation"
    assert "color" in result, "Expected CSS properties to survive sanitisation"


def test_css_injection_offorigin_url_rejected() -> None:
    """Off-origin url() is rejected (even alongside a valid benign rule)."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = _sanitize_enrichment_css(
            f"{_BENIGN_RULE} body {{ background: url(https://evil.example/img.png); }}"
        )
    assert result is None
    assert any("url" in str(ww.message).lower() for ww in w), (
        "Expected a warning mentioning url()"
    )


def test_css_injection_data_url_allowed() -> None:
    """data: URLs are allowed in url() and the benign rule survives."""
    # Concatenation (not f-string) so the CSS braces stay literal single braces.
    css = (
        _BENIGN_RULE
        + " body { background: url(data:image/gif;base64,"
        + "R0lGODlhAQABAIAAAAUEBAAAACwAAAAAAQABAAACAkQBADs=); }"
    )
    result = _sanitize_enrichment_css(css)
    assert result is not None, "data: URLs must be accepted"
    assert _BENIGN_MARKER in result, "Benign rule must survive a data: URL fragment"


# ── Fixture render tests ──────────────────────────────────────────────────────


def test_dashboard_is_self_contained(dashboard_html: str) -> None:
    """No external src= or href= URLs (no CDN resources)."""
    ext_src = re.findall(r'\bsrc=["\']https?://', dashboard_html, re.IGNORECASE)
    ext_link = re.findall(r'\bhref=["\']https?://(?!#)', dashboard_html, re.IGNORECASE)
    assert not ext_src, f"External src= found: {ext_src}"
    assert not ext_link, f"External href= found: {ext_link}"


def test_dashboard_contains_real_page_titles(dashboard_html: str) -> None:
    """All fixture page titles appear in the HTML output."""
    for expected_title in ("Alpha", "Beta", "Gamma", "Delta"):
        assert expected_title in dashboard_html, (
            f"Title {expected_title!r} not found in dashboard HTML"
        )


def test_dashboard_no_raw_wikilinks(dashboard_html: str) -> None:
    """No raw [[...]] wikilink markers survive into the output."""
    # The JS data contains escaped JSON; check the rendered page HTML (h field)
    # and also scan the full HTML for raw [[ that are NOT inside JS string keys.
    raw_wl = re.findall(r"\[\[", dashboard_html)
    assert not raw_wl, (
        f"Found {len(raw_wl)} raw [[ wikilink markers in dashboard output"
    )


def test_dashboard_has_group_nav(dashboard_html: str) -> None:
    """Type-grouped sidebar nav is present (nav-group CSS class and data)."""
    # The template uses the nav-group class for collapsible sidebar sections
    assert "nav-group" in dashboard_html, (
        "Expected nav-group CSS class in dashboard HTML"
    )
    # The GROUP_BY value is embedded as a JS constant
    assert '"type"' in dashboard_html or "GROUP_BY" in dashboard_html, (
        "Expected GROUP_BY constant in dashboard HTML"
    )


def test_dashboard_backlinks_wired(dashboard_html: str) -> None:
    """BACKLINKS JSON is embedded and non-empty for the fixture corpus."""
    # Extract BACKLINKS from the JS
    m = re.search(r"const BACKLINKS=(\{[^;]+\});", dashboard_html)
    assert m is not None, "Expected BACKLINKS= const in dashboard JS"
    try:
        bl = json.loads(m.group(1).replace(r"\/", "/"))
    except json.JSONDecodeError:
        # Escaped JSON — try unescaping
        raw = m.group(1)
        bl = json.loads(raw)
    # From expected.json: alpha has backlink from beta, beta from alpha, gamma from alpha
    for page, expected_bls in _EXPECTED["backlinks"].items():
        if expected_bls:  # only check pages that have backlinks
            assert page in bl, f"Page {page!r} missing from BACKLINKS"
            assert sorted(bl[page]) == sorted(expected_bls), (
                f"BACKLINKS[{page!r}]: got {bl[page]!r}, expected {expected_bls!r}"
            )


def test_dashboard_stat_pages_correct(dashboard_html: str) -> None:
    """STATS.pages equals the number of .md files in the fixture corpus."""
    expected_count = len(list(_FIXTURE_DIR.glob("*.md")))
    m = re.search(r'"pages"\s*:\s*(\d+)', dashboard_html)
    assert m is not None, "Expected pages stat in dashboard HTML"
    assert int(m.group(1)) == expected_count, (
        f"STATS.pages: got {m.group(1)}, expected {expected_count}"
    )


def test_dashboard_stat_tags_correct(dashboard_html: str) -> None:
    """STATS.tags equals the number of unique tags in the fixture corpus."""
    expected_tag_count = len(_EXPECTED["tags"])  # 2 tags: x, y
    m = re.search(r'"tags"\s*:\s*(\d+)', dashboard_html)
    assert m is not None, "Expected tags stat in dashboard HTML"
    assert int(m.group(1)) == expected_tag_count, (
        f"STATS.tags: got {m.group(1)}, expected {expected_tag_count}"
    )


def test_dashboard_non_repo_default_view_elements(dashboard_html: str) -> None:
    """Generated home view elements are present (stat cards, tag cloud, type bar)."""
    # These JS function names are in the template and implement the non-repo view
    for fn in (
        "renderStatCards",
        "renderRecentlyUpdated",
        "renderTagCloud",
        "renderTypeBar",
    ):
        assert fn in dashboard_html, (
            f"Expected JS function {fn!r} in dashboard HTML (non-repo default view)"
        )


def test_dashboard_almanac_tokens_present(dashboard_html: str) -> None:
    """Almanac theme tokens are present in the CSS."""
    for token in (
        "--wiki-bg:#FBF9F4",
        "--wiki-accent:#136F63",
        "--wiki-text:#23211C",
    ):
        # The CSS may use the exact form or have semicolons / whitespace
        assert token.replace(":", ":") in dashboard_html.replace(" ", ""), (
            f"Almanac token {token!r} not found in dashboard HTML"
        )


def test_dashboard_dark_mode_present(dashboard_html: str) -> None:
    """Almanac Night dark variant is present (prefers-color-scheme:dark)."""
    assert "prefers-color-scheme" in dashboard_html, (
        "Expected @media (prefers-color-scheme:dark) in dashboard HTML"
    )
    # Spot-check a dark color
    assert "1A1814" in dashboard_html, (
        "Expected Almanac Night dark background #1A1814 in dashboard HTML"
    )


def test_dashboard_badge_palette_present(dashboard_html: str) -> None:
    """Type-badge palette (hex colors) is embedded in the dashboard."""
    for hex_color in ("#3A5BA0", "#2F6F4F", "#1F6A6A", "#7A4B9C", "#9A5B2E"):
        assert hex_color.lower() in dashboard_html.lower(), (
            f"Badge palette color {hex_color!r} not found in dashboard HTML"
        )


def test_dashboard_enrichment_css_inlined(wiki: Path, tmp_path: Path) -> None:
    """Valid enrichment_css is sanitized and wrapped in data-wiki-enrichment."""
    out = tmp_path / "enriched.html"
    build_dashboard(
        wiki,
        out,
        enrichment_css=".wiki-extra { color: var(--wiki-accent); }",
    )
    html = out.read_text(encoding="utf-8")
    assert "data-wiki-enrichment" in html, (
        "Expected <style data-wiki-enrichment> in output"
    )
    assert "wiki-extra" in html, "Expected enrichment CSS content in output"


def test_dashboard_enrichment_data_emitted(wiki: Path, tmp_path: Path) -> None:
    """enrichment_data dict is emitted as CSS custom properties."""
    out = tmp_path / "enriched_data.html"
    build_dashboard(
        wiki,
        out,
        enrichment_data={"--my-custom-var": "red", "my-other": "blue"},
    )
    html = out.read_text(encoding="utf-8")
    assert "--my-custom-var" in html, "Expected --my-custom-var in CSS vars"
    assert "--my-other" in html, "Expected --my-other in CSS vars (auto-prefixed)"


# ── Theming tests (spec §5, §10) ─────────────────────────────────────────────


def test_theme_json_malformed_warns_and_builds(wiki: Path, tmp_path: Path) -> None:
    """A malformed theme.json emits a warning but the build still succeeds."""
    # Create a bad theme.json
    theme_dir = wiki / ".wiki-dashboard"
    theme_dir.mkdir(exist_ok=True)
    (theme_dir / "theme.json").write_text("NOT VALID JSON{{{", encoding="utf-8")

    out = tmp_path / "dash_bad_theme.html"
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        build_dashboard(wiki, out)

    # Build must succeed (file written)
    assert out.exists(), "Dashboard must still be written despite bad theme.json"
    html = out.read_text(encoding="utf-8")
    assert "<html" in html, "Dashboard must produce valid HTML"

    # Warning must have been emitted
    msgs = [str(ww.message) for ww in w]
    assert any("theme" in m.lower() or "parse" in m.lower() for m in msgs), (
        f"Expected a warning about theme.json parse failure. Got: {msgs}"
    )


def test_theme_json_unknown_key_warns(wiki: Path, tmp_path: Path) -> None:
    """Unknown keys in theme.json produce a warning."""
    theme_dir = wiki / ".wiki-dashboard"
    theme_dir.mkdir(exist_ok=True)
    (theme_dir / "theme.json").write_text(
        json.dumps({"--wiki-bg": "#EEEEEE", "--unknown-key": "red"}),
        encoding="utf-8",
    )

    out = tmp_path / "dash_unknown_key.html"
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        build_dashboard(wiki, out)

    msgs = [str(ww.message) for ww in w]
    assert any("unknown" in m.lower() for m in msgs), (
        f"Expected a warning about unknown key. Got: {msgs}"
    )
    # Build must succeed
    assert out.exists()


def test_theme_json_low_contrast_warns(wiki: Path, tmp_path: Path) -> None:
    """A low-contrast text/background token pair in theme.json emits a loud warning."""
    theme_dir = wiki / ".wiki-dashboard"
    theme_dir.mkdir(exist_ok=True)
    # Black text on black background — contrast = 1.0 (WCAG AA requires 4.5)
    (theme_dir / "theme.json").write_text(
        json.dumps({"--wiki-text": "#000000", "--wiki-bg": "#111111"}),
        encoding="utf-8",
    )

    out = tmp_path / "dash_low_contrast.html"
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        build_dashboard(wiki, out)

    msgs = [str(ww.message) for ww in w]
    # Must warn about contrast
    assert any("contrast" in m.lower() for m in msgs), (
        f"Expected a contrast warning. Got: {msgs}"
    )
    # Build still succeeds
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "<html" in html


def test_theme_json_falsy_values_accepted(wiki: Path, tmp_path: Path) -> None:
    """Falsy non-None values (0, empty string, false) are accepted as valid overrides.

    Spec §10: the token cascade must use explicit None-checks, not truthiness.
    """
    theme_dir = wiki / ".wiki-dashboard"
    theme_dir.mkdir(exist_ok=True)
    # 0 and "" are falsy but not None — they should survive as overrides
    # (In practice CSS custom properties with these values are degenerate,
    # but the spec requires we not silently drop them.)
    (theme_dir / "theme.json").write_text(
        json.dumps({"--wiki-radius": "0px", "--wiki-transition": "0ms"}),
        encoding="utf-8",
    )

    out = tmp_path / "dash_falsy.html"
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        build_dashboard(wiki, out)

    # No warnings about the falsy values themselves
    msgs = [str(ww.message) for ww in w]
    value_drop_warnings = [m for m in msgs if "0px" in m or "0ms" in m]
    assert not value_drop_warnings, (
        f"Falsy non-None values should not be warned about: {value_drop_warnings}"
    )

    html = out.read_text(encoding="utf-8")
    assert "0px" in html, "Expected --wiki-radius:0px to appear in CSS"
    assert "0ms" in html, "Expected --wiki-transition:0ms to appear in CSS"


def test_theme_json_none_value_skipped(wiki: Path, tmp_path: Path) -> None:
    """Explicit null (None) values in theme.json are skipped; default is used."""
    theme_dir = wiki / ".wiki-dashboard"
    theme_dir.mkdir(exist_ok=True)
    (theme_dir / "theme.json").write_text(
        json.dumps({"--wiki-bg": None}),  # null in JSON
        encoding="utf-8",
    )

    out = tmp_path / "dash_none.html"
    build_dashboard(wiki, out)
    html = out.read_text(encoding="utf-8")

    # Default --wiki-bg (#FBF9F4) must still appear
    assert "FBF9F4" in html, (
        "Expected default --wiki-bg (#FBF9F4) when theme.json supplies null"
    )


# ── Contrast utility unit tests ───────────────────────────────────────────────


def test_contrast_ratio_white_black() -> None:
    """White on black should be 21:1 (WCAG maximum)."""
    ratio = _contrast_ratio("#FFFFFF", "#000000")
    assert ratio is not None
    assert abs(ratio - 21.0) < 0.1, f"Expected ~21.0, got {ratio}"


def test_contrast_ratio_returns_none_for_invalid_hex() -> None:
    """Non-hex color strings return None."""
    assert _hex_to_luminance("not-a-color") is None
    assert _contrast_ratio("red", "#000000") is None


def test_contrast_almanac_defaults_pass_wcag() -> None:
    """Default Almanac token pairs all exceed their minimum contrast ratios."""
    from wiki_weaver.dashboard import ALMANAC_LIGHT, _CONTRAST_PAIRS

    for text_tok, bg_tok, min_ratio in _CONTRAST_PAIRS:
        t = ALMANAC_LIGHT[text_tok]
        b = ALMANAC_LIGHT[bg_tok]
        ratio = _contrast_ratio(t, b)
        assert ratio is not None, f"Could not compute contrast for {text_tok}/{bg_tok}"
        assert ratio >= min_ratio, (
            f"Almanac default {text_tok}:{t} on {bg_tok}:{b} "
            f"= {ratio:.2f}:1 (minimum {min_ratio:.1f}:1)"
        )


# ── Custom CSS is appended verbatim ──────────────────────────────────────────


def test_custom_css_appended_verbatim(wiki: Path, tmp_path: Path) -> None:
    """custom.css content is inlined verbatim (no sanitisation, trusted)."""
    theme_dir = wiki / ".wiki-dashboard"
    theme_dir.mkdir(exist_ok=True)
    marker = "/* my-custom-theme-marker-xyz */"
    (theme_dir / "custom.css").write_text(marker, encoding="utf-8")

    out = tmp_path / "dash_custom_css.html"
    build_dashboard(wiki, out)
    html = out.read_text(encoding="utf-8")

    assert marker in html, (
        "Expected custom.css marker to appear verbatim in dashboard HTML"
    )


# ── group_by parameter ────────────────────────────────────────────────────────


def test_group_by_parameter_respected(wiki: Path, tmp_path: Path) -> None:
    """Passing group_by='tags' embeds GROUP_BY='tags' in the JS."""
    out = tmp_path / "dash_groupby.html"
    build_dashboard(wiki, out, group_by="tags")
    html = out.read_text(encoding="utf-8")
    assert '"tags"' in html, "Expected GROUP_BY='tags' in dashboard HTML"


# ── Sidebar UX: collapsed default + alphabetical sort ────────────────────────


def _extract_script(html: str) -> str:
    """Return the contents of the single <script> block in *html*."""
    m = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
    assert m is not None, "No <script> block found in dashboard HTML"
    return m.group(1)


def test_sidebar_groups_collapsed_by_default(dashboard_html: str) -> None:
    """buildSidebar() must NOT set det.open=true — groups must start collapsed.

    The only place that opens a group should be setActive() (auto-open on
    navigate), not the initial render loop.
    """
    script = _extract_script(dashboard_html)
    # Extract buildSidebar body (everything up to the next top-level function)
    sidebar_start = script.find("function buildSidebar()")
    set_active_start = script.find("\nfunction setActive(")
    assert sidebar_start >= 0, "buildSidebar() not found in dashboard JS"
    assert set_active_start > sidebar_start, (
        "setActive() boundary marker not found after buildSidebar"
    )
    sidebar_code = script[sidebar_start:set_active_start]
    assert "det.open=true" not in sidebar_code, (
        "buildSidebar sets det.open=true — groups would render open by default. "
        "Remove det.open=true from buildSidebar; groups must start collapsed."
    )


def test_sidebar_active_group_auto_opens(dashboard_html: str) -> None:
    """setActive() must open the <details> group containing the activated page.

    When routing to a page (on load or click), the JS must call
    el.closest('details.nav-group') and set .open=true so the active page
    is visible in a collapsed sidebar.
    """
    script = _extract_script(dashboard_html)
    set_active_start = script.find("function setActive(")
    assert set_active_start >= 0, "setActive() not found in dashboard JS"
    # Grab a generous window that covers the function body
    set_active_code = script[set_active_start : set_active_start + 600]
    assert "closest('details.nav-group')" in set_active_code, (
        "setActive must use .closest('details.nav-group') to find the parent "
        "<details> group and open it on navigation"
    )
    assert "det.open=true" in set_active_code, (
        "setActive must set det.open=true to reveal the active page in its group"
    )


def test_sidebar_pages_sorted_alphabetically(dashboard_html: str) -> None:
    """Pages within sidebar groups are sorted alphabetically by title (case-insensitive).

    Structural check: buildSidebar must contain the sort expression.
    Behavioral check: for the wiki-min fixture the 'concept' group must
    render Alpha -> Beta -> Gamma (not by PAGES array index order).
    """
    script = _extract_script(dashboard_html)

    # -- Structural: sort code present in buildSidebar ----------------------
    sidebar_start = script.find("function buildSidebar()")
    set_active_start = script.find("\nfunction setActive(")
    assert sidebar_start >= 0, "buildSidebar() not found"
    sidebar_code = script[sidebar_start:set_active_start]

    assert ".sort(" in sidebar_code, (
        "buildSidebar must call .sort() to order page entries alphabetically"
    )
    assert "toLowerCase()" in sidebar_code, (
        "buildSidebar sort must use .toLowerCase() for case-insensitive comparison"
    )
    assert "PAGES[a].s" in sidebar_code, (
        "buildSidebar sort must use page slug (PAGES[a].s) as stable tiebreak"
    )

    # -- Behavioral: concept-group pages come out Alpha, Beta, Gamma --------
    pages_m = re.search(r"const PAGES=(\[.+?\]);", dashboard_html)
    assert pages_m is not None, "PAGES JSON not found in dashboard HTML"
    pages = json.loads(pages_m.group(1).replace(r"\/", "/"))

    concept_idxs = [i for i, p in enumerate(pages) if p.get("g") == "concept"]
    assert len(concept_idxs) >= 3, (
        f"Expected >=3 pages in 'concept' group for sort test, got {len(concept_idxs)}"
    )
    # Simulate the JS sort: title.toLowerCase() primary, slug secondary
    sorted_idxs = sorted(
        concept_idxs,
        key=lambda i: (pages[i]["t"].lower(), pages[i]["s"]),
    )
    titles = [pages[i]["t"] for i in sorted_idxs]
    assert titles == ["Alpha", "Beta", "Gamma"], (
        f"Concept group must render Alpha -> Beta -> Gamma in alphabetical order; "
        f"got {titles!r}"
    )
