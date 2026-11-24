"""Grooming bug reports, and scoring where the agent says the bug is against
the files the real fix changed (see defects.Bug).

    python -m defectrefiner.bugs agent --provider anthropic --model claude-haiku-5-5 --limit 5
    python -m defectrefiner.bugs probe --provider anthropic --model claude-haiku-5-5
    python -m defectrefiner.bugs report runs/bugs-agent-claude-haiku-5-5
    python -m defectrefiner.bugs open --only 3110 --provider anthropic --model claude-haiku-5-5

- agent: one model with the read-only code tools at the commit before the
  fix, then a final structured answer (the feature agent's loop).
- team: defectrefiner.bug_team, an investigator and a test scout in parallel,
  then a test planner and a ticket template.
- open: the team on open bugs (data/open_bugs.json), against the latest
  code. There's no fix to score against: the tickets are saved as drafts
  for a person to review (defectrefiner.review).
- probe: the ticket alone, no code. If a model names the fixed file without
  seeing the code, it may be recalling the issue from training rather than
  investigating, and the agent's score on that bug says less.

Scoring is a match against the fix, no judge, at three levels for the first
k suspects:
- line@k: lines cited within LINE_SLACK of a line the fix changed. The
  probe can't do this without the code: it's the measure of investigating.
- function@k: a function the fix changed, by name or by lines cited inside
  it (for a template or code outside functions, the file).
- file@k: a file the fix changed. Without code, Haiku names the right file
  first 75% of the time, so this says little.

The regression test is scored the same way: whether it names a test file
(test_*.py) the fix added its test to.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from defectrefiner import code, defects
from defectrefiner.defects import Bug, Location, load_bugs
from defectrefiner.grooming import Text, capped, capped_list
from defectrefiner.llm import DEFAULT_ANTHROPIC_MODEL, DEFAULT_OLLAMA_MODEL, chat_model, cost, parse
from defectrefiner.run import RUNS_DIR, parallel_map, slug
from defectrefiner.systems import explore

TICKETS_DIR = Path(__file__).resolve().parent.parent / "docs" / "tickets"  # approved; see defectrefiner.review
DRAFTS_DIR = TICKETS_DIR / "drafts"
MAX_TOOL_CALLS = 15  # more than features' 12: finding a cause takes following calls through the code
# Reports after this are later than the models' training data (check it
# against the model card of any new model), so they can't have been memorised.
UNSEEN_AFTER = datetime(2026, 6, 1, tzinfo=UTC)
LINE_SLACK = 10  # cited lines this close to a changed line count as a line hit


class Suspect(BaseModel):
    path: capped(200) = Field(description="A file in the repository, relative to its root, e.g. datasette/app.py.")
    function: capped(200) | None = Field(None, description="The function or method, e.g. TableView.data, if it's in one.")
    lines: capped(40) | None = Field(None, description="The lines, e.g. 825-829, as the code is now.")
    reason: Text = Field(description="What in the code points here.")


class BugGrooming(BaseModel):
    triage: Literal["bug", "duplicate", "already_fixed", "not_a_bug", "feature_request"]
    summary: Text
    steps_to_reproduce: capped_list(Text, 8)
    expected: Text
    actual: Text
    suspects: capped_list(Suspect, 5) = Field(description="Where the bug most likely is, most likely first.")
    cause: Text = Field(description="The most likely cause, and how sure you are.")
    fix_approach: Text
    regression_test: Text = Field(description="A test that fails now and passes once fixed.")
    severity: Literal["critical", "high", "medium", "low"]
    points: int | None = Field(None, description="Story points on the Fibonacci scale: 1, 2, 3, 5, 8 or 13.")
    open_questions: capped_list(Text, 6) = Field(default_factory=list,
                                                 description="Questions for the reporter that change the work.")


class Suspects(BaseModel):
    """The probe's answer."""
    suspects: capped_list(Suspect, 5) = Field(description="Where the bug most likely is, most likely first.")


