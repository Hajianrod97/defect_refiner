from defectrefiner.grade import agreement
from defectrefiner.score import Coverage, TicketScore


def test_agreement(tmp_path):
    run = tmp_path / "runs" / "agent" / "ds-1"
    run.mkdir(parents=True)
    verdicts = [Coverage(reason="", verdict=v) for v in ("yes", "yes", "no", "partly")]
    (run / "1.score.judge-x.json").write_text(TicketScore(ticket_id="ds-1", coverage=verdicts, testable=[], traps=[], points=0)
                                      .model_dump_json())
    mine = {"runs/agent/ds-1/1.json": ["yes", "no", "no", "yes"]}
    assert agreement(mine, tmp_path, "judge-x") == {"items": 4, "agree": 0.5, "judge_more_generous": 1, "judge_stricter": 1}
