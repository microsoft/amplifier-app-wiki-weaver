"""wiki_weaver/dashboard.py — Generic wiki dashboard generator (Increment 2).

Public API
----------
build_dashboard(corpus_dir, out_path, *,
                theme=None, group_by="type",
                group_link_template=None,
                enrichment_css=None, enrichment_data=None) -> None

Build a single self-contained HTML dashboard from any wiki corpus.

Domain-blind: NEVER references repo/owner/github — all domain specifics are
injected through the enrichment slot (repo-weaver's concern, not ours).

Reads the indexes written by index.build_indexes().  If indexes are absent or
stale the dashboard still builds; a staleness banner is embedded in the HTML.

CSS escaping (spec §4):
    Consumer-supplied ``enrichment_css`` is parsed by tinycss2, filtered
    (no @import / @charset / off-origin url()), re-serialized (canonical
    bytes only, no consumer text passes through), then every ``<`` is
    replaced with the CSS escape ``\\3c `` before wrapping in a dedicated
    ``<style data-wiki-enrichment>`` element.  Re-serialization cannot
    produce raw ``</style>``; the ``<`` replacement is the belt-and-suspenders
    second layer that covers CSS string values.

Theme (spec §5):
    Ships Almanac warm-paper light default + Almanac Night dark variant
    (``defaultScheme: auto`` via ``@media (prefers-color-scheme:dark)``).
    Reads optional ``<corpus>/.wiki-dashboard/theme.json`` per-token with
    explicit None-checks (0/""/False are valid values); unknown keys warn;
    contrast pairs validated loudly against WCAG AA (4.5:1).
    ``custom.css`` is appended VERBATIM (trusted — wiki-owner's own file;
    no contrast check; documented as the unchecked escape hatch).
"""

from __future__ import annotations

import html as _html_mod
import json
import re
import warnings
from pathlib import Path
from typing import Any

# ── Hard runtime deps (declared in pyproject; fail LOUD if absent) ────────────
#
# tinycss2 powers the enrichment-CSS SANITIZER (a security boundary) and
# markdown renders page bodies.  Both are declared dependencies, so they are
# imported at top level — never guarded behind a try/except that would silently
# disable the sanitizer.  A missing dep is an install error and must surface as
# an ImportError at import time, not a quiet "feature off" at runtime.
import markdown as _md_lib
import tinycss2 as _tinycss2

_MD_EXT = ["tables", "fenced_code", "sane_lists"]

# ── Index layer (increment 1 — consumed here) ─────────────────────────────────

from wiki_weaver.index import (  # noqa: E402
    EXPECTED_SCHEMA_VERSION,
    _body,  # noqa: PLC2701
    _parse_frontmatter,  # noqa: PLC2701
    _slug,  # noqa: PLC2701
)

# ── Almanac theme tokens (spec §5) ────────────────────────────────────────────

# ~21 exposed CSS custom properties (spec says "~18").
ALMANAC_LIGHT: dict[str, str] = {
    "--wiki-bg": "#FBF9F4",
    "--wiki-sidebar": "#F3EEE3",
    "--wiki-card": "#FFFFFF",
    "--wiki-subtle": "#F1ECE0",
    "--wiki-border": "#E4DCCB",
    "--wiki-border-strong": "#D2C7B0",
    "--wiki-text": "#23211C",
    "--wiki-text-secondary": "#6B6655",
    "--wiki-text-muted": "#938C7A",
    "--wiki-accent": "#136F63",
    "--wiki-accent-hover": "#0E574E",
    "--wiki-accent-tint": "#E3EFEB",
    "--wiki-font-reading": (
        'Charter,"Bitstream Charter","Iowan Old Style",'
        '"Source Serif 4",Georgia,Cambria,serif'
    ),
    "--wiki-font-ui": (
        'system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif'
    ),
    "--wiki-font-mono": (
        '"JetBrains Mono","SF Mono","Cascadia Code",'
        "ui-monospace,Menlo,Consolas,monospace"
    ),
    "--wiki-font-size": "16px",
    "--wiki-line-height": "1.65",
    "--wiki-content-width": "68ch",
    "--wiki-space-unit": "8px",
    "--wiki-radius": "8px",
    "--wiki-transition": "150ms",
}

# Dark overrides — only color vars change (spec §5 "Almanac Night")
ALMANAC_DARK: dict[str, str] = {
    "--wiki-bg": "#1A1814",
    "--wiki-sidebar": "#211E18",
    "--wiki-card": "#232019",
    "--wiki-subtle": "#2A261E",
    "--wiki-border": "#342F26",
    "--wiki-border-strong": "#463F32",
    "--wiki-text": "#ECE6D8",
    "--wiki-text-secondary": "#A39B88",
    "--wiki-text-muted": "#7C7565",
    "--wiki-accent": "#46B3A3",
    "--wiki-accent-hover": "#5FC8B8",
    "--wiki-accent-tint": "#1F2C28",
}

# Type-badge palette — text color + tint background (spec §5)
BADGE_PALETTE: dict[str, dict[str, str]] = {
    "module": {"text": "#3A5BA0", "tint": "#E7EDF7"},
    "capability": {"text": "#2F6F4F", "tint": "#E6F0E9"},
    "concept": {"text": "#1F6A6A", "tint": "#E3EFEB"},
    "decision": {"text": "#7A4B9C", "tint": "#F0E9F4"},
    "source": {"text": "#9A5B2E", "tint": "#F4EADD"},
}

# Contrast-check pairs: (text-token, bg-token, min-ratio)  spec §10
_CONTRAST_PAIRS: list[tuple[str, str, float]] = [
    ("--wiki-text", "--wiki-bg", 4.5),
    ("--wiki-text-secondary", "--wiki-sidebar", 4.5),
    ("--wiki-accent", "--wiki-bg", 3.0),
]

# ── CSS escaping (spec §4) ────────────────────────────────────────────────────


def _has_offorigin_url(tokens: list[Any]) -> bool:
    """Return True if any url() token in *tokens* references a non-data: URI."""
    for tok in tokens:
        ttype = getattr(tok, "type", None)
        if ttype == "url":
            val: str = getattr(tok, "value", "") or ""
            if not val.strip().lower().startswith("data:"):
                return True
        elif ttype == "function":
            lname: str = getattr(tok, "lower_name", "") or ""
            if lname == "url":
                for arg in getattr(tok, "arguments", []) or []:
                    if getattr(arg, "type", None) == "string":
                        v: str = getattr(arg, "value", "") or ""
                        if not v.strip().lower().startswith("data:"):
                            return True
        # Recurse into block content / prelude
        content = getattr(tok, "content", None)
        if content and _has_offorigin_url(content):
            return True
        prelude = getattr(tok, "prelude", None)
        if prelude and _has_offorigin_url(prelude):
            return True
    return False


