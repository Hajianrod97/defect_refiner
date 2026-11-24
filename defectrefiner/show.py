"""Read groomings as a person would.

    python -m defectrefiner.show runs/team-facilitator-claude-haiku-5-5 ds-1855
    python -m defectrefiner.show runs/team-facilitator-claude-haiku-5-5 --markdown [DIR]

The first prints one grooming. The second writes every grooming in the run as
a Markdown file, plus an index, to DIR (default: <run>/markdown), for
browsing in an editor or a notes app. Each file ends with the maintainer's
split, the answer key the agents never saw.
"""

from __future__ import annotations

import json
from pathlib import Path

from defectrefiner.grooming import Grooming
from defectrefiner.score import TicketScore
from defectrefiner.tickets import Ticket, load


def to_markdown(ticket: Ticket, result: dict, scores: dict[str, TicketScore]) -> str:
    """One grooming as Markdown: the ticket, the stories, questions, risks,
    the team's log if there is one, scores, and the reference split."""
    out = [f"# {ticket.id} · {ticket.title}", "", *[f"> {line}" if line else ">" for line in ticket.body.splitlines()], ""]
    meta = [result.get("system", ""), result.get("model", ""), f"{result.get('seconds', '?')} s"]
    if result.get("cost_usd") is not None:
        meta.append(f"${result['cost_usd']:.4f}")
    out += [f"*{' · '.join(m for m in meta if m)}*", ""]
    if "error" in result:
        return "\n".join([*out, f"**Failed:** {result['error']}", ""])

    grooming = Grooming.model_validate(result["grooming"])
    out.append("## Stories")
    for n, s in enumerate(grooming.stories, 1):
        points = f"{s.points} points" if s.points is not None else "not estimated"
        out += ["", f"### {n}. {s.title} ({points})", "", f"*{s.user_story}*", ""]
        out += [f"- [ ] {c}" for c in s.acceptance_criteria]
        if s.dependencies:
            out += ["", f"Depends on: {'; '.join(s.dependencies)}"]
    for heading, items in (("Open questions", grooming.open_questions), ("Risks", grooming.risks)):
        if items:
            out += ["", f"## {heading}", "", *[f"- {x}" for x in items]]
    if result.get("log"):
        out += ["", "## Team log", "", *[f"{n}. {line}" for n, line in enumerate(result["log"], 1)]]
    if result.get("tool_calls"):
        out += ["", "## Code looked at", "", *[f"- `{c}`" for c in result["tool_calls"]]]

    out += ["", "## Maintainer's split (reference, not shown to the agents)", ""]
    for n, item in enumerate(ticket.reference, 1):
        verdicts = " ".join(f"{judge}: {s.coverage[n - 1].verdict}" for judge, s in scores.items()
                            if len(s.coverage) >= n)
        out.append(f"- {item}" + (f"  \n  *{verdicts}*" if verdicts else ""))
    for judge, s in scores.items():
        testable = sum(s.testable) / len(s.testable) if s.testable else 0
        out.append(f"\n*{judge}: coverage {s.coverage_rate():.0%}, testable criteria {testable:.0%}*")
    return "\n".join(out) + "\n"


def _scores(result_path: Path) -> dict[str, TicketScore]:
    """{judge: its score} for one grooming."""
    found = {}
    for p in sorted(result_path.parent.glob(f"{result_path.stem}.score.*.json")):
        judge = p.name.removeprefix(f"{result_path.stem}.score.").removesuffix(".json")
        found[judge] = TicketScore.model_validate_json(p.read_text())
    return found


def export(run: Path, out: Path) -> int:
    """Write every grooming in `run` as Markdown to `out`, with an index.
    Returns how many were written."""
    tickets = {t.id: t for t in load()}
    out.mkdir(parents=True, exist_ok=True)
    index = [f"# {run.name}", "", "| Ticket | Stories | Time | Coverage |", "|---|---|---|---|"]
    count = 0
    for path in sorted(run.glob("*/[0-9]*.json")):
        if ".score." in path.name:
            continue
        result, ticket = json.loads(path.read_text()), tickets[path.parent.name]
        scores = _scores(path)
        name = f"{ticket.id}-{path.stem}.md"
        (out / name).write_text(to_markdown(ticket, result, scores))
        stories = len(result["grooming"]["stories"]) if "grooming" in result else "failed"
        coverage = ", ".join(f"{s.coverage_rate():.0%}" for s in scores.values()) or "-"
        index.append(f"| [{ticket.id} · {ticket.title}]({name}) | {stories} | {result.get('seconds', '?')} s | {coverage} |")
        count += 1
    (out / "index.md").write_text("\n".join(index) + "\n")
    return count


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m defectrefiner.show", description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, help="a run directory under runs/")
    parser.add_argument("ticket", nargs="?", help="a ticket id, e.g. ds-1855")
    parser.add_argument("--markdown", nargs="?", const="", metavar="DIR", help="export the whole run as Markdown")
    parser.add_argument("-n", type=int, default=1, help="which repeat of the ticket (default 1)")
    args = parser.parse_args(argv)

    if args.markdown is not None:
        out = Path(args.markdown) if args.markdown else args.run / "markdown"
        print(f"Wrote {export(args.run, out)} groomings to {out}/ (start at index.md)")
        return 0
    if not args.ticket:
        parser.error("give a ticket id, or --markdown")
    path = args.run / args.ticket / f"{args.n}.json"
    if not path.exists():
        parser.error(f"no grooming at {path}")
    ticket = {t.id: t for t in load()}[args.ticket]
    print(to_markdown(ticket, json.loads(path.read_text()), _scores(path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
