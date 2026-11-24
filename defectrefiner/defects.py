"""Defects. A closed bug whose fix commit is on the main line has an answer
key: the files the fix changed. The agents see the code as it was just before
the fix (the first fix commit's parent), so the diff applies to exactly what
they read. Open bugs have no answer key; they're for real use and the demo.

`python -m defectrefiner.defects` writes data/defect_candidates.json (closed bugs
with a fix found) and data/open_bugs.json. The curated set is data/bugs.json:
reports rewritten without the reporter's diagnosis (see data/README.md).
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import warnings
from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel

from defectrefiner.code import _git, mirror
from defectrefiner.github import fetch_issues
from defectrefiner.tickets import DATA_DIR

CANDIDATES = DATA_DIR / "defect_candidates.json"
OPEN_BUGS = DATA_DIR / "open_bugs.json"
BUGS = DATA_DIR / "bugs.json"
LABEL = "bug"

# GitHub's closing keywords ("Fixes #12", "closes #3, #4"), plus the
# maintainer's "Fix for #12".
_CLOSES = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?(?: for)?|resolve[sd]?):?\s+(#\d+(?:(?:\s*,\s*|\s+and\s+)#\d+)*)", re.I)


class Commit(BaseModel):
    sha: str
    parent: str | None  # first parent: the main line before this commit
    date: datetime
    message: str


class Defect(BaseModel):
    number: int
    title: str
    body: str
    url: str
    author: str
    created_at: datetime
    closed_at: datetime | None = None
    fix_shas: list[str] = []  # oldest first
    base_sha: str | None = None  # the first fix commit's parent: the code the agents see
    source_files: list[str] = []  # the answer key for localisation
    test_files: list[str] = []
    other_files: list[str] = []  # docs, config
    fixed_before_report: bool = False  # a fix older than the issue: the report describes known work
    names_a_file: list[str] = []  # changed source files the title or body already names: the answer leaks

    @property
    def usable(self) -> bool:
        return bool(self.base_sha and self.source_files and not self.fixed_before_report)


class Location(BaseModel):
    """Somewhere a fix changed code."""
    file: str
    function: str | None = None  # qualified, e.g. "TableView.data"; None for a template or module-level code
    names: list[str] = []  # the enclosing functions, outermost first: a suspect naming any of them hits
    start: int | None = None  # the function's first and last lines, before the fix
    end: int | None = None
    lines: list[int] = []  # the lines the fix changed here, before the fix


class Bug(BaseModel):
    """A bug in the eval set. The agents see `title` and `body` and the code at
    `base_sha`; `source_files` is the answer key."""
    id: str  # "bug-1228"
    repo: str
    title: str
    body: str  # rewritten: symptoms, steps and errors kept, the reporter's diagnosis cut
    created_at: datetime
    url: str
    reporter: Literal["outside", "maintainer"]
    traceback: bool  # the body has a stack trace, which can point at the file: scores are split on it
    removed: str | None  # what the rewrite took out, so the edits are open to review
    base_sha: str
    fix_shas: list[str]
    source_files: list[str]
    test_files: list[str]
    locations: list[Location] = []  # the answer key at function level (see fixed_locations)


def locate(bugs: list[Bug]) -> list[Bug]:
    """The bugs with `locations` worked out from their fix commits."""
    out = []
    for repo in {b.repo for b in bugs}:
        git_dir = mirror(repo)
        commits = {c.sha: c for c in main_line_commits(git_dir)}
        out += [b.model_copy(update={"locations": fixed_locations(git_dir, [commits[s] for s in b.fix_shas], b.source_files)})
                for b in bugs if b.repo == repo]
    return sorted(out, key=lambda b: int(b.id.split("-")[1]))


def load_bugs(path: Path | None = None) -> list[Bug]:
    return [Bug.model_validate(b) for b in json.loads((path or BUGS).read_text())]


def closes(message: str) -> set[int]:
    """Issue numbers a commit message says it closes or fixes."""
    return {int(n) for group in _CLOSES.findall(message) for n in re.findall(r"\d+", group)}


def main_line_commits(git_dir: Path) -> list[Commit]:
    """Every commit reachable from HEAD, oldest first."""
    out = _git("-C", git_dir, "log", "--reverse", "--format=%H%x00%P%x00%cI%x00%B%x1e", "HEAD")
    commits = []
    for record in filter(None, (r.strip("\n") for r in out.split("\x1e"))):
        sha, parents, date, message = record.split("\x00", 3)
        commits.append(Commit(sha=sha, parent=(parents.split() or [None])[0], date=date, message=message))
    return commits


def changed_files(git_dir: Path, commit: Commit) -> list[str]:
    """Files a commit changed against its first parent (so a merge counts as
    the whole branch)."""
    if commit.parent is None:
        return []
    return _git("-C", git_dir, "diff", "--name-only", commit.parent, commit.sha).splitlines()


def kind(path: str) -> str:
    if path.startswith("tests/") or Path(path).name.startswith("test_"):
        return "test"
    if path.endswith(".py") and not path.startswith(("docs/", "setup.py")):
        return "source"
    if path.startswith(f"{Path(path).parts[0]}/templates/") or path.endswith((".html", ".js", ".css")):
        return "source"  # templates and static assets are where some bugs live
    return "other"


def changed_lines(git_dir: Path, commit: Commit, path: str) -> list[int]:
    """Lines of `path` before the commit that it changed. A pure insertion
    counts as the line it was inserted after."""
    diff = _git("-C", git_dir, "diff", "-U0", commit.parent, commit.sha, "--", path)
    lines = []
    for start, count in re.findall(r"^@@ -(\d+)(?:,(\d+))? ", diff, re.M):
        start, count = int(start), 1 if count == "" else int(count)
        lines += range(start, start + count) if count else [max(start, 1)]
    return lines


class Spot(NamedTuple):
    function: str | None  # qualified name
    names: list[str]
    start: int | None = None
    end: int | None = None


def enclosing(source: str, line: int) -> Spot:
    """The innermost function holding `line`: its qualified name, the names of
    every function around it, and its span. Between a class's methods (a
    method added, say) it's the class; outside any class or function, nothing."""
    best = Spot(None, [])

    def visit(node: ast.AST, qual: list[str], funcs: list[str]) -> None:
        nonlocal best
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                start = min([child.lineno, *(d.lineno for d in child.decorator_list)])
                if start <= line <= child.end_lineno:
                    is_func = not isinstance(child, ast.ClassDef)
                    q, f = [*qual, child.name], [*funcs, child.name] if is_func else funcs
                    best = Spot(".".join(q), f if is_func or funcs else [child.name], start, child.end_lineno)
                    visit(child, q, f)
            else:
                visit(child, qual, funcs)

    with warnings.catch_warnings():  # old code's invalid escapes in strings
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    visit(tree, [], [])
    return best


