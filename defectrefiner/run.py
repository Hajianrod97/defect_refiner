"""Groom the ticket set with one system and save each result, then score them.

    python -m defectrefiner.run prompt --limit 5
    python -m defectrefiner.run agent --repeat 3
    python -m defectrefiner.run report runs/agent-qwen2.5-coder-7b

Results go to runs/<system>-<model>/<ticket id>/<n>.json; finished ones are
skipped and failed ones tried again, so an interrupted run carries on where it
stopped. Scores are cached
next to them as <n>.score.<judge>.json, so two judges' scores can be compared.
"""

from __future__ import annotations

import contextvars
import json
import re
import statistics
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langchain_core.callbacks import get_usage_metadata_callback

from defectrefiner import code, systems, team
from defectrefiner.grooming import Grooming
from defectrefiner.llm import DEFAULT_ANTHROPIC_MODEL, DEFAULT_OLLAMA_MODEL, chat_model, cost, structured
from defectrefiner.score import TicketScore, chore_stories, points_spread, score, trap_caught
from defectrefiner.tickets import Ticket, load

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"
GROOM_TEMPERATURE = 0.3  # Ollama only: some variety, so repeated runs say something about consistency
JUDGE_EFFORT = "low"  # the judge answers narrow yes/no questions; more thinking adds cost, not accuracy


def parallel_map(fn, items, workers: int) -> list:
    """`fn` over `items` in threads, each running in a copy of the caller's
    context: LangChain's usage callback lives in a context variable, and a
    plain thread pool would leave the calls uncounted."""
    ctx = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda item: ctx.copy().run(fn, item), items))


def groom(system: str, ticket: Ticket, chat, team_settings: dict | None = None) -> dict:
    """One grooming, with its time, tokens, cost and tool calls (and, for the
    team, its log)."""
    code_dir = code.checkout(ticket.repo, ticket.created_at)
    start = time.monotonic()
    calls: list[str] = []
    extra: dict = {}
    with get_usage_metadata_callback() as usage:
        if system == "prompt":
            grooming = systems.groom_with_prompt(ticket, code_dir, chat)
        elif system == "agent":
            grooming, calls = systems.groom_with_agent(ticket, code_dir, chat)
        elif system == "team":
            visible = ticket.model_dump(mode="json", exclude={"reference", "traps"})
            grooming, calls, log = team.groom_with_team(visible, str(code_dir), **team_settings)
            extra = {"log": log, **team_settings}
        else:
            raise ValueError(f"Unknown system: {system}")
    tokens = {k: sum(u.get(k, 0) for u in usage.usage_metadata.values()) for k in ("input_tokens", "output_tokens")}
    return {"grooming": grooming.model_dump(), "seconds": round(time.monotonic() - start, 1), **tokens,
            "cost_usd": round(cost(usage.usage_metadata), 5), "tool_calls": calls, **extra}


