"""The baselines the multi-agent system is measured against. Every system gets
the same instructions (GROOMING_RULES) and returns a Grooming; only the way
they work differs.

- prompt: one model call with the ticket and the repo's file list.
- agent: one model with read-only code tools, then a final structured answer.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from defectrefiner.grooming import Grooming
from defectrefiner.llm import parse
from defectrefiner.tickets import Ticket
from defectrefiner.tools import code_tools, file_listing

GROOMING_RULES = """You groom backlog tickets for {repo}, an open-source Python project, the way a scrum team does in refinement.

Turn the ticket into stories:
- Split the work into stories that each deliver something useful on their own. Keep unrelated requests in separate stories, but don't split one change into pieces nobody would ship separately.
- Tests and documentation are part of finishing a story: put them in its acceptance criteria, not in stories of their own.
- Each story has a user story ("As a ..., I want ..., so that ...") and acceptance criteria a tester could check: behaviour someone can observe, not how it's built. Where a criterion depends on an open question, write it for the most likely answer and say so.
- Estimate every story in points: 1, 2, 3, 5, 8 or 13. Split anything bigger than 8.
- Note dependencies between stories.
- Open questions are for the requester, about what they want: only questions whose answer changes the work, most important first, each ending with the assumption you made, as "(Assuming: ...)". Don't ask the requester what the code could answer.
- Risks: technical risks, conflicts with how the code works today, anything the ticket asks for that already exists, and anything about the code you couldn't confirm and someone should check.
Don't invent requirements the ticket doesn't imply."""

TICKET = """<ticket>
{title}

{body}
</ticket>"""

PROMPT_TASK = """The repository's files, for orientation:
<files>
{files}
</files>

Groom the ticket."""

AGENT_TASK = """You can look at the repository's code with list_files, grep and read_file before answering. Look at the parts the ticket touches, then stop: you have at most {turns} tool calls."""

AGENT_FINISH = """What you found in the code:
<findings>
{findings}
</findings>

Now groom the ticket, using what you found."""
MAX_TOOL_CALLS = 12
MAX_FILES = 400  # paths in the prompt baseline's file list


def _ticket(ticket: Ticket) -> str:
    return TICKET.format(title=ticket.title, body=ticket.body)


def groom_with_prompt(ticket: Ticket, code_dir: str | Path, chat: BaseChatModel) -> Grooming:
    files = [f for f in file_listing(Path(code_dir)) if not f.startswith(("tests/", "docs/"))][:MAX_FILES]
    messages = [SystemMessage(GROOMING_RULES.format(repo=ticket.repo)),
                HumanMessage(_ticket(ticket) + "\n\n" + PROMPT_TASK.format(files="\n".join(files)))]
    return parse(chat, Grooming).invoke(messages)


def _with_tool_calls(reply: AIMessage, tool_names: set[str]) -> AIMessage:
    """The reply, with tool calls written as plain JSON text turned into real
    ones. qwen2.5-coder in Ollama often answers `{"name": ..., "arguments": ...}`
    as text instead of a tool call, which would end the loop at once."""
    if reply.tool_calls or not isinstance(reply.content, str):
        return reply
    calls = []
    for line in reply.content.strip().removeprefix("```json").removesuffix("```").strip().splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return reply
        if not isinstance(data, dict) or data.get("name") not in tool_names:
            return reply
        calls.append({"name": data["name"], "args": data.get("arguments") or {}, "id": f"call_{uuid.uuid4().hex[:8]}"})
    return AIMessage(content="", tool_calls=calls) if calls else reply


def explore(chat: BaseChatModel, code_dir: str | Path, messages: list, max_tool_calls: int) -> tuple[list[str], list[str]]:
    """Let the model look at the code with the read-only tools until it stops
    or runs out of calls. Returns (findings: each call with its result, plus
    any notes the model wrote; the calls made, as "name(args)")."""
    tools = {t.name: t for t in code_tools(code_dir)}
    model = chat.bind_tools(list(tools.values()))
    messages = list(messages)
    calls: list[str] = []
    findings: list[str] = []
    while len(calls) < max_tool_calls:
        reply = _with_tool_calls(model.invoke(messages), set(tools))
        messages.append(reply)
        if reply.text.strip():
            findings.append(f"Note: {reply.text.strip()}")
        if not reply.tool_calls:
            break
        for call in reply.tool_calls:
            calls.append(f"{call['name']}({', '.join(f'{k}={v!r}' for k, v in call['args'].items())})")
            try:
                result = tools[call["name"]].invoke(call["args"]) if call["name"] in tools else f"Error: no tool {call['name']}"
            except Exception as e:  # a bad path or argument: tell the model, don't stop
                result = f"Error: {e}"
            messages.append(ToolMessage(str(result), tool_call_id=call["id"]))
            findings.append(f"{calls[-1]}:\n{result}")
    return findings, calls


def groom_with_agent(ticket: Ticket, code_dir: str | Path, chat: BaseChatModel,
                     max_tool_calls: int = MAX_TOOL_CALLS) -> tuple[Grooming, list[str]]:
    """(grooming, the tool calls made, as "name(args)" for the record)."""
    messages = [SystemMessage(GROOMING_RULES.format(repo=ticket.repo)),
                HumanMessage(_ticket(ticket) + "\n\n" + AGENT_TASK.format(turns=max_tool_calls))]
    findings, calls = explore(chat, code_dir, messages, max_tool_calls)
    # The answer is written from a text copy of the exploration rather than the
    # tool-call history: Claude rejects tool calls in a request that declares no
    # tools, and declaring them would invite more calls.
    final = [SystemMessage(GROOMING_RULES.format(repo=ticket.repo)),
             HumanMessage(_ticket(ticket) + "\n\n" + AGENT_FINISH.format(findings="\n\n".join(findings) or "(nothing)"))]
    return parse(chat, Grooming).invoke(final), calls
