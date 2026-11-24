"""The multi-agent grooming team, as a LangGraph graph.

    tech_lead -> product_owner -> qa -> estimator -> facilitator -> assemble
                      ^             ^       ^             |
                      +-------------+-------+-------------+  (sent back, then on down the line)

- tech_lead reads the code (the same tools and loop as the agent baseline)
  and writes notes: facts, what already exists, risks, how the work divides.
  Every role reads the ticket; the others get these notes instead of the code.
- product_owner writes the stories and the open questions.
- qa writes each story's acceptance criteria.
- estimator gives each story points.
- facilitator decides whether the draft is done or goes back to one role.
  Hard rules first (a story over 8 points goes back to be split, a story
  with no criteria goes to qa), then, with routing "facilitator", the
  model's own review. Each kind has its own limit (MAX_RULE_SENDBACKS,
  MAX_REVIEWS), so the model's reviews can't use up the rules' turns.

Each role can run on its own model: config["configurable"]["models"] maps a
role (or "default") to "provider:model".
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from defectrefiner.grooming import Grooming, Story, Text, capped, capped_list
from defectrefiner.llm import chat_model, parse
from defectrefiner.systems import TICKET, explore

ORDER = ["tech_lead", "product_owner", "qa", "estimator"]  # a sent-back role's work flows on down this line
MAX_REVIEWS = 2  # send-backs on the facilitator's own judgement, per ticket
MAX_RULE_SENDBACKS = 2  # send-backs by the hard rules (too big, no criteria), counted separately
MAX_POINTS = 8
TECH_LEAD_TOOL_CALLS = 8
DEFAULT_MODEL = "anthropic:claude-haiku-5-5"


# ----------------------------------------------------------------------
# What each role produces.

class TechNotes(BaseModel):
    findings: capped_list(Text, 10) = Field(description="Facts about the code the ticket touches, naming files, functions, settings and URLs.")
    already_exists: capped_list(Text, 5) = Field(default_factory=list, description="Parts of the request the code already does, naming the mechanism.")
    risks: capped_list(Text, 6) = Field(default_factory=list, description="Conflicts with how the code works, technical risks, things you couldn't confirm.")
    suggested_split: capped_list(Text, 6) = Field(default_factory=list, description="How the work divides into pieces worth shipping on their own.")


class DraftStory(BaseModel):
    title: capped(120)
    user_story: Text = Field(description="As a <role>, I want <goal>, so that <benefit>.")
    dependencies: capped_list(Text, 5) = Field(default_factory=list)


class Draft(BaseModel):
    stories: capped_list(DraftStory, 10)
    open_questions: capped_list(Text, 5) = Field(default_factory=list, description='Each ends with "(Assuming: ...)".')


class StoryCriteria(BaseModel):
    story: int = Field(description="The story's number, from 1.")
    criteria: capped_list(Text, 8)


class QaOutput(BaseModel):
    stories: list[StoryCriteria]


class Estimate(BaseModel):
    story: int = Field(description="The story's number, from 1.")
    reason: capped(400) = Field(description="One short sentence.")
    points: int = Field(description="1, 2, 3, 5, 8 or 13.")


class Estimates(BaseModel):
    estimates: list[Estimate]


class Review(BaseModel):
    reason: capped(300)
    next: Literal["done", "tech_lead", "product_owner", "qa", "estimator"]
    instruction: capped(400) = Field("", description="What the role should change. Empty when done.")


class TeamState(TypedDict, total=False):
    ticket: dict  # without the reference answers
    code_dir: str
    notes: dict  # TechNotes
    draft: dict  # Draft
    criteria: dict  # story number (as a string) -> criteria
    estimates: dict  # story number (as a string) -> {"points", "reason"}
    feedback: dict  # role -> the facilitator's instruction for its next turn
    reviews: int  # the facilitator's send-backs so far
    rule_sendbacks: int  # the hard rules' send-backs so far
    next: str
    grooming: dict  # the result
    log: Annotated[list[str], operator.add]  # who did what, in order
    calls: Annotated[list[str], operator.add]  # the tech lead's tool calls


# ----------------------------------------------------------------------
# Prompts.

TECH_LEAD = """You are the tech lead in a refinement session for {repo}, an open-source Python project. Before the team writes stories, find out what the code says about this ticket: where the change would go, what already exists that does part of it, and what it would conflict with. Be concrete: name files, functions, settings and URLs. Use list_files, grep and read_file; you have at most {turns} tool calls."""

TECH_LEAD_NOTES = """What you found in the code:
<findings>
{findings}
</findings>

