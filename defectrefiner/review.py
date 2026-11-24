"""Review draft tickets: a person approves, edits or rejects each one, and
every decision is logged.

    python -m defectrefiner.review            # go through docs/tickets/drafts
    python -m defectrefiner.review --report   # acceptance rate, edit rate, review time

The workflow runs to the end and saves a draft; nothing waits on a person.
An approved ticket moves to docs/tickets, a rejected one to
docs/tickets/rejected. In real use there's no answer key, so the log is the
quality measure: how often tickets are accepted as they are, and how much
people change.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import statistics
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from defectrefiner.bugs import DRAFTS_DIR, TICKETS_DIR
from defectrefiner.tickets import DATA_DIR

LOG = DATA_DIR / "reviews.jsonl"
UNREVIEWED = "Not reviewed by a person."
# An approval faster than this can't have been a read of the ticket. Quick
# approvals are counted apart, so the acceptance rate only reflects tickets
# someone read: the first 5 approvals took 0 to 2 seconds each.
QUICK_SECONDS = 15


def triage(ticket: str) -> str | None:
    found = re.search(r"\*\*Triage:\*\* (\w+)", ticket)
    return found.group(1) if found else None


def changed(before: str, after: str) -> float:
    """The share of lines a person changed, removed or added, out of the
    longer of the two versions."""
    a, b = before.splitlines(), after.splitlines()
    kept = sum(block.size for block in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
    return round(1 - kept / max(len(a), len(b), 1), 2)


def decide(draft: Path, decision: str, seconds: float, edited: str | None = None, reason: str | None = None,
           tickets_dir: Path = TICKETS_DIR, log: Path = LOG) -> dict:
    """Move the draft to where the decision puts it, and log the decision.
    decision is "approve", "edit" (approve the edited text) or "reject"."""
    text = draft.read_text()
    final = edited if decision == "edit" else text
    entry = {"ticket": draft.stem, "decision": decision, "triage": triage(text),
             "changed": changed(text, final), "seconds": round(seconds), "reason": reason,
             "at": datetime.now(UTC).isoformat(timespec="seconds")}
    if decision == "reject":
        target = tickets_dir / "rejected" / draft.name
    else:
        target = tickets_dir / draft.name
        note = "Edited and approved" if decision == "edit" else "Approved"
        final = final.replace(UNREVIEWED, f"{note} by a person on {entry['at'][:10]}.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(final)
    draft.unlink()
    with log.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def edit_in_editor(text: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(text)
    try:
        subprocess.run([*os.environ.get("EDITOR", "vi").split(), f.name], check=True)
        return Path(f.name).read_text()
    finally:
        os.unlink(f.name)


def show(ticket: str) -> str:
    """Triage and open questions first: they're what a person decides on."""
    body, _, questions = ticket.partition("## Open questions")
    first = ticket.splitlines()[0]
    return f"{first}\nTriage: {triage(ticket)}\nOpen questions:{questions.split('---')[0].rstrip()}\n\n{'=' * 70}\n{body}"


def review(drafts_dir: Path = DRAFTS_DIR, ask=input, editor=edit_in_editor, clock=time.monotonic, **where) -> list[dict]:
    decisions = []
    drafts = sorted(drafts_dir.glob("*.md"))
    for n, draft in enumerate(drafts, 1):
        print(f"\n[{n}/{len(drafts)}] {draft.name}\n{show(draft.read_text())}")
        start = clock()
        while True:
            choice = ask("[a]pprove, [e]dit, [r]eject, [s]kip, [q]uit? ").strip().lower()[:1]
            if choice == "a":
                decisions.append(decide(draft, "approve", clock() - start, **where))
            elif choice == "e":
                decisions.append(decide(draft, "edit", clock() - start, edited=editor(draft.read_text()), **where))
            elif choice == "r":
                reason = ask("Why? ").strip() or None
                decisions.append(decide(draft, "reject", clock() - start, reason=reason, **where))
            elif choice not in ("s", "q"):
                continue
            break
        if choice == "q":
            break
    return decisions


def report(log: Path = LOG) -> dict:
    entries = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    if not entries:
        return {"reviewed": 0}
    quick = [e for e in entries if e["decision"] == "approve" and e["seconds"] < QUICK_SECONDS]
    counted = [e for e in entries if e not in quick]

    def share(decision: str) -> str | None:
        count = sum(e["decision"] == decision for e in counted)
        return f"{count}/{len(counted)} ({count / len(counted):.0%})" if counted else None

    edits = [e["changed"] for e in counted if e["decision"] == "edit"]
    return {"reviewed": len(entries), "quick_approvals": len(quick),  # not counted in the shares below
            "approved_as_is": share("approve"), "approved_with_edits": share("edit"), "rejected": share("reject"),
            "median_share_changed_when_edited": statistics.median(edits) if edits else None,
            "median_review_seconds": statistics.median(e["seconds"] for e in entries),
            "rejection_reasons": [e["reason"] for e in entries if e["decision"] == "reject"]}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m defectrefiner.review", description=__doc__.splitlines()[0])
    parser.add_argument("--report", action="store_true", help="summarise the decisions logged so far")
    args = parser.parse_args(argv)
    if args.report:
        print(json.dumps(report(), indent=2))
    else:
        decisions = review()
        print(f"\n{len(decisions)} decided; {len(list(DRAFTS_DIR.glob('*.md')))} drafts left. Log: {LOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
