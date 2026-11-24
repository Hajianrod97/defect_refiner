import json

from fastapi.testclient import TestClient

from defectrefiner.web import create_app

DRAFT = """# Thing breaks

**Severity:** high · **Points:** 2 · **Triage:** bug

Summary with <script>alert(1)</script> from the report.

## Open questions
- Which version?

---
Not reviewed by a person.
"""


def client(tmp_path):
    (tmp_path / "drafts").mkdir()
    for n in (1, 2, 3):
        (tmp_path / "drafts" / f"issue-{n}.md").write_text(DRAFT)
    return TestClient(create_app(tmp_path / "drafts", tmp_path / "tickets", tmp_path / "reviews.jsonl"))


def test_review_through_the_page(tmp_path):
    c = client(tmp_path)
    assert c.get("/").text.startswith("<!doctype html>")
    assert c.get("/api/drafts").json()[0] == {"name": "issue-1", "title": "Thing breaks", "triage": "bug"}
    html = c.get("/api/drafts/issue-1").json()["html"]
    assert "<h2>Open questions</h2>" in html and "<script>" not in html  # a report's HTML is shown, not run

    assert c.post("/api/drafts/issue-1", json={"decision": "approve", "seconds": 12.4}).json()["decision"] == "approve"
    c.post("/api/drafts/issue-2", json={"decision": "edit", "seconds": 30, "edited": DRAFT.replace("Summary", "Better")})
    c.post("/api/drafts/issue-3", json={"decision": "reject", "seconds": 5, "reason": "Wrong cause"})

    assert c.get("/api/drafts").json() == []
    assert "Better" in (tmp_path / "tickets" / "issue-2.md").read_text()
    assert (tmp_path / "tickets" / "rejected" / "issue-3.md").exists()
    assert [json.loads(line)["decision"] for line in (tmp_path / "reviews.jsonl").read_text().splitlines()] == ["approve", "edit", "reject"]
    assert c.get("/api/report").json()["rejection_reasons"] == ["Wrong cause"]


def test_bad_requests(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/drafts/..%2F..%2Fsecret").status_code == 404
    assert c.post("/api/drafts/nope", json={"decision": "approve", "seconds": 1}).status_code == 404
    assert c.post("/api/drafts/issue-1", json={"decision": "edit", "seconds": 1}).status_code == 422  # no text
    assert c.post("/api/drafts/issue-1", json={"decision": "delete", "seconds": 1}).status_code == 422
    assert c.post("/api/preview", json={"text": "# Hi"}).json() == {"html": "<h1>Hi</h1>\n"}
