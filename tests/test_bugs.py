import json
from datetime import UTC, datetime

from defectrefiner import bugs
from defectrefiner.defects import Bug, Location


def bug(**overrides):
    return Bug(**{"id": "bug-1", "repo": "o/r", "title": "t", "body": "b", "created_at": datetime(2021, 1, 1, tzinfo=UTC),
                  "url": "u", "reporter": "outside", "traceback": False, "removed": None, "base_sha": "a" * 40,
                  "fix_shas": ["b" * 40], "source_files": ["datasette/views/table.py", "datasette/facets.py"],
                  "test_files": [], "locations": [
                      Location(file="datasette/views/table.py", function="TableView.data", names=["data"],
                               start=100, end=400, lines=[250, 251]),
                      Location(file="datasette/views/table.py", function="table_view_data.inner",
                               names=["table_view_data", "inner"], start=500, end=520, lines=[510]),
                      Location(file="datasette/templates/table.html", lines=[30])], **overrides})


def s(path, function=None, lines=None):
    return {"path": path, "function": function, "lines": lines, "reason": "r"}


def test_same_file():
    assert bugs._same_file("datasette/views/table.py", "datasette/views/table.py")
    assert bugs._same_file("./views/table.py", "datasette/views/table.py")
    assert not bugs._same_file("table.py", "datasette/templates/_table.py.html")
    assert not bugs._same_file("s/table.py", "datasette/views/table.py")  # a tail must be whole path parts
    assert not bugs._same_file("", "datasette/views/table.py")


def test_file_level():
    b = bug()
    suspects = [s("datasette/app.py"), s("datasette/utils/__init__.py"), s("datasette/facets.py")]
    assert not bugs.hit_at(suspects, b, 2, "file") and bugs.hit_at(suspects, b, 3, "file")
    assert not bugs.hit_at([s("datasette/app.py")], b, 3, "file")


def test_function_level():
    b = bug()
    assert bugs.hit_at([s("datasette/views/table.py", "TableView.data()")], b, 1)
    assert bugs.hit_at([s("views/table.py", "data")], b, 1)
    assert bugs.hit_at([s("datasette/views/table.py", "table_view_data")], b, 1)  # around the changed function
    assert not bugs.hit_at([s("datasette/views/table.py", "TableView")], b, 1)  # the class alone isn't the place
    assert not bugs.hit_at([s("datasette/views/table.py", "_next_value_and_url")], b, 1)
    assert not bugs.hit_at([s("datasette/views/table.py")], b, 1)  # the file alone
    assert not bugs.hit_at([s("datasette/views/row.py", "data")], b, 1)  # right name, wrong file
    assert bugs.hit_at([s("datasette/templates/table.html")], b, 1)  # a template has no functions
    assert bugs.hit_at([s("datasette/views/table.py", None, "about 380-420")], b, 1)  # lines inside the function
    assert bugs.hit_at([s("datasette/views/table.py", "wrong_name", "505")], b, 1)
    assert not bugs.hit_at([s("datasette/views/table.py", None, "401-499")], b, 1)


def test_line_level():
    b = bug()
    assert bugs.line_ranges("Lines 825-829, and 840–844; L12") == [(825, 829), (840, 844), (12, 12)]
    assert bugs.hit_at([s("datasette/views/table.py", None, "255-260")], b, 1, "line")
    assert bugs.hit_at([s("datasette/views/table.py", None, "520")], b, 1, "line")  # within LINE_SLACK of 510
    assert not bugs.hit_at([s("datasette/views/table.py", None, "300-320")], b, 1, "line")  # in the function, far off
    assert not bugs.hit_at([s("datasette/views/table.py", "data")], b, 1, "line")  # a name isn't a line
    assert not bugs.hit_at([s("datasette/views/row.py", None, "250")], b, 1, "line")
    # facets.py is a fixed file but has no location: a file hit, not a function hit.
    assert bugs.hit_at([s("datasette/facets.py", "x")], b, 1, "file") and not bugs.hit_at([s("datasette/facets.py", "x")], b, 1)


def test_report_splits_scores(tmp_path, monkeypatch):
    seen = bug(id="bug-1", test_files=["tests/test_api.py"])
    unseen = bug(id="bug-2", created_at=datetime(2026, 9, 1, tzinfo=UTC), traceback=True)
    monkeypatch.setattr(bugs, "load_bugs", lambda: [seen, unseen])

    def save(bug_id, paths):
        path = tmp_path / bug_id / "1.json"
        path.parent.mkdir()
        grooming = {"triage": "bug", "suspects": [s(*p) for p in paths], "regression_test": "In tests/test_api.py ..."}
        path.write_text(json.dumps({"bug_id": bug_id, "grooming": grooming, "tool_calls": ["grep()"],
                                    "seconds": 1.0, "cost_usd": 0.01}))

    save("bug-1", [("datasette/views/table.py", "data")])
    save("bug-2", [("datasette/app.py", None), ("datasette/views/table.py", "other")])
    summary = bugs.report(tmp_path)
    assert summary["function@1"]["all"] == "1/2 (50%)" and summary["function@3"]["all"] == "1/2 (50%)"
    assert summary["file@1"]["all"] == "1/2 (50%)" and summary["file@3"]["all"] == "2/2 (100%)"
    assert summary["function@1"]["no_traceback"] == "1/1 (100%)"
    assert summary["file@1"]["unseen"] == "0/1 (0%)" and summary["file@3"]["unseen"] == "1/1 (100%)"
    assert summary["triaged_as_bug"] == "2/2" and summary["total_cost_usd"] == 0.02
    assert summary["test_file"] == "1/1" and summary["names_any_test_file"] == "2/2"


def test_names_test_file():
    b = bug(test_files=["tests/fixtures.py", "tests/test_html.py"])
    assert bugs.fix_test_files(b) == ["tests/test_html.py"]
    assert bugs.names_test_file("Add a case to tests/test_html.py that ...", b)
    assert bugs.names_test_file("In test_html.py, ...", b)
    assert not bugs.names_test_file("In tests/test_table_html.py, ...", b)
    assert not bugs.names_test_file("Add a fixture to tests/fixtures.py", b)
