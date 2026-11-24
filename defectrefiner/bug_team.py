"""Bug grooming by two agents in parallel, as a LangGraph graph.

    investigator --------+
                         +--> test_planner --> write_ticket
    test_scout ----------+

- investigator reads the code to find the cause (the single agent's loop
  and prompt, defectrefiner.bugs.groom_bug) and grooms the bug.
- test_scout reads the tests at the same time: which test files cover the
  code the report touches, and the fixtures and patterns they use. It sees
  only the report, so it doesn't wait for the investigator.
- test_planner waits for both, then plans the tests: where the regression
  test goes, the scenarios to cover and which of them to automate. No
  tools: it gets the scout's raw findings, not a summary of them, so
  nothing the scout saw is lost in the handoff.
- write_ticket fills a template. No model: it's free, instant and the
  format is always right.

config["configurable"]["models"] maps a role (or "default") to
"provider:model", as in defectrefiner.team.
"""

from __future__ import annotations

import operator
import time
from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from defectrefiner.bugs import TICKET, BugGrooming, groom_bug
from defectrefiner.grooming import Text, capped, capped_list
from defectrefiner.llm import chat_model, parse
from defectrefiner.systems import explore

SCOUT_TOOL_CALLS = 10
DEFAULT_MODEL = "anthropic:claude-haiku-5-5"


class Scenario(BaseModel):
    description: Text = Field(description="What is tested: the input or situation, and the expected behaviour.")
    automate: bool = Field(description="Whether this should be an automated test.")
    where: capped(200) | None = Field(None, description="For an automated test, the test file; otherwise why it stays manual.")


class TestPlan(BaseModel):
    __test__ = False  # not a pytest test class

    test_file: capped(200) = Field(description="The test file the regression test belongs in, relative to the repository root, e.g. tests/test_api.py.")
    regression_test: capped(800) = Field(description="A test that fails now and passes once fixed: its name, setup and assertion.")
    reuse: capped_list(Text, 5) = Field(default_factory=list, description="Existing fixtures, helpers or tests to build on, by name.")
    scenarios: capped_list(Scenario, 8) = Field(description="Other cases the fix should be checked against: edge cases and nearby behaviour it could break.")


class BugTeamState(TypedDict, total=False):
    bug: dict  # id, repo, title, body: no answers
    code_dir: str
    grooming: dict  # BugGrooming, from the investigator
    test_findings: list[str]  # the scout's tool calls and results
    test_plan: dict  # TestPlan
    ticket: str  # the result, as Markdown
    calls: Annotated[list[str], operator.add]  # both agents' tool calls, "role: name(args)"
    seconds: Annotated[dict, operator.or_]  # each role's time, to show what running in parallel saves


# ----------------------------------------------------------------------
# Prompts.

SCOUT = """You are the test engineer for {repo}, an open-source Python project. A bug report has come in; someone else is finding the cause. Your job is to find how this code is tested, so the fix gets the right tests:
- the test files that cover the behaviour the report touches (start with list_files("tests") and grep the tests for the URLs, functions and options the report mentions),
- the fixtures, helpers and test databases those tests use,
- an existing test close to the one this bug needs, to copy the pattern from.
Use list_files, grep and read_file; you have at most {turns} tool calls. Stop once you know where a test for this bug would go."""

PLANNER = """You plan the tests for a bug fix in {repo}. You have the bug report, the investigation of its cause, and what the test engineer found in the tests.

- Test file: the existing test file the regression test belongs in, as the test engineer found it. Only suggest a new file if no existing one fits.
- Regression test: one test that fails on the code now and passes once fixed. Name it, and say its setup and assertion, reusing the fixtures found.
- Scenarios: other cases the fix should be checked against, such as edge cases of the same input and nearby behaviour the fix could break. Automate a scenario when it can run in the test suite; leave it manual only when it needs something the suite can't do (a browser, a real deployment), and say why.
Only name files, fixtures and tests that appear in the findings."""

PLANNER_INPUT = """{ticket}

<investigation>
Cause: {cause}
Suspects: {suspects}
Fix approach: {fix_approach}
</investigation>

<test_findings>
{findings}
</test_findings>"""

TICKET_TEMPLATE = """# {title}

**Severity:** {severity} · **Points:** {points} · **Triage:** {triage}

{summary}

## Steps to reproduce
{steps}

**Expected:** {expected}
**Actual:** {actual}

## Cause
{cause}

**Where:**
{suspects}

## Fix approach
{fix_approach}

## Tests
**Regression test** (`{test_file}`): {regression_test}

{reuse}**Scenarios:**
{scenarios}

## Open questions
{questions}
"""


# ----------------------------------------------------------------------
# The roles.

