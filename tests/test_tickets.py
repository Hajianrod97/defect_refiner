import httpx

from defectrefiner.github import fetch_closed_issues
from defectrefiner.tickets import checklist, is_candidate, load, save, strip_checklist, to_ticket

BODY = """Tokens should be able to carry fewer permissions.

- [x] Add the options to `create-token`
- [ ] Tests
* [X] Documentation

More notes."""


def issue(**overrides):
    return {"number": 1855, "title": "Reduced-permission tokens", "body": BODY, "state_reason": "completed",
            "created_at": "2022-10-25T18:00:00Z", "html_url": "https://github.com/simonw/datasette/issues/1855",
            **overrides}


def test_checklist_items_in_order():
    assert checklist(BODY) == ["Add the options to `create-token`", "Tests", "Documentation"]


def test_strip_checklist_keeps_the_rest():
    assert strip_checklist(BODY) == "Tokens should be able to carry fewer permissions.\n\nMore notes."


def test_candidate():
    assert is_candidate(issue())


def test_not_candidates():
    assert not is_candidate(issue(pull_request={}))
    assert not is_candidate(issue(state_reason="not_planned"))
    assert not is_candidate(issue(body="- [x] one\n- [x] two"))
    assert not is_candidate(issue(title="Release 1.0a15"))
    assert not is_candidate(issue(body=BODY + "\n- [x] I have searched existing issues"))
    assert not is_candidate(issue(body=None))


def test_ticket_hides_the_reference_from_the_body(tmp_path):
    ticket = to_ticket("simonw/datasette", issue())
    assert ticket.id == "ds-1855" and ticket.source == "real"
    assert ticket.reference == checklist(BODY)
    assert "[x]" not in ticket.body
    save([ticket], tmp_path / "t.json")
    assert load(tmp_path / "t.json") == [ticket]


def test_fetch_follows_pages():
    pages = {
        "/repos/o/r/issues": ([issue(number=1)], {"link": '<https://api.github.com/page2>; rel="next"'}),
        "/page2": ([issue(number=2)], {}),
    }

    def handler(request):
        body, headers = pages[request.url.path]
        return httpx.Response(200, json=body, headers=headers)

    issues = fetch_closed_issues("o/r", httpx.Client(transport=httpx.MockTransport(handler)))
    assert [i["number"] for i in issues] == [1, 2]