Write your notes for the team. Only what the code shows; say what you couldn't confirm."""

PRODUCT_OWNER = """You are the product owner in a refinement session for {repo}. Write the stories for this ticket.
- Split the work into stories that each deliver something useful on their own. Keep unrelated requests in separate stories, but don't split one change into pieces nobody would ship separately.
- Tests and documentation belong in a story's acceptance criteria (QA writes those), never in stories of their own.
- Each story has a user story: "As a ..., I want ..., so that ...". Note dependencies between stories.
- The tech lead's notes are facts about the code. Don't plan work for what already exists; say so instead.
- Open questions are for the requester, about what they want: only ones whose answer changes the work, most important first, each ending "(Assuming: ...)". Never ask what the tech lead's notes answer.
Don't invent requirements the ticket doesn't imply."""

QA = """You are QA in a refinement session for {repo}. Write acceptance criteria for each story: 3 to 6 conditions a tester could check, describing behaviour someone can observe, not how it's built. Include the tests and documentation the story needs. Where a criterion depends on an open question, write it for the assumption given and say so."""

ESTIMATOR = """You are the estimator in a refinement session for {repo}. Give each story points on the Fibonacci scale (1, 2, 3, 5, 8, 13), relative to each other, using the tech lead's notes for how much code each touches. One line of reasoning each. Anything over 8 is too big and should be split."""

FACILITATOR = """You facilitate a refinement session for {repo}. Review the team's draft against the ticket and the tech lead's notes. If there is a real problem, send it back to the one role that can fix it:
- product_owner: stories miss part of the ticket, plan work that already exists, merge unrelated requests, split one change needlessly, or ask the requester what the notes answer.
- qa: acceptance criteria (QA writes all of them) that are vague, untestable, missing a case, or missing tests or docs.
- estimator: estimates that are inconsistent with each other or with the notes.
- tech_lead: the team needs facts about the code that the notes don't have.
Open questions with stated assumptions are expected: they go to the requester after the session, so never ask the team to settle them. Otherwise answer "done". Don't send work back for polish."""

SENT_BACK = """

The facilitator sent your work back: {instruction}
Your previous version:
{previous}"""


# ----------------------------------------------------------------------
# Rendering state for prompts.

def _ticket(state: TeamState) -> str:
    return TICKET.format(title=state["ticket"]["title"], body=state["ticket"]["body"])


def _notes(state: TeamState) -> str:
    n = TechNotes.model_validate(state["notes"])
    parts = [("Findings", n.findings), ("Already exists", n.already_exists), ("Risks", n.risks),
             ("Suggested split", n.suggested_split)]
    body = "\n".join(f"{name}:\n" + "\n".join(f"- {x}" for x in xs) for name, xs in parts if xs)
    return f"<tech_lead_notes>\n{body or '(none)'}\n</tech_lead_notes>"


def _stories(state: TeamState, criteria: bool = False, points: bool = False) -> str:
    out = []
    for n, s in enumerate(Draft.model_validate(state["draft"]).stories, 1):
        line = f"Story {n}: {s.title}\n  {s.user_story}"
        if s.dependencies:
            line += f"\n  Depends on: {'; '.join(s.dependencies)}"
        if criteria:
            line += "".join(f"\n  - {c}" for c in state.get("criteria", {}).get(str(n), [])) or "\n  (no criteria)"
        if points and str(n) in state.get("estimates", {}):
            e = state["estimates"][str(n)]
            line += f"\n  {e['points']} points: {e['reason']}"
        out.append(line)
    questions = Draft.model_validate(state["draft"]).open_questions
    if questions:
        out.append("Open questions:\n" + "\n".join(f"- {q}" for q in questions))
    return "<draft>\n" + "\n\n".join(out) + "\n</draft>"


