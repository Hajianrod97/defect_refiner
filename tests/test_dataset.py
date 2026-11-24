"""Checks on the curated set in data/tickets.json."""

from defectrefiner.tickets import load


def test_ids_are_unique_and_sources_match():
    tickets = load()
    assert len({t.id for t in tickets}) == len(tickets)
    for t in tickets:
        assert t.id.startswith("ds-" if t.source == "real" else "w-")
        assert t.reference


def test_written_tickets_have_checkable_traps():
    for t in load():
        assert bool(t.traps) == (t.source == "written")
        for trap in t.traps:
            if trap.kind == "two_in_one":
                assert len(trap.separate) >= 2 and trap.where == ["stories"]
            else:
                assert trap.all_of and not trap.separate


def test_bodies_dont_give_the_answer_away():
    """No reference item appears word for word in what the agents see."""
    for t in load():
        text = f"{t.title}\n{t.body}".lower()
        assert "[x]" not in text and "[ ]" not in text
        for item in t.reference:
            assert item.lower() not in text, (t.id, item)