BUG_RULES = """You groom bug reports for {repo}, an open-source Python project, the way a scrum team does in refinement: so a developer can pick the bug up without redoing the investigation.

- Triage first: a bug in this code, a duplicate of how it's meant to work (not_a_bug), already fixed in this version of the code, or a feature request.
- Steps to reproduce, from the report, filled in where the code makes them clear. Expected and actual behaviour.
- Suspects: where the bug most likely is, most likely first: the file, the function or method, and the lines, each with what you saw in the code that points there. Name only code you found in the repository.
- Cause: the most likely mechanism, and how sure you are. If you couldn't confirm it, say what someone should check.
- Fix approach: an outline, not a patch. Regression test: what test would fail now and pass once fixed.
- Severity: critical (data loss, security, or everyone blocked), high (errors or wrong results on a common path), medium (a workaround exists, or an uncommon path), low (cosmetic).
- Estimate in points: 1, 2, 3, 5, 8 or 13.
- Open questions are for the reporter: only those whose answer changes the work, each ending with the assumption you made, as "(Assuming: ...)". Don't ask the reporter what the code could answer.
Don't invent details the report doesn't give."""

TICKET = """<bug_report>
{title}

{body}
</bug_report>"""

AGENT_TASK = """You can look at the repository's code with list_files, grep and read_file before answering. Search for the error messages and the code paths the report touches, follow them until you find the cause, then stop: you have at most {turns} tool calls."""

AGENT_FINISH = """What you found in the code:
<findings>
{findings}
</findings>

Now groom the bug report, using what you found."""

PROBE = """Without looking at any code: where in {repo} is this bug most likely? Give up to 3 places, most likely first: the file, relative to the repository root, the function or method, and the lines if you can.

{ticket}"""


def _ticket(bug: Bug) -> str:
    return TICKET.format(title=bug.title, body=bug.body)


def groom_bug(bug: Bug, code_dir: str | Path, chat: BaseChatModel,
              max_tool_calls: int = MAX_TOOL_CALLS) -> tuple[BugGrooming, list[str]]:
    """(grooming, the tool calls made). The answer is written from a text copy
    of the findings, as in systems.groom_with_agent."""
    rules = SystemMessage(BUG_RULES.format(repo=bug.repo))
    findings, calls = explore(chat, code_dir, [rules, HumanMessage(_ticket(bug) + "\n\n" + AGENT_TASK.format(turns=max_tool_calls))],
                              max_tool_calls)
    final = [rules, HumanMessage(_ticket(bug) + "\n\n" + AGENT_FINISH.format(findings="\n\n".join(findings) or "(nothing)"))]
    return parse(chat, BugGrooming).invoke(final), calls


def probe(bug: Bug, chat: BaseChatModel) -> Suspects:
    return parse(chat, Suspects).invoke([HumanMessage(PROBE.format(repo=bug.repo, ticket=_ticket(bug)))])


def _same_file(suspect: str, fixed: str) -> bool:
    """A suspect path matches if it is the fixed file, or a tail of its path
    ("views/table.py" for "datasette/views/table.py")."""
    suspect = suspect.strip().removeprefix("./").strip("/")
    return bool(suspect) and (suspect == fixed or fixed.endswith("/" + suspect))


def line_ranges(text: str | None) -> list[tuple[int, int]]:
    """"825-829", "about 588-650", "L12, 40–44" -> [(825, 829)], ..."""
    return [(int(a), int(b or a)) for a, b in re.findall(r"(\d+)(?:\s*[-–]\s*(\d+))?", text or "")]


def _same_function(suspect: dict, location: Location) -> bool:
    """A suspect matches a changed function if its last name ("data" in
    "TableView.data()") is that function or one around it, or if lines it
    cites fall inside it. A location with no function (a template, or code
    outside functions) is matched by the file alone."""
    if location.function is None:
        return True
    name = (suspect.get("function") or "").strip().removesuffix("()").rsplit(".", 1)[-1]
    return name in location.names or any(a <= location.end and b >= location.start
                                         for a, b in line_ranges(suspect.get("lines")))


def _near_lines(suspect: dict, location: Location) -> bool:
    return any(a - LINE_SLACK <= n <= b + LINE_SLACK for a, b in line_ranges(suspect.get("lines")) for n in location.lines)


def _hits(suspect: dict, bug: Bug, level: str) -> bool:
    if level == "file":
        return any(_same_file(suspect["path"], f) for f in bug.source_files)
    match = _same_function if level == "function" else _near_lines
    return any(_same_file(suspect["path"], loc.file) and match(suspect, loc) for loc in bug.locations)


def hit_at(suspects: list[dict], bug: Bug, k: int, level: str = "function") -> bool:
    """Whether one of the first k suspects ({"path", "function", "lines"}) is
    where the fix changed code, at "line", "function" or "file" level."""
    return any(_hits(s, bug, level) for s in suspects[:k])


