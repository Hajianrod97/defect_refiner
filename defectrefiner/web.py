"""The review page: a local web app for approving, editing or rejecting
draft tickets. It's a front end on defectrefiner.review, so decisions are
moved and logged the same way.

    uv run python -m defectrefiner.web        # opens http://127.0.0.1:8000

It listens on localhost only: it can change files in the repository.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from markdown_it import MarkdownIt
from pydantic import BaseModel

from defectrefiner import review
from defectrefiner.bugs import DRAFTS_DIR, TICKETS_DIR

PAGE = Path(__file__).with_name("web.html")
# Raw HTML in a ticket is shown as text, not run: tickets quote bug reports
# written by anyone. markdown-it also refuses javascript: links.
_markdown = MarkdownIt("commonmark", {"html": False}).enable("table")


class Decision(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    seconds: float
    edited: str | None = None
    reason: str | None = None


class Preview(BaseModel):
    text: str


def create_app(drafts_dir: Path = DRAFTS_DIR, tickets_dir: Path = TICKETS_DIR, log: Path = review.LOG) -> FastAPI:
    app = FastAPI(title="defectrefiner review")

    def draft(name: str) -> Path:
        # Only names of existing drafts: a name like "../x" never reaches the filesystem.
        found = {p.stem: p for p in drafts_dir.glob("*.md")}
        if name not in found:
            raise HTTPException(404, f"No draft {name}")
        return found[name]

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE.read_text()

    @app.get("/api/drafts")
    def drafts() -> list[dict]:
        return [{"name": p.stem, "title": p.read_text().splitlines()[0].removeprefix("# "), "triage": review.triage(p.read_text())}
                for p in sorted(drafts_dir.glob("*.md"))]

    @app.get("/api/drafts/{name}")
    def one(name: str) -> dict:
        text = draft(name).read_text()
        return {"name": name, "text": text, "html": _markdown.render(text)}

    @app.post("/api/preview")
    def preview(body: Preview) -> dict:
        return {"html": _markdown.render(body.text)}

    @app.post("/api/drafts/{name}")
    def decide(name: str, body: Decision) -> dict:
        if body.decision == "edit" and not body.edited:
            raise HTTPException(422, "An edit needs the edited text")
        return review.decide(draft(name), body.decision, body.seconds, edited=body.edited, reason=body.reason,
                             tickets_dir=tickets_dir, log=log)

    @app.get("/api/report")
    def report() -> dict:
        return review.report(log)

    return app


def main() -> int:
    import threading
    import webbrowser

    import uvicorn

    url = "http://127.0.0.1:8000"
    threading.Timer(1, webbrowser.open, [url]).start()
    print(f"Review page at {url} (Ctrl+C to stop)")
    uvicorn.run(create_app(), host="127.0.0.1", port=8000, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
