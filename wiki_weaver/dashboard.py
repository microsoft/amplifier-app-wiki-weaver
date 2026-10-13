"""Stage 1 dashboard: a minimal, self-contained HTML list of pages. No model."""

from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import quote

from .lib import page_files
from .sources import page_frontmatter


def build_dashboard(
    corpus: Path,
    out: Path,
    group_by: str = "type",
    link_template: str | None = None,
    theme: str | None = None,
) -> Path:
    title = corpus.name
    theme_path = Path(theme) if theme else corpus / ".wiki" / "dashboard" / "theme.json"
    if theme_path.is_file():
        try:
            title = json.loads(theme_path.read_text()).get("title", title)
        except (ValueError, AttributeError):
            pass
    if link_template and not link_template.startswith(("http://", "https://")):
        link_template = None
    groups: dict[str, list[tuple[str, str]]] = {}
    for p in page_files(corpus):
        fm = page_frontmatter(p) or {}
        val = fm.get(group_by)
        keys = [str(v) for v in val] if isinstance(val, list) else [str(val or "(none)")]
        for k in keys:
            groups.setdefault(k, []).append((str(fm.get("title") or p.stem), p.name))
    parts = [
        "<!doctype html><meta charset='utf-8'>",
        f"<title>{html.escape(title)}</title><h1>{html.escape(title)}</h1>",
    ]
    for g in sorted(groups):
        head = html.escape(g)
        if link_template:
            href = html.escape(link_template.replace("{group}", quote(g, safe="")))
            head = f"<a href='{href}'>{head}</a>"
        parts.append(f"<h2>{head}</h2><ul>")
        for t, name in sorted(groups[g]):
            parts.append(f"<li><a href='{html.escape(quote(name))}'>{html.escape(t)}</a></li>")
        parts.append("</ul>")
    out = Path(out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts) + "\n")
    return out
