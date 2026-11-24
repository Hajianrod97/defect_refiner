import os
import subprocess
from datetime import UTC, datetime

import pytest

from defectrefiner import code, graph, tickets


def commit(repo, text, date):
    (repo / "app.py").write_text(text)
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-qm", text], check=True, env=env)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A local repo with one commit in 2021 and one in 2023."""
    monkeypatch.setenv("DEFECTREFINER_CACHE", str(tmp_path / "cache"))
    path = tmp_path / "upstream"
    subprocess.run(["git", "init", "-q", "-b", "main", path], check=True)
    subprocess.run(["git", "-C", path, "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", path, "config", "user.name", "t"], check=True)
    commit(path, "old", "2021-01-01T00:00:00Z")
    commit(path, "new", "2023-01-01T00:00:00Z")
    return path


def test_checkout_shows_the_code_as_it_was(repo):
    old = code.checkout(str(repo), datetime(2022, 1, 1, tzinfo=UTC))
    new = code.checkout(str(repo), datetime(2024, 1, 1, tzinfo=UTC))
    assert (old / "app.py").read_text() == "old"
    assert (new / "app.py").read_text() == "new"
    assert code.checkout(str(repo), datetime(2022, 6, 1, tzinfo=UTC)) == old  # reused, not added again


def test_no_commit_before(repo):
    with pytest.raises(LookupError):
        code.checkout(str(repo), datetime(2020, 1, 1, tzinfo=UTC))


def test_graph_loads_ticket_and_code(repo, tmp_path, monkeypatch):
    ticket = tickets.Ticket(id="w-01", source="written", repo=str(repo), title="t", body="b",
                            created_at=datetime(2022, 1, 1, tzinfo=UTC), reference=["secret split"],
                            traps=[tickets.Trap(kind="ambiguous", expect="secret", all_of=[["x"]])])
    tickets.save([ticket], tmp_path / "t.json")
    monkeypatch.setattr(tickets, "TICKETS", tmp_path / "t.json")

    # The two nodes before the team (running the whole graph would call the models).
    state = graph.load_ticket({"ticket_id": "w-01"})
    assert "reference" not in state["ticket"] and "traps" not in state["ticket"]
    code_dir = graph.check_out_code(state)["code_dir"]
    assert open(os.path.join(code_dir, "app.py")).read() == "old"