def _chat(role: str, config: RunnableConfig):
    models = (config or {}).get("configurable", {}).get("models", {})
    provider, model = models.get(role, models.get("default", DEFAULT_MODEL)).split(":", 1)
    return chat_model(provider, model)


def _report(bug: dict) -> str:
    return TICKET.format(title=bug["title"], body=bug["body"])


def investigator(state: BugTeamState, config: RunnableConfig) -> dict:
    start = time.monotonic()
    grooming, calls = groom_bug(_BugView(**state["bug"]), state["code_dir"], _chat("investigator", config))
    return {"grooming": grooming.model_dump(), "calls": [f"investigator: {c}" for c in calls],
            "seconds": {"investigator": round(time.monotonic() - start, 1)}}


def test_scout(state: BugTeamState, config: RunnableConfig) -> dict:
    start = time.monotonic()
    bug = state["bug"]
    messages = [SystemMessage(SCOUT.format(repo=bug["repo"], turns=SCOUT_TOOL_CALLS)), HumanMessage(_report(bug))]
    findings, calls = explore(_chat("test_scout", config), state["code_dir"], messages, SCOUT_TOOL_CALLS)
    return {"test_findings": findings, "calls": [f"test_scout: {c}" for c in calls],
            "seconds": {"test_scout": round(time.monotonic() - start, 1)}}


def test_planner(state: BugTeamState, config: RunnableConfig) -> dict:
    start = time.monotonic()
    bug, grooming = state["bug"], state["grooming"]
    suspects = "; ".join(f"{s['path']} {s.get('function') or ''} {s.get('lines') or ''}".strip() for s in grooming["suspects"])
    user = PLANNER_INPUT.format(ticket=_report(bug), cause=grooming["cause"], suspects=suspects or "(none)",
                                fix_approach=grooming["fix_approach"], findings="\n\n".join(state.get("test_findings") or []) or "(nothing)")
    plan = parse(_chat("test_planner", config), TestPlan).invoke([SystemMessage(PLANNER.format(repo=bug["repo"])), HumanMessage(user)])
    return {"test_plan": plan.model_dump(), "seconds": {"test_planner": round(time.monotonic() - start, 1)}}


def write_ticket(state: BugTeamState) -> dict:
    return {"ticket": render_ticket(state["bug"]["title"], BugGrooming.model_validate(state["grooming"]),
                                    TestPlan.model_validate(state["test_plan"]))}


def render_ticket(title: str, g: BugGrooming, plan: TestPlan) -> str:
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {x}" for x in items) or "- (none)"

    suspects = [f"`{s.path}`" + (f" `{s.function}`" if s.function else "") + (f" lines {s.lines}" if s.lines else "")
                + f": {s.reason}" for s in g.suspects]
    scenarios = [f"[{'automate' if s.automate else 'manual'}] {s.description}" + (f" ({s.where})" if s.where else "")
                 for s in plan.scenarios]
    reuse = f"**Reuse:** {', '.join(plan.reuse)}\n\n" if plan.reuse else ""
    return TICKET_TEMPLATE.format(
        title=title, severity=g.severity, points=g.points or "?", triage=g.triage, summary=g.summary,
        steps="\n".join(f"{n}. {s}" for n, s in enumerate(g.steps_to_reproduce, 1)) or "(none given)",
        expected=g.expected, actual=g.actual, cause=g.cause, suspects=bullets(suspects), fix_approach=g.fix_approach,
        test_file=plan.test_file, regression_test=plan.regression_test, reuse=reuse, scenarios=bullets(scenarios),
        questions=bullets(g.open_questions))


class _BugView(BaseModel):
    """What groom_bug reads from a bug: the report, not the answers."""
    id: str
    repo: str
    title: str
    body: str


builder = StateGraph(BugTeamState)
builder.add_node("investigator", investigator)
builder.add_node("test_scout", test_scout)
builder.add_node("test_planner", test_planner)
builder.add_node("write_ticket", write_ticket)
builder.add_edge(START, "investigator")  # two edges from START: both run at once
builder.add_edge(START, "test_scout")
builder.add_edge(["investigator", "test_scout"], "test_planner")  # waits for both
builder.add_edge("test_planner", "write_ticket")
builder.add_edge("write_ticket", END)
bug_team = builder.compile()


def groom_with_bug_team(bug: dict, code_dir: str, models: dict[str, str] | None = None) -> dict:
    """The final state: grooming, test_plan, ticket, calls and seconds."""
    return bug_team.invoke({"bug": bug, "code_dir": code_dir}, {"configurable": {"models": models or {"default": DEFAULT_MODEL}}})


if __name__ == "__main__":  # print the graph as a Mermaid diagram
    print(bug_team.get_graph().draw_mermaid())