def _sent_back(state: TeamState, role: str, previous: str) -> str:
    instruction = state.get("feedback", {}).get(role)
    return SENT_BACK.format(instruction=instruction, previous=previous) if instruction else ""


def _chat(role: str, config: RunnableConfig):
    models = (config or {}).get("configurable", {}).get("models", {})
    provider, model = models.get(role, models.get("default", DEFAULT_MODEL)).split(":", 1)
    return chat_model(provider, model)


def _ask(role: str, config: RunnableConfig, system: str, user: str, schema):
    return parse(_chat(role, config), schema).invoke([SystemMessage(system), HumanMessage(user)])


# ----------------------------------------------------------------------
# The roles.

def tech_lead(state: TeamState, config: RunnableConfig) -> dict:
    repo = state["ticket"]["repo"]
    previous = _notes(state) if state.get("notes") else ""
    task = _ticket(state) + _sent_back(state, "tech_lead", previous)
    system = TECH_LEAD.format(repo=repo, turns=TECH_LEAD_TOOL_CALLS)
    findings, calls = explore(_chat("tech_lead", config), state["code_dir"],
                              [SystemMessage(system), HumanMessage(task)], TECH_LEAD_TOOL_CALLS)
    notes = _ask("tech_lead", config, system,
                 task + "\n\n" + TECH_LEAD_NOTES.format(findings="\n\n".join(findings) or "(nothing)"), TechNotes)
    return {"notes": notes.model_dump(), "calls": calls, "log": [f"tech_lead: {len(calls)} tool calls"]}


def product_owner(state: TeamState, config: RunnableConfig) -> dict:
    previous = _stories(state) if state.get("draft") else ""
    draft = _ask("product_owner", config, PRODUCT_OWNER.format(repo=state["ticket"]["repo"]),
                 _ticket(state) + "\n\n" + _notes(state) + _sent_back(state, "product_owner", previous), Draft)
    # New stories make the old criteria and estimates meaningless.
    return {"draft": draft.model_dump(), "criteria": {}, "estimates": {},
            "log": [f"product_owner: {len(draft.stories)} stories, {len(draft.open_questions)} questions"]}


def qa(state: TeamState, config: RunnableConfig) -> dict:
    previous = _stories(state, criteria=True) if state.get("criteria") else ""
    out = _ask("qa", config, QA.format(repo=state["ticket"]["repo"]),
               _ticket(state) + "\n\n" + _notes(state) + "\n\n" + _stories(state) + _sent_back(state, "qa", previous),
               QaOutput)
    criteria = {str(s.story): s.criteria for s in out.stories}
    return {"criteria": criteria, "log": [f"qa: criteria for {len(criteria)} stories"]}


def estimator(state: TeamState, config: RunnableConfig) -> dict:
    previous = _stories(state, criteria=True, points=True) if state.get("estimates") else ""
    out = _ask("estimator", config, ESTIMATOR.format(repo=state["ticket"]["repo"]),
               _ticket(state) + "\n\n" + _notes(state) + "\n\n" + _stories(state, criteria=True)
               + _sent_back(state, "estimator", previous), Estimates)
    estimates = {str(e.story): {"points": e.points, "reason": e.reason} for e in out.estimates}
    return {"estimates": estimates, "log": [f"estimator: {', '.join(str(e['points']) for e in estimates.values())}"]}


