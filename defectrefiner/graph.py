"""Graphs for LangGraph Studio (`uv run langgraph dev`, see langgraph.json).

- refine_bug: load a bug, check out its code, then the bug team
  (defectrefiner.bug_team) writes the ticket. Run it with
  {"bug_id": "bug-393"} (a closed bug, at the code before its fix) or
  {"bug_id": "issue-2924"} (an open bug, at the latest code; fetch them
  first with python -m defectrefiner.defects).
- groom: the earlier feature experiment. Load a ticket, check out the code
  as of that ticket, then the team (defectrefiner.team) grooms it. Run it
  with {"ticket_id": "ds-1855"}.

Studio shows each team as a subgraph.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from defectrefiner import bug_team, code, defects, team, tickets


class GroomState(TypedDict, total=False):
    ticket_id: str  # the input: which ticket to groom, e.g. "ds-1855"
    ticket: dict
    code_dir: str
    grooming: dict  # the team's result


def load_ticket(state: GroomState) -> dict:
    for ticket in tickets.load():
        if ticket.id == state["ticket_id"]:
            # The answers stay out of the state: the agents mustn't see them.
            return {"ticket": ticket.model_dump(mode="json", exclude={"reference", "traps"})}
    raise LookupError(f"No ticket {state['ticket_id']} in {tickets.TICKETS}")


def check_out_code(state: GroomState) -> dict:
    ticket = tickets.Ticket.model_validate({**state["ticket"], "reference": []})
    return {"code_dir": str(code.checkout(ticket.repo, ticket.created_at))}


builder = StateGraph(GroomState)
builder.add_node("load_ticket", load_ticket)
builder.add_node("check_out_code", check_out_code)
builder.add_edge(START, "load_ticket")
builder.add_edge("load_ticket", "check_out_code")
builder.add_node("team", team.team)
builder.add_edge("check_out_code", "team")
builder.add_edge("team", END)
graph = builder.compile()


class BugState(bug_team.BugTeamState, total=False):
    bug_id: str  # the input: "bug-393" or "issue-2924"


def load_bug(state: BugState) -> dict:
    """The report (not the fix) and the code to read: the commit before the
    fix for a closed bug, the latest commit for an open one."""
    bug_id = state["bug_id"]
    if bug_id.startswith("issue-"):
        found = [d for d in defects.load(defects.OPEN_BUGS) if f"issue-{d.number}" == bug_id]
        repo, sha = "simonw/datasette", "HEAD"
    else:
        found = [b for b in defects.load_bugs() if b.id == bug_id]
        repo, sha = (found[0].repo, found[0].base_sha) if found else (None, None)
    if not found:
        raise LookupError(f"No bug {bug_id}")
    return {"bug": {"id": bug_id, "repo": repo, "title": found[0].title, "body": found[0].body},
            "code_dir": str(code.checkout_at(repo, sha))}


bug_builder = StateGraph(BugState)
bug_builder.add_node("load_bug", load_bug)
bug_builder.add_node("bug_team", bug_team.bug_team)
bug_builder.add_edge(START, "load_bug")
bug_builder.add_edge("load_bug", "bug_team")
bug_builder.add_edge("bug_team", END)
bug_graph = bug_builder.compile()
