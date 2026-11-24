"""Grade groomings by hand, and see how often the judge agrees with you.

    python -m defectrefiner.grade runs/prompt-qwen2.5-coder-7b runs/agent-qwen2.5-coder-7b --count 15
    python -m defectrefiner.grade --agreement claude-haiku-5-5

You answer the judge's coverage question for each reference item: is that
work in the plan (y), partly (p), or not (n)? Grades are saved as you go to
data/grades.json, so you can stop and carry on later.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from defectrefiner.grooming import Grooming
from defectrefiner.run import score_path
from defectrefiner.score import TicketScore
from defectrefiner.tickets import DATA_DIR, load

GRADES = DATA_DIR / "grades.json"
ANSWERS = {"y": "yes", "p": "partly", "n": "no"}


def load_grades(path: Path = GRADES) -> dict[str, list[str]]:
    """{path of a grooming relative to the repo: one verdict per reference item}."""
    return json.loads(path.read_text()) if path.exists() else {}


def agreement(grades: dict[str, list[str]], root: Path, judge: str) -> dict:
    """How often the judge's verdict matches yours, item by item. Your "partly"
    and its "partly" match only each other."""
    same = total = 0
    judge_generous = judge_strict = 0  # it said covered when you didn't, and the reverse
    order = {"no": 0, "partly": 1, "yes": 2}
    for key, mine in grades.items():
        cache = score_path(root / key, judge)
        if not cache.exists():
            continue
        theirs = [c.verdict for c in TicketScore.model_validate_json(cache.read_text()).coverage]
        for a, b in zip(mine, theirs, strict=True):
            total += 1
            same += a == b
            judge_generous += order[b] > order[a]
            judge_strict += order[b] < order[a]
    return {"items": total, "agree": round(same / total, 2) if total else None,
            "judge_more_generous": judge_generous, "judge_stricter": judge_strict}


def grade(run_dirs: list[Path], count: int, root: Path, seed: int = 0) -> None:
    tickets = {t.id: t for t in load()}
    grades = load_grades()
    paths = [p for d in run_dirs for p in sorted(d.glob("*/[0-9].json"))]  # results, not scores
    random.Random(seed).shuffle(paths)  # mix systems and tickets, the same way each time
    todo = [p for p in paths if str(p.relative_to(root)) not in grades and "grooming" in json.loads(p.read_text())]
    for path in todo[: max(0, count - len(grades))]:
        result = json.loads(path.read_text())
        ticket = tickets[result["ticket_id"]]
        print("\n" + "=" * 70 + f"\n{ticket.title}\n{ticket.body}\n" + "-" * 70)
        print(Grooming.model_validate(result["grooming"]).render())  # the system's name stays hidden
        verdicts = []
        for n, item in enumerate(ticket.reference, 1):
            answer = ""
            while answer not in ANSWERS:
                answer = input(f"\n[{n}/{len(ticket.reference)}] Is this work in the plan? {item}\n  y / p / n: ").strip().lower()
            verdicts.append(ANSWERS[answer])
        grades[str(path.relative_to(root))] = verdicts
        GRADES.write_text(json.dumps(grades, indent=2) + "\n")
        print(f"Saved ({len(grades)} graded).")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m defectrefiner.grade", description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="*", type=Path)
    parser.add_argument("--count", type=int, default=15, help="groomings to grade in total")
    parser.add_argument("--agreement", metavar="JUDGE_MODEL", help="compare your grades with this judge's")
    args = parser.parse_args(argv)

    root = DATA_DIR.parent
    if args.agreement:
        print(json.dumps(agreement(load_grades(), root, args.agreement), indent=2))
    else:
        grade([r.resolve() for r in args.runs], args.count, root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
