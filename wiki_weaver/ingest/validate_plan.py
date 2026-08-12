"""wiki_weaver.ingest.validate_plan -- deterministic gate for arm C's page-plan.

Arm C's ``plan_pages`` node is an LLM: it decides what pages this source
should create or update and writes that decision to ``.ai/page-plan.json``.
This tool is the code-side check that a missing or malformed plan can
never silently proceed into ``weave`` -- "do not let a malformed plan
silently proceed" is the whole point of this node existing.

Same shape as ``wiki_weaver.ingest.validate``/``select_source``: ordinary,
anticipated outcomes (no plan file, bad JSON, wrong shape) are ROUTED via a
one-line stdout sentinel and exit 0 -- they are not Python exceptions and
must not be. Only a genuine unexpected crash (e.g. an unreadable
``--wiki-root``) should propagate as a non-zero exit with a traceback,
distinguishing a real tool failure from an ordinary "the LLM wrote a bad
plan" finding, per the same reasoning documented in validate.py.

Usage:
    python3 -m wiki_weaver.ingest.validate_plan --wiki-root <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wiki_weaver.lib import WikiRoot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.validate_plan")
    parser.add_argument("--wiki-root", required=True)
    return parser


def find_plan_issues(plan: object) -> list[str]:
    """Return a list of problems with ``plan``; empty list == well-formed.

    Required shape: ``{"create": [{"path", "title", "why"}, ...], "update": [str, ...]}``.
    An empty plan (no create, no update entries at all) is itself a problem --
    a plan that decides nothing is not a decision, it's a no-op the graph
    should not silently execute as "success".
    """
    if not isinstance(plan, dict):
        return ["page-plan.json must be a JSON object with 'create' and 'update' keys"]

    issues: list[str] = []

    create = plan.get("create", [])
    if not isinstance(create, list):
        issues.append("'create' must be a list")
    else:
        for i, entry in enumerate(create):
            if not isinstance(entry, dict):
                issues.append(f"create[{i}] must be an object with 'path', 'title', 'why'")
                continue
            for field in ("path", "title", "why"):
                value = entry.get(field)
                if not isinstance(value, str) or not value.strip():
                    issues.append(f"create[{i}].{field} is missing or empty")

    update = plan.get("update", [])
    if not isinstance(update, list):
        issues.append("'update' must be a list")
    else:
        for i, entry in enumerate(update):
            if not isinstance(entry, str) or not entry.strip():
                issues.append(f"update[{i}] must be a non-empty page filename")

    if isinstance(create, list) and isinstance(update, list) and not create and not update:
        issues.append("plan has no 'create' or 'update' entries -- an empty plan decides nothing")

    return issues


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))
    plan_path = wr.page_plan_file

    if not plan_path.is_file():
        print(f"{plan_path} does not exist -- plan_pages must run first", file=sys.stderr)
        print("plan_bad")
        return 0

    text = plan_path.read_text(encoding="utf-8")
    try:
        plan = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"{plan_path} is not valid JSON: {exc}", file=sys.stderr)
        print("plan_bad")
        return 0

    issues = find_plan_issues(plan)
    if issues:
        for issue in issues:
            print(f"page-plan.json: {issue}", file=sys.stderr)
        print("plan_bad")
        return 0

    print(f"plan ok: {len(plan.get('create', []))} to create, {len(plan.get('update', []))} to update", file=sys.stderr)
    print("plan_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
