"""Scoring a grooming against a ticket's answers.

- Traps: keyword checks, no judge (see tickets.Trap).
- Coverage: for each reference item, a judge says whether the stories cover
  it. One narrow question per item: small models answer those far more
  reliably than "rate this grooming 1-10".
- Criteria criteria: a judge says whether each story's acceptance criteria
  could be checked by a tester.
- Estimate spread: how much total points vary when a ticket is groomed again.
"""

from __future__ import annotations

import re
import statistics
from typing import Literal

from pydantic import BaseModel, Field

from defectrefiner.grooming import Grooming, capped
from defectrefiner.llm import Ask
from defectrefiner.tickets import Ticket, Trap


def _has(text: str, words: list[str]) -> bool:
    """Whether any of the words starts a word in text ("rate" matches "rate-limit", not "generate")."""
    return any(re.search(r"(?<![a-z0-9_])" + re.escape(w.lower()), text.lower()) for w in words)


def trap_caught(trap: Trap, grooming: Grooming) -> bool:
    if trap.separate:
        # Each group needs a story of its own: one whose title names it and none of
        # the others. Titles, not full text: a rate-limiting story's criteria can
        # fairly say "count requests" without being about row counts.
        stories = [story.title for story in grooming.stories]
        return all(any(_has(s, group) and not any(_has(s, other) for other in trap.separate if other is not group)
                       for s in stories)
                   for group in trap.separate)
    items = [item for field in trap.where for item in grooming.items(field)]
    return any(all(_has(item, group) for group in trap.all_of) for item in items)


COVERAGE_SYSTEM = """You check whether a team's plan covers one piece of required work.
Answer "yes" if some story in the plan clearly includes that work, "partly" if a story touches it but leaves out an important part, and "no" if nothing in the plan does it. Judge the substance, not the wording. Plans may also contain extra work; ignore it."""

COVERAGE_USER = """<ticket>
{ticket}
</ticket>

<plan>
{plan}
</plan>

<required_work>
{item}
</required_work>

Does the plan cover the required work?"""


# Capped so a small local judge can't repeat itself until it runs out of tokens
# before reaching the verdict, which comes after the reason.
Reason = capped(300)


class Coverage(BaseModel):
    reason: Reason = Field(description="One sentence: which story covers it, or what is missing.")
    verdict: Literal["yes", "partly", "no"]


TESTABLE_SYSTEM = """You review acceptance criteria for one user story. They are testable if a tester could check each one and say pass or fail without guessing what was meant: concrete behaviour, inputs or outputs, not "works well" or "is user friendly"."""


class Criteria(BaseModel):
    reason: Reason = Field(description="One sentence.")
    testable: bool


class TicketScore(BaseModel):
    ticket_id: str
    coverage: list[Coverage]  # one per reference item, in order
    testable: list[bool]  # one per story
    traps: list[bool]  # one per trap
    points: int

    def coverage_rate(self) -> float:
        """Share of reference items covered, a "partly" counting half."""
        weights = {"yes": 1.0, "partly": 0.5, "no": 0.0}
        return sum(weights[c.verdict] for c in self.coverage) / len(self.coverage) if self.coverage else 0.0


def judge_coverage(ticket: Ticket, grooming: Grooming, ask: Ask) -> list[Coverage]:
    plan = grooming.render()
    return [ask(COVERAGE_SYSTEM, COVERAGE_USER.format(ticket=f"{ticket.title}\n{ticket.body}", plan=plan, item=item),
                Coverage)
            for item in ticket.reference]


def judge_testable(grooming: Grooming, ask: Ask) -> list[bool]:
    out = []
    for story in grooming.stories:
        criteria = "\n".join(f"- {c}" for c in story.acceptance_criteria) or "(none)"
        out.append(ask(TESTABLE_SYSTEM, f"Story: {story.title}\n{story.user_story}\n\nAcceptance criteria:\n{criteria}",
                       Criteria).testable)
    return out


def score(ticket: Ticket, grooming: Grooming, ask: Ask) -> TicketScore:
    return TicketScore(ticket_id=ticket.id, coverage=judge_coverage(ticket, grooming, ask),
                       testable=judge_testable(grooming, ask), traps=[trap_caught(t, grooming) for t in ticket.traps],
                       points=grooming.total_points())


_CHORE_TITLE = re.compile(r"\b(tests?|tested|testing|unit tests|documentation|docs|document|documented)\b", re.I)