def fixed_locations(git_dir: Path, fixes: list[Commit], source_files: list[str]) -> list[Location]:
    """Where the fixes changed the source files: the functions for Python
    (read as the code was just before each fix), the file for templates and
    for files changed only outside functions. Line numbers are as of each fix
    commit's parent, which for all but a later commit of a multi-commit fix
    is the code the agents see."""
    found: dict[tuple[str, str | None], Location] = {}
    for commit in fixes:
        for path in source_files:
            lines = changed_lines(git_dir, commit, path)
            if not lines:
                continue
            try:
                source = _git("-C", git_dir, "show", f"{commit.parent}:{path}") if path.endswith(".py") else None
                spots = [enclosing(source, n) for n in lines] if source is not None else [Spot(None, [])] * len(lines)
            except (subprocess.CalledProcessError, SyntaxError):  # a new file, or code that won't parse
                spots = [Spot(None, [])] * len(lines)
            for line, spot in zip(lines, spots, strict=True):
                loc = found.setdefault((path, spot.function), Location(file=path, function=spot.function, names=spot.names,
                                                                       start=spot.start, end=spot.end))
                loc.lines.append(line)
    # An import added at the top shouldn't let naming the file alone count
    # when the fix also changed functions in it.
    with_functions = {path for path, function in found if function}
    return [loc for (path, function), loc in found.items() if function or path not in with_functions]


