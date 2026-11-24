import pytest

from defectrefiner import team
from defectrefiner.team import Draft, DraftStory, Estimate, Estimates, QaOutput, Review, StoryCriteria, TechNotes


@pytest.fixture
def fake_team(monkeypatch):
    """Roles answer from scripts; records which roles ran, in order."""
    script = {"points": [[13], [3, 5]], "reviews": []}
    ran = []

    def explore(chat, code_dir, messages, max_tool_calls):
        return ["grep(pattern='cors'):\ncli.py:378: --cors"], ["grep(pattern='cors')"]

    def ask(role, config, system, user, schema):
        ran.append(role)
        if schema is TechNotes:
            return TechNotes(findings=["--cors flag in cli.py"], already_exists=["--cors sends the header"])
        if schema is Draft:
            n = 1 if ran.count("product_owner") == 1 else 2
            return Draft(stories=[DraftStory(title=f"Story {i}", user_story="As a user...") for i in range(1, n + 1)],
                         open_questions=["Which origins? (Assuming: any)"])
        if schema is QaOutput:
            n = len(script["points"][0]) if ran.count("product_owner") == 1 else 2
            return QaOutput(stories=[StoryCriteria(story=i, criteria=[f"criterion {i}"]) for i in range(1, n + 1)])
        if schema is Estimates:
            points = script["points"].pop(0) if len(script["points"]) > 1 else script["points"][0]
            return Estimates(estimates=[Estimate(story=i, reason="", points=p) for i, p in enumerate(points, 1)])
        if schema is Review:
            return script["reviews"].pop(0) if script["reviews"] else Review(reason="fine", next="done")
        raise AssertionError(schema)

    monkeypatch.setattr(team, "explore", explore)
    monkeypatch.setattr(team, "_ask", ask)
    monkeypatch.setattr(team, "_chat", lambda role, config: None)
    return script, ran


TICKET = {"repo": "simonw/datasette", "title": "Let other websites call our API", "body": "..."}


def test_too_big_goes_back_to_the_product_owner(fake_team):
    _, ran = fake_team
    grooming, calls, log = team.groom_with_team(TICKET, "/x", team.parse_models(None), "fixed")
    assert ran == ["tech_lead", "product_owner", "qa", "estimator", "product_owner", "qa", "estimator"]
    assert [s.points for s in grooming.stories] == [3, 5]
    assert grooming.stories[0].acceptance_criteria == ["criterion 1"]
    assert grooming.risks == ["Already exists: --cors sends the header"]
    assert calls == ["grep(pattern='cors')"]
    assert "back to product_owner" in log[4]


def test_facilitator_review_routes_and_is_limited(fake_team):
    script, ran = fake_team
    script["points"] = [[3]]
    script["reviews"] = [Review(reason="vague", next="qa", instruction="Make criterion 1 testable")] * 5
    grooming, _, log = team.groom_with_team(TICKET, "/x", team.parse_models(None), "facilitator")
    assert ran.count("qa") == 1 + team.MAX_REVIEWS
    assert log[-1] == "facilitator: done (no send-backs left)"


def test_rules_have_their_own_limit(fake_team):
    script, ran = fake_team
    script["points"] = [[3]]
    script["reviews"] = [Review(reason="vague", next="qa", instruction="sharper")] * 2
    script["points"] = [[3], [3], [13], [3, 5]]  # the second review's redo makes a story too big
    grooming, _, log = team.groom_with_team(TICKET, "/x", team.parse_models(None), "facilitator")
    assert any("Split stories" in line for line in log)  # the rule still fired after both reviews
    assert all(s.points <= team.MAX_POINTS for s in grooming.stories)


def test_parse_models():
    models = team.parse_models("tech_lead=anthropic:claude-sonnet-5-5")
    assert models == {"default": team.DEFAULT_MODEL, "tech_lead": "anthropic:claude-sonnet-5-5"}
    with pytest.raises(ValueError):
        team.parse_models("boss=anthropic:x")