def chore_stories(grooming: Grooming, ticket_title: str = "") -> int:
    """Stories that are only tests or docs, judged by their title: work that
    belongs in another story's acceptance criteria. Not counted when the
    ticket itself is about tests or docs, where such stories are the work."""
    if _CHORE_TITLE.search(ticket_title):
        return 0
    return sum(bool(_CHORE_TITLE.search(s.title)) and not re.search(r"\b(add|support|implement|allow)\b", s.title, re.I)
               for s in grooming.stories)


def points_spread(totals: list[int]) -> float | None:
    """(max - min) / median of a ticket's total points over repeated runs:
    0 means every run agreed. None when there's nothing to compare."""
    if len(totals) < 2 or statistics.median(totals) == 0:
        return None
    return (max(totals) - min(totals)) / statistics.median(totals)


# ----------------------------------------------------------------------
# Checking the judge before trusting it.

GOOD_CRITERIA = [
    ("Export a table as CSV", ["GET /db/table.csv returns text/csv with a header row", "Filters in the URL apply to the exported rows", "Values containing commas are quoted"]),
    ("Rate-limit the write API", ["A token making more than 100 write requests in a minute gets HTTP 429", "The 429 response includes a Retry-After header", "Read requests are not limited"]),
    ("Dark mode", ["With the OS set to dark, pages use the dark palette on first load", "Text contrast is at least 4.5:1 in both themes", "Choosing a theme in settings overrides the OS setting and persists after reload"]),
    ("Drop table API", ["POST /db/table/-/drop without confirm returns the row count and a confirm prompt", "With confirm=true the table is gone and the response is 200", "A token without drop-table permission gets 403"]),
]
BAD_CRITERIA = [
    ("Export a table as CSV", ["Export works well", "Users are happy with the format"]),
    ("Rate-limit the write API", ["The API is protected from abuse", "Performance is good"]),
    ("Dark mode", ["Looks nice in the dark", "Is user friendly"]),
    ("Drop table API", ["Dropping tables is safe", "Handles errors properly"]),
]


def judge_check(tickets: list[Ticket], ask: Ask) -> dict:
    """How often the judge is right where the answer is known."""
    from defectrefiner.grooming import Story

    coverage = {"kept": {"yes": 0, "partly": 0, "no": 0}, "dropped": {"yes": 0, "partly": 0, "no": 0}}
    for ticket in tickets:
        keep = len(ticket.reference) // 2 + len(ticket.reference) % 2
        plan = Grooming(stories=[Story(title=item, user_story="", acceptance_criteria=[]) for item in ticket.reference[:keep]])
        for n, verdict in enumerate(judge_coverage(ticket, plan, ask)):
            coverage["kept" if n < keep else "dropped"][verdict.verdict] += 1
    testable = {"good": 0, "bad": 0}
    for kind, examples in (("good", GOOD_CRITERIA), ("bad", BAD_CRITERIA)):
        stories = [Story(title=t, user_story="", acceptance_criteria=c) for t, c in examples]
        testable[kind] = sum(judge_testable(Grooming(stories=stories), ask))
    return {"coverage": coverage, "testable": testable, "testable_of": len(GOOD_CRITERIA)}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import time

    from defectrefiner.llm import chat_model, structured
    from defectrefiner.tickets import load

    parser = argparse.ArgumentParser(prog="python -m defectrefiner.score", description="Check the judge on known answers.")
    parser.add_argument("--provider", choices=["ollama", "anthropic"], default="ollama")
    parser.add_argument("--model")
    parser.add_argument("--limit", type=int, default=10, help="real tickets to use")
    args = parser.parse_args(argv)

    tickets = [t for t in load() if t.source == "real" and len(t.reference) >= 3][: args.limit]
    start = time.monotonic()
    effort = "low" if args.provider == "anthropic" else None  # same setting as the report's judge
    result = judge_check(tickets, structured(chat_model(args.provider, args.model, effort=effort)))
    c = result["coverage"]
    print(f"Judge: {args.provider} {args.model or ''}, {len(tickets)} tickets, {time.monotonic() - start:.0f}s")
    print(f"  work in the plan    -> yes {c['kept']['yes']}, partly {c['kept']['partly']}, no {c['kept']['no']}")
    print(f"  work left out       -> yes {c['dropped']['yes']}, partly {c['dropped']['partly']}, no {c['dropped']['no']}")
    print(f"  testable criteria: good judged testable {result['testable']['good']}/{result['testable_of']}, "
          f"bad judged testable {result['testable']['bad']}/{result['testable_of']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