def facilitator(state: TeamState, config: RunnableConfig) -> dict:
    count = len(state["draft"]["stories"])
    too_big = [n for n, e in state.get("estimates", {}).items() if e["points"] > MAX_POINTS]
    no_criteria = [str(n) for n in range(1, count + 1) if not state.get("criteria", {}).get(str(n))]
    rule_sendbacks, reviews = state.get("rule_sendbacks", 0), state.get("reviews", 0)
    # Hard rules first, whatever the routing, with their own limit.
    review = None
    if rule_sendbacks < MAX_RULE_SENDBACKS:
        if too_big:
            review = Review(reason="over the point limit", next="product_owner",
                            instruction=f"Split stories {', '.join(too_big)}: each is over {MAX_POINTS} points.")
        elif no_criteria:
            review = Review(reason="stories without criteria", next="qa",
                            instruction=f"Stories {', '.join(no_criteria)} have no acceptance criteria.")
    if review:
        rule_sendbacks += 1
    elif (config or {}).get("configurable", {}).get("routing", "facilitator") == "fixed":
        review = Review(reason="fixed routing", next="done")
    elif reviews >= MAX_REVIEWS:
        review = Review(reason="no send-backs left", next="done")
    else:
        review = _ask("facilitator", config, FACILITATOR.format(repo=state["ticket"]["repo"]),
                      _ticket(state) + "\n\n" + _notes(state) + "\n\n" + _stories(state, criteria=True, points=True),
                      Review)
        if review.next != "done":
            reviews += 1
    if review.next == "done":
        return {"next": "done", "log": [f"facilitator: done ({review.reason})"]}
    return {"next": review.next, "reviews": reviews, "rule_sendbacks": rule_sendbacks,
            "feedback": {**state.get("feedback", {}), review.next: review.instruction},
            "log": [f"facilitator: back to {review.next}: {review.instruction}"]}


def assemble(state: TeamState) -> dict:
    """The team's work as one Grooming, the format every system returns."""
    draft = Draft.model_validate(state["draft"])
    notes = TechNotes.model_validate(state["notes"])
    stories = [Story(title=s.title, user_story=s.user_story, dependencies=s.dependencies,
                     acceptance_criteria=state.get("criteria", {}).get(str(n), []),
                     points=state.get("estimates", {}).get(str(n), {}).get("points"))
               for n, s in enumerate(draft.stories, 1)]
    risks = [f"Already exists: {x}" for x in notes.already_exists] + notes.risks
    return {"grooming": Grooming(stories=stories, open_questions=draft.open_questions, risks=risks).model_dump()}


def _after_review(state: TeamState) -> str:
    return "assemble" if state["next"] == "done" else state["next"]


builder = StateGraph(TeamState)
for name, node in [("tech_lead", tech_lead), ("product_owner", product_owner), ("qa", qa),
                   ("estimator", estimator), ("facilitator", facilitator), ("assemble", assemble)]:
    builder.add_node(name, node)
builder.add_edge(START, "tech_lead")
for a, b in zip(ORDER, [*ORDER[1:], "facilitator"], strict=True):
    builder.add_edge(a, b)
builder.add_conditional_edges("facilitator", _after_review, [*ORDER, "assemble"])
builder.add_edge("assemble", END)
team = builder.compile()


def parse_models(spec: str | None) -> dict[str, str]:
    """"tech_lead=anthropic:claude-sonnet-5-5,default=anthropic:claude-haiku-5-5" -> a dict."""
    models = {"default": DEFAULT_MODEL}
    for part in filter(None, (spec or "").split(",")):
        role, _, model = part.partition("=")
        if role not in [*ORDER, "facilitator", "default"] or ":" not in model:
            raise ValueError(f"Bad model setting: {part!r} (want role=provider:model)")
        models[role] = model
    return models


def groom_with_team(ticket: dict, code_dir: str, models: dict[str, str], routing: str) -> tuple[Grooming, list[str], list[str]]:
    """(grooming, the tech lead's tool calls, the team's log)."""
    out = team.invoke({"ticket": ticket, "code_dir": code_dir},
                      {"configurable": {"models": models, "routing": routing}, "recursion_limit": 40})
    return Grooming.model_validate(out["grooming"]), out.get("calls", []), out.get("log", [])


if __name__ == "__main__":  # print the graph as a Mermaid diagram
    print(team.get_graph().draw_mermaid())