def _sanitize_enrichment_css(css_text: str) -> str | None:
    """Parse, validate, and re-serialize consumer-supplied CSS.

    Spec §4 defense chain:
    1. Parse with tinycss2.  Any ParseError token → reject entire fragment.
    2. Reject ``@import`` and ``@charset`` at-rules (exfiltration defense).
    3. Reject off-origin ``url()`` (overlay defense).
    4. Re-serialize — canonical output; no consumer bytes reach the output.
    5. Replace every ``<`` with CSS escape ``\\3c `` — prevents any residual
       ``</style>`` sequence from breaking out of the wrapper element, even
       for CSS string values that legitimately contain ``<``.

    Returns the sanitized CSS string on success, or ``None`` (+ warning) on
    rejection of a SPECIFIC fragment.  Note the distinction: a missing tinycss2
    is an install error (fails loud at import, above) — it never reaches here.
    What returns None here is a *rejected attack fragment*, which is correct
    fail-safe behavior for untrusted consumer input.
    """
    try:
        rules = _tinycss2.parse_stylesheet(
            css_text, skip_comments=True, skip_whitespace=False
        )
    except Exception as exc:  # noqa: BLE001
        warnings.warn(
            f"enrichment_css: CSS parse failed ({exc}) — enrichment slot omitted",
            UserWarning,
            stacklevel=3,
        )
        return None

    for rule in rules:
        rtype: str = getattr(rule, "type", "")
        if rtype == "error":
            warnings.warn(
                "enrichment_css: CSS parse error "
                f"({getattr(rule, 'message', '?')!r}) — enrichment slot omitted",
                UserWarning,
                stacklevel=3,
            )
            return None
        if rtype == "at-rule":
            kw: str = (getattr(rule, "at_keyword", "") or "").lower()
            if kw in ("import", "charset"):
                warnings.warn(
                    f"enrichment_css: @{kw} is forbidden — enrichment slot omitted",
                    UserWarning,
                    stacklevel=3,
                )
                return None
        if _has_offorigin_url(getattr(rule, "prelude", None) or []):
            warnings.warn(
                "enrichment_css: off-origin url() in rule prelude "
                "— enrichment slot omitted",
                UserWarning,
                stacklevel=3,
            )
            return None
        if _has_offorigin_url(getattr(rule, "content", None) or []):
            warnings.warn(
                "enrichment_css: off-origin url() in rule content "
                "— enrichment slot omitted",
                UserWarning,
                stacklevel=3,
            )
            return None

    try:
        serialized: str = _tinycss2.serialize(rules)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(
            f"enrichment_css: re-serialization failed ({exc}) "
            "— enrichment slot omitted",
            UserWarning,
            stacklevel=3,
        )
        return None

    # Belt-and-suspenders: replace every `<` with CSS escape `\3c `.
    # This prevents any `</style>` sequence from closing the wrapper <style>
    # element — even for CSS string values that validly contain `<`.
    return serialized.replace("<", r"\3c ")


# ── Group-link-template validation ─────────────────────────────────────────


def _validate_group_link_template(template: str, *, stacklevel: int = 3) -> str | None:
    """Validate group_link_template; return it if safe, None on rejection.

    Only http/https schemes are accepted.  Non-http templates are rejected
    with a warning; build continues with plain (non-linked) group headers.
    The template is opaque to wiki-weaver beyond the scheme check — any
    URL structure, path, or query string is the consumer's concern.
    """
    t = template.strip()
    low = t.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        warnings.warn(
            "group_link_template: non-http/https scheme rejected "
            "— group headers will render as plain text",
            UserWarning,
            stacklevel=stacklevel,
        )
        return None
    return t


# ── Contrast validation (spec §10) ───────────────────────────────────────────


def _hex_to_luminance(hex_color: str) -> float | None:
    """WCAG relative luminance (0–1) for a #RRGGBB or #RGB hex color."""
    h = hex_color.strip().lstrip("#")
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    if len(h) != 6:
        return None
    try:
        r, g, b = (
            int(h[0:2], 16) / 255,
            int(h[2:4], 16) / 255,
            int(h[4:6], 16) / 255,
        )
    except ValueError:
        return None

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast_ratio(hex1: str, hex2: str) -> float | None:
    """WCAG contrast ratio between two hex colors, or None if unparseable."""
    l1, l2 = _hex_to_luminance(hex1), _hex_to_luminance(hex2)
    if l1 is None or l2 is None:
        return None
    lo, hi = min(l1, l2), max(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# ── Theme loading (spec §5, §10) ─────────────────────────────────────────────


def _load_theme_overrides(corpus_dir: Path, theme_param: Any) -> dict[str, str]:
    """Return per-token overrides from theme.json (or the theme= param dict).

    Rules:
    - ``theme_param`` dict → used directly as overrides (skip theme.json).
    - ``theme_param`` is None → read ``<corpus>/.wiki-dashboard/theme.json``.
    - Unknown keys warn and are ignored (not silently swallowed).
    - ``None`` values are skipped (explicit None-check; 0/""/False are valid).
    - Contrast pairs are validated loudly; no silent auto-correct.
    """
    known_keys: set[str] = set(ALMANAC_LIGHT.keys())
    overrides: dict[str, str] = {}

    if isinstance(theme_param, dict):
        raw: dict[str, Any] = theme_param
    else:
        theme_path = corpus_dir / ".wiki-dashboard" / "theme.json"
        if not theme_path.exists():
            return {}
        try:
            raw = json.loads(theme_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            warnings.warn(
                f"theme.json: failed to parse ({exc}); using Almanac defaults",
                UserWarning,
                stacklevel=3,
            )
            return {}
        if not isinstance(raw, dict):
            warnings.warn(
                "theme.json: expected a JSON object; using Almanac defaults",
                UserWarning,
                stacklevel=3,
            )
            return {}

    for key, value in raw.items():
        if key.startswith("_"):
            continue  # private metadata
        if key == "title":
            continue  # branding, not a --wiki-* token; read separately for the heading
        if key not in known_keys:
            warnings.warn(
                f"theme.json: unknown token {key!r} — ignored",
                UserWarning,
                stacklevel=3,
            )
            continue
        if value is None:  # explicit None-check — 0/""/False are valid
            continue
        overrides[key] = str(value)

    # Loud contrast validation against the merged light palette
    merged = {**ALMANAC_LIGHT, **overrides}
    for text_tok, bg_tok, min_ratio in _CONTRAST_PAIRS:
        t_val = merged.get(text_tok, "")
        b_val = merged.get(bg_tok, "")
        if not (t_val.startswith("#") and b_val.startswith("#")):
            continue
        ratio = _contrast_ratio(t_val, b_val)
        if ratio is not None and ratio < min_ratio:
            warnings.warn(
                f"theme.json CONTRAST WARNING: {text_tok}:{t_val!r} on "
                f"{bg_tok}:{b_val!r} = {ratio:.2f}:1 "
                f"(minimum {min_ratio:.1f}:1 for WCAG AA — low contrast!)",
                UserWarning,
                stacklevel=3,
            )

    return overrides


def _build_theme_css(overrides: dict[str, str]) -> str:
    """Return the complete Almanac CSS custom-properties block."""
    light = {**ALMANAC_LIGHT, **overrides}

    def css_vars(d: dict[str, str]) -> str:
        return "".join(f"  {k}:{v};\n" for k, v in d.items())

    badge_rules = "\n".join(
        f".badge-{bt}{{color:{c['text']};background:{c['tint']}}}"
        for bt, c in BADGE_PALETTE.items()
    )
    return (
        f":root{{\n{css_vars(light)}}}\n"
        f"@media (prefers-color-scheme:dark){{\n"
        f"  :root{{\n{css_vars(ALMANAC_DARK)}  }}\n}}\n"
        f"{badge_rules}\n"
    )


# ── Markdown + wikilink rendering ─────────────────────────────────────────────

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|]([^\]]*))?\]\]")


