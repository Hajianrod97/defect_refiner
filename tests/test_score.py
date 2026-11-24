from datetime import UTC, datetime

from defectrefiner.grooming import Grooming, Story
from defectrefiner.score import (
    BAD_CRITERIA,
    Coverage,
    Criteria,
    chore_stories,
    judge_check,
    points_spread,
    score,
    trap_caught,
)
from defectrefiner.tickets import Ticket, Trap

EXISTS = ["already", "exist"]


def story(title, *criteria, points=3):
    return Story(title=title, user_story=f"As a user I want {title}", acceptance_criteria=list(criteria), points=points)


def test_mention_trap():
    trap = Trap(kind="already_exists", expect="", where=["risks"], all_of=[["cors"], EXISTS])
    assert trap_caught(trap, Grooming(stories=[], risks=["CORS is already supported with --cors"]))
    assert not trap_caught(trap, Grooming(stories=[], risks=["Add CORS headers", "This already exists elsewhere"]))
    assert not trap_caught(trap, Grooming(stories=[story("CORS already exists")]))  # not in risks


def test_words_match_at_word_starts():
    trap = Trap(kind="two_in_one", expect="", where=["stories"], separate=[["rate"], ["count"]])
    assert not trap_caught(trap, Grooming(stories=[story("Generate account pages"), story("Moderate")]))
    assert trap_caught(trap, Grooming(stories=[story("Rate-limit writes"), story("Row counts")]))


def test_two_in_one_needs_separate_stories():
    trap = Trap(kind="two_in_one", expect="", where=["stories"], separate=[["dark"], ["vacuum"]])
    assert not trap_caught(trap, Grooming(stories=[story("Dark mode and vacuum command")]))
    assert not trap_caught(trap, Grooming(stories=[story("Dark mode and vacuum"), story("Vacuum CLI")]))
    assert trap_caught(trap, Grooming(stories=[story("Dark mode"), story("Vacuum CLI")]))


def test_points_spread():
    assert points_spread([8, 8, 8]) == 0
    assert points_spread([5, 8, 13]) == 1
    assert points_spread([5]) is None


def fake_ask(system, user, schema):
    """Says "yes" when the required work appears in the plan word for word."""
    if schema is Coverage:
        plan = user.split("<plan>")[1].split("</plan>")[0]
        item = user.split("<required_work>")[1].split("</required_work>")[0].strip()
        return Coverage(reason="", verdict="yes" if item in plan else "no")
    bad = {c for _, criteria in BAD_CRITERIA for c in criteria}
    return Criteria(reason="", testable=not any(c in user for c in bad))


TICKET = Ticket(id="ds-1", source="real", repo="r", title="t", body="b", created_at=datetime(2024, 1, 1, tzinfo=UTC),
                reference=["Add the CLI option", "Write tests", "Document it"])


def test_score():
    grooming = Grooming(stories=[story("Add the CLI option", "exit code 0", points=3), story("Write tests", points=2)])
    result = score(TICKET, grooming, fake_ask)
    assert [c.verdict for c in result.coverage] == ["yes", "yes", "no"]
    assert result.coverage_rate() == 2 / 3
    assert result.points == 5 and result.traps == []


def test_judge_check_with_a_perfect_judge():
    result = judge_check([TICKET], fake_ask)
    assert result["coverage"]["kept"]["yes"] == 2 and result["coverage"]["dropped"]["no"] == 1
    assert result["testable"] == {"good": 4, "bad": 0}


def test_long_text_is_trimmed_not_rejected():
    long = "x" * 500
    assert len(Coverage(reason=long, verdict="yes").reason) == 300
    assert len(story(long).title) == 120
    assert Coverage.model_json_schema()["properties"]["reason"]["maxLength"] == 300  # still there for Ollama


def test_long_lists_are_trimmed_not_rejected():
    g = Grooming(stories=[story("s")], open_questions=[f"q{n}" for n in range(12)])
    assert len(g.open_questions) == 8


def test_chore_stories():
    g = Grooming(stories=[story("Tests for create-token options"), story("Update documentation"),
                          story("Add test fixtures support"), story("Limited-permission tokens")])
    assert chore_stories(g) == 2
    assert chore_stories(g, "Some plugin hooks have no tests") == 0  # there, test stories are the work


def test_two_in_one_reads_titles():
    trap = Trap(kind="two_in_one", expect="", where=["stories"], separate=[["count"], ["rate"]])
    g = Grooming(stories=[story("Row counts on the database page"), story("Rate-limit writes", "count requests per token")])
    assert trap_caught(trap, g)
