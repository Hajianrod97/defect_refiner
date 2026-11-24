import os
import subprocess

import httpx
import pytest

from defectrefiner import code, defects
from defectrefiner.github import fetch_issues


def commit(repo, files, message, date):
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-qm", message], check=True, env=env)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Buggy code, then a fix for #7 with a test, then an unrelated change."""
    monkeypatch.setenv("DEFECTREFINER_CACHE", str(tmp_path / "cache"))
    path = tmp_path / "upstream"
    subprocess.run(["git", "init", "-q", "-b", "main", path], check=True)
    subprocess.run(["git", "-C", path, "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", path, "config", "user.name", "t"], check=True)
    commit(path, {"pkg/app.py": "buggy"}, "Start", "2021-01-01T00:00:00Z")
    commit(path, {"pkg/app.py": "fixed", "tests/test_app.py": "t", "docs/n.rst": "n"}, "Fix for #7", "2021-02-01T00:00:00Z")
    commit(path, {"pkg/other.py": "x"}, "Refs #7, unrelated", "2021-03-01T00:00:00Z")
    return path


def issue(number=7, created_at="2021-01-15T00:00:00Z", **overrides):
    return {"number": number, "title": "App crashes", "body": "It crashes.", "user": {"login": "someone"},
            "html_url": f"https://github.com/o/r/issues/{number}", "created_at": created_at,
            "state_reason": "completed", **overrides}


def test_closes():
    assert defects.closes("Fixes #12") == {12}
    assert defects.closes("closes #3, #4 and #5") == {3, 4, 5}
    assert defects.closes("Fix for #9\n\nRefs #10") == {9}
    assert defects.closes("Refs #10, see #11") == set()


def test_kind():
    assert defects.kind("tests/test_app.py") == "test"
    assert defects.kind("datasette/app.py") == "source"
    assert defects.kind("datasette/templates/table.html") == "source"
    assert defects.kind("docs/changelog.rst") == "other"
    assert defects.kind("setup.py") == "other"


def test_fix_found_and_base_is_its_parent(repo):
    git_dir = code.mirror(str(repo))
    commits = defects.main_line_commits(git_dir)
    fixes = [c for c in commits if 7 in defects.closes(c.message)]
    defect = defects.attach_fix(defects.to_defect(issue()), fixes, git_dir)

    assert defect.fix_shas == [commits[1].sha] and defect.base_sha == commits[0].sha
    assert defect.source_files == ["pkg/app.py"]
    assert defect.test_files == ["tests/test_app.py"] and defect.other_files == ["docs/n.rst"]
    assert defect.usable and not defect.names_a_file
    # The agents see the code before the fix, not after.
    assert (code.checkout_at(str(repo), defect.base_sha) / "pkg/app.py").read_text() == "buggy"


def test_flags(repo):
    git_dir = code.mirror(str(repo))
    fixes = [c for c in defects.main_line_commits(git_dir) if 7 in defects.closes(c.message)]
    late = defects.attach_fix(defects.to_defect(issue(created_at="2021-06-01T00:00:00Z")), fixes, git_dir)
    assert late.fixed_before_report and not late.usable
    named = defects.attach_fix(defects.to_defect(issue(body="Traceback in app.py line 3")), fixes, git_dir)
    assert named.names_a_file == ["pkg/app.py"]
    assert not defects.to_defect(issue()).usable  # no fix found


def test_is_bug():
    assert defects.is_bug(issue()) and defects.is_bug(issue(state_reason=None))
    assert not defects.is_bug(issue(pull_request={}))
    assert not defects.is_bug(issue(state_reason="not_planned"))


def test_fetch_filters_by_state_and_label():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=[issue()])

    fetch_issues("o/r", "open", "bug", httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen == [{"state": "open", "per_page": "100", "labels": "bug"}]


def test_bug_set():
    """data/bugs.json: every bug has an answer key and a base to check out,
    and only the bugs flagged as having a stack trace name a fixed file."""
    bugs = defects.load_bugs()
    assert len(bugs) == len({b.id for b in bugs})
    for b in bugs:
        assert b.source_files and b.base_sha and b.fix_shas
        names = [f for f in b.source_files if f.rsplit("/", 1)[-1] in b.title + b.body]
        assert not names or b.traceback, b.id


SOURCE = '''import re

PATTERN = re.compile("x")


class View:
    limit = 10

    @property
    def path(self):
        return 1

    async def data(self):
        def inner():
            return 2
        return inner()
'''


def test_enclosing():
    assert defects.enclosing(SOURCE, 3)[:2] == (None, [])  # module level
    assert defects.enclosing(SOURCE, 7)[:2] == ("View", ["View"])  # class body, between methods
    assert defects.enclosing(SOURCE, 9) == ("View.path", ["path"], 9, 11)  # the decorator line; the span includes it
    assert defects.enclosing(SOURCE, 11)[:2] == ("View.path", ["path"])
    assert defects.enclosing(SOURCE, 15) == ("View.data.inner", ["data", "inner"], 14, 15)
    assert defects.enclosing(SOURCE, 16) == ("View.data", ["data"], 13, 16)


def test_fixed_locations(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFECTREFINER_CACHE", str(tmp_path / "cache"))
    path = tmp_path / "upstream"
    subprocess.run(["git", "init", "-q", "-b", "main", path], check=True)
    subprocess.run(["git", "-C", path, "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", path, "config", "user.name", "t"], check=True)
    commit(path, {"pkg/app.py": SOURCE, "pkg/page.html": "<p>"}, "Start", "2021-01-01T00:00:00Z")
    fixed = SOURCE.replace("import re", "import re\nimport os").replace("return 2", "return 3")
    commit(path, {"pkg/app.py": fixed, "pkg/page.html": "<p>!"}, "Fixes #7", "2021-02-01T00:00:00Z")

    git_dir = code.mirror(str(path))
    fixes = [c for c in defects.main_line_commits(git_dir) if 7 in defects.closes(c.message)]
    assert defects.changed_lines(git_dir, fixes[0], "pkg/app.py") == [1, 15]  # an insertion counts as the line before
    locations = defects.fixed_locations(git_dir, fixes, ["pkg/app.py", "pkg/page.html"])
    # The added import (module level) is dropped: the file also has a changed function.
    assert [(loc.file, loc.function, loc.lines) for loc in locations] == [("pkg/app.py", "View.data.inner", [15]),
                                                                          ("pkg/page.html", None, [1])]
    assert (locations[0].start, locations[0].end) == (14, 15)
