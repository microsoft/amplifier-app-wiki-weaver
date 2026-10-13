"""One-off corpus migration: filename citations -> source-id citations.

    [<source-filename>: "quote"]  ->  [s<source_id>: "quote"]

The ledger is the map: a filename resolves to its most recent converged source_id (the
version now in _sources/). Nothing is changed if any cited filename is missing from the
ledger. Run `wiki-weaver lint` afterwards: it must report 0 citation errors.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from .ledger import read_rows
from .lib import page_files
from .sources import read_text

OLD_CITE_RE = re.compile(r'\[([^\[\]\n]+?\.md): "([^"\n]+)"\]')


def filename_ids(wiki: Path) -> dict[str, int]:
    ids: dict[str, int] = {}
    for r in read_rows(wiki):
        if r.get("converged") and isinstance(r.get("source_id"), int):
            ids[r["source"]] = max(ids.get(r["source"], 0), r["source_id"])
    return ids


def transform_citations(wiki: Path, apply: bool = True) -> dict:
    wiki = Path(wiki)
    ids = filename_ids(wiki)
    unknown: set[str] = set()
    plan: dict[Path, str] = {}
    total = 0
    for p in page_files(wiki):
        text = read_text(p)
        hits = list(OLD_CITE_RE.finditer(text))
        if not hits:
            continue
        total += len(hits)
        unknown |= {m.group(1).strip() for m in hits if m.group(1).strip() not in ids}
        plan[p] = OLD_CITE_RE.sub(
            lambda m: f'[s{ids.get(m.group(1).strip(), 0)}: "{m.group(2)}"]', text
        )
    report = {"pages": len(plan), "citations": total, "unknown_filenames": sorted(unknown)}
    if unknown or not apply:
        report["applied"] = False
        return report
    for p, text in plan.items():
        p.write_text(text, encoding="utf-8")
    report["applied"] = True
    return report


if __name__ == "__main__":
    import json

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    dry = "--dry-run" in sys.argv
    rep = transform_citations(target, apply=not dry)
    print(json.dumps(rep, indent=2))
    sys.exit(1 if rep["unknown_filenames"] else 0)