def fix_test_files(bug: Bug) -> list[str]:
    """The test files the fix changed, without helpers like fixtures.py."""
    return [f for f in bug.test_files if Path(f).name.startswith("test_")]


def names_test_file(text: str, bug: Bug) -> bool:
    """Whether the text names a test file the fix changed ("test_html.py"
    doesn't match "test_table_html.py")."""
    return any(re.search(rf"(?<![\w.]){re.escape(Path(f).name)}", text) for f in fix_test_files(bug))


# ----------------------------------------------------------------------
# Running and reporting, like run.py.

def run(system: str, provider: str, model: str, bugs: list[Bug], repeat: int, out: Path, workers: int = 1) -> None:
    chat = chat_model(provider, model)
    if system in ("agent", "team"):
        for bug in bugs:  # check out first: parallel git worktree adds would collide
            code.checkout_at(bug.repo, bug.base_sha)

    def one(bug: Bug, n: int) -> None:
        path = out / bug.id / f"{n}.json"
        start = time.monotonic()
        try:
            with get_usage_metadata_callback() as usage:
                if system == "agent":
                    grooming, calls = groom_bug(bug, code.checkout_at(bug.repo, bug.base_sha), chat)
                    answer = {"grooming": grooming.model_dump(), "tool_calls": calls}
                elif system == "team":
                    from defectrefiner.bug_team import groom_with_bug_team  # it imports this module
                    visible = bug.model_dump(include={"id", "repo", "title", "body"})  # no answers
                    state = groom_with_bug_team(visible, str(code.checkout_at(bug.repo, bug.base_sha)),
                                                {"default": f"{provider}:{model}"})
                    answer = {key: state[key] for key in ("grooming", "test_plan", "ticket")}
                    answer |= {"tool_calls": state["calls"], "seconds_by_role": state["seconds"]}
                else:
                    answer = {"grooming": probe(bug, chat).model_dump(), "tool_calls": []}
            tokens = {k: sum(u.get(k, 0) for u in usage.usage_metadata.values()) for k in ("input_tokens", "output_tokens")}
            result = {**answer, "seconds": round(time.monotonic() - start, 1), **tokens,
                      "cost_usd": round(cost(usage.usage_metadata), 5)}
            print(f"{bug.id} #{n}: suspects {[(s['path'], s.get('function'), s.get('lines')) for s in answer['grooming']['suspects']]}")
        except Exception as e:  # one failure shouldn't stop the run
            result = {"error": f"{type(e).__name__}: {e}"}
            print(f"{bug.id} #{n}: failed: {result['error'][:150]}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"bug_id": bug.id, "system": system, "model": model, **result}, indent=2))

    def done(path: Path) -> bool:  # failed attempts are tried again
        return path.exists() and "error" not in json.loads(path.read_text())

    todo = [(b, n) for b in bugs for n in range(1, repeat + 1) if not done(out / b.id / f"{n}.json")]
    parallel_map(lambda job: one(*job), todo, workers)


def refine_open(numbers: set[int], repo: str, provider: str, model: str, out: Path, workers: int = 1) -> None:
    from defectrefiner.bug_team import groom_with_bug_team  # it imports this module

    head = code.checkout_at(repo, "HEAD")
    sha = code._git("-C", head, "rev-parse", "HEAD").strip()

    def one(bug: defects.Defect) -> None:
        start = time.monotonic()
        with get_usage_metadata_callback() as usage:
            state = groom_with_bug_team({"id": f"issue-{bug.number}", "repo": repo, "title": bug.title, "body": bug.body},
                                        str(head), {"default": f"{provider}:{model}"})
        seconds, spent = round(time.monotonic() - start), cost(usage.usage_metadata)
        footer = (f"\n---\nRefined from [{repo}#{bug.number}]({bug.url}) by defectrefiner ({model}), "
                  f"against commit `{sha[:12]}`, in {seconds} s for ${spent:.3f}. Not reviewed by a person.\n")
        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        (DRAFTS_DIR / f"issue-{bug.number}.md").write_text(state["ticket"] + footer)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"issue-{bug.number}.json").write_text(json.dumps({**state, "commit": sha, "seconds": seconds,
                                                                  "cost_usd": round(spent, 5)}, indent=2))
        print(f"issue-{bug.number}: {state['grooming']['triage']}, {seconds} s, ${spent:.3f}")

    parallel_map(one, [d for d in defects.load(defects.OPEN_BUGS) if d.number in numbers], workers)


