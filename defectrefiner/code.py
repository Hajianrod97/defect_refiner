"""The repo's code as it was when a ticket was written, or just before a bug
was fixed: one bare clone per repo, and a read-only worktree per commit, so a
2022 ticket is groomed against 2022's code. Lives in DEFECTREFINER_CACHE (default
~/.cache/defectrefiner).
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path


def cache_dir() -> Path:
    return Path(os.environ.get("DEFECTREFINER_CACHE", "~/.cache/defectrefiner")).expanduser()


def _git(*args: str | Path) -> str:
    return subprocess.run(["git", *map(str, args)], check=True, capture_output=True, text=True).stdout.strip()


def _clone_url(repo: str) -> str:
    """`owner/name` on GitHub, or a local path (used by the tests)."""
    return repo if Path(repo).exists() else f"https://github.com/{repo}.git"


def mirror(repo: str) -> Path:
    """A bare clone of the repo, made once and fetched on later calls."""
    name = re.sub(r"[^A-Za-z0-9_.-]+", "__", repo.strip("/"))
    git_dir = cache_dir() / f"{name}.git"
    if git_dir.exists():
        _git("-C", git_dir, "fetch", "--quiet", "origin", "+refs/heads/*:refs/heads/*")
    else:
        git_dir.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--quiet", "--bare", _clone_url(repo), git_dir)
    return git_dir


def commit_before(git_dir: Path, when: datetime) -> str:
    """The default branch's last commit before `when`. --first-parent stays
    on the main line, so a merged branch's older commits don't count."""
    sha = _git("-C", git_dir, "rev-list", "-1", "--first-parent", f"--before={when.isoformat()}", "HEAD")
    if not sha:
        raise LookupError(f"No commit before {when.isoformat()}")
    return sha


def checkout(repo: str, when: datetime) -> Path:
    """A directory holding the repo's code as of `when`."""
    git_dir = mirror(repo)
    return _worktree(git_dir, commit_before(git_dir, when))


def checkout_at(repo: str, sha: str) -> Path:
    """A directory holding the repo's code at commit `sha`."""
    git_dir = mirror(repo)
    return _worktree(git_dir, _git("-C", git_dir, "rev-parse", "--verify", f"{sha}^{{commit}}"))


def _worktree(git_dir: Path, sha: str) -> Path:
    target = cache_dir() / "checkouts" / f"{git_dir.stem}-{sha[:12]}"
    if not target.exists():
        _git("-C", git_dir, "worktree", "add", "--quiet", "--detach", target, sha)
    return target
