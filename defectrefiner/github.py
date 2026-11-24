"""Reading issues from the GitHub API. Without GITHUB_TOKEN the limit is 60
requests an hour, enough for a repo with a few thousand issues."""

from __future__ import annotations

import os

import httpx

API = "https://api.github.com"


def fetch_closed_issues(repo: str, client: httpx.Client | None = None) -> list[dict]:
    return fetch_issues(repo, "closed", client=client)


def fetch_issues(repo: str, state: str, label: str | None = None, client: httpx.Client | None = None) -> list[dict]:
    """Every issue of `owner/name` in `state` (open, closed or all), only
    those labelled `label` if given. The API mixes in pull requests; callers
    tell them apart by their `pull_request` key."""
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    client = client or httpx.Client(timeout=30)
    url: str | None = f"{API}/repos/{repo}/issues?state={state}&per_page=100"
    if label:
        url += f"&labels={label}"
    issues: list[dict] = []
    while url:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        issues.extend(response.json())
        url = response.links.get("next", {}).get("url")
    return issues
