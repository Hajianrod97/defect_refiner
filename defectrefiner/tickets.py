"""Tickets. Real ones are issues whose checklist is the maintainer's own split
of the work: the checklist is the reference answer, so it is cut from the text
the agents see. Written ones carry planted traps (see Trap). The curated set
is data/tickets.json; data/candidates.json is what fetching found.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from defectrefiner.github import fetch_closed_issues

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CANDIDATES = DATA_DIR / "candidates.json"
TICKETS = DATA_DIR / "tickets.json"
MIN_ITEMS = 3  # fewer checklist items isn't much of a split

_CHECKLIST_ITEM = re.compile(r"^\s*[-*] \[[ xX]\] (.+?)\s*$", re.M)
# Issue-template boxes ("I have searched existing issues") aren't a split of the work.
_TEMPLATE = re.compile(r"I have (searched|confirmed|read)|Code of Conduct|minimal reproducible", re.I)


class Trap(BaseModel):
    """A problem planted in a written ticket, which a good grooming catches.
    Checked by keywords, no judge: some item in one of `where` must contain a
    word from every group in `all_of`, and each group in `separate` must
    appear in a different story."""
    kind: Literal["already_exists", "ambiguous", "two_in_one", "conflict"]
    expect: str  # what a good grooming says, in plain words
    where: list[str] = ["open_questions", "risks"]
    all_of: list[list[str]] = []
    separate: list[list[str]] = []


class Ticket(BaseModel):
    id: str  # "ds-1855" for a real issue, "w-01" for a written one
    source: Literal["real", "written"]
    repo: str
    title: str
    body: str  # what the agents see: for real issues, rewritten without the checklist
    created_at: datetime  # the code is groomed as of this moment
    url: str | None = None
    reference: list[str]  # how the work was really split (real) or should be (written)
    traps: list[Trap] = []


def checklist(body: str) -> list[str]:
    return _CHECKLIST_ITEM.findall(body or "")


def strip_checklist(body: str) -> str:
    text = _CHECKLIST_ITEM.sub("", body or "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def is_candidate(issue: dict) -> bool:
    """A closed, completed issue (not a PR) whose body splits the work into
    at least MIN_ITEMS checklist items. Release checklists are chores, not
    feature work, so they're left out."""
    body = issue.get("body") or ""
    return ("pull_request" not in issue
            and issue.get("state_reason") == "completed"
            and len(checklist(body)) >= MIN_ITEMS
            and not _TEMPLATE.search(body)
            and not issue["title"].lower().startswith("release"))


def to_ticket(repo: str, issue: dict) -> Ticket:
    return Ticket(id=f"ds-{issue['number']}", source="real", repo=repo, title=issue["title"],
                  body=strip_checklist(issue["body"]), created_at=issue["created_at"],
                  url=issue["html_url"], reference=checklist(issue["body"]))


def save(tickets: list[Ticket], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([t.model_dump(mode="json") for t in tickets], indent=2) + "\n")


def load(path: Path | None = None) -> list[Ticket]:
    return [Ticket.model_validate(t) for t in json.loads((path or TICKETS).read_text())]


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m defectrefiner.tickets", description="Fetch candidate tickets.")
    parser.add_argument("repo", nargs="?", default="simonw/datasette")
    parser.add_argument("--out", type=Path, default=CANDIDATES)
    args = parser.parse_args(argv)

    issues = fetch_closed_issues(args.repo)
    tickets = sorted((to_ticket(args.repo, i) for i in issues if is_candidate(i)), key=lambda t: int(t.id.split("-")[1]))
    save(tickets, args.out)
    print(f"{len(issues)} closed issues, {len(tickets)} with a checklist split -> {args.out}")
    for t in tickets:
        print(f"  {t.id}  {len(t.reference)} items  {t.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
