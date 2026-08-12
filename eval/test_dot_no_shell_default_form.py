"""Regression guard: the ${var:-default} (brace-with-shell-default) form must
never appear in a pipeline .dot file's tool_command/tool_env-adjacent
attribute strings.

THE DEFECT (proven twice now -- see pipeline/CLI-CONTRACT.md and the
2026-07-25 "$py substitution" fix commit): the attractor engine's
substitution (amplifier_module_loop_pipeline.substitution.substitute_context)
matches ``${key}`` by treating EVERYTHING between the braces as the lookup
key -- so ``${max_sources_per_run:-200}`` looks up the context key
``"max_sources_per_run:-200"`` (never present), leaves the token
UNCHANGED, and the literal text is then handed to bash. Bash's own
unset-variable expansion resolves the ``:-default`` suffix itself --
ALWAYS applying the shell default and discarding whatever the pipeline
(or a --param the caller supplied) actually computed. ``${var:-default}``
and ``--param``/in-context values are therefore mutually exclusive: you get
the shell default or the real value, never both, and the failure is
completely silent (no error, no warning -- just the wrong number).

THE FIX: use the bare ``$var`` form instead. The engine's substitution DOES
handle bare ``$var`` correctly (phase 2 of substitute_context). A bare
``$var`` whose key is absent from context is left as a literal ``$var``
token, which bash then expands via its OWN unset-variable rule to an empty
string -- so the receiving Python CLI must accept "" and apply its default
there (see wiki_weaver.lib.int_or_default), which keeps the default in
exactly ONE place instead of duplicated between the .dot shell expression
and the Python argparse default.

This test scans every .dot file under pipeline/ and evals/arms/ (glob, not a
hardcoded list) so a reintroduced instance -- in an existing file or a brand
new pipeline -- fails loud in CI instead of silently disabling a parameter.
"""

from __future__ import annotations

import re
from pathlib import Path

# Matches `${identifier:-...}` -- the brace-with-shell-default form. Does NOT
# match a bare `${identifier}` (no `:-` suffix), which the engine substitutes
# correctly and is not part of this defect.
_SHELL_DEFAULT_FORM_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_.]*:-[^}]*\}")

REPO_ROOT = Path(__file__).parent.parent
SCAN_DIRS = ("pipeline", "evals/arms")


def _dot_files() -> list[Path]:
    files: list[Path] = []
    for rel_dir in SCAN_DIRS:
        scan_dir = REPO_ROOT / rel_dir
        if scan_dir.is_dir():
            files.extend(sorted(scan_dir.rglob("*.dot")))
    return files


def test_at_least_one_dot_file_is_scanned():
    """Sanity check: if this ever returns zero, the test below is vacuously
    passing and silently not guarding anything -- fail loud instead."""
    assert _dot_files(), f"no .dot files found under {SCAN_DIRS} relative to {REPO_ROOT}"


def test_no_shell_default_form_in_any_pipeline_dot_file():
    """Fails loud if `${var:-default}` reappears in ANY .dot file under
    pipeline/ or evals/arms/ -- this exact form silently discards --param
    values and in-context state (see module docstring). Use the bare `$var`
    form instead, with the default applied on the Python side (e.g.
    wiki_weaver.lib.int_or_default) -- never duplicated in the shell
    expression."""
    violations: list[str] = []
    for dot_file in _dot_files():
        text = dot_file.read_text(encoding="utf-8")
        for match in _SHELL_DEFAULT_FORM_RE.finditer(text):
            line_number = text.count("\n", 0, match.start()) + 1
            violations.append(f"{dot_file.relative_to(REPO_ROOT)}:{line_number}: {match.group(0)!r}")

    assert not violations, (
        "Forbidden ${var:-default} (shell-default) substitution form found:\n"
        + "\n".join(f"  - {v}" for v in violations)
        + "\n\n"
        "WHY THIS IS FORBIDDEN: the attractor engine's substitution treats "
        "everything between ${ and } as the context-key lookup, so "
        "${var:-default} looks up a key that never exists, leaves the token "
        "unsubstituted, and bash's own unset-variable expansion silently "
        "applies the shell default -- discarding any --param or in-context "
        "value the pipeline actually computed. This has caused two separate "
        "production incidents (see pipeline/CLI-CONTRACT.md).\n\n"
        'WHAT TO USE INSTEAD: the bare $var form (e.g. "$max_sources_per_run" '
        'not "${max_sources_per_run:-200}"). The engine substitutes bare '
        "$var correctly. When the key is absent, $var is left literal and "
        "bash expands it to an empty string -- handle that on the Python "
        "side with a single default (see wiki_weaver.lib.int_or_default), "
        "never in the shell expression."
    )
