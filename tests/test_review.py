import json

from defectrefiner import review

DRAFT = """# Thing breaks

**Severity:** high · **Points:** 2 · **Triage:** {triage}

Summary.

## Open questions
- Which version? (Assuming: the latest.)

---
Refined from [o/r#1](u) by defectrefiner. Not reviewed by a person.
"""


def test_review_moves_and_logs(tmp_path):
    drafts, tickets, log = tmp_path / "drafts", tmp_path / "tickets", tmp_path / "reviews.jsonl"
    drafts.mkdir()
    for n, verdict in [(1, "bug"), (2, "already_fixed"), (3, "bug"), (4, "bug")]:
        (drafts / f"issue-{n}.md").write_text(DRAFT.format(triage=verdict))
    answers = iter(["a", "x", "e", "r", "Wrong file", "s"])  # "x" isn't a choice: asked again
    ticks = iter(range(0, 100, 5))

    decisions = review.review(drafts, ask=lambda _: next(answers), editor=lambda text: text.replace("Summary.", "Better."),
                              clock=lambda: next(ticks), tickets_dir=tickets, log=log)

    assert [d["decision"] for d in decisions] == ["approve", "edit", "reject"]
    assert decisions[1]["triage"] == "already_fixed" and decisions[1]["changed"] == 0.09  # 1 of 11 lines
    assert "Approved by a person on" in (tickets / "issue-1.md").read_text()
    edited = (tickets / "issue-2.md").read_text()
    assert "Better." in edited and "Edited and approved by a person" in edited
    assert (tickets / "rejected" / "issue-3.md").read_text().endswith("Not reviewed by a person.\n")
    assert [p.name for p in drafts.iterdir()] == ["issue-4.md"]  # skipped: still a draft

    summary = review.report(log)
    assert summary["reviewed"] == 3 and summary["quick_approvals"] == 1  # the approval took 5 s
    assert summary["approved_as_is"] == "0/2 (0%)" and summary["rejected"] == "1/2 (50%)"
    assert summary["rejection_reasons"] == ["Wrong file"] and summary["median_share_changed_when_edited"] == 0.09
    assert json.loads(log.read_text().splitlines()[0])["seconds"] == 5


def test_show_puts_questions_first():
    shown = review.show(DRAFT.format(triage="not_a_bug"))
    assert shown.index("Which version?") < shown.index("Summary.")
    assert shown.splitlines()[1] == "Triage: not_a_bug"


def test_changed():
    assert review.changed("a\nb\nc\nd", "a\nb\nc\nd") == 0
    assert review.changed("a\nb\nc\nd", "a\nX\nc\nd") == 0.25
    assert review.changed("a\nb", "a\nb\nnew line") == 0.33  # an added line counts too


def test_quick_approvals_are_counted_apart(tmp_path):
    log = tmp_path / "reviews.jsonl"
    rows = [("approve", 1), ("approve", 2), ("approve", 40), ("edit", 3), ("reject", 60)]  # a quick edit or reject was still a choice
    log.write_text("".join(json.dumps({"decision": d, "seconds": t, "changed": 0.1, "reason": None}) + "\n" for d, t in rows))
    summary = review.report(log)
    assert summary["quick_approvals"] == 2
    assert summary["approved_as_is"] == "1/3 (33%)" and summary["approved_with_edits"] == "1/3 (33%)"


def test_only_quick_approvals(tmp_path):
    log = tmp_path / "reviews.jsonl"
    log.write_text(json.dumps({"decision": "approve", "seconds": 1, "changed": 0, "reason": None}) + "\n")
    assert review.report(log)["approved_as_is"] is None  # nothing was read, so no rate
