"""wiki_weaver.aitl.audit -- append-only, inspectable record of every gate
decision the proxy made.

docs/DESIGN.md Sec 7 guardrail: "Never let the proxy self-certify ...
Proxy decisions are provisional and logged; `wiki-weaver review` shows you
what was decided on your behalf since you last looked." This module is the
"logged" half of that guardrail.

pipeline/CLI-CONTRACT.md's ``wiki_weaver.review`` entry (added post-council,
"the user's only recourse against a proxy acting on their behalf") already
names the exact path used here -- ``.ai/gate-decisions.jsonl`` -- so this
follows the existing contract rather than inventing a new one. Building
``wiki_weaver.review`` itself is deliberately OUT of scope for this change
(see the AITL proxy delivery notes): the format below is what it will read
whenever it is built.

One line per gate interaction -- INCLUDING refusals and give-ups,
*especially* those. An undiagnosable silent refusal is exactly as bad as an
invented answer: both leave a human unable to tell what happened on their
behalf.

Never cleaned up by any ``persist`` step (unlike other ``.ai/`` breadcrumbs,
e.g. ``init.persist``'s ``rm -f .ai/interview-notes.md ...``) -- this file
is the durable audit trail, not per-invocation scratch, even though it
lives under ``.ai/`` (matching the location the existing
``wiki_weaver.review`` contract already specifies).
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from wiki_weaver.lib import WikiRoot, atomic_append_jsonl


def gate_decisions_path(wiki_root: str | Path) -> Path:
    return WikiRoot(wiki_root).gate_decisions_file


def persona_digest(persona_text: str) -> str:
    """Short, stable fingerprint of the EXACT persona text that produced a
    decision. Lets a human verify a past decision against the character
    document as it existed at decision time, even after ``lens/persona.md``
    is later edited -- "what in the character document justified it" has to
    survive the document changing out from under it.
    """
    return hashlib.sha256(persona_text.encode("utf-8")).hexdigest()[:12]


def record_decision(
    wiki_root: str | Path,
    *,
    stage: str,
    gate_type: str,
    question: str,
    choices: list[str] | None,
    decision: str,
    reason: str,
    grounding: str | None = None,
    answer_text: str | None = None,
    choice_key: str | None = None,
    choice_label: str | None = None,
    persona_text: str | None = None,
    corrections_text: str | None = None,
    mode: str | None = None,
) -> None:
    """Append one audit entry.

    ``decision`` is one of ``"answered" | "give_up" | "refused" | "error"``
    -- "refused" is the deterministic pre-check (persona missing/invalid,
    no backend call made); "give_up" is the backend's own judgment call;
    "error" is an unexpected exception from the backend. Durable (fsynced)
    before returning, per the ordering law in CLI-CONTRACT.md: the decision
    is committed to disk before the pipeline is allowed to act on it.

    ``mode`` is which ``--gate-mode`` (or per-site ``--gate-mode-for``
    override -- see ``wiki_weaver.aitl.dispatch``) answered THIS gate:
    ``"fail" | "auto-approve" | "console" | "proxy"``. Optional (defaults
    to ``None``) so existing callers that predate per-site gate-mode
    selection keep working unchanged; every current call site
    (``ProxyInterviewer`` and ``wiki_weaver.aitl.dispatch
    .AuditingInterviewer``) passes it explicitly. Without this field, an
    A/B run across gate configurations cannot attribute which mode
    produced which decision after the fact -- exactly the attribution gap
    that already corrupted one experiment in this project.

    ``corrections_text``, when given, is the standing ``lens/corrections/``
    text (``wiki_weaver.aitl.corrections.load_corrections``) that was in
    effect for this decision -- hashed the SAME way ``persona_text`` is
    (see ``persona_digest``), so ``corrections_digest`` lets a reader of
    ``.ai/gate-decisions.jsonl`` tell, per entry, whether a standing
    correction was present, and -- by comparing digests across two runs of
    the SAME gate -- whether it was the SAME correction. ``None`` (the
    default) means no correction was in effect, the common case, never
    recorded as any kind of failure.
    """
    entry: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stage": stage,
        "gate_type": gate_type,
        "question": question,
        "choices": choices,
        "decision": decision,
        "reason": reason,
        "grounding": grounding,
        "answer_text": answer_text,
        "choice_key": choice_key,
        "choice_label": choice_label,
        "persona_digest": persona_digest(persona_text) if persona_text else None,
        "corrections_digest": persona_digest(corrections_text) if corrections_text else None,
        "mode": mode,
    }
    atomic_append_jsonl(gate_decisions_path(wiki_root), entry)
