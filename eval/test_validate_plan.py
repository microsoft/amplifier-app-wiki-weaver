"""wiki_weaver.ingest.validate_plan -- arm C's deterministic gate on the
LLM-authored .ai/page-plan.json. "Do not let a malformed plan silently
proceed" is the entire point of this subcommand."""

from __future__ import annotations

import json

from wiki_weaver.ingest import validate_plan
from wiki_weaver.lib import WikiRoot, ensure_dir


def _wr(wiki_root) -> WikiRoot:
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    return wr


def _write_plan(wr: WikiRoot, payload) -> None:
    if isinstance(payload, str):
        wr.page_plan_file.write_text(payload, encoding="utf-8")
    else:
        wr.page_plan_file.write_text(json.dumps(payload), encoding="utf-8")


def test_missing_plan_file_is_plan_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    code = validate_plan.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "plan_bad"


def test_invalid_json_is_plan_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_plan(wr, "{not valid json")
    code = validate_plan.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "plan_bad"


def test_plan_that_is_not_an_object_is_plan_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_plan(wr, ["create", "a", "list", "not", "an", "object"])
    code = validate_plan.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "plan_bad"


def test_empty_plan_no_create_no_update_is_plan_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_plan(wr, {"create": [], "update": []})
    code = validate_plan.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "plan_bad"


def test_create_entry_missing_required_field_is_plan_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_plan(wr, {"create": [{"path": "new-page.md", "title": "New Page"}], "update": []})
    code = validate_plan.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "plan_bad"


def test_update_entry_not_a_string_is_plan_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_plan(wr, {"create": [], "update": [{"path": "existing.md"}]})
    code = validate_plan.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "plan_bad"


def test_well_formed_create_only_plan_is_plan_ok(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_plan(
        wr,
        {
            "create": [{"path": "new-topic.md", "title": "New Topic", "why": "source introduces a new entity"}],
            "update": [],
        },
    )
    code = validate_plan.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "plan_ok"


def test_well_formed_update_only_plan_is_plan_ok(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_plan(wr, {"create": [], "update": ["existing-page.md"]})
    code = validate_plan.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "plan_ok"


def test_well_formed_mixed_plan_is_plan_ok(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_plan(
        wr,
        {
            "create": [{"path": "new-topic.md", "title": "New Topic", "why": "new entity"}],
            "update": ["index.md", "overview.md"],
        },
    )
    code = validate_plan.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "plan_ok"


def test_stdout_is_a_single_line_sentinel(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_plan(wr, {"create": [], "update": ["index.md"]})
    validate_plan.main(["--wiki-root", str(wr.root)])
    out = capsys.readouterr().out.strip()
    assert len(out.splitlines()) == 1
    assert out == "plan_ok"


def test_find_plan_issues_directly_for_non_dict_message():
    issues = validate_plan.find_plan_issues("not a dict")
    assert len(issues) == 1
    assert "JSON object" in issues[0]