def to_defect(issue: dict) -> Defect:
    return Defect(number=issue["number"], title=issue["title"], body=issue.get("body") or "",
                  url=issue["html_url"], author=issue["user"]["login"], created_at=issue["created_at"],
                  closed_at=issue.get("closed_at"))


def attach_fix(defect: Defect, fixes: list[Commit], git_dir: Path) -> Defect:
    """Fill in the fix commits, the base to check out and the changed files."""
    if not fixes:
        return defect
    files = sorted({f for c in fixes for f in changed_files(git_dir, c)})
    text = f"{defect.title}\n{defect.body}"
    source = [f for f in files if kind(f) == "source"]
    return defect.model_copy(update={
        "fix_shas": [c.sha for c in fixes],
        "base_sha": fixes[0].parent,
        "source_files": source,
        "test_files": [f for f in files if kind(f) == "test"],
        "other_files": [f for f in files if kind(f) == "other"],
        "fixed_before_report": fixes[0].date < defect.created_at,
        "names_a_file": [f for f in source if Path(f).name in text],
    })


def is_bug(issue: dict) -> bool:
    """A bug report, not a PR; closed ones must be completed, not "not planned"."""
    return "pull_request" not in issue and issue.get("state_reason") in (None, "completed", "reopened")


def find(repo: str) -> tuple[list[Defect], list[Defect]]:
    """Closed bugs with their fixes, and open bugs."""
    git_dir = mirror(repo)
    commits = main_line_commits(git_dir)
    fixes_for: dict[int, list[Commit]] = {}
    for c in commits:
        for n in closes(c.message):
            fixes_for.setdefault(n, []).append(c)

    closed = [attach_fix(to_defect(i), fixes_for.get(i["number"], []), git_dir)
              for i in fetch_issues(repo, "closed", LABEL) if is_bug(i)]
    open_ = [to_defect(i) for i in fetch_issues(repo, "open", LABEL) if is_bug(i)]
    return sorted(closed, key=lambda d: d.number), sorted(open_, key=lambda d: d.number)


def save(defects: list[Defect], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([d.model_dump(mode="json") for d in defects], indent=2) + "\n")


def load(path: Path | None = None) -> list[Defect]:
    return [Defect.model_validate(d) for d in json.loads((path or CANDIDATES).read_text())]


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m defectrefiner.defects", description="Find bugs and their fixes.")
    parser.add_argument("repo", nargs="?", default="simonw/datasette")
    parser.add_argument("--locate", action="store_true", help="fill in the fixed functions of data/bugs.json instead")
    args = parser.parse_args(argv)

    if args.locate:
        bugs = locate(load_bugs())
        BUGS.write_text(json.dumps([b.model_dump(mode="json") for b in bugs], indent=2, ensure_ascii=False) + "\n")
        print(f"{len(bugs)} bugs, {sum(len(b.locations) for b in bugs)} locations -> {BUGS}")
        for b in bugs:
            print(f"  {b.id}  " + ", ".join(f"{loc.file.rsplit('/', 1)[-1]}:{loc.function or '-'}" for loc in b.locations))
        return 0

    closed, open_ = find(args.repo)
    found = [d for d in closed if d.fix_shas]
    usable = [d for d in found if d.usable]
    save(found, CANDIDATES)
    save(open_, OPEN_BUGS)

    print(f"{len(closed)} closed bugs, {len(found)} with a fix commit -> {CANDIDATES}")
    print(f"  no source file changed: {sum(not d.source_files for d in found)}")
    print(f"  fixed before reported:  {sum(d.fixed_before_report for d in found)}")
    print(f"  usable:                 {len(usable)}")
    print(f"    with a test added:    {sum(bool(d.test_files) for d in usable)}")
    print(f"    one fix commit:       {sum(len(d.fix_shas) == 1 for d in usable)}")
    print(f"    names a fixed file:   {sum(bool(d.names_a_file) for d in usable)}")
    print(f"    by the maintainer:    {sum(d.author == 'simonw' for d in usable)}")
    print(f"{len(open_)} open bugs -> {OPEN_BUGS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
