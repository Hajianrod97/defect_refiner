"""Read-only tools for looking at the checked-out code. Paths are relative to
the checkout, and anything outside it is refused."""

from __future__ import annotations

import subprocess
from pathlib import Path

from langchain_core.tools import BaseTool, tool

MAX_LINES = 60  # of one tool reply, so a careless call can't fill the context
MAX_FILES = 200  # of a listing: file names are short, and an agent that sees only part of tests/ misses test files
MAX_LINE_CHARS = 300  # of one line: a match in minified JavaScript is one line of 100,000+ characters


def _inside(root: Path, path: str) -> Path:
    target = (root / path).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"{path} is outside the repository")
    return target


def _short(line: str) -> str:
    return line if len(line) <= MAX_LINE_CHARS else f"{line[:MAX_LINE_CHARS]}... ({len(line)} characters)"


def _clip(lines: list[str], limit: int = MAX_LINES) -> str:
    lines = [_short(line) for line in lines]
    if len(lines) > limit:
        lines = [*lines[:limit], f"... {len(lines) - limit} more lines"]
    return "\n".join(lines) or "(nothing)"


def file_listing(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True, check=True).stdout
    return out.splitlines()


def code_tools(root: str | Path) -> list[BaseTool]:
    root = Path(root).resolve()

    @tool
    def list_files(directory: str = ".") -> str:
        """List the files under a directory of the repository, e.g. "datasette/views"."""
        prefix = "" if directory in (".", "") else directory.strip("/") + "/"
        _inside(root, directory)
        return _clip([f for f in file_listing(root) if f.startswith(prefix)], MAX_FILES)

    @tool
    def grep(pattern: str, directory: str = ".") -> str:
        """Search the repository's files for a regular expression. Returns path:line: text."""
        _inside(root, directory)
        result = subprocess.run(["git", "-C", root, "grep", "-n", "-I", "-E", "-e", pattern, "--", directory],
                                capture_output=True, text=True)
        if result.returncode > 1:
            return f"Error: {result.stderr.strip()}"
        return _clip(result.stdout.splitlines())

    @tool
    def read_file(path: str, start: int = 1) -> str:
        """Read a file of the repository, MAX_LINES lines from line `start`."""
        target = _inside(root, path)
        if not target.is_file():
            return f"Error: no file {path}"
        lines = target.read_text(errors="replace").splitlines()
        window = [f"{n}: {_short(line)}" for n, line in enumerate(lines[start - 1:start - 1 + MAX_LINES], start)]
        more = f"\n... file continues to line {len(lines)}" if start - 1 + MAX_LINES < len(lines) else ""
        return "\n".join(window) + more

    read_file.description = read_file.description.replace("MAX_LINES", str(MAX_LINES))
    return [list_files, grep, read_file]