def _render_markdown(text: str) -> str:
    """Render markdown to HTML via the (hard-dependency) ``markdown`` package."""
    return _md_lib.markdown(text, extensions=_MD_EXT)


def _resolve_wikilinks(body: str, slug_to_idx: dict[str, int]) -> str:
    """Replace ``[[target]]`` / ``[[target|display]]`` with JS-navigable anchors."""

    def repl(m: re.Match[str]) -> str:
        target_raw = m.group(1).strip()
        display_raw = m.group(2).strip() if m.group(2) else target_raw
        sl = _slug(target_raw)
        if sl in slug_to_idx:
            idx = slug_to_idx[sl]
            return f'<a class="wl" data-p="{idx}">{_html_mod.escape(display_raw)}</a>'
        return _html_mod.escape(display_raw)  # unresolved → plain text

    return _WIKILINK_RE.sub(repl, body)


def _strip_html(html_text: str) -> str:
    """Strip HTML tags; used for plain-text search bodies."""
    return re.sub(r"<[^>]+>", " ", html_text)


# ── Corpus + index loading ────────────────────────────────────────────────────


def _load_corpus_pages(corpus_dir: Path) -> list[dict[str, Any]]:
    """Return one dict per ``*.md`` file in *corpus_dir*."""
    pages: list[dict[str, Any]] = []
    for p in sorted(corpus_dir.glob("*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        fm = _parse_frontmatter(text)
        body_raw = _body(text)
        slug = _slug(p.stem)
        raw_tags = fm.get("tags", [])
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        pages.append(
            {
                "slug": slug,
                "title": str(fm.get("title", slug)),
                "type": str(fm.get("type", "")) if fm.get("type") is not None else "",
                "tags": [str(t) for t in (raw_tags or [])],
                "last_updated": str(fm.get("last_updated", "")),
                "body_raw": body_raw,
                "_fm": fm,  # keep for group_by field lookup
            }
        )
    return pages


def _read_indexes_safe(corpus_dir: Path) -> dict[str, Any]:
    """Read the five index files; return empty data on any failure + warn.

    Returns dict with keys: backlinks, tags, aliases, stale.
    """
    result: dict[str, Any] = {
        "backlinks": {},
        "tags": {},
        "aliases": {},
        "stale": False,
    }
    index_dir = corpus_dir / ".wiki" / "index"

    def _read_one(name: str) -> dict[str, Any] | None:
        path = index_dir / name
        if not path.exists():
            warnings.warn(
                f"dashboard: {name} not found — run build_indexes() first",
                UserWarning,
                stacklevel=5,
            )
            return None
        try:
            env: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            warnings.warn(
                f"dashboard: failed to read {name} ({exc})",
                UserWarning,
                stacklevel=5,
            )
            return None
        sv = env.get("schema_version")
        if sv != EXPECTED_SCHEMA_VERSION:
            warnings.warn(
                f"dashboard: {name} schema_version {sv!r} "
                f"!= expected {EXPECTED_SCHEMA_VERSION!r} — rebuild indexes",
                UserWarning,
                stacklevel=5,
            )
            return None
        return env

    bl_env = _read_one("backlinks.json")
    if bl_env is not None:
        result["backlinks"] = bl_env["data"]
        built = bl_env.get("built", {})
        pages_md = list(corpus_dir.glob("*.md"))
        if pages_md:
            cur_max = max(p.stat().st_mtime for p in pages_md)
            result["stale"] = cur_max > built.get("max_mtime", 0.0)

    tags_env = _read_one("tags.json")
    if tags_env is not None:
        result["tags"] = tags_env["data"]

    aliases_env = _read_one("aliases.json")
    if aliases_env is not None:
        result["aliases"] = aliases_env["data"]

    return result


# ── JSON serialization ────────────────────────────────────────────────────────


def _safe_json(obj: Any) -> str:
    """Compact JSON safe for embedding inside ``<script>`` elements."""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    # Prevent `</script>` / `</style>` in content values from closing the tag.
    return s.replace("</", r"<\/")


# ── HTML template ─────────────────────────────────────────────────────────────
# Placeholders: __TITLE__, __THEME_CSS__, __CUSTOM_CSS_BLOCK__,
# __ENRICHMENT_DATA_STYLE__, __ENRICHMENT_STYLE__,
# __PAGES_JSON__, __BACKLINKS_JSON__, __TAG_IDX_JSON__,
# __STATS_JSON__, __GROUP_BY_JSON__, __STALE_JSON__, __BADGE_PALETTE_JSON__,
# __GROUP_LINK_TEMPLATE_JSON__

_DASH_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
/* === Almanac theme tokens + component CSS === */
__THEME_CSS__
/* === Reset === */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{font-size:var(--wiki-font-size);color-scheme:light dark}
body{font-family:var(--wiki-font-ui);background:var(--wiki-bg);color:var(--wiki-text);
     display:flex;flex-direction:column;height:100vh;overflow:hidden}
/* === Stale banner === */
.stale-banner{background:#fef3cd;border-bottom:1px solid #ffc107;
  padding:6px 16px;font-size:.8rem;color:#664d03;text-align:center;flex-shrink:0}
.stale-banner code{font-family:var(--wiki-font-mono);font-size:.85em}
/* === Header === */
.hdr{height:48px;min-height:48px;flex-shrink:0;
  background:var(--wiki-sidebar);border-bottom:1px solid var(--wiki-border);
  display:flex;align-items:center;gap:12px;padding:0 16px}
.brand{font-size:.95rem;font-weight:700;letter-spacing:-.01em;color:var(--wiki-text)}
.hdr-stats{display:flex;gap:6px;margin-left:auto}
.stat-chip{background:var(--wiki-card);border:1px solid var(--wiki-border);
  border-radius:var(--wiki-radius);padding:3px 10px;
  display:flex;flex-direction:column;align-items:center;line-height:1.2}
.stat-n{font-size:.95rem;font-weight:700;color:var(--wiki-accent);
  font-variant-numeric:tabular-nums}
.stat-l{font-size:.6rem;color:var(--wiki-text-muted);text-transform:uppercase;
  letter-spacing:.05em}
/* === App layout === */
.app{display:flex;height:calc(100vh - 48px);overflow:hidden}
/* === Sidebar === */
.sidebar{width:260px;min-width:260px;background:var(--wiki-sidebar);
  border-right:1px solid var(--wiki-border);
  display:flex;flex-direction:column;overflow:hidden}
.search-wrap{padding:8px 10px;flex-shrink:0;border-bottom:1px solid var(--wiki-border)}
#search{width:100%;padding:5px 10px;border-radius:calc(var(--wiki-radius) - 2px);
  background:var(--wiki-bg);border:1px solid var(--wiki-border-strong);
  color:var(--wiki-text);font-size:.82rem;font-family:var(--wiki-font-ui);outline:none}
#search:focus{border-color:var(--wiki-accent)}
#search::placeholder{color:var(--wiki-text-muted)}
/* === Nav tree === */
#nav-tree{overflow-y:auto;flex:1;padding-bottom:16px}
#nav-tree::-webkit-scrollbar{width:3px}
#nav-tree::-webkit-scrollbar-thumb{background:var(--wiki-border-strong);border-radius:2px}
details.nav-group{border-bottom:1px solid var(--wiki-border)}
details.nav-group summary.group-hdr{
  display:flex;align-items:center;gap:6px;padding:7px 10px;
  cursor:pointer;color:var(--wiki-text-secondary);font-size:.78rem;
  font-weight:600;user-select:none;list-style:none;
  transition:background var(--wiki-transition)}
details.nav-group summary.group-hdr::-webkit-details-marker{display:none}
details.nav-group summary.group-hdr:hover{background:var(--wiki-subtle);color:var(--wiki-text)}
.group-chev{font-size:.6rem;color:var(--wiki-text-muted);
  transition:transform var(--wiki-transition);display:inline-block}
details.nav-group[open] .group-chev{transform:rotate(90deg)}
.group-name{flex:1}
.group-cnt{font-size:.65rem;color:var(--wiki-text-muted);background:var(--wiki-bg);
  border:1px solid var(--wiki-border);border-radius:8px;padding:0 5px}
.nav-item{padding:4px 10px 4px 16px;cursor:pointer;font-size:.74rem;
  color:var(--wiki-text-secondary);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;display:flex;align-items:center;gap:6px;
  transition:background var(--wiki-transition),color var(--wiki-transition)}
.nav-item:hover{background:var(--wiki-subtle);color:var(--wiki-text)}
.nav-item.active{background:var(--wiki-accent-tint);color:var(--wiki-accent);
  border-left:2px solid var(--wiki-accent);padding-left:14px}
.tdot{width:6px;height:6px;border-radius:50%;flex-shrink:0;background:var(--wiki-text-muted)}
.tdot-module{background:#3A5BA0}.tdot-capability{background:#2F6F4F}
.tdot-concept{background:#1F6A6A}.tdot-decision{background:#7A4B9C}
.tdot-source{background:#9A5B2E}
/* === Search results === */
#search-results{overflow-y:auto;flex:1;padding-bottom:16px}
.sr-item{padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--wiki-border)}
.sr-item:hover{background:var(--wiki-subtle)}
.sr-title{font-size:.8rem;color:var(--wiki-text);font-weight:500;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sr-snip{font-size:.7rem;color:var(--wiki-text-secondary);margin-top:2px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.sr-snip em,.sr-title em{color:var(--wiki-accent);font-style:normal;font-weight:600}
.sr-empty{padding:20px;text-align:center;color:var(--wiki-text-muted);font-size:.8rem}
/* === Main area === */
#main{flex:1;display:flex;flex-direction:column;overflow:hidden}
#page-meta-bar{flex-shrink:0;padding:8px 24px;
  border-bottom:1px solid var(--wiki-border);background:var(--wiki-card);min-height:36px}
.page-meta{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.meta-date{font-size:.75rem;color:var(--wiki-text-muted)}
.meta-tags{display:flex;gap:4px;flex-wrap:wrap}
.pg-tag{font-size:.68rem;color:var(--wiki-text-secondary);background:var(--wiki-subtle);
  border:1px solid var(--wiki-border);border-radius:4px;padding:1px 6px}
.meta-bl{font-size:.72rem;color:var(--wiki-text-muted)}
/* === Article + ledger rail (spec §5) === */
#article{flex:1;overflow-y:auto;padding:24px 24px 48px;
  display:flex;flex-direction:column;gap:32px}
#article::-webkit-scrollbar{width:4px}
#article::-webkit-scrollbar-thumb{background:var(--wiki-border-strong);border-radius:2px}
.article-body{max-width:var(--wiki-content-width);
  font-family:var(--wiki-font-reading);font-size:17px;
  line-height:var(--wiki-line-height);color:var(--wiki-text);
  padding-left:24px;border-left:1px solid var(--wiki-border)}
.article-body h1,.article-body h2,.article-body h3,
.article-body h4,.article-body h5,.article-body h6{
  font-family:var(--wiki-font-ui);color:var(--wiki-text);
  margin:1.4em 0 .4em;position:relative}
.article-body h2::before,.article-body h3::before{
  content:"";position:absolute;left:-26px;top:.2em;
  height:1em;width:2px;background:var(--wiki-accent);border-radius:1px}
.article-body p{margin:.7em 0}
.article-body ul,.article-body ol{margin:.5em 0 .5em 1.4em}
.article-body li{margin:.25em 0}
.article-body code{font-family:var(--wiki-font-mono);font-size:.88em;
  background:var(--wiki-subtle);border:1px solid var(--wiki-border);
  border-radius:3px;padding:.1em .3em}
.article-body pre{background:var(--wiki-subtle);border:1px solid var(--wiki-border);
  border-radius:var(--wiki-radius);padding:12px 16px;overflow-x:auto;margin:1em 0}
.article-body pre code{background:none;border:none;padding:0;font-size:.87em}
.article-body blockquote{border-left:3px solid var(--wiki-border-strong);
  padding-left:12px;color:var(--wiki-text-secondary);margin:1em 0}
.article-body table{border-collapse:collapse;width:100%;margin:1em 0;font-size:.9em}
.article-body th,.article-body td{border:1px solid var(--wiki-border);padding:6px 10px}
.article-body th{background:var(--wiki-subtle);font-weight:600;text-align:left}
.article-body a,.wl{color:var(--wiki-accent);text-decoration:none;cursor:pointer}
.article-body a:hover,.wl:hover{color:var(--wiki-accent-hover);text-decoration:underline}
/* === Panels (outline + backlinks) === */
.page-panels{display:flex;gap:16px;flex-wrap:wrap;
  max-width:var(--wiki-content-width)}
.panel{flex:1;min-width:180px;background:var(--wiki-card);
  border:1px solid var(--wiki-border);border-radius:var(--wiki-radius);
  padding:12px 14px}
.panel-label{font-size:.65rem;font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;color:var(--wiki-text-muted);margin-bottom:8px}
.outline-item{display:block;font-size:.78rem;color:var(--wiki-accent);
  text-decoration:none;padding:2px 0}
.outline-item:hover{color:var(--wiki-accent-hover)}
.outline-h3{padding-left:12px;color:var(--wiki-text-secondary)}
.bl-item{display:block;font-size:.78rem;color:var(--wiki-text-secondary);padding:2px 0}
.bl-item.nav-item{cursor:pointer;padding:3px 6px;border-radius:3px}
.bl-item.nav-item:hover{background:var(--wiki-subtle);color:var(--wiki-accent)}
/* === Badges === */
.badge{display:inline-block;padding:1px 8px;border-radius:4px;
  font-size:.72rem;font-weight:600;font-family:var(--wiki-font-ui)}
.badge-default{color:var(--wiki-text-secondary);background:var(--wiki-subtle)}
/* === Home page === */
.home-stats{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0 24px}
.stat-card-big{background:var(--wiki-card);border:1px solid var(--wiki-border);
  border-radius:var(--wiki-radius);padding:16px 24px;
  display:flex;flex-direction:column;align-items:center;min-width:90px}
.stat-n-big{font-size:1.8rem;font-weight:700;color:var(--wiki-accent);
  font-variant-numeric:tabular-nums}
.stat-l-big{font-size:.7rem;color:var(--wiki-text-muted);text-transform:uppercase;
  letter-spacing:.05em;margin-top:2px}
.home-section{margin:24px 0}
.section-label{font-size:.7rem;font-weight:700;letter-spacing:.07em;
  text-transform:uppercase;color:var(--wiki-text-muted);margin-bottom:12px}
.recent-list{list-style:none;display:flex;flex-direction:column;gap:4px}
.recent-list li{display:flex;align-items:baseline;gap:8px}
.recent-link{font-size:.85rem;color:var(--wiki-accent);cursor:pointer}
.recent-link:hover{color:var(--wiki-accent-hover);text-decoration:underline}
.recent-date{font-size:.72rem;color:var(--wiki-text-muted)}
.tag-cloud{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}
.tag-cloud-item{color:var(--wiki-accent);cursor:pointer}
.tag-cloud-item:hover{color:var(--wiki-accent-hover)}
.chart-wrap{display:block;overflow:visible}
.chart-label{font-family:var(--wiki-font-ui);font-size:11px;fill:var(--wiki-text-secondary)}
.chart-count{font-family:var(--wiki-font-ui);font-size:11px;fill:var(--wiki-text-muted)}
.chart-bar{fill:var(--wiki-accent);opacity:.75}
</style>
__CUSTOM_CSS_BLOCK__
</head>
<body>
<div id="stale-banner" class="stale-banner" hidden>
  &#9888; Dashboard built from a stale index &mdash; run
  <code>wiki-weaver build-dashboard</code> to refresh.
</div>
<header class="hdr">
  <span class="brand">__TITLE__</span>
  <div class="hdr-stats">
    <div class="stat-chip">
      <span class="stat-n" id="sh-pages">&#x2013;</span>
      <span class="stat-l">pages</span>
    </div>
    <div class="stat-chip">
      <span class="stat-n" id="sh-tags">&#x2013;</span>
      <span class="stat-l">tags</span>
    </div>
    <div class="stat-chip">
      <span class="stat-n" id="sh-links">&#x2013;</span>
      <span class="stat-l">links</span>
    </div>
  </div>
</header>
<div class="app">
  <nav class="sidebar" role="navigation" aria-label="Wiki navigation">
    <div class="search-wrap">
      <input type="search" id="search"
             placeholder="Search pages&hellip;"
             autocomplete="off" spellcheck="false"
             aria-label="Search wiki pages">
    </div>
    <div id="nav-tree" role="tree"></div>
    <div id="search-results" hidden></div>
  </nav>
  <main id="main" role="main">
    <div id="page-meta-bar" aria-label="Page metadata"></div>
    <div id="article" tabindex="0"></div>
  </main>
</div>
__ENRICHMENT_DATA_STYLE__
__ENRICHMENT_STYLE__
<script>
"use strict";
const PAGES=__PAGES_JSON__;
const BACKLINKS=__BACKLINKS_JSON__;
const TAG_IDX=__TAG_IDX_JSON__;
const STATS=__STATS_JSON__;
const GROUP_BY=__GROUP_BY_JSON__;
const STALE=__STALE_JSON__;
const BADGE_PALETTE=__BADGE_PALETTE_JSON__;
const GROUP_LINK_TEMPLATE=__GROUP_LINK_TEMPLATE_JSON__;

/* --- Utilities --- */
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function $(id){return document.getElementById(id);}

/* --- Boot --- */
$('sh-pages').textContent=STATS.pages;
$('sh-tags').textContent=STATS.tags;
$('sh-links').textContent=STATS.links;
if(STALE){$('stale-banner').removeAttribute('hidden');}

/* --- Sidebar --- */
function buildSidebar(){
  const groups={};
  PAGES.forEach((p,i)=>{
    /* g is always a list; empty list → untyped bucket */
    const gs=p.g&&p.g.length?p.g:['(untyped)'];
    gs.forEach(function(g){if(!groups[g])groups[g]=[];groups[g].push(i);});
  });
  const sorted=Object.keys(groups).sort();
  const tree=$('nav-tree');
  tree.innerHTML='';
  sorted.forEach(g=>{
    const idxs=groups[g].slice().sort((a,b)=>{
      const ta=PAGES[a].t.toLowerCase(),tb=PAGES[b].t.toLowerCase();
      if(ta!==tb)return ta<tb?-1:1;
      return PAGES[a].s<PAGES[b].s?-1:1;
    });
    /* group header: plain text or hyperlink depending on GROUP_LINK_TEMPLATE */
    const grpHdr=GROUP_LINK_TEMPLATE
      ?'<a href="'+esc(GROUP_LINK_TEMPLATE.replace('{group}',g.split('/').map(encodeURIComponent).join('/')))+'" target="_blank" rel="noopener noreferrer">'+esc(g)+'</a>'
      :esc(g);
    const det=document.createElement('details');
    det.className='nav-group';
    det.innerHTML=
      '<summary class="group-hdr">'+
        '<span class="group-chev">&#9654;</span>'+
        '<span class="group-name">'+grpHdr+'</span>'+
        '<span class="group-cnt">'+idxs.length+'</span>'+
      '</summary>'+
      idxs.map(i=>{
        const p=PAGES[i];
        const tc=p.y?'tdot tdot-'+p.y:'tdot';
        return '<div class="nav-item" data-p="'+i+'" role="treeitem">'+
               '<span class="'+tc+'"></span>'+esc(p.t)+'</div>';
      }).join('');
    tree.appendChild(det);
  });
  tree.addEventListener('click',e=>{
    const item=e.target.closest('.nav-item[data-p]');
    if(item)navigateTo(+item.dataset.p);
  });
}

/* --- Active state --- */
function clearActive(){
  document.querySelectorAll('.nav-item.active')
          .forEach(el=>el.classList.remove('active'));
}
function setActive(idx){
  clearActive();
  const el=document.querySelector('.nav-item[data-p="'+idx+'"]');
  if(el){
    el.classList.add('active');
    const det=el.closest('details.nav-group');
    if(det)det.open=true;
    el.scrollIntoView({block:'nearest'});
  }
}

/* --- Page navigation --- */
function navigateTo(idx){
  clearSearch();
  const p=PAGES[idx];
  if(!p)return;
  setActive(idx);

  const tags=(p.tags||[]).map(t=>'<span class="pg-tag">'+esc(t)+'</span>').join('');
  const bl=(BACKLINKS[p.s]||[]).length;
  const bp=BADGE_PALETTE[p.y];
  const badgeStyle=bp?'style="color:'+bp.text+';background:'+bp.tint+'"':'';
  $('page-meta-bar').innerHTML=
    '<div class="page-meta">'+
    (p.y?'<span class="badge" '+badgeStyle+'>'+esc(p.y)+'</span>':'')+
    (p.d?'<span class="meta-date">'+esc(p.d)+'</span>':'')+
    (tags?'<span class="meta-tags">'+tags+'</span>':'')+
    (bl?'<span class="meta-bl">'+bl+' backlink'+(bl===1?'':'s')+'</span>':'')+
    '</div>';

  const art=$('article');
  art.innerHTML='<div class="article-body">'+p.h+'</div>';
  attachHeadingIds(art);

  const outline=buildOutlinePanel(art);
  const backlinks=buildBacklinksPanel(p.s);
  if(outline||backlinks){
    const panels=document.createElement('div');
    panels.className='page-panels';
    if(outline)panels.appendChild(outline);
    if(backlinks)panels.appendChild(backlinks);
    art.appendChild(panels);
  }
  $('main').scrollTo(0,0);
  history.replaceState(null,'','#page-'+idx);
}

function attachHeadingIds(el){
  const seen={};
  el.querySelectorAll('h2,h3,h4').forEach(h=>{
    const base=(h.textContent||'').trim().toLowerCase()
      .replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'');
    let id=base||'heading';let n=1;
    while(seen[id]){id=base+'-'+n++;}
    seen[id]=true;h.id=id;
  });
}

function buildOutlinePanel(el){
  const hs=[...el.querySelectorAll('h2,h3')];
  if(!hs.length)return null;
  const panel=document.createElement('div');
  panel.className='panel outline-panel';
  panel.innerHTML='<div class="panel-label">On this page</div>'+
    hs.map(h=>'<a class="outline-item outline-h'+h.tagName[1]+
              '" href="#'+h.id+'">'+esc(h.textContent||'')+'</a>').join('');
  return panel;
}

function buildBacklinksPanel(slug){
  const bls=BACKLINKS[slug]||[];
  if(!bls.length)return null;
  const panel=document.createElement('div');
  panel.className='panel backlinks-panel';
  panel.innerHTML='<div class="panel-label">Linked from</div>'+
    bls.map(s=>{
      const idx=PAGES.findIndex(p=>p.s===s);
      if(idx<0)return'<span class="bl-item">'+esc(s)+'</span>';
      return'<div class="bl-item nav-item" data-p="'+idx+'">'+
             esc(PAGES[idx].t)+'</div>';
    }).join('');
  panel.addEventListener('click',e=>{
    const item=e.target.closest('.nav-item[data-p]');
    if(item)navigateTo(+item.dataset.p);
  });
  return panel;
}

/* --- Home --- */
function navigateHome(){
  clearSearch();clearActive();
  $('page-meta-bar').innerHTML='';
  $('article').innerHTML='<div class="article-body">'+renderHome()+'</div>';
  /* Wire recently-updated and tag-cloud click handlers */
  $('article').querySelectorAll('.recent-link[data-p]').forEach(el=>{
    el.addEventListener('click',()=>navigateTo(+el.dataset.p));
  });
  $('article').querySelectorAll('.tag-cloud-item[data-tag]').forEach(el=>{
    el.addEventListener('click',()=>{
      const idxs=TAG_IDX[el.dataset.tag]||[];
      if(idxs.length)navigateTo(idxs[0]);
    });
  });
  $('main').scrollTo(0,0);
  history.replaceState(null,'','#home');
}

function renderHome(){
  const ov=PAGES.find(p=>p.s==='overview'||p.s==='index');
  if(ov)return ov.h;
  return renderStatCards()+renderRecentlyUpdated()+renderTagCloud()+renderTypeBar();
}

function renderStatCards(){
  function card(n,label){
    return'<div class="stat-card-big"><div class="stat-n-big">'+n+
           '</div><div class="stat-l-big">'+label+'</div></div>';
  }
  return'<div class="home-section"><div class="section-label">At a glance</div>'+
    '<div class="home-stats">'+
    card(STATS.pages,'pages')+card(STATS.tags,'tags')+card(STATS.links,'links')+
    '</div></div>';
}

function renderRecentlyUpdated(){
  const sorted=PAGES.filter(p=>p.d).sort((a,b)=>b.d.localeCompare(a.d)).slice(0,10);
  if(!sorted.length)return'';
  return'<div class="home-section"><div class="section-label">Recently updated</div>'+
    '<ul class="recent-list">'+sorted.map(p=>{
      const i=PAGES.indexOf(p);
      return'<li><span class="recent-link" data-p="'+i+'">'+esc(p.t)+'</span>'+
             '<span class="recent-date">'+esc(p.d)+'</span></li>';
    }).join('')+'</ul></div>';
}

function renderTagCloud(){
  const tags=Object.entries(TAG_IDX).sort((a,b)=>b[1].length-a[1].length);
  if(!tags.length)return'';
  const max=tags[0][1].length||1;
  return'<div class="home-section"><div class="section-label">Tags</div>'+
    '<div class="tag-cloud">'+tags.map(([t,idxs])=>{
      const sz=(0.75+(idxs.length/max)*0.75).toFixed(2);
      return'<span class="tag-cloud-item" data-tag="'+esc(t)+
             '" style="font-size:'+sz+'rem">'+esc(t)+' <sup>'+idxs.length+'</sup></span>';
    }).join(' ')+'</div></div>';
}

function renderTypeBar(){
  const groups={};
  PAGES.forEach(p=>{const t=p.y||'(untyped)';groups[t]=(groups[t]||0)+1;});
  const entries=Object.entries(groups).sort((a,b)=>b[1]-a[1]);
  if(!entries.length)return'';
  const max=entries[0][1]||1;
  const bh=20,gap=5,lw=110,cw=200;
  let bars='';
  entries.forEach(([t,count],i)=>{
    const y=i*(bh+gap);
    const bw=Math.max(3,Math.round((count/max)*cw));
    bars+='<g transform="translate(0,'+y+')">'+
      '<text x="'+(lw-6)+'" y="'+(bh/2+4)+'" text-anchor="end" class="chart-label">'+esc(t)+'</text>'+
      '<rect x="'+lw+'" y="1" width="'+bw+'" height="'+(bh-2)+'" class="chart-bar" rx="3"/>'+
      '<text x="'+(lw+bw+6)+'" y="'+(bh/2+4)+'" class="chart-count">'+count+'</text>'+
      '</g>';
  });
  const svgH=entries.length*(bh+gap);
  return'<div class="home-section"><div class="section-label">Pages by '+esc(GROUP_BY)+'</div>'+
    '<svg class="chart-wrap" width="'+(lw+cw+60)+'" height="'+svgH+
    '" viewBox="0 0 '+(lw+cw+60)+' '+svgH+'" aria-label="Pages by type">'+bars+'</svg></div>';
}

/* --- Wikilink click delegation --- */
$('article').addEventListener('click',e=>{
  const wl=e.target.closest('.wl[data-p]');
  if(wl){e.preventDefault();navigateTo(+wl.dataset.p);}
});

/* --- Search --- */
let _stimer=null;
$('search').addEventListener('input',e=>{
  clearTimeout(_stimer);
  const q=e.target.value.trim();
  _stimer=setTimeout(()=>{q?runSearch(q):clearSearch();},150);
});
$('search').addEventListener('keydown',e=>{
  if(e.key==='Escape'){clearSearch();e.target.value='';}
});

function runSearch(q){
  const ql=q.toLowerCase();
  const results=[];
  for(let i=0;i<PAGES.length;i++){
    const p=PAGES[i];
    const tl=p.t.toLowerCase();
    let score=0,snippet='';
    if(tl===ql)score=400;
    else if(tl.startsWith(ql))score=300;
    else if(tl.includes(ql))score=200;
    if(!score){
      const bi=p.b.indexOf(ql);
      if(bi>=0){
        score=100;
        const s=Math.max(0,bi-50),e=Math.min(p.b.length,bi+ql.length+80);
        snippet=(s>0?'\u2026':'')+p.b.slice(s,e)+(e<p.b.length?'\u2026':'');
      }
    }
    if(score)results.push({i,score,snippet});
  }
  results.sort((a,b)=>b.score-a.score);
  $('nav-tree').hidden=true;
  const sr=$('search-results');
  sr.hidden=false;
  if(!results.length){
    sr.innerHTML='<div class="sr-empty">No results for &ldquo;'+esc(q)+'&rdquo;</div>';
    return;
  }
  sr.innerHTML=results.slice(0,40).map(({i,snippet})=>{
    const p=PAGES[i];
    return'<div class="sr-item" data-p="'+i+'">'+
      '<div class="sr-title">'+hilite(p.t,ql)+'</div>'+
      (snippet?'<div class="sr-snip">'+hilite(snippet,ql)+'</div>':'')+
      '</div>';
  }).join('');
  sr.addEventListener('click',e=>{
    const item=e.target.closest('.sr-item[data-p]');
    if(item){clearSearch();$('search').value='';navigateTo(+item.dataset.p);}
  },{once:true});
}

function clearSearch(){
  $('nav-tree').hidden=false;
  const sr=$('search-results');sr.hidden=true;sr.innerHTML='';
}

function hilite(text,q){
  if(!q)return esc(text);
  const idx=text.toLowerCase().indexOf(q);
  if(idx<0)return esc(text);
  return esc(text.slice(0,idx))+'<em>'+esc(text.slice(idx,idx+q.length))+'</em>'+
         esc(text.slice(idx+q.length));
}

/* --- Hash routing --- */
function routeFromHash(){
  const h=location.hash.slice(1);
  if(!h||h==='home'){navigateHome();return;}
  if(h.startsWith('page-')){
    const idx=+h.slice(5);
    if(!isNaN(idx)&&idx>=0&&idx<PAGES.length){navigateTo(idx);return;}
  }
  navigateHome();
}

/* --- Boot --- */
buildSidebar();
routeFromHash();
window.addEventListener('hashchange',routeFromHash);
</script>
</body>
</html>
"""  # noqa: E501


# ── HTML generation ────────────────────────────────────────────────────────────

_WL_COUNT_RE = re.compile(r"\[\[([^\]|#]+)")


def _generate_html(  # noqa: PLR0913
    pages: list[dict[str, Any]],
    slug_to_idx: dict[str, int],
    backlinks: dict[str, list[str]],
    tags_data: dict[str, list[str]],
    stale: bool,
    group_by: str,
    theme_css: str,
    custom_css_block: str,
    enrichment_data_style: str,
    enrichment_style: str,
    title: str,
    group_link_template: str | None = None,
) -> str:
    """Fill _DASH_HTML with corpus data and return the complete HTML string."""
    # Render pages: resolve wikilinks, render markdown, build search body.
    rendered: list[dict[str, Any]] = []
    total_links = 0
    for i, p in enumerate(pages):
        link_count = len(_WL_COUNT_RE.findall(p["body_raw"]))
        total_links += link_count
        body_html = _render_markdown(_resolve_wikilinks(p["body_raw"], slug_to_idx))
        body_text = _strip_html(body_html).lower()
        # _group_vals is always a list (set in build_dashboard)
        grp_vals: list[str] = p.get("_group_vals", [p["type"] or ""])
        rendered.append(
            {
                "i": i,
                "s": p["slug"],
                "t": p["title"],
                "y": p["type"],
                "g": grp_vals,
                "tags": p["tags"],
                "d": p["last_updated"],
                "h": body_html,
                "b": body_text,
            }
        )

    # TAG_IDX: tag → [page indices]
    tag_idx: dict[str, list[int]] = {}
    for tag, slugs in tags_data.items():
        idxs = [slug_to_idx[s] for s in slugs if s in slug_to_idx]
        if idxs:
            tag_idx[tag] = idxs

    stats = {
        "pages": len(pages),
        "tags": len(tags_data),
        "links": total_links,
    }

    html = _DASH_HTML
    html = html.replace("__TITLE__", _html_mod.escape(title))
    html = html.replace("__THEME_CSS__", theme_css)
    html = html.replace("__CUSTOM_CSS_BLOCK__", custom_css_block)
    html = html.replace("__PAGES_JSON__", _safe_json(rendered))
    html = html.replace("__BACKLINKS_JSON__", _safe_json(backlinks))
    html = html.replace("__TAG_IDX_JSON__", _safe_json(tag_idx))
    html = html.replace("__STATS_JSON__", _safe_json(stats))
    html = html.replace("__GROUP_BY_JSON__", _safe_json(group_by))
    html = html.replace("__STALE_JSON__", _safe_json(stale))
    html = html.replace("__ENRICHMENT_DATA_STYLE__", enrichment_data_style)
    html = html.replace("__ENRICHMENT_STYLE__", enrichment_style)
    html = html.replace("__BADGE_PALETTE_JSON__", _safe_json(BADGE_PALETTE))
    html = html.replace("__GROUP_LINK_TEMPLATE_JSON__", _safe_json(group_link_template))
    return html


# ── Public API ────────────────────────────────────────────────────────────────


def build_dashboard(
    corpus_dir: str | Path,
    out_path: str | Path,
    *,
    theme: dict[str, str] | None = None,
    group_by: str = "type",
    group_link_template: str | None = None,
    enrichment_css: str | None = None,
    enrichment_data: dict[str, Any] | None = None,
) -> None:
    """Build a self-contained HTML dashboard from a wiki corpus.

    Parameters
    ----------
    corpus_dir
        Path to the wiki corpus directory (containing ``*.md`` files and the
        ``.wiki/index/`` folder written by ``index.build_indexes()``).
    out_path
        Destination ``.html`` file.  Parent directories are created as needed.
    theme
        Optional dict of ``--wiki-*`` CSS var overrides.  If ``None``, reads
        ``<corpus>/.wiki-dashboard/theme.json`` when present.
    group_by
        Frontmatter field that groups the sidebar (default: ``"type"``).
        Scalar values work as before; **list-valued** fields (e.g. ``tags``,
        ``repos``) cause each page to appear under *every* value as a separate
        group (multi-membership).  Missing/empty fields fall back to ``type``.
    group_link_template
        Optional URL template for group header links.  ``{group}`` is replaced
        by the URL-encoded group value; the rendered ``<a>`` opens in a new
        tab with ``rel=noopener noreferrer``.  Only ``http://`` and ``https://``
        schemes are accepted; non-http templates are rejected with a warning
        and fall back to plain-text headers.  Domain-opaque — wiki-weaver
        applies no meaning beyond the scheme check and ``{group}`` substitution.
    enrichment_css
        Optional consumer-supplied CSS fragment (cross-seam, sanitized via
        tinycss2 parse→filter→reserialize before inlining).  Domain-opaque.
    enrichment_data
        Optional ``{key: value}`` dict emitted as ``--key: value`` CSS custom
        properties in a scoped ``<style>`` block.
    """
    corpus = Path(corpus_dir).expanduser().resolve()
    out = Path(out_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    # ── Load corpus pages ──────────────────────────────────────────────────
    pages = _load_corpus_pages(corpus)

    # ── Read indexes ───────────────────────────────────────────────────────
    idx = _read_indexes_safe(corpus)
    backlinks: dict[str, list[str]] = idx["backlinks"]
    tags_data: dict[str, list[str]] = idx["tags"]
    aliases: dict[str, Any] = idx["aliases"]
    stale: bool = idx["stale"]

    # ── Build slug → page-index map (includes alias resolutions) ──────────
    slug_to_idx: dict[str, int] = {p["slug"]: i for i, p in enumerate(pages)}
    for alias, target in aliases.items():
        if alias.startswith("_"):
            continue
        if isinstance(target, str) and target in slug_to_idx:
            slug_to_idx[alias] = slug_to_idx[target]

    # ── Annotate pages with group_by field value ───────────────────────────
    for p in pages:
        fm = p.get("_fm", {})
        raw_val = fm.get(group_by, fm.get("type", ""))
        if isinstance(raw_val, list):
            vals = [str(v) for v in raw_val if v is not None]
            p["_group_vals"] = vals if vals else [""]
        else:
            p["_group_vals"] = [str(raw_val) if raw_val is not None else ""]

    # ── Validate group_link_template ─────────────────────────────────────────────
    validated_template: str | None = None
    if group_link_template is not None:
        validated_template = _validate_group_link_template(
            group_link_template, stacklevel=2
        )

    # ── Theme ──────────────────────────────────────────────────────────────
    theme_overrides = _load_theme_overrides(corpus, theme)
    theme_css = _build_theme_css(theme_overrides)

    # ── custom.css — verbatim / trusted (spec §10) ─────────────────────────
    custom_path = corpus / ".wiki-dashboard" / "custom.css"
    if custom_path.exists():
        custom_content = custom_path.read_text(encoding="utf-8")
        custom_css_block = f'<style id="wiki-custom">\n{custom_content}\n</style>'
    else:
        custom_css_block = ""

    # ── Enrichment data → CSS custom properties ────────────────────────────
    enrichment_data_style = ""
    if enrichment_data:
        var_pairs = []
        for k, v in enrichment_data.items():
            var_name = k if k.startswith("--") else f"--{k}"
            var_pairs.append(f"  {var_name}: {v};")
        enrichment_data_style = (
            '<style id="wiki-enrich-data">\n:root {\n'
            + "\n".join(var_pairs)
            + "\n}\n</style>"
        )

    # ── Enrichment CSS → sanitized (spec §4) ──────────────────────────────
    enrichment_style = ""
    if enrichment_css is not None:
        safe_css = _sanitize_enrichment_css(enrichment_css)
        if safe_css is not None:
            enrichment_style = f"<style data-wiki-enrichment>\n{safe_css}\n</style>"

    # ── Wiki title from theme.json branding (optional) ─────────────────────
    title = "Wiki"
    tj_path = corpus / ".wiki-dashboard" / "theme.json"
    if tj_path.exists():
        try:
            tj = json.loads(tj_path.read_text(encoding="utf-8"))
            if isinstance(tj, dict) and tj.get("title"):
                title = str(tj["title"])
        except Exception:  # noqa: BLE001
            pass

    # ── Generate and write ─────────────────────────────────────────────────
    html = _generate_html(
        pages=pages,
        slug_to_idx=slug_to_idx,
        backlinks=backlinks,
        tags_data=tags_data,
        stale=stale,
        group_by=group_by,
        theme_css=theme_css,
        custom_css_block=custom_css_block,
        enrichment_data_style=enrichment_data_style,
        enrichment_style=enrichment_style,
        title=title,
        group_link_template=validated_template,
    )

    out.write_text(html, encoding="utf-8")
