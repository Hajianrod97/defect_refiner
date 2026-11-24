import json
from datetime import UTC, datetime

from defectrefiner import show, tickets
from defectrefiner.grooming import Grooming, Story
from defectrefiner.score import Coverage, TicketScore

TICKET = tickets.Ticket(id="ds-1", source="real", repo="r", title="Limited tokens", body="Tokens carry all permissions.",
                        created_at=datetime(2024, 1, 1, tzinfo=UTC), reference=["Add the CLI options", "Write docs"])
GROOMING = Grooming(stories=[Story(title="Limit token actions", user_story="As an admin...", acceptance_criteria=["A token without view-table gets 403"], points=5)],
                    open_questions=["Expiry required? (Assuming: optional)"], risks=["Already exists: signed actor cookies"])


def test_markdown_has_the_grooming_and_the_answer_key():
    score = TicketScore(ticket_id="ds-1", coverage=[Coverage(reason="", verdict="yes"), Coverage(reason="", verdict="no")],
                        testable=[True], traps=[], points=5)
    md = show.to_markdown(TICKET, {"system": "team", "seconds": 97, "grooming": GROOMING.model_dump(),
                                   "log": ["tech_lead: 8 tool calls"]}, {"sonnet": score})
    assert "# ds-1 · Limited tokens" in md
    assert "### 1. Limit token actions (5 points)" in md and "- [ ] A token without view-table gets 403" in md
    assert "Expiry required?" in md and "## Team log" in md
    assert "- Add the CLI options  \n  *sonnet: yes*" in md
    assert "coverage 50%" in md


def test_export_writes_files_and_an_index(tmp_path, monkeypatch):
    monkeypatch.setattr(show, "load", lambda: [TICKET])
    run = tmp_path / "team-x"
    (run / "ds-1").mkdir(parents=True)
    (run / "ds-1" / "1.json").write_text(json.dumps({"seconds": 5, "grooming": GROOMING.model_dump()}))
    assert show.export(run, tmp_path / "md") == 1
    assert "[ds-1 · Limited tokens](ds-1-1.md)" in (tmp_path / "md" / "index.md").read_text()
    assert (tmp_path / "md" / "ds-1-1.md").exists()
