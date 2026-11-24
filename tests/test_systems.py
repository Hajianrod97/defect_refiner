import subprocess
from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage

from defectrefiner.run import summarise
from defectrefiner.score import Coverage, TicketScore
from defectrefiner.systems import _with_tool_calls
from defectrefiner.tickets import Ticket, Trap
from defectrefiner.tools import code_tools


@pytest.fixture
def tools(tmp_path):
    subprocess.run(["git", "init", "-q", tmp_path], check=True)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_text("\n".join(f"line {n}" for n in range(1, 101)) + "\nCORS = True\n")
    (tmp_path / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", tmp_path, "add", "."], check=True)
    return {t.name: t for t in code_tools(tmp_path)}


def test_tools_read_the_checkout(tools):
    assert tools["list_files"].invoke({"directory": "pkg"}) == "pkg/app.py"
    assert tools["grep"].invoke({"pattern": "CORS"}) == "pkg/app.py:101:CORS = True"
    page = tools["read_file"].invoke({"path": "pkg/app.py", "start": 41})
    assert page.startswith("41: line 41") and "file continues to line 101" in page


def test_long_lines_are_cut(tmp_path):
    subprocess.run(["git", "init", "-q", tmp_path], check=True)
    (tmp_path / "app.min.js").write_text("var x=1;" * 10_000 + "\n")  # minified: one line of 80,000 characters
    subprocess.run(["git", "-C", tmp_path, "add", "."], check=True)
    tools = {t.name: t for t in code_tools(tmp_path)}
    assert tools["grep"].invoke({"pattern": "var"}).endswith("... (80013 characters)")
    assert len(tools["read_file"].invoke({"path": "app.min.js"})) < 400


def test_listings_show_more_than_a_page(tmp_path):
    subprocess.run(["git", "init", "-q", tmp_path], check=True)
    (tmp_path / "tests").mkdir()
    for n in range(105):  # datasette's tests/ today
        (tmp_path / "tests" / f"test_{n:03}.py").write_text("")
    subprocess.run(["git", "-C", tmp_path, "add", "."], check=True)
    listing = {t.name: t for t in code_tools(tmp_path)}["list_files"].invoke({"directory": "tests"})
    assert "tests/test_104.py" in listing and "more lines" not in listing


def test_tools_stay_inside_the_checkout(tools):
    with pytest.raises(ValueError):
        tools["read_file"].invoke({"path": "../../etc/passwd"})
    assert tools["read_file"].invoke({"path": "missing.py"}).startswith("Error")


def test_tool_calls_written_as_text_become_real():
    reply = AIMessage(content='{"name": "grep", "arguments": {"pattern": "cors"}}')
    fixed = _with_tool_calls(reply, {"grep", "read_file"})
    assert fixed.tool_calls[0]["name"] == "grep" and fixed.tool_calls[0]["args"] == {"pattern": "cors"}


def test_ordinary_answers_stay_answers():
    for content in ["Here is my plan", '{"name": "rm", "arguments": {}}', '{"stories": []}']:
        assert _with_tool_calls(AIMessage(content=content), {"grep"}).tool_calls == []


def test_summarise():
    when = datetime(2025, 1, 1, tzinfo=UTC)
    tickets = {
        "ds-1": Ticket(id="ds-1", source="real", repo="r", title="t", body="b", created_at=when, reference=["a", "b"]),
        "w-01": Ticket(id="w-01", source="written", repo="r", title="t", body="b", created_at=when, reference=["a"],
                       traps=[Trap(kind="ambiguous", expect="", all_of=[["x"]])]),
    }
    yes, no = Coverage(reason="", verdict="yes"), Coverage(reason="", verdict="no")
    run = {"seconds": 10, "input_tokens": 100, "output_tokens": 50, "grooming": {"stories": [{"title": "x", "user_story": "", "acceptance_criteria": []}]}}
    rows = [
        (run, TicketScore(ticket_id="ds-1", coverage=[yes, no], testable=[True, False], traps=[], points=8)),
        (run, TicketScore(ticket_id="ds-1", coverage=[yes, yes], testable=[True], traps=[], points=4)),
        (run, TicketScore(ticket_id="w-01", coverage=[yes], testable=[True], traps=[True], points=3)),
        ({"error": "boom"}, None),
    ]
    summary = summarise(rows, tickets)
    assert summary["groomings"] == 4 and summary["failed"] == 1
    assert summary["coverage_real"] == 0.75 and summary["coverage_written"] == 1
    assert summary["traps"] == {"ambiguous": "1/1"}
    assert summary["points_spread"] == round(4 / 6, 2)
    assert summary["mean_tokens"] == 150


def test_cost_counts_cache_reads_cheaply():
    from defectrefiner.llm import cost
    usage = {"claude-haiku-5-5": {"input_tokens": 1_000_000, "output_tokens": 0,
                                  "input_token_details": {"cache_read": 900_000, "cache_creation": 0}}}
    assert round(cost(usage), 4) == round((100_000 * 0.10 + 900_000 * 0.01) / 1e6, 4)


def test_parallel_map_keeps_the_usage_callback():
    from langchain_core.callbacks import get_usage_metadata_callback
    from langchain_core.language_models import GenericFakeChatModel

    from defectrefiner.run import parallel_map
    usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    model = GenericFakeChatModel(messages=iter([AIMessage("x", usage_metadata=usage, response_metadata={"model_name": "fake"})
                                                for _ in range(4)]))
    with get_usage_metadata_callback() as cb:
        parallel_map(lambda _: model.invoke("hi"), range(4), 4)
    assert cb.usage_metadata["fake"]["input_tokens"] == 40  # all four threaded calls counted
