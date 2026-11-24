import threading

from defectrefiner import bug_team
from defectrefiner.bug_team import Scenario, TestPlan
from defectrefiner.bugs import BugGrooming, Suspect

GROOMING = BugGrooming(triage="bug", summary="CSV export drops the last row", steps_to_reproduce=["Export a table"],
                       expected="All rows", actual="One missing", suspects=[Suspect(path="datasette/views/base.py",
                       function="as_csv", lines="380-390", reason="off by one")], cause="range stops early",
                       fix_approach="Fix the range", regression_test="...", severity="high", points=2)
PLAN = TestPlan(test_file="tests/test_csv.py", regression_test="test_csv_includes_last_row", reuse=["app_client"],
                scenarios=[Scenario(description="Empty table", automate=True, where="tests/test_csv.py")])
BUG = {"id": "bug-1", "repo": "simonw/datasette", "title": "CSV export loses a row", "body": "..."}


def test_agents_run_in_parallel_then_join(monkeypatch):
    both_running = threading.Barrier(2, timeout=5)  # breaks unless the two agents run at the same time
    seen = {}

    def groom_bug(bug, code_dir, chat):
        both_running.wait()
        return GROOMING, ["grep(pattern='csv')"]

    def explore(chat, code_dir, messages, max_tool_calls):
        both_running.wait()
        return ["grep(pattern='csv', directory='tests'):\ntests/test_csv.py:10: def test_table_csv"], ["grep(pattern='csv')"]

    class Planner:
        def invoke(self, messages):
            seen["planner_input"] = messages[1].content
            return PLAN

    monkeypatch.setattr(bug_team, "groom_bug", groom_bug)
    monkeypatch.setattr(bug_team, "explore", explore)
    monkeypatch.setattr(bug_team, "parse", lambda chat, schema: Planner())
    monkeypatch.setattr(bug_team, "_chat", lambda role, config: None)

    out = bug_team.groom_with_bug_team(BUG, "/x")
    # The planner sees the investigator's cause and the scout's raw findings.
    assert "range stops early" in seen["planner_input"] and "tests/test_csv.py:10" in seen["planner_input"]
    assert sorted(out["calls"]) == ["investigator: grep(pattern='csv')", "test_scout: grep(pattern='csv')"]
    assert set(out["seconds"]) == {"investigator", "test_scout", "test_planner"}
    assert "**Regression test** (`tests/test_csv.py`): test_csv_includes_last_row" in out["ticket"]
    assert "[automate] Empty table (tests/test_csv.py)" in out["ticket"]
    assert "`datasette/views/base.py` `as_csv` lines 380-390: off by one" in out["ticket"]


def test_studio_graph_loads_the_report_not_the_fix(monkeypatch):
    from defectrefiner import code, graph
    from defectrefiner.defects import load_bugs

    checked_out = []
    monkeypatch.setattr(code, "checkout_at", lambda repo, sha: checked_out.append((repo, sha)) or "/code")
    bug = next(b for b in load_bugs() if b.id == "bug-393")
    state = graph.load_bug({"bug_id": "bug-393"})
    assert state["bug"] == {"id": "bug-393", "repo": bug.repo, "title": bug.title, "body": bug.body}  # no fix, no answers
    assert checked_out == [(bug.repo, bug.base_sha)] and state["code_dir"] == "/code"
    nodes = set(graph.bug_graph.get_graph(xray=1).nodes)  # xray: Studio shows the team inside
    assert {"load_bug", "bug_team:investigator", "bug_team:test_scout", "bug_team:test_planner",
            "bug_team:write_ticket"} <= nodes