def run(system: str, provider: str, model: str, tickets: list[Ticket], repeat: int, out: Path,
        workers: int = 1, team_settings: dict | None = None) -> None:
    chat = chat_model(provider, model, temperature=GROOM_TEMPERATURE) if system != "team" else None
    for ticket in tickets:  # check out the code first: parallel git worktree adds would collide
        code.checkout(ticket.repo, ticket.created_at)

    def one(ticket: Ticket, n: int) -> None:
        path = out / ticket.id / f"{n}.json"
        try:
            result = groom(system, ticket, chat, team_settings)
            print(f"{ticket.id} #{n}: {len(result['grooming']['stories'])} stories, {result['seconds']}s")
        except Exception as e:  # a model that fails one ticket shouldn't stop the run
            result = {"error": f"{type(e).__name__}: {e}"}
            print(f"{ticket.id} #{n}: failed: {result['error'][:150]}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"ticket_id": ticket.id, "system": system, "model": model, **result}, indent=2))

    def done(path: Path) -> bool:  # failed attempts are tried again
        return path.exists() and "error" not in json.loads(path.read_text())

    todo = [(t, n) for t in tickets for n in range(1, repeat + 1) if not done(out / t.id / f"{n}.json")]
    parallel_map(lambda job: one(*job), todo, workers)


def slug(model: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", model.lower())


def score_path(result_path: Path, judge: str) -> Path:
    return result_path.with_name(f"{result_path.stem}.score.{slug(judge)}.json")


def report(out: Path, provider: str, model: str | None, workers: int = 1, only: set[str] | None = None) -> dict:
    """Score every saved grooming in `out` and sum it up. With workers > 1,
    groomings are judged in parallel (for an API judge; Ollama answers one
    request at a time anyway)."""
    tickets = {t.id: t for t in load()}
    judge = model or (DEFAULT_OLLAMA_MODEL if provider == "ollama" else DEFAULT_ANTHROPIC_MODEL)
    ask = structured(chat_model(provider, judge, effort=JUDGE_EFFORT if provider == "anthropic" else None))
    paths = sorted(p for p in out.glob("*/[0-9]*.json") if ".score." not in p.name and (not only or p.parent.name in only))

    def one(path: Path) -> tuple[dict, TicketScore | None, bool]:
        """(result, score or None, whether the judge failed)."""
        result = json.loads(path.read_text())
        if "error" in result:
            return result, None, False
        cache = score_path(path, judge)
        if cache.exists():
            scored = TicketScore.model_validate_json(cache.read_text())
            # Trap checks are keyword matches, cheap to redo: recheck them so a
            # change to a ticket's traps never needs the judge again.
            grooming = Grooming.model_validate(result["grooming"])
            scored.traps = [trap_caught(t, grooming) for t in tickets[result["ticket_id"]].traps]
            return result, scored, False
        try:
            scored = score(tickets[result["ticket_id"]], Grooming.model_validate(result["grooming"]), ask)
        except Exception as e:  # one bad judge reply shouldn't lose the other scores
            print(f"{path.relative_to(out)}: not scored: {type(e).__name__}: {str(e)[:120]}")
            return result, None, True
        cache.write_text(scored.model_dump_json(indent=2))
        return result, scored, False

    with get_usage_metadata_callback() as usage:
        outcomes = parallel_map(one, paths, workers)
    rows = [(r, s) for r, s, failed in outcomes if not failed]
    return {**summarise(rows, tickets), "judge": judge, "judge_failed": sum(f for _, _, f in outcomes),
            "judge_cost_usd": round(cost(usage.usage_metadata), 4)}


def summarise(rows: list[tuple[dict, TicketScore | None]], tickets: dict[str, Ticket]) -> dict:
    done = [(r, s) for r, s in rows if s]
    mean = lambda xs: round(statistics.mean(xs), 2) if xs else None  # noqa: E731

    traps: dict[str, list[bool]] = defaultdict(list)
    totals: dict[str, list[int]] = defaultdict(list)
    for _, s in done:
        for trap, caught in zip(tickets[s.ticket_id].traps, s.traps, strict=True):
            traps[trap.kind].append(caught)
        totals[s.ticket_id].append(s.points)
    spreads = [x for x in (points_spread(v) for v in totals.values()) if x is not None]
    return {
        "groomings": len(rows),
        "failed": len(rows) - len(done),
        "coverage_real": mean([s.coverage_rate() for _, s in done if tickets[s.ticket_id].source == "real"]),
        "coverage_written": mean([s.coverage_rate() for _, s in done if tickets[s.ticket_id].source == "written"]),
        "traps": {kind: f"{sum(v)}/{len(v)}" for kind, v in sorted(traps.items())},
        "testable": mean([t for _, s in done for t in s.testable]),
        "stories_per_ticket": mean([len(r["grooming"]["stories"]) for r, _ in done]),
        "test_or_doc_stories": sum(chore_stories(Grooming.model_validate(r["grooming"]), tickets[s.ticket_id].title)
                                   for r, s in done),
        "points_spread": mean(spreads),
        "median_seconds": statistics.median([r["seconds"] for r, _ in done]) if done else None,
        "mean_tokens": round(statistics.mean([r["input_tokens"] + r["output_tokens"] for r, _ in done])) if done else None,
        "mean_cost_usd": round(statistics.mean([r["cost_usd"] for r, _ in done]), 4) if done and all("cost_usd" in r for r, _ in done) else None,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m defectrefiner.run", description=__doc__.splitlines()[0])
    parser.add_argument("system", choices=["prompt", "agent", "team", "report"])
    parser.add_argument("dir", nargs="?", type=Path, help="report: the run directory to score")
    parser.add_argument("--provider", choices=["ollama", "anthropic"], default="ollama")
    parser.add_argument("--model")
    parser.add_argument("--repeat", type=int, default=1, help="groomings per ticket")
    parser.add_argument("--limit", type=int, help="only the first N tickets")
    parser.add_argument("--only", help="comma-separated ticket ids")
    parser.add_argument("--models", help='team: per-role models, e.g. "tech_lead=anthropic:claude-sonnet-5-5,default=anthropic:claude-haiku-5-5"')
    parser.add_argument("--routing", choices=["facilitator", "fixed"], default="facilitator",
                        help="team: whether the facilitator's model reviews the draft, or only the hard rules apply")
    parser.add_argument("--tag", help="added to the run directory's name, to keep a variant next to the original")
    parser.add_argument("--judge-provider", choices=["ollama", "anthropic"], default="ollama")
    parser.add_argument("--judge-model")
    parser.add_argument("--workers", type=int, default=1, help="groomings made, or judged, at once (for API models)")
    args = parser.parse_args(argv)

    if args.system == "report":
        if not args.dir:
            parser.error("report needs a run directory")
        print(json.dumps(report(args.dir, args.judge_provider, args.judge_model, args.workers,
                                set(args.only.split(",")) if args.only else None), indent=2))
        return 0

    model = args.model or (DEFAULT_OLLAMA_MODEL if args.provider == "ollama" else DEFAULT_ANTHROPIC_MODEL)
    team_settings = None
    if args.system == "team":
        team_settings = {"models": team.parse_models(args.models), "routing": args.routing}
        model = team_settings["models"]["default"].split(":", 1)[1]
    tickets = load()
    if args.only:
        wanted = set(args.only.split(","))
        tickets = [t for t in tickets if t.id in wanted]
    tickets = tickets[: args.limit]
    name = args.system if args.system != "team" else f"team-{args.routing}"
    out = RUNS_DIR / "-".join([name, slug(model), *([slug(args.tag)] if args.tag else [])])
    run(args.system, args.provider, model, tickets, args.repeat, out, args.workers, team_settings)
    print(f"Saved to {out}. Score with: python -m defectrefiner.run report {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