def _test_text(result: dict) -> str:
    """Where a result says the regression test goes: the team's test plan
    has a field for it; the single agent only describes the test."""
    plan = result.get("test_plan")
    return plan["test_file"] if plan else result["grooming"]["regression_test"]


def report(out: Path) -> dict:
    bugs = {b.id: b for b in load_bugs()}
    results = [json.loads(p.read_text()) for p in sorted(out.glob("*/[0-9]*.json"))]
    done = [r for r in results if "error" not in r]

    def rate(rows: list[dict], k: int, level: str) -> str | None:
        if not rows:
            return None
        hits = sum(hit_at(r["grooming"]["suspects"], bugs[r["bug_id"]], k, level) for r in rows)
        return f"{hits}/{len(rows)} ({hits / len(rows):.0%})"

    groups = {
        "all": done,
        "no_traceback": [r for r in done if not bugs[r["bug_id"]].traceback],
        "outside": [r for r in done if bugs[r["bug_id"]].reporter == "outside"],
        "maintainer": [r for r in done if bugs[r["bug_id"]].reporter == "maintainer"],
        "unseen": [r for r in done if bugs[r["bug_id"]].created_at >= UNSEEN_AFTER],
    }
    summary = {"results": len(results), "failed": len(results) - len(done),
               **{f"{level}@{k}": {name: rate(rows, k, level) for name, rows in groups.items()}
                  for level in ("line", "function", "file") for k in (1, 3)}}
    if done and "triage" in done[0]["grooming"]:
        summary["triaged_as_bug"] = f"{sum(r['grooming']['triage'] == 'bug' for r in done)}/{len(done)}"
        tested = [r for r in done if fix_test_files(bugs[r["bug_id"]])]
        hits = sum(names_test_file(_test_text(r), bugs[r["bug_id"]]) for r in tested)
        named = sum(bool(re.search(r"test_\w+\.py", _test_text(r))) for r in done)
        summary["test_file"] = f"{hits}/{len(tested)}"  # of the bugs whose fix added a test
        summary["names_any_test_file"] = f"{named}/{len(done)}"
    if done:
        summary["median_seconds"] = statistics.median(r["seconds"] for r in done)
        summary["mean_tool_calls"] = round(statistics.mean(len(r["tool_calls"]) for r in done), 1)
        summary["mean_cost_usd"] = round(statistics.mean(r["cost_usd"] for r in done), 4)
        summary["total_cost_usd"] = round(sum(r["cost_usd"] for r in done), 3)
    if done and "seconds_by_role" in done[0]:
        summary["median_seconds_by_role"] = {role: statistics.median(r["seconds_by_role"][role] for r in done)
                                             for role in done[0]["seconds_by_role"]}
    return summary


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m defectrefiner.bugs", description=__doc__.splitlines()[0])
    parser.add_argument("system", choices=["agent", "team", "open", "probe", "report"])
    parser.add_argument("dir", nargs="?", type=Path, help="report: the run directory")
    parser.add_argument("--provider", choices=["ollama", "anthropic"], default="ollama")
    parser.add_argument("--model")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--limit", type=int, help="only the first N bugs")
    parser.add_argument("--only", help="comma-separated bug ids (open: issue numbers)")
    parser.add_argument("--tag", help="added to the run directory's name")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)

    if args.system == "report":
        if not args.dir:
            parser.error("report needs a run directory")
        print(json.dumps(report(args.dir), indent=2))
        return 0

    model = args.model or (DEFAULT_OLLAMA_MODEL if args.provider == "ollama" else DEFAULT_ANTHROPIC_MODEL)
    if args.system == "open":
        if not args.only:
            parser.error("open needs --only with issue numbers")
        refine_open({int(n) for n in args.only.split(",")}, "simonw/datasette", args.provider, model,
                    RUNS_DIR / f"open-team-{slug(model)}", args.workers)
        print(f"Drafts in {DRAFTS_DIR}. Review them with: python -m defectrefiner.review")
        return 0
    bugs = load_bugs()
    if args.only:
        bugs = [b for b in bugs if b.id in set(args.only.split(","))]
    bugs = bugs[: args.limit]
    out = RUNS_DIR / "-".join(["bugs", args.system, slug(model), *([slug(args.tag)] if args.tag else [])])
    run(args.system, args.provider, model, bugs, args.repeat, out, args.workers)
    print(f"Saved to {out}. Score with: python -m defectrefiner.bugs report {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
